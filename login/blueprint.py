from flask import Blueprint, render_template, request, jsonify, session
from .db import get_db_connection, init_db_structures
from .utils import encrypt_password, decrypt_password, authenticate_odoo, get_odoo_config
import xmlrpc.client


def init_login_db(app):
    max_attempts = app.config.get('LOGIN_MAX_FAILED_ATTEMPTS', 3)
    app_name = app.config.get('LOGIN_APP_NAME', 'App')
    init_db_structures(app, app_name=app_name, max_failed_attempts=max_attempts)


def create_login_blueprint(app):
    bp = Blueprint('login_bp', __name__, template_folder='templates')

    ALLOWED_ROLES = ("SuperAdmin", "Admin", "Responsable", "Usuario")
    MAX_FAILED_ATTEMPTS = app.config.get('LOGIN_MAX_FAILED_ATTEMPTS', 3)
    THIS_APP_NAME = app.config.get('LOGIN_APP_NAME', 'App')
    ODOO_URL, ODOO_DB = get_odoo_config(app)

    def _maybe_decrypt(val: str):
        if not val:
            return None
        try:
            return decrypt_password(val)
        except Exception:
            return val

    def _resolve_assignment(cur, application_name: str, username: str):
        try:
            cur.execute(
                """
                SELECT r.id, r.failed_attempts, r.is_blocked, r.blocked_at
                FROM app_user_roles r
                JOIN applications a ON a.id = r.application_id
                JOIN odoo_users u ON u.id = r.odoo_user_id
                WHERE r.user_kind='odoo' AND a.name = %s AND u.username = %s
                LIMIT 1
                """,
                (application_name, username),
            )
            row = cur.fetchone()
            if row:
                return row
        except Exception:
            pass
        try:
            cur.execute(
                """
                SELECT id, failed_attempts, is_blocked, blocked_at
                FROM app_user_roles
                WHERE application_name = %s AND odoo_username = %s
                LIMIT 1
                """,
                (application_name, username),
            )
            return cur.fetchone()
        except Exception:
            return None

    def _get_assignment_role(cur, application_name: str, username: str):
        try:
            cur.execute(
                """
                SELECT r.id, r.role, r.is_blocked
                FROM app_user_roles r
                JOIN applications a ON a.id = r.application_id
                JOIN odoo_users u ON u.id = r.odoo_user_id
                WHERE r.user_kind='odoo' AND a.name = %s AND u.username = %s
                LIMIT 1
                """,
                (application_name, username),
            )
            row = cur.fetchone()
            if row:
                return row
        except Exception:
            cur.execute(
                """
                SELECT id, role, is_blocked
                FROM app_user_roles
                WHERE application_name = %s AND odoo_username = %s
                LIMIT 1
                """,
                (application_name, username),
            )
            return cur.fetchone()

    @bp.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'GET':
            nxt = request.args.get('next')
            redirect_url = nxt if nxt else None
            return render_template('login/login.html', redirect_url=redirect_url, MAX_LOGIN_ATTEMPTS=MAX_FAILED_ATTEMPTS)

        data = request.get_json(silent=True) or {}
        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            return jsonify({"success": False, "message": "Usuario y contraseña requeridos"}), 400

        # 1) Try INTERNAL user path
        internal_ok = False
        internal_odoo_user = None
        with get_db_connection(app) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "SELECT id, internal_password, odoo_username, odoo_password FROM internal_users WHERE name=%s",
                        (username,)
                    )
                    iu = cur.fetchone()
                except Exception:
                    iu = None
        if iu:
            stored_plain = _maybe_decrypt(iu[1]) if iu[1] else None
            if stored_plain and stored_plain == password:
                with get_db_connection(app) as conn:
                    with conn.cursor() as cur:
                        try:
                            cur.execute(
                                """
                                SELECT r.id, r.role, r.is_blocked, iu.odoo_username, iu.odoo_password
                                FROM app_user_roles r
                                JOIN applications a ON a.id = r.application_id
                                JOIN internal_users iu ON iu.id = r.internal_user_id
                                WHERE r.user_kind='internal' AND a.name=%s AND iu.name=%s
                                LIMIT 1
                                """,
                                (THIS_APP_NAME, username),
                            )
                            rrow = cur.fetchone()
                        except Exception:
                            rrow = None
                if rrow:
                    assignment_id, role, is_blocked, iu_ouser, iu_opass = rrow
                    if is_blocked:
                        return jsonify({"success": False, "message": "Usuario bloqueado para esta aplicación"}), 423
                    if role not in ("SuperAdmin", "Admin"):
                        return jsonify({"success": False, "message": "No tiene permisos para acceder a esta aplicación"}), 403
                    ouser = _maybe_decrypt(iu_ouser) if iu_ouser else None
                    opass = _maybe_decrypt(iu_opass) if iu_opass else None
                    if not ouser or not opass:
                        return jsonify({"success": False, "message": "Usuario interno sin credenciales de Odoo configuradas"}), 401
                    uid = authenticate_odoo(ODOO_URL, ODOO_DB, ouser, opass)
                    if not uid:
                        return jsonify({"success": False, "message": "Credenciales de Odoo inválidas para el usuario interno"}), 401
                    session.clear()
                    session['username'] = username
                    session['role'] = role
                    session['app_name'] = THIS_APP_NAME
                    session['user_kind'] = 'internal'
                    session['odoo_username'] = ouser
                    session['odoo_password'] = opass
                    session['display_name'] = username
                    return jsonify({"success": True})
                else:
                    with get_db_connection(app) as conn:
                        with conn.cursor() as cur:
                            try:
                                cur.execute(
                                    """
                                    SELECT r.id, r.is_blocked
                                    FROM app_user_roles r
                                    JOIN applications a ON a.id = r.application_id
                                    JOIN internal_users iu ON iu.id = r.internal_user_id
                                    WHERE r.user_kind='internal' AND a.name=%s AND iu.name=%s
                                    LIMIT 1
                                    """,
                                    (THIS_APP_NAME, username),
                                )
                                prow = cur.fetchone()
                                if prow and not prow[1]:
                                    cur.execute("UPDATE app_user_roles SET failed_attempts = failed_attempts + 1 WHERE id=%s", (prow[0],))
                            except Exception:
                                pass
                    return jsonify({"success": False, "message": "No tiene permisos para acceder a esta aplicación"}), 403
            else:
                with get_db_connection(app) as conn:
                    with conn.cursor() as cur:
                        try:
                            cur.execute(
                                """
                                SELECT r.id, r.is_blocked
                                FROM app_user_roles r
                                JOIN applications a ON a.id = r.application_id
                                JOIN internal_users iu ON iu.id = r.internal_user_id
                                WHERE r.user_kind='internal' AND a.name=%s AND iu.name=%s
                                LIMIT 1
                                """,
                                (THIS_APP_NAME, username),
                            )
                            prow = cur.fetchone()
                            if prow and not prow[1]:
                                cur.execute("UPDATE app_user_roles SET failed_attempts = failed_attempts + 1 WHERE id=%s", (prow[0],))
                        except Exception:
                            pass

        # 2) Fallback to ODOO user path
        uid = authenticate_odoo(ODOO_URL, ODOO_DB, username, password)
        if uid:
            with get_db_connection(app) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, password FROM odoo_users WHERE username=%s", (username,))
                    row_user = cur.fetchone()
                    enc_pwd = encrypt_password(password)
                    if not row_user:
                        cur.execute(
                            "INSERT INTO odoo_users (username, password) VALUES (%s, %s)",
                            (username, enc_pwd),
                        )
                    else:
                        if row_user[1] != enc_pwd:
                            cur.execute(
                                "UPDATE odoo_users SET password=%s WHERE id=%s",
                                (enc_pwd, row_user[0]),
                            )

            with get_db_connection(app) as conn:
                with conn.cursor() as cur:
                    assignment = _get_assignment_role(cur, THIS_APP_NAME, username)
                    if not assignment:
                        return jsonify({"success": False, "message": "No tiene permisos para acceder a esta aplicación"}), 403
                    assignment_id, role, is_blocked = assignment
                    if is_blocked:
                        return jsonify({"success": False, "message": "Usuario bloqueado para esta aplicación"}), 423
                    if role not in ("SuperAdmin", "Admin"):
                        return jsonify({"success": False, "message": "No tiene permisos para acceder a esta aplicación"}), 403

            session.clear()
            session['username'] = username
            session['role'] = role
            session['app_name'] = THIS_APP_NAME
            session['user_kind'] = 'odoo'
            try:
                common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
                uid2 = common.authenticate(ODOO_DB, username, password, {})
                if uid2:
                    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
                    rows = models.execute_kw(
                        ODOO_DB, uid2, password,
                        'res.users', 'read',
                        [[uid2], ['name']],
                    )
                    if rows and isinstance(rows, list) and 'name' in rows[0]:
                        session['display_name'] = rows[0]['name'] or username
                    else:
                        session['display_name'] = username
                else:
                    session['display_name'] = username
            except Exception:
                session['display_name'] = username
            return jsonify({"success": True})

        # Invalid credentials: increment attempts
        with get_db_connection(app) as conn:
            with conn.cursor() as cur:
                row = _resolve_assignment(cur, THIS_APP_NAME, username)
                if row and not row[2]:
                    cur.execute("UPDATE app_user_roles SET failed_attempts = failed_attempts + 1 WHERE id=%s", (row[0],))
        return jsonify({"success": False, "message": "Credenciales inválidas"}), 401

    @bp.route('/api/status')
    def api_status():
        application = request.args.get("application")
        username = request.args.get("username")
        if not application or not username:
            return jsonify({"success": False, "error": "Parámetros requeridos: application, username"}), 400
        with get_db_connection(app) as conn:
            with conn.cursor() as cur:
                row = _resolve_assignment(cur, application, username)
                if not row:
                    return jsonify({"success": False, "error": "Asignación no encontrada"}), 404
                assignment_id, failed_attempts, is_blocked, blocked_at = row
                return jsonify({
                    "success": True,
                    "application": application,
                    "username": username,
                    "failed_attempts": failed_attempts,
                    "blocked": bool(is_blocked),
                    "blocked_at": blocked_at.isoformat() if blocked_at else None,
                })

    @bp.route('/api/failed_login', methods=['POST'])
    def api_failed_login():
        data = request.get_json(silent=True) or {}
        application = data.get("application")
        username = data.get("username")
        if not application or not username:
            return jsonify({"success": False, "error": "Parámetros requeridos: application, username"}), 400
        with get_db_connection(app) as conn:
            with conn.cursor() as cur:
                row = _resolve_assignment(cur, application, username)
                if not row:
                    return jsonify({"success": False, "error": "Asignación no encontrada"}), 404
                assignment_id, failed_attempts, is_blocked, _ = row
                if is_blocked:
                    cur.execute("SELECT failed_attempts, is_blocked, blocked_at FROM app_user_roles WHERE id=%s", (assignment_id,))
                    r2 = cur.fetchone()
                    return jsonify({
                        "success": True,
                        "application": application,
                        "username": username,
                        "failed_attempts": r2[0],
                        "blocked": bool(r2[1]),
                        "blocked_at": r2[2].isoformat() if r2[2] else None,
                    })
                new_attempts = failed_attempts + 1
                if new_attempts >= MAX_FAILED_ATTEMPTS:
                    cur.execute(
                        "UPDATE app_user_roles SET failed_attempts=%s, is_blocked=TRUE, blocked_at=NOW() WHERE id=%s",
                        (new_attempts, assignment_id),
                    )
                    blocked = True
                else:
                    cur.execute("UPDATE app_user_roles SET failed_attempts=%s WHERE id=%s", (new_attempts, assignment_id))
                    blocked = False
                return jsonify({
                    "success": True,
                    "application": application,
                    "username": username,
                    "failed_attempts": new_attempts,
                    "blocked": blocked,
                })

    @bp.route('/api/unblock', methods=['POST'])
    def api_unblock():
        data = request.get_json(silent=True) or {}
        application = data.get("application")
        username = data.get("username")
        if not application or not username:
            return jsonify({"success": False, "error": "Parámetros requeridos: application, username"}), 400
        with get_db_connection(app) as conn:
            with conn.cursor() as cur:
                row = _resolve_assignment(cur, application, username)
                if not row:
                    return jsonify({"success": False, "error": "Asignación no encontrada"}), 404
                assignment_id = row[0]
                cur.execute("UPDATE app_user_roles SET failed_attempts=0, is_blocked=FALSE, blocked_at=NULL WHERE id=%s", (assignment_id,))
        return jsonify({"success": True, "message": "Usuario desbloqueado y contadores reiniciados"})

    @bp.route('/api/block', methods=['POST'])
    def api_block():
        data = request.get_json(silent=True) or {}
        application = data.get("application")
        username = data.get("username")
        if not application or not username:
            return jsonify({"success": False, "error": "Parámetros requeridos: application, username"}), 400
        with get_db_connection(app) as conn:
            with conn.cursor() as cur:
                row = _resolve_assignment(cur, application, username)
                if not row:
                    return jsonify({"success": False, "error": "Asignación no encontrada"}), 404
                assignment_id = row[0]
                cur.execute("UPDATE app_user_roles SET is_blocked=TRUE, blocked_at=COALESCE(blocked_at, NOW()) WHERE id=%s", (assignment_id,))
        return jsonify({"success": True, "message": "Usuario bloqueado"})

    return bp

import os
import psycopg2
from typing import Optional
from config import login_db_config


def get_db_connection(app=None):
    """Create a new psycopg2 connection using app config or environment variables."""
    host = None
    dbname = None
    user = None
    password = None
    if app is not None:
        host = app.config.get('DB_HOST')
        dbname = app.config.get('DB_NAME')
        user = app.config.get('DB_USER')
        password = app.config.get('DB_PASSWORD')
    if host is None or dbname is None or user is None or password is None:
        cfg = login_db_config()
        host = host or cfg['host']
        dbname = dbname or cfg['database']
        user = user or cfg['user']
        password = password or cfg['password']
    conn = psycopg2.connect(host=host, database=dbname, user=user, password=password)
    conn.autocommit = True
    return conn


def init_db_structures(app=None, app_name: Optional[str] = None, max_failed_attempts: int = 3):
    """Create/update tables and triggers required for login + lockout logic.
    Safe to re-run multiple times.
    """
    from .utils import encrypt_password
    app_name = app_name or (app and app.config.get('LOGIN_APP_NAME')) or os.environ.get('LOGIN_APP_NAME', 'App')
    with get_db_connection(app) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS internal_users (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    internal_password TEXT NOT NULL,
                    odoo_username TEXT,
                    odoo_password TEXT,
                    odoo_user_id INTEGER
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS odoo_users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                )
                """
            )
            try:
                cur.execute(
                    "INSERT INTO applications (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (app_name,),
                )
            except Exception:
                conn.rollback()

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_user_roles (
                    id SERIAL PRIMARY KEY,
                    user_kind TEXT NOT NULL CHECK (user_kind IN ('internal','odoo')),
                    internal_user_id INTEGER REFERENCES internal_users(id),
                    odoo_user_id INTEGER REFERENCES odoo_users(id),
                    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('SuperAdmin','Admin','Responsable','Usuario')),
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                    blocked_at TIMESTAMP NULL,
                    application_name TEXT,
                    odoo_username TEXT,
                    CHECK (
                        (user_kind='internal' AND internal_user_id IS NOT NULL AND odoo_user_id IS NULL) OR
                        (user_kind='odoo' AND odoo_user_id IS NOT NULL AND internal_user_id IS NULL)
                    )
                )
                """
            )

            try:
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_roles_internal ON app_user_roles (internal_user_id, application_id) WHERE user_kind='internal'"
                )
            except Exception:
                conn.rollback()
            try:
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_roles_odoo ON app_user_roles (odoo_user_id, application_id) WHERE user_kind='odoo'"
                )
            except Exception:
                conn.rollback()
            try:
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS ix_app_user_roles_app_user ON app_user_roles (application_name, odoo_username)"
                )
            except Exception:
                conn.rollback()

            try:
                cur.execute(
                    f"""
                    CREATE OR REPLACE FUNCTION app_user_roles_lockout_enforce()
                    RETURNS trigger AS $$
                    BEGIN
                        IF NEW.failed_attempts >= {max_failed_attempts} THEN
                            NEW.is_blocked := TRUE;
                            IF NEW.blocked_at IS NULL THEN
                                NEW.blocked_at := NOW();
                            END IF;
                        END IF;
                        IF NEW.failed_attempts = 0 THEN
                            NEW.is_blocked := FALSE;
                            NEW.blocked_at := NULL;
                        END IF;
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql;

                    DROP TRIGGER IF EXISTS trg_app_user_roles_lockout ON app_user_roles;
                    CREATE TRIGGER trg_app_user_roles_lockout
                    BEFORE INSERT OR UPDATE OF failed_attempts ON app_user_roles
                    FOR EACH ROW
                    EXECUTE PROCEDURE app_user_roles_lockout_enforce();
                    """
                )
            except Exception:
                conn.rollback()

            superadmins = []
            if app is not None:
                raw = app.config.get('SUPERADMIN_EMAILS')
                if raw:
                    superadmins = [e.strip().lower() for e in raw.split(',') if e.strip()]
            else:
                raw = os.environ.get('SUPERADMIN_EMAILS')
                if raw:
                    superadmins = [e.strip().lower() for e in raw.split(',') if e.strip()]

            if superadmins:
                cur.execute("SELECT id FROM applications WHERE name=%s", (app_name,))
                app_row = cur.fetchone()
                if app_row:
                    app_id = app_row[0]
                    for email in superadmins:
                        try:
                            cur.execute(
                                "INSERT INTO odoo_users (username, password) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
                                (email, encrypt_password('setme')),
                            )
                        except Exception:
                            conn.rollback()
                        cur.execute("SELECT id FROM odoo_users WHERE username=%s", (email,))
                        ou = cur.fetchone()
                        if ou:
                            ou_id = ou[0]
                            try:
                                cur.execute(
                                    "DELETE FROM app_user_roles WHERE user_kind='odoo' AND odoo_user_id=%s AND application_id=%s",
                                    (ou_id, app_id),
                                )
                            except Exception:
                                conn.rollback()
                            cur.execute(
                                """
                                INSERT INTO app_user_roles (user_kind, odoo_user_id, application_id, role, application_name, odoo_username)
                                VALUES ('odoo', %s, %s, 'SuperAdmin', %s, %s)
                                """,
                                (ou_id, app_id, app_name, email),
                            )

"""
Capa de acceso a datos para panelDistri.
Lee de dos bases de datos PostgreSQL: distriTest y ControlDistriV2Test.
Crea tablas propias del panel en distriTest.
"""

import psycopg2
import psycopg2.extras
from datetime import datetime, date
from config import CONFIG


# ─── Conexiones ───────────────────────────────────────────────────────────────

def get_distri_conn():
    return psycopg2.connect(**CONFIG['DISTRI_DB'], cursor_factory=psycopg2.extras.RealDictCursor)

def get_control_conn():
    return psycopg2.connect(**CONFIG['CONTROL_DB'], cursor_factory=psycopg2.extras.RealDictCursor)


# ─── Inicialización de tablas propias del panel ───────────────────────────────

def init_panel_tables():
    """Crea las tablas específicas del panel si no existen."""
    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                # Metadata extra por pedido: prioridad, pickea, corre, etc.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS panel_pedidos_meta (
                        pedido         TEXT PRIMARY KEY,
                        prioridad      TEXT DEFAULT 'MEDIA',
                        pickea         TEXT,
                        corre          TEXT,
                        confirmado     BOOLEAN DEFAULT FALSE,
                        fecha_pedido   DATE,
                        cod_cliente    TEXT,
                        updated_at     TIMESTAMPTZ DEFAULT now()
                    )
                """)
                # Motivos de órdenes incompletas
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS panel_motivos (
                        id              SERIAL PRIMARY KEY,
                        pedido          TEXT NOT NULL,
                        cliente         TEXT,
                        motivo          TEXT NOT NULL,
                        submotivo       TEXT,
                        observaciones   TEXT,
                        registrado_por  TEXT,
                        fecha           TIMESTAMPTZ DEFAULT now()
                    )
                """)
            conn.commit()
    except Exception as e:
        print(f"[panelDistri] Error inicializando tablas: {e}")


# ─── Lectura de datos ─────────────────────────────────────────────────────────

def get_pedidos_estado():
    """
    Consolida pedidos de ambas bases de datos.
    Fuente primaria: distriTest.order_state_tracking (current_state='sale') + sales_queue + panel_pedidos_meta
    Enriquecido con: ControlDistriV2Test.control_distri_history
    """
    pedidos = {}

    # 1. Cola de pedidos (distriTest)
    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        ost.order_name                AS sale_order,
                        COALESCE(sq.client_name, '') AS client_name,
                        COALESCE(sq.pick_lines,  0)  AS pick_lines,
                        ost.current_state,
                        ost.last_checked,
                        pm.prioridad,
                        pm.pickea,
                        pm.corre,
                        pm.confirmado,
                        pm.fecha_pedido,
                        pm.cod_cliente
                    FROM order_state_tracking ost
                    LEFT JOIN sales_queue sq        ON sq.sale_order = ost.order_name
                    LEFT JOIN panel_pedidos_meta pm ON pm.pedido     = ost.order_name
                    WHERE ost.current_state = 'sale'
                    -- AND ost.last_checked >= CURRENT_DATE - INTERVAL '7 days'
                    ORDER BY
                        CASE
                            WHEN pm.prioridad = 'ALTA'  THEN 1
                            WHEN pm.prioridad = 'MEDIA' THEN 2
                            ELSE 3
                        END,
                        ost.order_name DESC
                """)
                for row in cur.fetchall():
                    pedidos[row['sale_order']] = {
                        'pedido':              row['sale_order'],
                        'cliente':             row['client_name'] or '',
                        'pick_lines':          row['pick_lines'] or 0,
                        'estado':              row['current_state'] or 'draft',
                        'ultima_actualizacion': row['last_checked'].isoformat() if row['last_checked'] else None,
                        'prioridad':           row['prioridad'] or 'MEDIA',
                        'pickea':              row['pickea'] or '',
                        'corre':               row['corre'] or '',
                        'confirmado':          row['confirmado'] or False,
                        'fecha_pedido':        row['fecha_pedido'].strftime('%d/%m') if row['fecha_pedido'] else '',
                        'cod_cliente':         row['cod_cliente'] or '',
                        'ss_pick':             0,
                        'ss_pick_total':       row['pick_lines'] or 0,
                        'ss_corre':            0,
                        'ss_corre_total':      0,
                        'control_status':      None,
                        'control_user':        '',
                    }
    except Exception as e:
        print(f"[panelDistri] Error leyendo distriTest: {e}")

    # 2. Progreso de control (ControlDistriV2Test)
    try:
        with get_control_conn() as conn:
            with conn.cursor() as cur:
                # Historial de control del día actual (primera ocurrencia por pedido)
                cur.execute("""
                    SELECT DISTINCT ON (order_name)
                        order_name,
                        user_name,
                        status,
                        total_items_scanned,
                        total_items_required
                    FROM control_distri_history
                    WHERE DATE(COALESCE(end_time, start_time)) = CURRENT_DATE
                    ORDER BY order_name, COALESCE(end_time, start_time) DESC
                """)
                for row in cur.fetchall():
                    name = row['order_name']
                    if name in pedidos:
                        pedidos[name]['ss_corre']       = row['total_items_scanned'] or 0
                        pedidos[name]['ss_corre_total'] = row['total_items_required'] or 0
                        pedidos[name]['control_status'] = row['status']
                        pedidos[name]['control_user']   = row['user_name'] or ''
                    else:
                        # Pedido controlado que ya salió de la cola
                        pedidos[name] = {
                            'pedido':              name,
                            'cliente':             '',
                            'pick_lines':          0,
                            'estado':              row['status'] or '',
                            'ultima_actualizacion': None,
                            'prioridad':           'MEDIA',
                            'pickea':              '',
                            'corre':               row['user_name'] or '',
                            'confirmado':          row['status'] == 'completed',
                            'fecha_pedido':        '',
                            'cod_cliente':         '',
                            'ss_pick':             0,
                            'ss_pick_total':       0,
                            'ss_corre':            row['total_items_scanned'] or 0,
                            'ss_corre_total':      row['total_items_required'] or 0,
                            'control_status':      row['status'],
                            'control_user':        row['user_name'] or '',
                        }

                # Controles activos en este momento (quién está trabajando)
                cur.execute("""
                    SELECT order_id, locked_by_name
                    FROM active_order_controls
                    WHERE locked_by_name IS NOT NULL
                """)
                activos = {str(r['order_id']): r['locked_by_name'] for r in cur.fetchall()}
                # order_id es el id de Odoo; si el nombre del pedido es numérico se puede matchear
                for pedido_name, data in pedidos.items():
                    if not data.get('corre') and pedido_name.lstrip('S').isdigit():
                        odoo_id = pedido_name.lstrip('S')
                        if odoo_id in activos:
                            data['corre'] = activos[odoo_id]
    except Exception as e:
        print(f"[panelDistri] Error leyendo ControlDistriV2Test: {e}")

    # Ordenar por número de pedido descendente (independiente del origen)
    resultado = list(pedidos.values())
    resultado.sort(key=lambda p: p['pedido'], reverse=True)
    return resultado


def get_stats_hoy():
    """Estadísticas del día para el informe del panel."""
    stats = {
        'ordenes_activas':   0,
        'finalizadas_hoy':   17,   # default demo hasta que haya datos reales
        'en_control':        0,
        'total_cola':        0,
    }

    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM order_state_tracking
                    WHERE current_state = 'sale'
                """)
                row = cur.fetchone()
                stats['total_cola'] = row['cnt'] if row else 0

                cur.execute("""
                    SELECT COUNT(DISTINCT pedido) AS cnt
                    FROM action_log
                    WHERE fecha::date = CURRENT_DATE
                      AND (estado_final ILIKE '%done%'
                           OR estado_final ILIKE '%hecho%'
                           OR estado_final ILIKE '%finaliz%')
                """)
                row = cur.fetchone()
                stats['finalizadas_hoy'] = row['cnt'] if row else 0
    except Exception as e:
        print(f"[panelDistri] Error stats distriTest: {e}")

    try:
        with get_control_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM active_order_controls
                    WHERE locked_by_name IS NOT NULL
                """)
                row = cur.fetchone()
                stats['en_control'] = row['cnt'] if row else 0

                cur.execute("""
                    SELECT COUNT(DISTINCT order_name) AS cnt
                    FROM control_distri_history
                    WHERE DATE(COALESCE(end_time, start_time)) = CURRENT_DATE
                """)
                row = cur.fetchone()
                stats['ordenes_activas'] = row['cnt'] if row else 0
    except Exception as e:
        print(f"[panelDistri] Error stats ControlDistriV2Test: {e}")

    return stats


def get_motivos_hoy():
    """Motivos de órdenes incompletas registrados hoy."""
    motivos = []
    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, pedido, cliente, motivo, submotivo, observaciones, registrado_por, fecha
                    FROM panel_motivos
                    WHERE fecha::date = CURRENT_DATE
                    ORDER BY fecha DESC
                """)
                for row in cur.fetchall():
                    motivos.append({
                        'id':             row['id'],
                        'pedido':         row['pedido'],
                        'cliente':        row['cliente'] or '',
                        'motivo':         row['motivo'],
                        'submotivo':      row['submotivo'] or '',
                        'observaciones':  row['observaciones'] or '',
                        'registrado_por': row['registrado_por'] or '',
                        'hora':           row['fecha'].strftime('%H:%M') if row['fecha'] else '',
                    })
    except Exception as e:
        print(f"[panelDistri] Error leyendo motivos: {e}")
    return motivos


# ─── Escritura de datos ───────────────────────────────────────────────────────

def upsert_pedido_meta(pedido, prioridad=None, pickea=None, corre=None,
                       confirmado=None, fecha_pedido=None, cod_cliente=None):
    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO panel_pedidos_meta
                        (pedido, prioridad, pickea, corre, confirmado, fecha_pedido, cod_cliente, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (pedido) DO UPDATE SET
                        prioridad    = COALESCE(EXCLUDED.prioridad,    panel_pedidos_meta.prioridad),
                        pickea       = COALESCE(EXCLUDED.pickea,       panel_pedidos_meta.pickea),
                        corre        = COALESCE(EXCLUDED.corre,        panel_pedidos_meta.corre),
                        confirmado   = COALESCE(EXCLUDED.confirmado,   panel_pedidos_meta.confirmado),
                        fecha_pedido = COALESCE(EXCLUDED.fecha_pedido, panel_pedidos_meta.fecha_pedido),
                        cod_cliente  = COALESCE(EXCLUDED.cod_cliente,  panel_pedidos_meta.cod_cliente),
                        updated_at   = now()
                """, (pedido, prioridad, pickea, corre, confirmado, fecha_pedido, cod_cliente))
            conn.commit()
        return True
    except Exception as e:
        print(f"[panelDistri] Error upsert_pedido_meta: {e}")
        return False


def insertar_motivo(pedido, motivo, cliente=None, submotivo=None,
                    observaciones=None, registrado_por=None):
    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO panel_motivos
                        (pedido, cliente, motivo, submotivo, observaciones, registrado_por)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (pedido, cliente, motivo, submotivo, observaciones, registrado_por))
                new_id = cur.fetchone()['id']
            conn.commit()
        return new_id
    except Exception as e:
        print(f"[panelDistri] Error insertar_motivo: {e}")
        return None


def eliminar_motivo(motivo_id):
    try:
        with get_distri_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM panel_motivos WHERE id = %s", (motivo_id,))
            conn.commit()
        return True
    except Exception as e:
        print(f"[panelDistri] Error eliminar_motivo: {e}")
        return False

"""
Configuración de panelDistri.
Cambiar MODE a 'prod' para apuntar a las bases de datos de producción.
"""

MODE = 'dev'  # 'dev' | 'prod'

_CONFIGS = {
    'dev': {
        'DISTRI_DB': {
            'host': 'wstd.com.ar',
            'dbname': 'distriTest',
            'user': 'wstd',
            'password': 'Wstd.admin.1822',
            'connect_timeout': 10,
        },
        'CONTROL_DB': {
            'host': 'wstd.com.ar',
            'dbname': 'ControlDistriV2Test',
            'user': 'wstd',
            'password': 'Wstd.admin.1822',
            'connect_timeout': 10,
        },
    },
    'prod': {
        'DISTRI_DB': {
            'host': 'wstd.com.ar',
            'dbname': 'distri',
            'user': 'wstd',
            'password': 'Wstd.admin.1822',
            'connect_timeout': 10,
        },
        'CONTROL_DB': {
            'host': 'wstd.com.ar',
            'dbname': 'ControlDistriV2',
            'user': 'wstd',
            'password': 'Wstd.admin.1822',
            'connect_timeout': 10,
        },
    },
}

CONFIG = _CONFIGS[MODE]

# ─── DB de usuarios compartida (login) ───────────────────────────────────────

LOGIN_DB = {
    'host':     'wstd.com.ar',
    'database': 'usuarios',
    'user':     'wstd',
    'password': 'Wstd.admin.1822',
}

def login_db_config():
    return LOGIN_DB

# ─── Odoo (autenticación XML-RPC) ─────────────────────────────────────────────

ODOO_URL = 'https://wstd.ar'
ODOO_DB  = 'odoo'

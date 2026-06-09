import base64
import xmlrpc.client


def encrypt_password(password: str) -> str:
    return base64.b64encode(password.encode()).decode()


def decrypt_password(encrypted_password: str) -> str:
    return base64.b64decode(encrypted_password.encode()).decode()


def authenticate_odoo(odoo_url: str, odoo_db: str, username: str, password: str):
    """Return uid if credentials are valid in Odoo, otherwise False."""
    common = xmlrpc.client.ServerProxy(f"{odoo_url}/xmlrpc/2/common")
    try:
        uid = common.authenticate(odoo_db, username, password, {})
    except Exception:
        uid = False
    return uid


def get_odoo_config(app=None):
    if app is not None:
        url = app.config.get('ODOO_URL', 'https://wstd.ar')
        db  = app.config.get('ODOO_DB', 'odoo')
    else:
        url = 'https://wstd.ar'
        db  = 'odoo'
    return url, db

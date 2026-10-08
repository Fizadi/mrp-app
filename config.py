# config.py
import os
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
    load_dotenv(os.environ.get('DOTENV_FILE', '.env'))
except ImportError:
    pass


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-this-in-production')

    _db_server   = os.environ.get('DB_SERVER', 'localhost\\SQLEXPRESS')
    _db_port     = os.environ.get('DB_PORT', '').strip()
    _db_name     = os.environ.get('DB_NAME', 'MRP_database')
    _db_user     = os.environ.get('DB_USER', 'mrp_user')
    _db_pass     = os.environ.get('DB_PASSWORD', '')

    # SQLAlchemy host syntax: 'host' for named instance, 'host:port' for TCP.
    _host_part = f'{_db_server}:{_db_port}' if _db_port else _db_server

    # URL-encode user + password so @ : / ? # etc. don't break the URI.
    SQLALCHEMY_DATABASE_URI = (
        f'mssql+pyodbc://{quote_plus(_db_user)}:{quote_plus(_db_pass)}@{_host_part}/{_db_name}'
        '?driver=ODBC+Driver+17+for+SQL+Server'
        '&TrustServerCertificate=yes'
        '&Encrypt=no'
    )

    DB_PATH = SQLALCHEMY_DATABASE_URI
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    AI_PROVIDER = os.environ.get('AI_PROVIDER', 'avalai')
    AI_API_KEY  = os.environ.get('AI_API_KEY', '')
    AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://api.avalai.ir/v1')
    AI_MODEL    = os.environ.get('AI_MODEL', 'gemini-3.5-flash-lite')
    AI_TIMEOUT  = int(os.environ.get('AI_TIMEOUT', '60'))
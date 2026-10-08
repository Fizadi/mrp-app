import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-this-in-production')
    DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'Instances', 'MRP_database.db'))
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG')
    LOG_FORMAT = '%(asctime)s %(levelname)s: %(message)s'
    SESSION_COOKIE_NAME = 'mrp_session'
    PERMANENT_SESSION_LIFETIME = 28800
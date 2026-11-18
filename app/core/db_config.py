# app/core/db_config.py
"""
Single source of truth for database connection strings.
Reads from environment variables (loaded from .env.windows / .env.docker).
Never hardcode credentials in Python files.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def get_db_connection_string(database=None):
    """Return an ODBC connection string for the MRP database."""
    server = os.environ.get('DB_SERVER', r'.\SQLEXPRESS')
    db     = database or os.environ.get('DB_NAME', 'MRP_database')
    user   = os.environ.get('DB_USER', 'mrp_user')
    pwd    = os.environ.get('DB_PASSWORD', '')
    if not pwd:
        raise RuntimeError(
            "DB_PASSWORD is not set. Copy .env.windows to .env and restart."
        )
    return (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={db};"
        f"UID={user};"
        f"PWD={pwd};"
        f"TrustServerCertificate=yes;"
    )


def get_hamkaran_connection_string():
    """Connection string for the Hamkaran sample ERP database."""
    return get_db_connection_string(database='Hamkaran_Database')
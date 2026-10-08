# app/core/database.py
import pyodbc
import logging
from flask import g, session
from config import Config


def _build_connection_string():
    """
    Build a proper pyodbc connection string.

    Config.SQLALCHEMY_DATABASE_URI is a SQLAlchemy URI
    (mssql+pyodbc://...), which pyodbc.connect() does NOT understand.
    We convert it to a real ODBC connection string.

    Authentication: SQL Server login (UID/PWD), not Windows Trusted Connection.
    """
    return (
        'DRIVER={ODBC Driver 17 for SQL Server};'
        'SERVER=localhost\\SQLEXPRESS;'
        'DATABASE=MRP_database;'
        'UID=mrp_user;'
        'PWD=YourStrongPassword123!;'
        'TrustServerCertificate=yes;'
        'Encrypt=no;'
    )


def get_db_connection(use_teardown=True):
    """
    Get database connection.

    Args:
        use_teardown: If True, use Flask's g object (auto-closed at teardown).
                      If False, return a new independent connection.
    """
    conn_str = _build_connection_string()

    if use_teardown:
        if 'db' not in g:
            try:
                g.db = pyodbc.connect(conn_str)
            except pyodbc.Error as e:
                logging.error(f"Database connection error: {e}")
                return None
        return g.db
    else:
        # Return a new independent connection
        try:
            conn = pyodbc.connect(conn_str)
            return conn
        except pyodbc.Error as e:
            logging.error(f"Database connection error: {e}")
            return None


def close_db_connection(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(app):
    """Register close_db_connection to run at app teardown."""
    app.teardown_appcontext(close_db_connection)


def get_row_value(row, key, default=None):
    """Safely get a value from a pyodbc row."""
    if row is None:
        return default
    try:
        if hasattr(row, key):
            value = getattr(row, key)
        else:
            try:
                if hasattr(row, 'cursor') and row.cursor and row.cursor.description:
                    col_names = [desc[0] for desc in row.cursor.description]
                    if key in col_names:
                        idx = col_names.index(key)
                        value = row[idx]
                    else:
                        return default
                else:
                    return default
            except (IndexError, ValueError, AttributeError):
                return default
        return value if value is not None else default
    except (KeyError, IndexError, AttributeError):
        return default


def execute_query(conn, query, params=None):
    """
    Execute a query with parameters.

    Args:
        conn: Database connection
        query: SQL query string with ? placeholders
        params: Dictionary of parameters {'param': value}

    Returns:
        Cursor object
    """
    if params is None:
        params = {}
    cursor = conn.cursor()

    # Convert dictionary params to list for SQL Server
    param_values = list(params.values()) if isinstance(params, dict) else params
    cursor.execute(query, param_values)
    return cursor


def fetch_one(conn, query, params=None):
    """Fetch one row from a query."""
    cursor = execute_query(conn, query, params)
    return cursor.fetchone()


def fetch_all(conn, query, params=None):
    """Fetch all rows from a query."""
    cursor = execute_query(conn, query, params)
    return cursor.fetchall()


def get_last_insert_id(conn):
    """Get the last inserted ID in SQL Server."""
    cursor = conn.cursor()
    cursor.execute("SELECT SCOPE_IDENTITY()")
    return cursor.fetchone()[0]


def get_industry_filter(alias=None):
    """
    Get SQL WHERE clause for industry filtering.

    Args:
        alias: Optional table alias (e.g., 'p.' for t_Product)

    Returns:
        Tuple of (where_clause, params)
    """
    industry = session.get('demo_industry', 'valve')
    prefix = f"{alias}" if alias else ""
    return f"{prefix}DemoIndustryCode = ?", {'industry': industry}


def get_industry_filter_simple():
    """
    Get industry filter as a simple string for use in SQL WHERE clauses.

    Returns:
        String like "DemoIndustryCode = 'valve'"
    """
    industry = session.get('demo_industry', 'valve')
    return f"DemoIndustryCode = '{industry}'"


def get_industry_filter_with_or_null(alias=None):
    """
    Get industry filter that includes NULL values (for backward compatibility).

    Args:
        alias: Optional table alias (e.g., 'p.' for t_Product)

    Returns:
        String like "(DemoIndustryCode = 'valve' OR DemoIndustryCode IS NULL)"
    """
    industry = session.get('demo_industry', 'valve')
    prefix = f"{alias}" if alias else ""
    return f"({prefix}DemoIndustryCode = '{industry}' OR {prefix}DemoIndustryCode IS NULL)"
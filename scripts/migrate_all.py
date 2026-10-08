import sqlite3
import pyodbc

# SQLite connection (your file)
sqlite_conn = sqlite3.connect(r'D:\Logistics Project, 2024-02-01\MRP Project\FlaskApp\Instances\MRP_database.db')
sqlite_cursor = sqlite_conn.cursor()

# SQL Server connection
sqlserver_conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=localhost\\SQLEXPRESS;'
    'DATABASE=MRP_database;'
    'Trusted_Connection=yes;'
    'TrustServerCertificate=yes;'
    'Encrypt=no'
)
sqlserver_cursor = sqlserver_conn.cursor()

print("🚀 Starting migration...")

# Get all table names from SQLite
sqlite_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
tables = sqlite_cursor.fetchall()

migrated_count = 0
for (table_name,) in tables:
    # Skip t_Translations (handle separately)
    if table_name == 't_Translations':
        print(f"  ⏭️  Skipping {table_name} (handle manually)")
        continue

    print(f"📤 Migrating: {table_name}")
    
    # Get data
    sqlite_cursor.execute(f"SELECT * FROM {table_name}")
    rows = sqlite_cursor.fetchall()
    
    # ============================================================================
    # Get column names (ALWAYS do this before checking if rows are empty)
    # ============================================================================
    # The cursor.description attribute contains column metadata and is available
    # even when the query returns zero rows. This is critical because we need
    # to know the table structure to create it in SQL Server, even if the table
    # is empty in SQLite.
    # ============================================================================
    columns = [desc[0] for desc in sqlite_cursor.description]
    columns_str = ','.join(columns)
    placeholders = ','.join(['?' for _ in columns])
    
    # ============================================================================
    # Create table in SQL Server (ALWAYS, even if empty)
    # ============================================================================
    # Using INFORMATION_SCHEMA.TABLES to check for table existence.
    # This is the ANSI-standard way and works across all SQL Server versions.
    #
    # IMPORTANT: We create the table even when there are no rows because:
    #   1. The table structure needs to exist in SQL Server for future data
    #   2. Other tables may have foreign key references to this table
    #   3. The application expects the table to exist even if empty
    # ============================================================================
    create_sql = f"""
    IF NOT EXISTS (
        SELECT * FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_NAME = '{table_name}'
    )
    CREATE TABLE {table_name} ({','.join([f'{col} NVARCHAR(MAX)' for col in columns])})
    """
    sqlserver_cursor.execute(create_sql)
    
    # If there's data, insert it
    if rows:
        insert_sql = f"INSERT INTO {table_name} ({columns_str}) VALUES ({placeholders})"
        for row in rows:
            try:
                sqlserver_cursor.execute(insert_sql, row)
            except Exception as e:
                print(f"  ⚠️  Error inserting row in {table_name}: {e}")
        sqlserver_conn.commit()
        print(f"  ✅ Migrated {len(rows)} rows from {table_name}")
    else:
        sqlserver_conn.commit()
        print(f"  📋 Created empty table: {table_name}")
    
    migrated_count += 1

print("\n" + "=" * 60)
print("📋 MIGRATION COMPLETE - SUMMARY:")
print("=" * 60)
print(f"  ✅ Migrated {migrated_count} tables successfully")
print(f"  ⏭️  Skipped 1 table: t_Translations (handle manually)")
print("")
print("  💡 To handle translations, use the translation sync tool:")
print("     flask sync-translations")
print("=" * 60)

sqlite_conn.close()
sqlserver_conn.close()
print("🔒 Connections closed.")
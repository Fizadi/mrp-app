import sqlite3
import pyodbc
import sys
from collections import defaultdict

# ============================================================================
# CONFIGURATION
# ============================================================================
SQLITE_DB_PATH = r'D:\Logistics Project, 2024-02-01\MRP Project\FlaskApp\Instances\MRP_database.db'

SQL_SERVER_CONN_STR = (
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=localhost\\SQLEXPRESS;'
    'DATABASE=MRP_database;'
    'Trusted_Connection=yes;'
    'TrustServerCertificate=yes;'
    'Encrypt=no'
)

# ============================================================================
# 1. CONNECT
# ============================================================================

print("🔗 Connecting to databases...")
sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
sqlite_cursor = sqlite_conn.cursor()

sqlserver_conn = pyodbc.connect(SQL_SERVER_CONN_STR)
sqlserver_cursor = sqlserver_conn.cursor()
print("✅ Connected successfully.\n")

# ============================================================================
# 2. COMPARE TABLE LISTS
# ============================================================================

print("📋 Step 1: Comparing Table Lists...")

sqlite_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
sqlite_tables = set([row[0] for row in sqlite_cursor.fetchall()])

sqlserver_cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE'")
sqlserver_tables = set([row[0] for row in sqlserver_cursor.fetchall()])

missing_in_sqlserver = sqlite_tables - sqlserver_tables
missing_in_sqlite = sqlserver_tables - sqlite_tables

if missing_in_sqlserver:
    print(f"❌ Tables missing in SQL Server: {sorted(missing_in_sqlserver)}")
else:
    print("✅ All tables from SQLite exist in SQL Server.")

if missing_in_sqlite:
    if missing_in_sqlite == {'sqlite_sequence', 'sqlite_stat1'}:
        print("ℹ️  SQL Server has additional system tables (sqlite_sequence, sqlite_stat1) – this is normal.")
    else:
        print(f"⚠️ Tables missing in SQLite (but exist in SQL Server): {sorted(missing_in_sqlite)}")

print(f"   Total SQLite tables: {len(sqlite_tables)}, SQL Server tables: {len(sqlserver_tables)}\n")

common_tables = sqlite_tables.intersection(sqlserver_tables)
if not common_tables:
    print("❌ No common tables found. Exiting.")
    sys.exit(1)

# ============================================================================
# 3. COMPARE COLUMNS
# ============================================================================

print("📋 Step 2: Comparing Table Schemas (Columns)...")

def get_sqlite_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]

def get_sqlserver_columns(cursor, table_name):
    cursor.execute(f"""
        SELECT COLUMN_NAME 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_NAME = '{table_name}'
    """)
    return [row[0] for row in cursor.fetchall()]

column_mismatches = defaultdict(list)
for table in sorted(common_tables):
    sqlite_cols = set(get_sqlite_columns(sqlite_cursor, table))
    sqlserver_cols = set(get_sqlserver_columns(sqlserver_cursor, table))
    
    if table == 't_Translations':
        expected = {'TranslationKey', 'English', 'Persian'}
        if sqlserver_cols == expected:
            print(f"   ✅ t_Translations has correct structure")
        else:
            column_mismatches[table].append(f"Expected {expected}, got {sqlserver_cols}")
        continue
    
    if sqlite_cols != sqlserver_cols:
        missing_in_srv = sqlite_cols - sqlserver_cols
        missing_in_sql = sqlserver_cols - sqlite_cols
        if missing_in_srv:
            column_mismatches[table].append(f"Missing in SQL Server: {missing_in_srv}")
        if missing_in_sql:
            column_mismatches[table].append(f"Missing in SQLite: {missing_in_sql}")

if column_mismatches:
    print("❌ Column mismatches found:")
    for table, issues in column_mismatches.items():
        print(f"   - {table}: {', '.join(issues)}")
else:
    print("✅ All tables have identical column sets.\n")

# ============================================================================
# 4. COMPARE ROW COUNTS
# ============================================================================

print("📋 Step 3: Comparing Row Counts...")

row_count_mismatches = []
for table in sorted(common_tables):
    safe_table = f"[{table}]"
    
    sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table}")
    sqlite_count = sqlite_cursor.fetchone()[0]
    
    sqlserver_cursor.execute(f"SELECT COUNT(*) FROM {safe_table}")
    sqlserver_count = sqlserver_cursor.fetchone()[0]
    
    if table == 't_Translations':
        print(f"   ℹ️  t_Translations: SQLite={sqlite_count} raw, SQL Server={sqlserver_count} grouped (correct)")
        continue
    
    if sqlite_count != sqlserver_count:
        row_count_mismatches.append((table, sqlite_count, sqlserver_count))

if row_count_mismatches:
    print("❌ Row count mismatches found:")
    for table, sqlite_count, sqlserver_count in row_count_mismatches:
        print(f"   - {table}: SQLite={sqlite_count}, SQL Server={sqlserver_count}")
else:
    print("✅ All tables have matching row counts.\n")

# ============================================================================
# 5. SAMPLE DATA COMPARISON (FIXED FOR SQLITE)
# ============================================================================

print("📋 Step 4: Sampling Data (First 5 rows)...")

def get_table_sample(cursor, table_name, limit=5, db_type='sqlite'):
    try:
        safe_table = f"[{table_name}]"
        if db_type == 'sqlite':
            cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
        else:
            cursor.execute(f"SELECT TOP {limit} * FROM {safe_table}")
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if rows else []
        return columns, rows
    except Exception as e:
        return None, str(e)

data_mismatches = []
for table in sorted(common_tables):
    if table == 't_Translations':
        print(f"   ℹ️  Skipping sample check for t_Translations")
        continue
    
    sqlite_cols, sqlite_sample = get_table_sample(sqlite_cursor, table, db_type='sqlite')
    sqlserver_cols, sqlserver_sample = get_table_sample(sqlserver_cursor, table, db_type='sqlserver')
    
    if isinstance(sqlite_sample, str):
        data_mismatches.append(f"⚠️ Could not sample {table}: SQLite error: {sqlite_sample}")
        continue
    if isinstance(sqlserver_sample, str):
        data_mismatches.append(f"⚠️ Could not sample {table}: SQL Server error: {sqlserver_sample}")
        continue
    
    if sqlite_cols != sqlserver_cols:
        data_mismatches.append(f"⚠️ {table}: Column order/names differ")
        continue
    
    sqlite_data = [tuple(row) for row in sqlite_sample]
    sqlserver_data = [tuple(row) for row in sqlserver_sample]
    
    if sqlite_data != sqlserver_data:
        data_mismatches.append(f"⚠️ {table}: Sample data differs")

if data_mismatches:
    print("❌ Sample data mismatches found:")
    for issue in data_mismatches:
        print(f"   {issue}")
else:
    print("✅ All sampled data matches.\n")

# ============================================================================
# 6. SUMMARY
# ============================================================================

print("="*60)
print("📊 FINAL COMPARISON SUMMARY")
print("="*60)

real_issues = False

if missing_in_sqlserver:
    real_issues = True
    print(f"❌ Missing tables in SQL Server: {len(missing_in_sqlserver)}")

if column_mismatches:
    real_issues = True
    print(f"❌ Tables with column mismatches: {len(column_mismatches)}")

if row_count_mismatches:
    real_issues = True
    print(f"❌ Tables with row count mismatches: {len(row_count_mismatches)}")

if data_mismatches:
    real_issues = True
    print(f"❌ Tables with data mismatches: {len(data_mismatches)}")

if not real_issues:
    print("🎉✅ PERFECT MATCH: All tables, columns, row counts, and sampled data are identical!")
else:
    print("\n⚠️ Please investigate the discrepancies listed above.")
    print("   ℹ️  Note: t_Translations discrepancies are expected and intentional.")

sqlite_conn.close()
sqlserver_conn.close()
print("\n🔒 Connections closed.")
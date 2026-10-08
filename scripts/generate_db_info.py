# =====================================================================
# DATABASE STRUCTURE EXPORTER - SQL SERVER VERSION
# =====================================================================

import pyodbc
from pathlib import Path
from datetime import datetime
import os

# =====================================================================
# CONNECTION CONFIGURATION - FIXED
# =====================================================================

# Connection string with correct database name
CONNECTION_STRING = (
    "Driver={SQL Server};"
    "Server=localhost\\SQLEXPRESS;"
    "Database=MRP_database;"  # FIXED: Correct database name
    "Trusted_Connection=yes;"
)

# Alternative: SQL Server Authentication (uncomment if needed)
# CONNECTION_STRING = (
#     "Driver={SQL Server};"
#     "Server=localhost\\SQLEXPRESS;"
#     "Database=MRP_database;"
#     "UID=your_username;"
#     "PWD=your_password;"
# )

OUTPUT_FOLDER = Path("db_module_docs")

# =====================================================================
# DATABASE HELPERS - SQL SERVER VERSION
# =====================================================================

def get_all_tables(conn):
    """Get all user tables from SQL Server"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT TABLE_SCHEMA + '.' + TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_SCHEMA NOT IN ('sys', 'information_schema')
        ORDER BY TABLE_NAME
    """)
    return [row[0] for row in cursor.fetchall()]

def get_table_info(conn, table):
    """Get column info from SQL Server"""
    cursor = conn.cursor()
    # Parse schema and table name
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    cursor.execute("""
        SELECT 
            COLUMN_NAME,
            DATA_TYPE,
            IS_NULLABLE,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, (schema, table_name))
    return cursor.fetchall()

def get_primary_keys(conn, table):
    """Get primary key columns from SQL Server"""
    cursor = conn.cursor()
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    cursor.execute("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
          AND CONSTRAINT_NAME IN (
              SELECT CONSTRAINT_NAME
              FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
              WHERE CONSTRAINT_TYPE = 'PRIMARY KEY'
                AND TABLE_SCHEMA = ?
                AND TABLE_NAME = ?
          )
    """, (schema, table_name, schema, table_name))
    return [row[0] for row in cursor.fetchall()]

def get_foreign_keys(conn, table):
    """Get foreign keys from SQL Server"""
    cursor = conn.cursor()
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    cursor.execute("""
        SELECT 
            fk.name AS FK_Name,
            OBJECT_SCHEMA_NAME(fk.parent_object_id) AS FK_Schema,
            OBJECT_NAME(fk.parent_object_id) AS FK_Table,
            COL_NAME(fk.parent_object_id, fkc.parent_column_id) AS FK_Column,
            OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS Ref_Schema,
            OBJECT_NAME(fk.referenced_object_id) AS Ref_Table,
            COL_NAME(fk.referenced_object_id, fkc.referenced_column_id) AS Ref_Column
        FROM sys.foreign_keys fk
        INNER JOIN sys.foreign_key_columns fkc
            ON fk.object_id = fkc.constraint_object_id
        WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) = ?
          AND OBJECT_NAME(fk.parent_object_id) = ?
    """, (schema, table_name))
    return cursor.fetchall()

def get_indexes(conn, table):
    """Get indexes from SQL Server"""
    cursor = conn.cursor()
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    cursor.execute("""
        SELECT 
            i.name AS Index_Name,
            i.is_unique,
            i.is_primary_key,
            STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal) AS Columns
        FROM sys.indexes i
        INNER JOIN sys.index_columns ic
            ON i.object_id = ic.object_id
            AND i.index_id = ic.index_id
        INNER JOIN sys.columns c
            ON ic.object_id = c.object_id
            AND ic.column_id = c.column_id
        WHERE OBJECT_SCHEMA_NAME(i.object_id) = ?
          AND OBJECT_NAME(i.object_id) = ?
          AND i.name IS NOT NULL
        GROUP BY i.name, i.is_unique, i.is_primary_key
        ORDER BY i.is_primary_key DESC
    """, (schema, table_name))
    return cursor.fetchall()

def get_row_count(conn, table):
    """Get row count from SQL Server"""
    cursor = conn.cursor()
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    try:
        cursor.execute(f"SELECT COUNT(*) FROM [{schema}].[{table_name}]")
        return cursor.fetchone()[0]
    except:
        return 0

def get_sample_rows(conn, table, limit=3):
    """Get sample rows from SQL Server"""
    cursor = conn.cursor()
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    try:
        cursor.execute(f"SELECT TOP {limit} * FROM [{schema}].[{table_name}]")
        rows = cursor.fetchall()
        
        # Get column names
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """, (schema, table_name))
        cols = [row[0] for row in cursor.fetchall()]
        
        return rows, cols
    except:
        return [], []

def get_table_triggers(conn, table):
    """Get triggers from SQL Server"""
    cursor = conn.cursor()
    if '.' in table:
        schema, table_name = table.split('.', 1)
    else:
        schema = 'dbo'
        table_name = table
    
    cursor.execute("""
        SELECT 
            tr.name AS Trigger_Name,
            OBJECT_DEFINITION(tr.object_id) AS Trigger_Definition
        FROM sys.triggers tr
        WHERE OBJECT_SCHEMA_NAME(tr.parent_id) = ?
          AND OBJECT_NAME(tr.parent_id) = ?
    """, (schema, table_name))
    return cursor.fetchall()

# =====================================================================
# OUTPUT FOLDER
# =====================================================================

def prepare_output_folder():
    """Prepare output folder"""
    if not OUTPUT_FOLDER.exists():
        OUTPUT_FOLDER.mkdir(parents=True)
        print(f"Created output folder: {OUTPUT_FOLDER}")
    return OUTPUT_FOLDER

# =====================================================================
# GENERATE FULL REPORT
# =====================================================================

def generate_full_database_report(conn):
    tables = get_all_tables(conn)
    
    report = OUTPUT_FOLDER / "DATABASE_FULL_REPORT.txt"
    
    with open(report, "w", encoding="utf-8") as f:
        
        # ===== HEADER =====
        f.write("="*100 + "\n")
        f.write("DATABASE FULL REPORT - SQL SERVER\n")
        f.write("="*100 + "\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Server: localhost\\SQLEXPRESS\n")
        f.write(f"Database: MRP_database\n")
        f.write("="*100 + "\n\n")
        
        # ===== TABLE DIGEST =====
        f.write("="*100 + "\n")
        f.write("TABLE DIGEST (A-Z)\n")
        f.write("="*100 + "\n\n")
        
        for i, table in enumerate(tables, 1):
            rows = get_row_count(conn, table)
            f.write(f"{i:>3}. {table:<50} rows: {rows}\n")
        
        f.write("\n\n")
        
        # ===== DETAILED TABLE INFORMATION =====
        for table in tables:
            f.write("\n" + "="*100 + "\n")
            f.write(f"TABLE: {table}\n")
            f.write("="*100 + "\n")
            
            rows = get_row_count(conn, table)
            f.write(f"Total Rows: {rows}\n\n")
            
            # ---- COLUMN INFO ----
            f.write("COLUMNS\n")
            f.write("-"*100 + "\n")
            f.write("Name | Type | Nullable | MaxLength | Precision | Scale | Default\n")
            f.write("-"*100 + "\n")
            
            cols = get_table_info(conn, table)
            for c in cols:
                # c = (COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH, 
                #      NUMERIC_PRECISION, NUMERIC_SCALE, COLUMN_DEFAULT)
                max_len = c[3] if c[3] else 'NULL'
                precision = c[4] if c[4] is not None else 'NULL'
                scale = c[5] if c[5] is not None else 'NULL'
                default = c[6] if c[6] else 'NULL'
                f.write(f"{c[0]:<30} | {c[1]:<15} | {c[2]:<8} | {max_len:<9} | {precision:<9} | {scale:<5} | {default}\n")
            
            f.write("\n")
            
            # ---- PRIMARY KEYS ----
            f.write("PRIMARY KEYS\n")
            f.write("-"*100 + "\n")
            pks = get_primary_keys(conn, table)
            if pks:
                f.write(f"  {', '.join(pks)}\n")
            else:
                f.write("  (No primary key)\n")
            
            f.write("\n")
            
            # ---- FOREIGN KEYS ----
            f.write("FOREIGN KEYS\n")
            f.write("-"*100 + "\n")
            fks = get_foreign_keys(conn, table)
            if fks:
                for fk in fks:
                    # fk = (FK_Name, FK_Schema, FK_Table, FK_Column, Ref_Schema, Ref_Table, Ref_Column)
                    f.write(f"  {fk[3]} -> {fk[5]}.{fk[6]}\n")
                    f.write(f"    (Name: {fk[0]})\n")
            else:
                f.write("  (No foreign keys)\n")
            
            f.write("\n")
            
            # ---- INDEXES ----
            f.write("INDEXES\n")
            f.write("-"*100 + "\n")
            indexes = get_indexes(conn, table)
            if indexes:
                for idx in indexes:
                    # idx = (Index_Name, is_unique, is_primary_key, Columns)
                    pk_flag = " (PRIMARY KEY)" if idx[2] else ""
                    unique_flag = " UNIQUE" if idx[1] else ""
                    f.write(f"  {idx[0]}{unique_flag}{pk_flag}: {idx[3]}\n")
            else:
                f.write("  (No indexes)\n")
            
            f.write("\n")
            
            # ---- TRIGGERS ----
            f.write("TRIGGERS\n")
            f.write("-"*100 + "\n")
            triggers = get_table_triggers(conn, table)
            if triggers:
                for tr in triggers:
                    f.write(f"  Name: {tr[0]}\n")
                    f.write(f"  Definition: {tr[1][:200]}...\n" if tr[1] and len(tr[1]) > 200 else f"  Definition: {tr[1]}\n")
            else:
                f.write("  (No triggers)\n")
            
            f.write("\n")
            
            # ---- SAMPLE ROWS ----
            f.write("SAMPLE ROWS (First 3 records)\n")
            f.write("-"*100 + "\n")
            
            sample, col_names = get_sample_rows(conn, table)
            if sample:
                f.write(" | ".join(col_names) + "\n")
                f.write("-"*100 + "\n")
                for r in sample:
                    row_strs = [str(val) if val is not None else 'NULL' for val in r]
                    f.write(" | ".join(row_strs) + "\n")
            else:
                f.write("  (No sample data)\n")
            
            f.write("\n")
    
    return report

# =====================================================================
# MAIN
# =====================================================================

def main():
    print("="*60)
    print("DATABASE STRUCTURE EXPORTER - SQL SERVER")
    print("="*60)
    print(f"Server: localhost\\SQLEXPRESS")
    print(f"Database: MRP_database")
    print(f"Output: {OUTPUT_FOLDER.absolute()}")
    print()
    
    try:
        print("Connecting to SQL Server...")
        conn = pyodbc.connect(CONNECTION_STRING)
        print("Connected successfully!")
    except Exception as e:
        print(f"ERROR connecting: {e}")
        print("\nTroubleshooting tips:")
        print("1. Verify SQL Server is running")
        print("2. Check the server name: localhost\\SQLEXPRESS")
        print("3. Ensure Windows Authentication is enabled")
        print("4. Try using SQL Server Authentication instead")
        return
    
    prepare_output_folder()
    
    print("Generating full database report...")
    report = generate_full_database_report(conn)
    
    conn.close()
    
    print("\n" + "="*60)
    print("EXPORT COMPLETED")
    print("="*60)
    print(f"Report saved to:\n{report.absolute()}")
    print(f"File size: {report.stat().st_size / 1024:.2f} KB")

if __name__ == "__main__":
    main()
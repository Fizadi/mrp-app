import sqlite3
import pyodbc

# SQLite connection
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

# Clear existing data (start fresh)
sqlserver_cursor.execute("DELETE FROM t_Translations")
sqlserver_conn.commit()

# Get all rows from SQLite
sqlite_cursor.execute("SELECT translation_key, Language, Value FROM t_Translations")
rows = sqlite_cursor.fetchall()

# Group by translation_key
translations = {}
for translation_key, language, value in rows:
    if translation_key not in translations:
        translations[translation_key] = {}
    translations[translation_key][language] = value

# Insert into SQL Server
insert_sql = "INSERT INTO t_Translations (TranslationKey, English, Persian) VALUES (?, ?, ?)"

batch = []
for key, langs in translations.items():
    english = langs.get('en', '')
    persian = langs.get('fa', '')
    batch.append((key, english, persian))

# Insert in batches of 100
batch_size = 100
for i in range(0, len(batch), batch_size):
    sqlserver_cursor.executemany(insert_sql, batch[i:i+batch_size])
    sqlserver_conn.commit()
    print(f"✅ Inserted rows {i+1} to {min(i+batch_size, len(batch))}")

print(f"\n🎉 Successfully migrated {len(batch)} translation keys to t_Translations")

sqlite_conn.close()
sqlserver_conn.close()
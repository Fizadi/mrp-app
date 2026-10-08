import csv
from app import create_app
from sqlalchemy.sql import text

def main():
    app = create_app()
    with app.app_context():
        from app import db
        conn = db.engine.connect()
        rows = conn.execute(text("SELECT Key, Value FROM t_Translations WHERE Language = 'en' ORDER BY Key")).fetchall()
        with open('translations_to_fill.csv', 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['Key', 'English', 'Persian'])
            for key, eng in rows:
                # Check if Persian exists
                fa = conn.execute(text("SELECT Value FROM t_Translations WHERE Key = :key AND Language = 'fa'"), {'key': key}).scalar()
                writer.writerow([key, eng, fa or ''])
        conn.close()
    print("Exported to translations_to_fill.csv (UTF-8 with BOM)")

if __name__ == "__main__":
    main()
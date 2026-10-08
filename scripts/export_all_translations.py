#!/usr/bin/env python3
import csv
from app import create_app
from sqlalchemy.sql import text

def main():
    app = create_app()
    with app.app_context():
        from app import db
        conn = db.engine.connect()
        rows = conn.execute(text("SELECT Key, Value FROM t_Translations WHERE Language = 'en' ORDER BY Key")).fetchall()
        with open('all_translations.csv', 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['Key', 'English', 'Persian'])
            for key, eng in rows:
                # Get existing Persian if any
                fa = conn.execute(text("SELECT Value FROM t_Translations WHERE Key = :key AND Language = 'fa'"), {'key': key}).scalar()
                writer.writerow([key, eng, fa or ''])
        conn.close()
    print("Exported all keys to all_translations.csv")

if __name__ == "__main__":
    main()
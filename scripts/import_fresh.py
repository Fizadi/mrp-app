import csv
import sys
from app import create_app
from sqlalchemy.sql import text

def main():
    csv_file = 'translations_to_fill.csv'
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
    
    app = create_app()
    with app.app_context():
        from app import db
        conn = db.engine.connect()
        updated = 0
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row['Key'].strip()
                persian = row['Persian'].strip()
                if not key or not persian:
                    continue
                # Upsert Persian
                conn.execute(
                    text("INSERT OR REPLACE INTO t_Translations (Key, Language, Value) VALUES (:key, 'fa', :value)"),
                    {'key': key, 'value': persian}
                )
                updated += 1
                if updated % 100 == 0:
                    print(f"Processed {updated}...")
        conn.commit()
        conn.close()
        print(f"Imported {updated} Persian translations.")

if __name__ == "__main__":
    main()
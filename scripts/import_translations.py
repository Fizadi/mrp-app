#!/usr/bin/env python3
import sys
import csv
from app import create_app
from sqlalchemy.sql import text

def detect_encoding(filepath):
    """Try common encodings to open the file."""
    encodings = ['utf-8-sig', 'utf-8', 'cp1256', 'cp1252', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                f.read(1000)  # Try reading first 1000 characters
            return enc
        except UnicodeDecodeError:
            continue
    return None

def main():
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
    else:
        csv_file = input("Enter the path to the CSV file: ").strip().strip('"')
    
    # Detect encoding
    encoding = detect_encoding(csv_file)
    if not encoding:
        print("ERROR: Could not detect encoding. Please save the CSV as UTF-8.")
        sys.exit(1)
    print(f"Detected encoding: {encoding}")
    
    app = create_app()
    with app.app_context():
        from app import db
        conn = db.engine.connect()
        updated = 0
        with open(csv_file, 'r', encoding=encoding) as f:
            # Read first line to check for BOM or header
            first_line = f.readline()
            f.seek(0)
            # Use csv.reader with the same file object
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                print("ERROR: CSV file has no headers.")
                sys.exit(1)
            for row in reader:
                key = row.get('Key', '').strip()
                persian = row.get('Persian', '').strip()
                if not key or not persian:
                    continue
                existing = conn.execute(
                    text("SELECT 1 FROM t_Translations WHERE Key = :key AND Language = 'fa'"),
                    {'key': key}
                ).fetchone()
                if existing:
                    conn.execute(
                        text("UPDATE t_Translations SET Value = :value WHERE Key = :key AND Language = 'fa'"),
                        {'value': persian, 'key': key}
                    )
                else:
                    conn.execute(
                        text("INSERT INTO t_Translations (Key, Language, Value) VALUES (:key, 'fa', :value)"),
                        {'key': key, 'value': persian}
                    )
                updated += 1
                print(f"Updated {key} -> {persian}")
        conn.commit()
        conn.close()
        print(f"Import completed. Updated {updated} Persian translations.")

if __name__ == '__main__':
    main()
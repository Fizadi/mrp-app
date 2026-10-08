#!/usr/bin/env python3
"""
Export translation keys from a specific HTML template to a CSV file.
The CSV will have columns: Key, English, Persian (empty).
Usage: python export_translations.py templates/supply_chain/suppliers.html
"""

import sys
import re
import csv
from app import create_app
from sqlalchemy.sql import text

def extract_keys_from_template(template_path):
    """Extract (key, default_text) pairs from a template file."""
    pattern = re.compile(r"get_translation\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]")
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()
    matches = pattern.findall(content)
    # If default_text is empty, use key as fallback
    return [(key, default if default else key) for key, default in matches]

def main():
    if len(sys.argv) < 2:
        print("Usage: python export_translations.py <template_file>")
        print("Example: python export_translations.py templates/supply_chain/suppliers.html")
        sys.exit(1)

    template_file = sys.argv[1]
    print(f"Processing file: {template_file}")

    # Extract keys from template
    key_defaults = extract_keys_from_template(template_file)
    if not key_defaults:
        print("No get_translation keys found in this file.")
        sys.exit(0)

    # Create Flask app context and fetch English values from DB
    app = create_app()
    with app.app_context():
        from app import db
        conn = db.engine.connect()
        rows = []
        for key, default_text in key_defaults:
            # Get English value from DB (should match the default_text after sync)
            result = conn.execute(
                text("SELECT Value FROM t_Translations WHERE Key = :key AND Language = 'en'"),
                {'key': key}
            ).fetchone()
            english = result[0] if result else default_text
            rows.append({'Key': key, 'English': english, 'Persian': ''})
        conn.close()

    # Write CSV
    output_file = 'translations_to_fill.csv'
    with open(output_file, 'w', newline='', encoding='utf-8-sig') as csvfile:
        fieldnames = ['Key', 'English', 'Persian']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} keys to '{output_file}'. Open it in a spreadsheet editor, fill the Persian column, then run import_translations.py")

if __name__ == '__main__':
    main()
"""
check_translations.py

Cross-checks translation keys used by ALL HTML templates against what's in the DB.

Run from Flask root: python check_translations.py
"""

import os
import re
import sys
from pathlib import Path

# ============================================================
# Configuration — corrected for your folder structure
# ============================================================
TEMPLATES_DIR = Path("TEMPLATES")   # uppercase, matches your filesystem

# DB connection (Windows auth by default)
DB_CONFIG = {
    'server':   'DESKTOP-D77OTSK\\SQLEXPRESS',
    'database': 'MRP_database',
    'trusted_connection': 'yes',
    # If you need SQL auth instead, comment out trusted_connection above
    # and uncomment the two lines below:
    # 'uid': 'sa',
    # 'pwd': 'your_password_here',
}
# ============================================================

# Regex for get_translation('...') or get_translation("...")
TRANSLATION_PATTERN = re.compile(
    r"""get_translation\(\s*['"]([^'"]+)['"]""",
    re.MULTILINE
)


def extract_keys_from_templates():
    """Walk templates dir, find every get_translation() call."""
    keys_by_file = {}
    all_keys = set()

    if not TEMPLATES_DIR.exists():
        print(f"❌ Templates dir not found: {TEMPLATES_DIR.resolve()}")
        print(f"   Current working dir: {os.getcwd()}")
        print(f"   Folders here: {[p.name for p in Path('.').iterdir() if p.is_dir()]}")
        sys.exit(1)

    html_files = list(TEMPLATES_DIR.rglob("*.html"))
    print(f"📂 Scanning {len(html_files)} HTML files under {TEMPLATES_DIR.resolve()}...")

    for html_file in html_files:
        try:
            content = html_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = html_file.read_text(encoding="latin-1")
            except Exception as e:
                print(f"⚠️  Skipping {html_file}: {e}")
                continue

        matches = TRANSLATION_PATTERN.findall(content)
        if matches:
            rel_path = html_file.relative_to(TEMPLATES_DIR)
            keys_by_file[str(rel_path)] = sorted(set(matches))
            all_keys.update(matches)

    return keys_by_file, all_keys


def get_db_keys():
    """Query DB for all translations. Returns dict {(key, lang): value}."""
    try:
        import pyodbc
    except ImportError:
        print("\n❌ pyodbc not installed.")
        print("   Install with: pip install pyodbc")
        sys.exit(1)

    # Build connection string
    parts = [
        "DRIVER={ODBC Driver 17 for SQL Server}",
        f"SERVER={DB_CONFIG['server']}",
        f"DATABASE={DB_CONFIG['database']}",
    ]

    if DB_CONFIG.get('trusted_connection') == 'yes':
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={DB_CONFIG['uid']}")
        parts.append(f"PWD={DB_CONFIG['pwd']}")

    conn_str = ";".join(parts) + ";"

    try:
        conn = pyodbc.connect(conn_str, timeout=10)
    except Exception as e:
        print(f"❌ DB connection failed: {e}")
        print(f"   Connection string used (password hidden if any): {conn_str}")
        print("   Adjust DB_CONFIG at the top of this script.")
        sys.exit(1)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT translation_key, Language, Value
        FROM t_Translations
    """)
    rows = cursor.fetchall()
    conn.close()

    db_keys = {}
    for row in rows:
        db_keys[(row[0], row[1])] = row[2]

    return db_keys


def main():
    print("=" * 72)
    print("  TRANSLATION AUDIT — ALL MODULES")
    print("=" * 72)

    # 1. Extract keys from templates
    keys_by_file, all_used_keys = extract_keys_from_templates()
    print(f"\n📊 Found {len(all_used_keys)} unique translation keys across "
          f"{len(keys_by_file)} HTML files")

    # 2. Get DB keys
    print(f"\n🔌 Connecting to DB...")
    db_keys = get_db_keys()
    print(f"✓ Loaded {len(db_keys)} translation rows from DB")

    db_en_keys = {k for (k, lang) in db_keys if lang == 'en'}
    db_fa_keys = {k for (k, lang) in db_keys if lang == 'fa'}

    # 3. Global comparison
    print("\n" + "=" * 72)
    print("  GLOBAL ANALYSIS")
    print("=" * 72)

    missing_en = sorted(all_used_keys - db_en_keys)
    missing_fa = sorted(all_used_keys - db_fa_keys)
    unused_keys = sorted((db_en_keys | db_fa_keys) - all_used_keys)

    if missing_en:
        print(f"\n❌ Keys used in templates but MISSING from DB (English): {len(missing_en)}")
        for k in missing_en[:50]:
            print(f"   - {k}")
        if len(missing_en) > 50:
            print(f"   ... and {len(missing_en) - 50} more")
    else:
        print(f"\n✅ Every template key has an English row in DB")

    if missing_fa:
        print(f"\n❌ Keys used in templates but MISSING from DB (Farsi): {len(missing_fa)}")
        for k in missing_fa[:50]:
            print(f"   - {k}")
        if len(missing_fa) > 50:
            print(f"   ... and {len(missing_fa) - 50} more")
    else:
        print(f"✅ Every template key has a Farsi row in DB")

    # 4. Per-file summary
    print("\n" + "=" * 72)
    print("  PER-FILE ANALYSIS (files with missing keys only)")
    print("=" * 72)

    any_problems = False
    for file, keys in sorted(keys_by_file.items()):
        file_missing_fa = [k for k in keys if k not in db_fa_keys]
        file_missing_en = [k for k in keys if k not in db_en_keys]
        if file_missing_fa or file_missing_en:
            any_problems = True
            print(f"\n📄 {file}   ({len(keys)} keys total)")
            if file_missing_en:
                print(f"   ❌ Missing EN ({len(file_missing_en)}):")
                for k in file_missing_en[:15]:
                    print(f"      - {k}")
                if len(file_missing_en) > 15:
                    print(f"      ... and {len(file_missing_en) - 15} more")
            if file_missing_fa:
                print(f"   ❌ Missing FA ({len(file_missing_fa)}):")
                for k in file_missing_fa[:15]:
                    print(f"      - {k}")
                if len(file_missing_fa) > 15:
                    print(f"      ... and {len(file_missing_fa) - 15} more")

    if not any_problems:
        print("\n✅ All HTML files have complete translation coverage.")

    # 5. Summary
    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print(f"  HTML files scanned:            {len(keys_by_file)}")
    print(f"  Unique keys in templates:      {len(all_used_keys)}")
    print(f"  Keys in DB (en):               {len(db_en_keys)}")
    print(f"  Keys in DB (fa):               {len(db_fa_keys)}")
    print(f"  Missing EN:                    {len(missing_en)}")
    print(f"  Missing FA:                    {len(missing_fa)}")
    print(f"  In DB but unused by templates: {len(unused_keys)}")
    print("=" * 72)

    # 6. Optional — dump full list of missing FA keys to a file
    if missing_fa:
        out_file = Path("missing_fa_keys.txt")
        out_file.write_text("\n".join(missing_fa), encoding="utf-8")
        print(f"\n💾 Full list of missing FA keys written to: {out_file.resolve()}")

    if missing_en:
        out_file = Path("missing_en_keys.txt")
        out_file.write_text("\n".join(missing_en), encoding="utf-8")
        print(f"💾 Full list of missing EN keys written to: {out_file.resolve()}")


if __name__ == "__main__":
    main()
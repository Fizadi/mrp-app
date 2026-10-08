#!/usr/bin/env python3
"""
Automatically add language‑aware date formatting to HTML templates and API endpoints.
Usage: python auto_date_formatter.py
"""

import os
import re
import ast
from pathlib import Path
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
PROJECT_ROOT = Path(".")
TEMPLATES_DIR = PROJECT_ROOT / "templates"
MODULES_DIR = PROJECT_ROOT / "app" / "modules"

# Regex patterns for date keys in templates
TEMPLATE_DATE_PATTERNS = [
    r'{{.*?\.(?:DueDateKey|StartDateKey|EndDateKey|CreatedDateKey|ModifiedDateKey|DateKey).*?}}',
    r'{{.*?\.(?:dueDateKey|startDateKey|endDateKey|createdDateKey|modifiedDateKey|dateKey).*?}}',
]

# Regex for finding JSON returns in API endpoints that contain date keys
API_RETURN_PATTERN = re.compile(r'return\s+jsonify\((\{.*?\})\)', re.DOTALL)

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def find_template_date_variables(content):
    """Return set of variable names that look like date keys."""
    variables = set()
    for pattern in TEMPLATE_DATE_PATTERNS:
        matches = re.findall(pattern, content)
        for m in matches:
            # Extract the variable name (e.g., 'order.DueDateKey')
            var_match = re.search(r'{{.*?([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*Key).*?}}', m)
            if var_match:
                variables.add(var_match.group(1))
    return variables

def suggest_filter_replacement(original):
    """Suggest replacement: {{ variable }} -> {{ variable|format_date }}"""
    return re.sub(r'{{(.*?)}}', r'{{\1|format_date}}', original)

def process_template_file(file_path):
    """Show diff and ask user to apply filter."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    variables = find_template_date_variables(content)
    if not variables:
        return False
    print(f"\n📄 {file_path}")
    print(f"   Found date variables: {', '.join(variables)}")
    # For each occurrence, replace
    new_content = content
    for var in variables:
        # Replace {{ var }} with {{ var|format_date }}
        pattern = re.compile(r'{{.*?' + re.escape(var) + r'.*?}}')
        new_content = pattern.sub(lambda m: m.group(0).replace('}}', '|format_date}}'), new_content)
    if new_content == content:
        return False
    # Show unified diff
    import difflib
    diff = difflib.unified_diff(
        content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=str(file_path),
        tofile=str(file_path) + " (modified)",
        lineterm=''
    )
    diff_text = ''.join(diff)
    print("\n" + "="*80)
    print(diff_text)
    print("="*80)
    choice = input("Apply this change? (y/n/skip): ").strip().lower()
    if choice == 'y':
        backup = file_path.with_suffix(file_path.suffix + '.datebak')
        with open(backup, 'w', encoding='utf-8') as f:
            f.write(content)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("✅ Applied.")
        return True
    elif choice == 'skip':
        print("⏭️ Skipped.")
        return False
    else:
        print("❌ Not applied.")
        return False

def find_api_endpoints_with_dates(api_file_content):
    """Look for places where a date key is returned in jsonify."""
    # Very basic: search for 'jsonify' and then for keys like 'DueDateKey' inside the same line
    lines = api_file_content.splitlines()
    candidates = []
    for i, line in enumerate(lines):
        if 'jsonify' in line and ('Key' in line or 'Date' in line):
            # Try to extract the variable name
            match = re.search(r"'([a-zA-Z_][a-zA-Z0-9_]*Key)':", line)
            if match:
                candidates.append((i+1, match.group(1)))
    return candidates

def process_api_file(file_path):
    """Show API modifications (manual guidance)."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    candidates = find_api_endpoints_with_dates(content)
    if not candidates:
        return False
    print(f"\n📄 {file_path}")
    print("   API endpoints that return date keys (may need manual formatting):")
    for line_no, key in candidates:
        print(f"      Line {line_no}: returns '{key}'")
    print("\n   Suggested addition (in the endpoint):")
    print("""
        from flask import session
        from app.core.utils import format_date
        lang = session.get('lang', 'en')
        date_format = 'persian' if lang == 'fa' else 'gregorian'
        for item in items:
            if item.get('DueDateKey'):
                item['DueDateFormatted'] = format_date(item['DueDateKey'], date_format, lang)
    """)
    input("Press Enter to continue...")
    return True

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    print("🔍 Scanning templates for date variables...")
    html_files = list(TEMPLATES_DIR.rglob("*.html"))
    for f in html_files:
        process_template_file(f)

    print("\n🔍 Scanning API files for date keys in jsonify...")
    api_files = list(MODULES_DIR.rglob("api.py"))
    for f in api_files:
        process_api_file(f)

    print("\n✅ Done. Remember to add the Jinja2 filter in app/__init__.py if not already done.")
    print("   Add: app.jinja_env.filters['format_date'] = format_date_filter")

if __name__ == "__main__":
    main()
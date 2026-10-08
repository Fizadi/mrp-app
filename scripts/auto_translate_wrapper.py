#!/usr/bin/env python3
"""
Automatically wrap hardcoded text in Flask templates with get_translation().
Process one file at a time, review changes interactively.
"""

import os
import sys
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
from bs4.element import Comment

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
TEMPLATES_DIR = "templates"          # relative to script location
BACKUP_SUFFIX = ".bak"               # backup original before overwriting
KEY_PREFIX = "auto"                  # keys will be auto.XXXXX

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def generate_key(text, filepath, index):
    """Generate a unique key for a text fragment."""
    unique = f"{filepath}:{index}:{text}"
    h = hashlib.md5(unique.encode()).hexdigest()[:8]
    return f"{KEY_PREFIX}.{h}"

def should_skip_parent(tag):
    """Skip text inside <script>, <style>, or tags with certain classes/attrs."""
    for parent in tag.parents:
        if parent.name in ('script', 'style'):
            return True
        # Skip if parent has class 'no-translate' or data-no-translate
        if parent.get('class') and 'no-translate' in parent.get('class'):
            return True
        if parent.get('data-no-translate') == 'true':
            return True
    return False

def wrap_text_nodes(soup, filepath):
    """
    Find all visible text strings (not inside tags like script/style),
    and replace each with a Jinja2 expression: {{ get_translation('key', 'text') }}
    Returns (modified_soup, count, changes).
    """
    changes = []
    index = 0
    text_nodes = []
    for element in soup.find_all(string=True):
        if isinstance(element, Comment):
            continue
        if should_skip_parent(element):
            continue
        stripped = element.strip()
        if not stripped:
            continue
        # Skip if already has Jinja2 delimiters
        if '{{' in element or '{%' in element or '}}' in element or '%}' in element:
            continue
        text_nodes.append((element, stripped))

    for original_element, original_text in text_nodes:
        index += 1
        key = generate_key(original_text, filepath, index)
        replacement = f"{{{{ get_translation('{key}', '{original_text}') }}}}"
        original_element.replace_with(replacement)
        changes.append((original_text, replacement, key))
    return soup, len(changes), changes

def show_diff(original_content, new_content):
    """Simple diff output using difflib."""
    import difflib
    diff = difflib.unified_diff(
        original_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile='original',
        tofile='modified',
        lineterm=''
    )
    diff_text = ''.join(diff)
    if diff_text:
        print("\n" + "="*80)
        print("DIFF (original -> modified):")
        print("="*80)
        print(diff_text)
    else:
        print("\nNo changes detected.")

def interactive_process_file(html_path):
    """Process a single HTML file with user interaction."""
    print(f"\n📄 Processing: {html_path}")
    with open(html_path, 'r', encoding='utf-8') as f:
        original_content = f.read()

    soup = BeautifulSoup(original_content, 'html.parser')
    modified_soup, change_count, changes = wrap_text_nodes(soup, str(html_path))

    if change_count == 0:
        print("   No translatable text found. Skipping.")
        return True

    new_content = str(modified_soup)
    show_diff(original_content, new_content)

    print(f"\n📊 Found {change_count} text fragment(s) to wrap.")
    print("Options:")
    print("  [y] Yes, apply these changes")
    print("  [n] No, skip this file")
    print("  [e] Edit manually (opens file in default editor, then re-run diff)")
    print("  [q] Quit entirely")
    choice = input("Your choice [y/n/e/q]: ").strip().lower()

    if choice == 'y':
        backup_path = html_path + BACKUP_SUFFIX
        os.rename(html_path, backup_path)
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"✅ Applied changes. Backup saved as {backup_path}")
        return True
    elif choice == 'n':
        print("⏭️  Skipped.")
        return True
    elif choice == 'e':
        # Use notepad on Windows, otherwise fallback to nano or vi
        editor = os.environ.get('EDITOR', 'notepad' if sys.platform == 'win32' else 'nano')
        os.system(f"{editor} {html_path}")
        print("Re-reading file after manual edit...")
        return interactive_process_file(html_path)
    elif choice == 'q':
        print("Quitting.")
        return False
    else:
        print("Invalid choice, skipping file.")
        return True

def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if os.path.isfile(target):
            files = [target]
        else:
            print("Please provide a single file path.")
            return
    else:
        templates_path = Path(TEMPLATES_DIR)
        if not templates_path.exists():
            print(f"Templates directory '{TEMPLATES_DIR}' not found.")
            return
        files = list(templates_path.rglob("*.html"))
        if not files:
            print("No .html files found.")
            return
        print(f"Found {len(files)} HTML files.")

    for filepath in files:
        if not interactive_process_file(filepath):
            break
    print("\nAll done.")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Batch wrap all HTML templates with get_translation().
Usage: python batch_wrap_templates.py
"""

import os
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
from bs4.element import Comment

TEMPLATES_DIR = "templates"
KEY_PREFIX = "auto"

def generate_key(text, filepath, index):
    # Convert Path to string for hashing
    unique = f"{str(filepath)}:{index}:{text}"
    h = hashlib.md5(unique.encode()).hexdigest()[:8]
    return f"{KEY_PREFIX}.{h}"

def should_skip_parent(tag):
    for parent in tag.parents:
        if parent.name in ('script', 'style'):
            return True
        if parent.get('class') and 'no-translate' in parent.get('class'):
            return True
        if parent.get('data-no-translate') == 'true':
            return True
    return False

def wrap_text_nodes(soup, filepath):
    text_nodes = []
    for element in soup.find_all(string=True):
        if isinstance(element, Comment):
            continue
        if should_skip_parent(element):
            continue
        stripped = element.strip()
        if not stripped:
            continue
        if '{{' in element or '{%' in element or '}}' in element or '%}' in element:
            continue
        text_nodes.append((element, stripped))
    changes = 0
    for idx, (orig, text) in enumerate(text_nodes):
        key = generate_key(text, filepath, idx)
        replacement = f"{{{{ get_translation('{key}', '{text}') }}}}"
        orig.replace_with(replacement)
        changes += 1
    return soup, changes

def process_file(html_path):
    html_path_str = str(html_path)
    with open(html_path_str, 'r', encoding='utf-8') as f:
        content = f.read()
    soup = BeautifulSoup(content, 'html.parser')
    new_soup, changes = wrap_text_nodes(soup, html_path)
    if changes == 0:
        return False
    backup = html_path_str + '.bak'
    if not os.path.exists(backup):
        os.rename(html_path_str, backup)
    with open(html_path_str, 'w', encoding='utf-8') as f:
        f.write(str(new_soup))
    print(f"Wrapped {changes} texts in {html_path_str}")
    return True

def main():
    templates_dir = Path(TEMPLATES_DIR)
    if not templates_dir.exists():
        print(f"Templates directory not found: {templates_dir}")
        return
    html_files = list(templates_dir.rglob("*.html"))
    print(f"Found {len(html_files)} HTML files.")
    for f in html_files:
        try:
            process_file(f)
        except Exception as e:
            print(f"Error processing {f}: {e}")

if __name__ == "__main__":
    main()
 
def is_inside_jinja(text, whole_content, pos):
    # Simplified: check if the text node is inside {% ... %} or {{ ... }}
    # But since BeautifulSoup doesn't easily give position, a safer approach:
    # Do not replace any text that contains '{{' or '{%' (we already skip)
    pass
#!/usr/bin/env python3
"""
Extract all action buttons and status labels from Flask HTML templates.
Usage: python extract_ui_elements.py /path/to/templates
"""

import os
import sys
from bs4 import BeautifulSoup
from collections import defaultdict
import re

def extract_elements_from_file(filepath):
    """Extract buttons, links, and status labels from one HTML file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')

    # Data structures for this file
    actions = []      # (text, icon_class, btn_classes, element_type)
    statuses = []     # (text, parent_classes, element_name)

    # 1. Find action buttons / links
    # Look for <button> and <a> that have an <i> inside or contain 'btn' class
    for elem in soup.find_all(['button', 'a']):
        # Skip if no relevant class or no icon
        classes = elem.get('class', [])
        if not any('btn' in c for c in classes) and not elem.find('i'):
            continue

        # Get button text (excluding icon text)
        text = elem.get_text(strip=True)
        if not text:
            continue

        # Get icon class from first <i> child
        icon = elem.find('i')
        icon_class = ''
        if icon and icon.get('class'):
            icon_class = ' '.join(icon.get('class'))

        # Capture the element's classes (for uniformity analysis)
        btn_classes = ' '.join(classes)

        actions.append((text, icon_class, btn_classes, elem.name))

    # 2. Find status labels (typically badges, spans, or divs with status text)
    # Common patterns: <span class="badge">, <div class="status">, etc.
    # Also look for <td> containing status text if it has a specific class or parent pattern
    for elem in soup.find_all(['span', 'div', 'td', 'label']):
        classes = elem.get('class', [])
        text = elem.get_text(strip=True)
        if not text or len(text) > 50:   # ignore long text
            continue

        # Heuristic: status text is short, often contains words like 'progress', 'draft', 'completed', etc.
        # Alternatively, you can keep all short texts and manually filter later
        if any(word in text.lower() for word in ['progress', 'draft', 'completed', 'pending', 'approved', 'rejected', 'closed', 'open', 'active', 'inactive', 'on hold']):
            parent_classes = ' '.join(elem.parent.get('class', [])) if elem.parent else ''
            statuses.append((text, parent_classes, elem.name))

        # Also capture any element with class containing 'badge', 'status', 'state'
        if any(c in classes for c in ['badge', 'status', 'state', 'tag']):
            parent_classes = ' '.join(elem.parent.get('class', [])) if elem.parent else ''
            statuses.append((text, parent_classes, elem.name))

    return actions, statuses

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_ui_elements.py /path/to/templates")
        sys.exit(1)

    template_dir = sys.argv[1]
    if not os.path.isdir(template_dir):
        print(f"Error: {template_dir} is not a directory")
        sys.exit(1)

    all_actions = defaultdict(set)   # key: action text, value: set of (icon_class, btn_classes, element_type)
    all_statuses = defaultdict(set)  # key: status text, value: set of (parent_classes, element_name)

    for root, dirs, files in os.walk(template_dir):
        for file in files:
            if file.endswith('.html'):
                filepath = os.path.join(root, file)
                actions, statuses = extract_elements_from_file(filepath)
                for act in actions:
                    text, icon, btn_classes, elem_type = act
                    all_actions[text].add((icon, btn_classes, elem_type))
                for stat in statuses:
                    text, parent_classes, elem_name = stat
                    all_statuses[text].add((parent_classes, elem_name))

    # Output results
    print("\n" + "="*80)
    print("UNIQUE ACTION BUTTONS FOUND")
    print("="*80)
    for text, variants in sorted(all_actions.items()):
        print(f"\n🔹 {text}")
        for icon, classes, elem_type in variants:
            print(f"   - {elem_type}: classes='{classes}', icon='{icon}'")

    print("\n" + "="*80)
    print("UNIQUE STATUS LABELS FOUND")
    print("="*80)
    for text, variants in sorted(all_statuses.items()):
        print(f"\n🏷️  {text}")
        for parent_classes, elem_name in variants:
            print(f"   - {elem_name} with parent classes='{parent_classes}'")

if __name__ == '__main__':
    main()
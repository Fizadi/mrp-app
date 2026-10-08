#!/usr/bin/env python3
"""
Convert auto.xxx keys to semantic keys in a template file (non‑interactive).
Usage:
    python rename_to_semantic_keys.py templates/supply_chain/suppliers.html --auto
    python rename_to_semantic_keys.py templates/supply_chain/suppliers.html --auto --dry-run
    python rename_to_semantic_keys.py templates/supply_chain/suppliers.html --auto --force
"""

import sys
import re
import os
import json
import argparse
from pathlib import Path
from app import create_app
from sqlalchemy.sql import text

def extract_auto_keys_with_defaults(content):
    """Return list of (key, default_text) for auto.xxx keys."""
    pattern = re.compile(r"get_translation\(\s*['\"](auto\.[a-f0-9]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]")
    matches = pattern.findall(content)
    # Remove duplicates (keep first occurrence as the default text source)
    seen = {}
    for key, default in matches:
        if key not in seen:
            seen[key] = default
    return list(seen.items())

def slugify(text):
    """Convert text to a safe key fragment (lowercase, underscores, no spaces)."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)  # remove punctuation
    text = re.sub(r'\s+', '_', text)     # replace spaces with underscore
    # Remove consecutive underscores
    text = re.sub(r'_+', '_', text)
    # Remove leading/trailing underscores
    text = text.strip('_')
    # Limit length to 50 characters (optional)
    if len(text) > 50:
        text = text[:50]
    return text

def generate_semantic_key(default_text, module):
    """Generate a semantic key: module.default_text_slug."""
    slug = slugify(default_text)
    if not slug:
        # fallback if slug is empty
        slug = "untitled"
    return f"{module}.{slug}"

def get_module_from_file(file_path, templates_dir):
    """Extract module name from file path (first directory under templates)."""
    rel_path = os.path.relpath(file_path, templates_dir)
    parts = Path(rel_path).parts
    if len(parts) > 1:
        return parts[0]
    return 'root'

def update_template_and_db(file_path, mapping, conn, delete_old=False):
    """Update template and database with new semantic keys."""
    # Read template
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Apply replacements
    new_content = content
    for old_key, new_key in mapping.items():
        pattern = re.compile(rf"(get_translation\(\s*['\"]){re.escape(old_key)}(['\"]\s*,)")
        new_content = pattern.sub(rf"\1{new_key}\2", new_content)
    
    # Backup and write
    backup_path = file_path + '.bak2'
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"✅ Template updated. Backup saved to {backup_path}")
    
    # Update database
    for old_key, new_key in mapping.items():
        # Check if new key already exists
        existing_en = conn.execute(
            text("SELECT Value FROM t_Translations WHERE Key = :key AND Language = 'en'"),
            {'key': new_key}
        ).fetchone()
        if existing_en:
            print(f"⚠️  New key '{new_key}' already has English: '{existing_en[0]}'. Skipping copy.")
        else:
            # Copy English
            old_en = conn.execute(
                text("SELECT Value FROM t_Translations WHERE Key = :key AND Language = 'en'"),
                {'key': old_key}
            ).fetchone()
            if old_en:
                conn.execute(
                    text("INSERT INTO t_Translations (Key, Language, Value) VALUES (:key, 'en', :value)"),
                    {'key': new_key, 'value': old_en[0]}
                )
            # Copy Persian
            old_fa = conn.execute(
                text("SELECT Value FROM t_Translations WHERE Key = :key AND Language = 'fa'"),
                {'key': old_key}
            ).fetchone()
            if old_fa and old_fa[0]:
                conn.execute(
                    text("INSERT INTO t_Translations (Key, Language, Value) VALUES (:key, 'fa', :value)"),
                    {'key': new_key, 'value': old_fa[0]}
                )
            print(f"Copied translations: {old_key} -> {new_key}")
        conn.commit()
    
    # Delete old keys if requested
    if delete_old:
        for old_key in mapping.keys():
            conn.execute(text("DELETE FROM t_Translations WHERE Key = :key"), {'key': old_key})
        conn.commit()
        print("Old auto keys deleted.")
    else:
        print("Old keys kept (you can delete later with --delete-old).")

def main():
    parser = argparse.ArgumentParser(description='Rename auto.xxx keys to semantic keys.')
    parser.add_argument('template_file', help='Path to the HTML template file')
    parser.add_argument('--auto', action='store_true', help='Automatically accept suggested keys (no prompts)')
    parser.add_argument('--dry-run', action='store_true', help='Show what will be changed without writing')
    parser.add_argument('--force', action='store_true', help='Skip final confirmation (use with --auto)')
    parser.add_argument('--delete-old', action='store_true', help='Delete old auto.xxx keys from database after copy')
    parser.add_argument('--mapping', help='JSON file mapping old_key -> new_key (overrides auto-generation)')
    args = parser.parse_args()
    
    if not args.auto:
        print("This script is intended for auto‑accept. Use --auto to run non‑interactively.")
        print("If you need interactive mode, use the previous version.")
        sys.exit(1)
    
    template_file = args.template_file
    if not os.path.isfile(template_file):
        print(f"File not found: {template_file}")
        sys.exit(1)
    
    print("Loading Flask app context...")
    app = create_app()
    with app.app_context():
        from app import db
        conn = db.engine.connect()
        
        # Read template content
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()
        auto_keys = extract_auto_keys_with_defaults(content)
        if not auto_keys:
            print("No auto.xxx keys found in this file.")
            conn.close()
            return
        
        # Determine module hint
        project_root = os.path.dirname(app.root_path)
        templates_dir = os.path.join(project_root, 'templates')
        module = get_module_from_file(template_file, templates_dir)
        print(f"Module: '{module}'")
        
        # Build mapping
        mapping = {}
        if args.mapping and os.path.exists(args.mapping):
            with open(args.mapping, 'r', encoding='utf-8') as f:
                custom_map = json.load(f)
            for old_key, default_text in auto_keys:
                new_key = custom_map.get(old_key)
                if not new_key:
                    new_key = generate_semantic_key(default_text, module)
                mapping[old_key] = new_key
        else:
            for old_key, default_text in auto_keys:
                mapping[old_key] = generate_semantic_key(default_text, module)
        
        # Show changes
        print("\nWill perform the following renames:")
        for old, new in mapping.items():
            print(f"  {old} -> {new}")
        
        if args.dry_run:
            print("\nDry run completed. No changes made.")
            conn.close()
            return
        
        if not args.force:
            confirm = input("\nProceed with these changes? (y/n): ").strip().lower()
            if confirm != 'y':
                print("Aborted.")
                conn.close()
                return
        
        # Execute update
        update_template_and_db(template_file, mapping, conn, delete_old=args.delete_old)
        conn.close()
        print("\n✅ Done!")

if __name__ == '__main__':
    main()
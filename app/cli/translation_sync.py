import re
import os
from pathlib import Path
import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy.sql import text

TRANSLATION_PATTERN = re.compile(r"get_translation\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]")

def extract_key_default_from_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return TRANSLATION_PATTERN.findall(content)

def get_module_prefix(file_path, templates_dir):
    rel_path = os.path.relpath(file_path, templates_dir)
    parts = Path(rel_path).parts
    if len(parts) == 1:
        return 'root.'
    return parts[0] + '.'

@click.command('sync-translations')
@click.option('--module', default=None, help='Only sync keys with this prefix (e.g., supply_chain)')
@click.option('--file', 'file_path', default=None, help='Only sync keys from this template file (relative to project root)')
@click.option('--replace', is_flag=True, help='Delete existing keys for the module or file before inserting')
@click.option('--add-fa-placeholders', is_flag=True, help='Insert empty Persian rows for new keys')
@with_appcontext
def sync_translations(module, file_path, replace, add_fa_placeholders):
    project_root = os.path.dirname(current_app.root_path)
    templates_dir = os.path.join(project_root, 'templates')
    
    if not os.path.exists(templates_dir):
        click.echo(f"ERROR: Templates directory not found: {templates_dir}")
        return

    key_data = {}

    if file_path:
        if not os.path.isabs(file_path):
            file_path = os.path.join(project_root, file_path)
        if not os.path.isfile(file_path):
            click.echo(f"ERROR: File not found: {file_path}")
            return
        files_to_scan = [file_path]
        click.echo(f"Scanning single file: {file_path}")
    else:
        files_to_scan = []
        for root, dirs, files in os.walk(templates_dir):
            for f in files:
                if f.endswith('.html'):
                    files_to_scan.append(os.path.join(root, f))
        click.echo(f"Scanning all {len(files_to_scan)} template files")

    for fpath in files_to_scan:
        pairs = extract_key_default_from_file(fpath)
        if pairs:
            prefix = get_module_prefix(fpath, templates_dir)
            for key, default_text in pairs:
                if not default_text:
                    default_text = key
                key_data[key] = (default_text, prefix)

    if not key_data:
        click.echo("No translation keys found.")
        return

    all_keys = set(key_data.keys())

    if module:
        module_prefix = module + '.'
        filtered_keys = {k for k in all_keys if k.startswith(module_prefix)}
        if not filtered_keys:
            click.echo(f"No keys found with prefix '{module_prefix}'")
            return
        all_keys = filtered_keys
        click.echo(f"Filtered to module '{module}': {len(all_keys)} keys.")
    else:
        click.echo(f"Found {len(all_keys)} unique translation keys.")

    engine = current_app.extensions['sqlalchemy'].engine
    with engine.connect() as conn:
        try:
            if replace:
                if module:
                    module_prefix = module + '.'
                    conn.execute(text("DELETE FROM t_Translations WHERE Key LIKE :pattern"),
                                 {'pattern': module_prefix + '%'})
                    conn.commit()
                    click.echo(f"Deleted translations with prefix '{module_prefix}'.")
                elif file_path:
                    if all_keys:
                        for key in all_keys:
                            conn.execute(text("DELETE FROM t_Translations WHERE Key = :key"),
                                         {'key': key})
                        conn.commit()
                        click.echo(f"Deleted translations for {len(all_keys)} keys from file.")
                else:
                    click.echo("--replace requires --module or --file to avoid deleting everything.")
                    return

            for key in all_keys:
                default_text = key_data[key][0]
                existing_en = conn.execute(
                    text("SELECT 1 FROM t_Translations WHERE Key = :key AND Language = 'en'"),
                    {'key': key}
                ).fetchone()
                if not existing_en:
                    conn.execute(
                        text("INSERT INTO t_Translations (Key, Language, Value) VALUES (:key, 'en', :value)"),
                        {'key': key, 'value': default_text}
                    )
                    click.echo(f"Added English: {key} = '{default_text}'")

                if add_fa_placeholders:
                    existing_fa = conn.execute(
                        text("SELECT 1 FROM t_Translations WHERE Key = :key AND Language = 'fa'"),
                        {'key': key}
                    ).fetchone()
                    if not existing_fa:
                        conn.execute(
                            text("INSERT INTO t_Translations (Key, Language, Value) VALUES (:key, 'fa', '')"),
                            {'key': key}
                        )
                        click.echo(f"Added Persian placeholder: {key}")

            conn.commit()
            click.echo("Sync completed.")
        except Exception as e:
            conn.rollback()
            click.echo(f"ERROR: {e}")
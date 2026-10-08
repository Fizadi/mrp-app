# app/core/translation.py
print("🔴 LOADING translation.py")

from flask import session, current_app
from sqlalchemy.sql import text
import logging

# ============================================================
# CORE TRANSLATION FUNCTIONS - MULTI-LANGUAGE SUPPORT
# ============================================================

def load_translations():
    """Load all translations for the current language from database."""
    lang = session.get('lang', 'en')
    translations = {}
    
    print(f"🔴 load_translations() called for language: {lang}")
    
    try:
        engine = current_app.extensions['sqlalchemy'].engine
        
        with engine.connect() as conn:
            # Use the new schema with translation_key and Language
            rows = conn.execute(
                text("SELECT translation_key, Value FROM t_Translations WHERE Language = :lang"),
                {'lang': lang}
            ).fetchall()
            
            print(f"🔴 Found {len(rows)} translations for language: {lang}")
            
            for row in rows:
                if row and len(row) >= 2:
                    key = row[0]
                    value = row[1]
                    if key and value and str(value).strip():
                        translations[str(key)] = str(value)
                    
    except Exception as e:
        print(f"❌ Error loading translations: {e}")
        logging.error(f"Error loading translations: {e}")
    
    print(f"🔴 Loaded {len(translations)} translations for language: {lang}")
    
    # Print some sample keys to debug
    if len(translations) > 0:
        sample_keys = list(translations.keys())[:10]
        print(f"🔴 Sample keys: {sample_keys}")
        for key in sample_keys:
            print(f"🔴   {key} = {translations[key]}")
    else:
        print("⚠️ WARNING: No translations loaded!")
    
    return translations


def get_translation(key, default=None):
    """Generic translation lookup with multi-language support."""
    lang = session.get('lang', 'en')
    
    try:
        engine = current_app.extensions['sqlalchemy'].engine
        
        with engine.connect() as conn:
            # Use the new schema
            row = conn.execute(
                text("SELECT Value FROM t_Translations WHERE translation_key = :key AND Language = :lang"),
                {'key': key, 'lang': lang}
            ).fetchone()
            
            if row and row[0] and str(row[0]).strip():
                return str(row[0])
                
    except Exception as e:
        print(f"❌ Translation error for key '{key}': {e}")
    
    # Fallback: return default or key
    return default if default is not None else key


def inject_translations():
    """Make translation helpers available inside templates."""
    print("🔴 inject_translations() called")
    
    lang = session.get('lang', 'en')
    
    # Simple time formatter fallback
    def format_time_ago(ts):
        return str(ts) if ts else ''
    
    # Load all translations
    translations = load_translations()
    
    # CRITICAL: Make sure ALL translations are included in js_translations
    # This includes the status and workflow translations
    all_translations = translations.copy()
    
    # Also add fallback translations in case the database doesn't have them
    fallbacks = {
        'global.draft': 'Draft',
        'global.released': 'Released',
        'global.in_progress': 'In Progress',
        'global.completed': 'Completed',
        'global.closed': 'Closed',
        'global.hold': 'Hold',
        'global.blocked': 'Blocked',
        'global.canceled': 'Canceled',
        'global.manufacturing': 'Manufacturing',
        'global.subcontract': 'Subcontract',
        'global.repair': 'Repair',
        'global.actions': 'Actions',
        'global.status': 'Status',
        'global.view': 'View',
        'global.edit': 'Edit',
        'global.save': 'Save',
        'global.cancel': 'Cancel',
        'global.delete': 'Delete',
        'global.confirm': 'Confirm',
        'global.start': 'Start',
        'global.complete': 'Complete',
        'global.close': 'Close',
        'global.close_order': 'Close',
        'global.issue': 'Issue',
        'global.receive': 'Receive',
        'global.quality': 'Quality',
        'global.qty': 'Qty',
        'global.release': 'Release',
        'global.resume': 'Resume',
        'global.to_supplier': 'To Supplier',
        'global.search': 'Search',
        'global.loading': 'Loading...',
        'global.no_data': 'No data available',
        'global.error': 'Error',
        'global.success': 'Success',
        'workflow.pending': 'Pending',
        'workflow.approved': 'Approved',
        'workflow.rejected': 'Rejected',
        'workflow.in_progress': 'In Progress',
        'workflow.completed': 'Completed',
        'workflow.escalated': 'Escalated',
        'workflow.not_started': 'Not Started',
    }
    
    # Add fallbacks only if the translation doesn't exist
    for key, value in fallbacks.items():
        if key not in all_translations or not all_translations[key]:
            all_translations[key] = value
    
    print(f"🔴 Returning {len(all_translations)} translations for language: {lang}")
    print(f"🔴 Status translations in js_translations:")
    status_keys = ['global.draft', 'global.released', 'global.in_progress', 'global.completed', 'global.closed']
    for key in status_keys:
        print(f"🔴   {key} = {all_translations.get(key, 'NOT FOUND')}")
    
    return {
        'lang': lang,
        'get_translation': get_translation,
        'get_localized': lambda row, eng, loc, lang=None: row.get(eng, '') if row else '',
        'get_product_translation': lambda product, lang=None: product.get('descEnglish', '') if product else '',
        'get_operation_translation': lambda op, lang=None: op.get('Name', '') if op else '',
        'format_time_ago': format_time_ago,
        'js_translations': all_translations
    }


# ============================================================
# ALIASES FOR BACKWARD COMPATIBILITY
# ============================================================
translation = get_translation
localize = lambda row, eng, loc, lang=None: row.get(eng, '') if row else ''
get_localized = localize
get_product_translation = lambda product, lang=None: product.get('descEnglish', '') if product else ''
get_operation_translation = lambda op, lang=None: op.get('Name', '') if op else ''
safe_format_time_ago = lambda ts: str(ts) if ts else ''
load_translations = load_translations
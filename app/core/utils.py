"""
Utility functions for the Flask application
"""
from flask import session
import datetime
from datetime import timezone

def safe_format_time_ago(dt):
    """
    Safely format a datetime as a human-readable time ago string.
    
    Args:
        dt: datetime object or string
    
    Returns:
        String like "2 hours ago" or empty string if error
    """
    if not dt:
        return ""
    
    try:
        # If dt is a string, parse it
        if isinstance(dt, str):
            # Try to parse ISO format
            if 'T' in dt:
                dt = datetime.datetime.fromisoformat(dt.replace('Z', '+00:00'))
            else:
                # Try other common formats
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        dt = datetime.datetime.strptime(dt, fmt)
                        break
                    except ValueError:
                        continue
        
        # Ensure dt is timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        now = datetime.datetime.now(timezone.utc)
        diff = now - dt
        
        # Calculate time differences
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif seconds < 604800:
            days = int(seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"
        elif seconds < 2592000:
            weeks = int(seconds / 604800)
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"
        elif seconds < 31536000:
            months = int(seconds / 2592000)
            return f"{months} month{'s' if months != 1 else ''} ago"
        else:
            years = int(seconds / 31536000)
            return f"{years} year{'s' if years != 1 else ''} ago"
            
    except Exception as e:
        # Log error if needed
        # print(f"Error formatting time: {e}")
        return ""

def get_products():
    """Get all products from database"""
    from app.core.database import get_db_connection
    conn = get_db_connection()
    if conn:
        try:
            products = conn.execute("""
                SELECT ProductId as code, descEnglish as name 
                FROM t_Product 
                ORDER BY descEnglish
            """).fetchall()
            # REMOVED: conn.close()
            return [dict(product) for product in products]
        except Exception as e:
            print(f"Error fetching products: {e}")
            return []
    return []

def get_work_centers():
    """Get all work centers from database"""
    from app.core.database import get_db_connection
    conn = get_db_connection()
    if conn:
        try:
            work_centers = conn.execute("""
                SELECT WorkCenterID as code, Name as name 
                FROM t_WorkCenter 
                ORDER BY Name
            """).fetchall()
            # REMOVED: conn.close()
            return [dict(wc) for wc in work_centers]
        except Exception as e:
            print(f"Error fetching work centers: {e}")
            return []
    return []



def format_date(date_key, date_format=None, lang=None):
    """
    Format a date using the DimDate table.
    
    Args:
        date_key: Integer date key (e.g., 20250101)
        date_format: 'gregorian', 'persian', 'hijri' (None = auto-detect from session)
        lang: Language code (None = use session)
    
    Returns:
        Formatted date string
    """
    from flask import session
    from app.core.database import get_db_connection
    
    if not date_key:
        return ''
    
    # Get language from session if not provided
    if lang is None:
        lang = session.get('lang', 'en')
    
    # Get calendar from session if not provided
    if date_format is None:
        date_format = session.get('calendar', 'gregorian')
    
    conn = get_db_connection()
    if not conn:
        return str(date_key)
    
    try:
        row = conn.execute("SELECT * FROM DimDate WHERE DateKey = ?", (date_key,)).fetchone()
        if not row:
            return str(date_key)
        
        # Row is a sqlite3.Row, can access by column name
        if date_format == 'persian':
            if lang == 'fa':
                return row['PersianStr']
            else:
                return f"{row['PersianYearInt']}/{row['PersianMonthNo']:02d}/{row['PersianDayInMonth']:02d}"
        elif date_format == 'hijri':
            if lang == 'ar':
                return row['HijriStr']
            else:
                return f"{row['HijriYearInt']}/{row['HijriMonthNo']:02d}/{row['HijriDayInMonth']:02d}"
        else:  # gregorian
            if lang == 'en':
                return row['GregorianStr']
            else:
                return f"{row['GregorianYearInt']}/{row['GregorianMonthNo']:02d}/{row['GregorianDayInMonth']:02d}"
    except Exception as e:
        print(f"Error formatting date: {e}")
        return str(date_key)
    finally:
        conn.close()


def format_date_filter(date_key):
    """
    Jinja2 filter to format date key according to current language and calendar.
    Usage in templates: {{ date_key|format_date }}
    """
    if not date_key:
        return ''
    return format_date(date_key)


def get_user_language_and_calendar(user_id):
    """
    Get user's language and calendar preferences from database.
    Used when loading user session.
    """
    from app.core.database import get_db_connection
    conn = get_db_connection()
    if not conn:
        return 'en', 'gregorian'
    
    try:
        row = conn.execute(
            "SELECT LanguageCode, DefaultCalendar FROM t_Users WHERE UserID = ?",
            (user_id,)
        ).fetchone()
        if row:
            return row[0] or 'en', row[1] or 'gregorian'
    except Exception as e:
        print(f"Error getting user preferences: {e}")
    finally:
        conn.close()
    
    return 'en', 'gregorian'  
 
# app/core/utils.py

def get_localized(row, english_field, local_field, lang='en'):
    """
    Return the correct language value from a row.
    lang = 'en' → english_field
    lang = 'fa', 'it', 'de', 'ja' → local_field (fallback to english)
    
    Args:
        row: Dictionary or Row object from database
        english_field: Name of the English column (e.g., 'descEnglish')
        local_field: Name of the Local column (e.g., 'descFarsi')
        lang: Current language code
    
    Returns:
        String value in the appropriate language
    """
    if not row:
        return ''
    
    # Convert row to dict if it's a Row object
    if hasattr(row, '_asdict'):
        row = row._asdict()
    elif hasattr(row, '__getitem__') and not isinstance(row, dict):
        # If it's a tuple/list, we can't access by column name
        return str(row[0]) if row else ''
    
    # If language is English, return English field
    if lang == 'en':
        return row.get(english_field, '')
    
    # For other languages, try local field first
    local_value = row.get(local_field)
    if local_value and str(local_value).strip():
        return local_value
    
    # Fallback to English
    return row.get(english_field, '')
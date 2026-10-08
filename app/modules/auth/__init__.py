from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
import hashlib
import secrets

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a password against stored hash (salt:sha256)"""
    if ':' not in stored_hash:
        return False
    salt, pwd_hash = stored_hash.split(':', 1)
    computed = hashlib.sha256((salt + password).encode()).hexdigest()
    return computed == pwd_hash

# ============================================================================
# USER PREFERENCE HELPERS
# ============================================================================

def get_user_language_and_calendar(user_id):
    """
    Get user's language and calendar preferences from database.
    Returns tuple (language_code, calendar)
    """
    from app import db
    from sqlalchemy.sql import text
    
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("""SELECT LanguageCode, DefaultCalendar 
                       FROM t_Users WHERE UserID = :uid"""),
                {'uid': user_id}
            ).fetchone()
            if row:
                lang = row[0] or 'en'
                cal = row[1] or ('persian' if lang == 'fa' else 'gregorian')
                return lang, cal
    except Exception as e:
        print(f"Error loading user preferences: {e}")
    
    return 'en', 'gregorian'

def set_user_session_preferences(user_id):
    """
    Load user preferences from database and set them in session.
    Returns tuple (language_code, calendar)
    """
    lang, calendar = get_user_language_and_calendar(user_id)
    session['lang'] = lang
    session['calendar'] = calendar
    print(f"✅ User preferences loaded: lang={lang}, calendar={calendar}")
    return lang, calendar

# ============================================================================
# AUTH ROUTES
# ============================================================================

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/') and not next_page.startswith('//'):
            return redirect(next_page)
        return redirect(url_for('dashboard.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = 'remember' in request.form
        
        from app import db
        from sqlalchemy.sql import text
        
        with db.engine.connect() as conn:
            # Find user by Username or Email
            user_row = conn.execute(
                text("""
                    SELECT UserID, Username, PasswordHash, IsActive, MustChangePassword, FullName, UserRoles
                    FROM t_Users
                    WHERE (Username = :un OR Email = :un) AND UserType = 'Internal'
                """),
                {'un': username}
            ).fetchone()
        
        if user_row and verify_password(user_row[2], password):
            if not user_row[3]:  # IsActive = 0
                flash('Your account is deactivated. Contact administrator.', 'danger')
                return render_template('auth/login.html')
            
            # ============================================================
            # LOAD USER PREFERENCES FROM DATABASE
            # ============================================================
            user_id = user_row[0]
            lang, calendar = get_user_language_and_calendar(user_id)
            
            # Set session preferences
            session['lang'] = lang
            session['calendar'] = calendar
            session['user_name'] = user_row[4]  # FullName
            session['user_id'] = user_id
            session['user_roles'] = user_row[6] if user_row[6] else '[]'
            
            print(f"✅ User {username} logged in with lang={lang}, calendar={calendar}")
            
            # Create user object for Flask-Login
            from flask_login import UserMixin
            class User(UserMixin):
                def __init__(self, id, username, fullname):
                    self.id = str(id)
                    self.username = username
                    self.fullname = fullname
            user = User(user_row[0], user_row[1], user_row[4])
            login_user(user, remember=remember)
            
            # Update last login
            with db.engine.connect() as conn:
                conn.execute(text("UPDATE t_Users SET LastLogin = CURRENT_TIMESTAMP WHERE UserID = :uid"), {'uid': user_row[0]})
                conn.commit()
            
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None
            return redirect(next_page or url_for('dashboard.dashboard'))
        else:
            flash('Invalid username/email or password', 'danger')
    
    return render_template('auth/login.html')


@auth_bp.route('/portal/login', methods=['GET', 'POST'])
def portal_login():
    if current_user.is_authenticated:
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/') and not next_page.startswith('//'):
            return redirect(next_page)
        return redirect(url_for('dashboard.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = 'remember' in request.form
        
        from app import db
        from sqlalchemy.sql import text
        
        with db.engine.connect() as conn:
            user_row = conn.execute(
                text("""
                    SELECT UserID, Username, PasswordHash, IsActive, FullName, UserRoles
                    FROM t_Users
                    WHERE (Username = :un OR Email = :un)
                      AND UserType = 'External'
                      AND PortalAccess = 1
                """),
                {'un': username}
            ).fetchone()
        
        if user_row and verify_password(user_row[2], password) and user_row[3]:
            # ============================================================
            # LOAD USER PREFERENCES FROM DATABASE
            # ============================================================
            user_id = user_row[0]
            lang, calendar = get_user_language_and_calendar(user_id)
            
            # Set session preferences
            session['lang'] = lang
            session['calendar'] = calendar
            session['user_name'] = user_row[4]  # FullName
            session['user_id'] = user_id
            session['user_roles'] = user_row[5] if user_row[5] else '[]'
            
            print(f"✅ Portal user {username} logged in with lang={lang}, calendar={calendar}")
            
            from flask_login import UserMixin
            class PortalUser(UserMixin):
                def __init__(self, id, username, fullname):
                    self.id = str(id)
                    self.username = username
                    self.fullname = fullname
            user = PortalUser(user_row[0], user_row[1], user_row[4])
            login_user(user, remember=remember)
            
            # update last login
            with db.engine.connect() as conn:
                conn.execute(text("UPDATE t_Users SET LastLogin = CURRENT_TIMESTAMP WHERE UserID = :uid"), {'uid': user_row[0]})
                conn.commit()
            
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None
            return redirect(next_page or url_for('dashboard.dashboard'))
        else:
            flash('Invalid username/email or password, or account inactive', 'danger')
    
    return render_template('auth/portal_login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ============================================================================
# DUPLICATE verify_password FUNCTION (Removed duplicate)
# ============================================================================
# Note: verify_password is defined at the top of the file.
# The duplicate at the bottom has been removed.
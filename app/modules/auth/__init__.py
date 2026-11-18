from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
import hashlib
import secrets
from werkzeug.security import check_password_hash, generate_password_hash

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

def verify_password(stored_hash: str, password: str) -> bool:
    """Verify Werkzeug hashes first, then legacy salt:sha256 hashes.

    Werkzeug hashes (scrypt:…, pbkdf2:sha256:…) also contain ':' so the
    legacy branch must NOT run first — it would silently reject every
    modern hash. Also supports the PBKDF2 hex format written by
    app/core/auth.py:hash_password() (salt:64-hex-chars, no scheme prefix).
    """
    if not stored_hash or not password:
        return False

    # 1) Werkzeug (scrypt:, pbkdf2:sha256:…)
    if stored_hash.startswith(('scrypt:', 'pbkdf2:')):
        try:
            return check_password_hash(stored_hash, password)
        except Exception:
            return False

    # 2) Legacy salt:sha256 (64 hex chars after the colon) — used by existing Valve users
    if ':' in stored_hash:
        salt, pwd_hash = stored_hash.split(':', 1)
        if len(pwd_hash) == 64:  # sha256 hex
            return hashlib.sha256((salt + password).encode()).hexdigest() == pwd_hash
        # 3) PBKDF2 salt:64-hex (written by app/core/auth.py:hash_password)
        try:
            key = hashlib.pbkdf2_hmac(
                'sha256', password.encode('utf-8'),
                salt.encode('utf-8'), 100000
            )
            return key.hex() == pwd_hash
        except Exception:
            return False

    return False


def hash_password(pw: str) -> str:
    """Create a new password hash using Werkzeug's secure password hashing."""
    if not pw:
        raise ValueError("Password must not be empty")
    return generate_password_hash(pw)

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
# SAFE NEXT-PAGE HELPER
# ============================================================================

def _safe_next_page():
    """
    Read the `next` target from query string OR form body (form body wins).
    Return it only if it is a safe, same-site relative path.

    Blocks:
      - absolute URLs (https://evil.com)
      - protocol-relative URLs (//evil.com)
      - paths that don't start with '/'
    """
    from urllib.parse import urlparse

    next_page = request.args.get('next') or request.form.get('next')
    if not next_page:
        return None

    parsed = urlparse(next_page)
    if (
        parsed.netloc
        or parsed.scheme
        or not next_page.startswith('/')
        or next_page.startswith('//')
    ):
        return None

    return next_page

# ============================================================================
# AUTH ROUTES
# ============================================================================

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        next_page = _safe_next_page()
        return redirect(next_page or url_for('dashboard.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = 'remember' in request.form
        
        from app import db
        from sqlalchemy.sql import text
        
        with db.engine.connect() as conn:
            # Find user by Username or Email — includes TenantID (v50)
            user_row = conn.execute(
                text("""
                    SELECT UserID, Username, PasswordHash, IsActive, MustChangePassword, FullName, UserRoles, TenantID
                    FROM t_Users
                    WHERE (Username = :un OR Email = :un) AND UserType = 'Internal'
                """),
                {'un': username}
            ).fetchone()
        
        if user_row and verify_password(user_row[2], password):
            # §B2: IsActive may be '1' / 1 / 'True' / True — string-aware check
            is_active_val = user_row[3]
            if str(is_active_val).strip() not in ('1', 'True', 'true'):
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
            session['tenant_id'] = user_row[7] if user_row[7] is not None else 1  # Fallback to Tenant 1 if NULL

            # §E3: set legacy demo_industry so context_processors/template header
            # shows the right industry name for tenants 1-9 (not just after switch).
            _tid = session['tenant_id']
            session['demo_industry'] = {
                1: 'valve', 2: 'furniture', 3: 'jobshop', 4: 'gauge',
                5: 'paint', 6: 'personal', 7: 'elec', 8: 'valve',
                9: 'solar',
            }.get(_tid, 'valve')
            
            print(f"✅ User {username} logged in with lang={lang}, calendar={calendar}, tenant_id={session['tenant_id']}, industry={session['demo_industry']}")
            
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
            
            next_page = _safe_next_page()
            return redirect(next_page or url_for('dashboard.dashboard'))
        else:
            flash('Invalid username/email or password', 'danger')
    
    return render_template('auth/login.html')


@auth_bp.route('/portal/login', methods=['GET', 'POST'])
def portal_login():
    if current_user.is_authenticated:
        next_page = _safe_next_page()
        return redirect(next_page or url_for('dashboard.dashboard'))
    
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
        
        # §B3: IsActive string-aware
        if user_row and verify_password(user_row[2], password) and str(user_row[3]).strip() in ('1', 'True', 'true'):
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
            
            next_page = _safe_next_page()
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
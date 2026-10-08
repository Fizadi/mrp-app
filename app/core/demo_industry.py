from flask import Blueprint, request, jsonify, session, redirect
from flask_login import login_required, current_user
from app.core.context_processors import INDUSTRY_DATA

# Create blueprint
industry_bp = Blueprint('industry', __name__, url_prefix='/api')


@industry_bp.route('/get-demo-industry', methods=['GET'])
@login_required
def get_demo_industry():
    """Get the current demo industry."""
    industry = session.get('demo_industry', 'valve')
    return jsonify({
        'industry': industry,
        'data': INDUSTRY_DATA.get(industry, INDUSTRY_DATA['valve'])
    })


@industry_bp.route('/set-demo-industry', methods=['POST'])
@login_required
def set_demo_industry():
    """Switch demo industry."""
    try:
        data = request.get_json()
        industry = data.get('industry', 'valve')

        print(f"🔍 Received industry: {industry}")

        if industry not in INDUSTRY_DATA:
            return jsonify({
                'error': f'Invalid industry. Must be one of: {", ".join(INDUSTRY_DATA.keys())}'
            }), 400

        # Set session variable
        session['demo_industry'] = industry
        # ✅ Reset project filter so we don't filter by a project of the previous industry
        session['current_project_id'] = 'All'
        session.permanent = True
        session.modified = True

        print(f"✅ Industry switched to: {industry}")
        print(f"📋 Session after update: {dict(session)}")

        return jsonify({
            'status': 'ok',
            'industry': industry,
            'data': INDUSTRY_DATA.get(industry),
            'message': f'Switched to {INDUSTRY_DATA[industry]["name"]} demo'
        })
    except Exception as e:
        print(f"❌ Error in set_demo_industry: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# LANGUAGE SWITCHER API ENDPOINT
# ============================================================================

@industry_bp.route('/set-language', methods=['POST'])
@login_required
def set_language():
    """
    Set the user's language preference.
    Called by the JavaScript language switcher in the sidebar.
    """
    try:
        data = request.get_json()
        lang = data.get('language', 'en')

        print(f"🔍 Received language: {lang}")

        if lang not in ['en', 'fa']:
            return jsonify({
                'error': f'Invalid language. Must be one of: en, fa'
            }), 400

        session['lang'] = lang
        session.permanent = True
        session.modified = True

        print(f"✅ Language switched to: {lang}")
        print(f"📋 Session after update: {dict(session)}")

        # Persist to user record if logged in
        if current_user.is_authenticated:
            try:
                from app import db
                from sqlalchemy.sql import text
                with db.engine.connect() as conn:
                    conn.execute(
                        text("UPDATE t_Users SET LanguageCode = :lang WHERE UserID = :uid"),
                        {'lang': lang, 'uid': current_user.id}
                    )
                    conn.commit()
                print(f"✅ User language preference saved to database: {current_user.id} -> {lang}")
            except Exception as e:
                print(f"⚠️ Could not save language preference to database: {e}")

        return jsonify({
            'status': 'ok',
            'language': lang,
            'message': f'Language switched to {lang}'
        })
    except Exception as e:
        print(f"❌ Error in set_language: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# GET CURRENT LANGUAGE API ENDPOINT
# ============================================================================

@industry_bp.route('/get-language', methods=['GET'])
@login_required
def get_language():
    """Get the current user's language preference."""
    lang = session.get('lang', 'en')
    return jsonify({
        'status': 'ok',
        'language': lang,
        'user_language': current_user.language_code if current_user.is_authenticated else None
    })
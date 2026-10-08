"""
AI parser routes — production endpoints.
Replaces ai_test_routes.py.
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required
from app.core.ai_service import parse_crm_text, AIServiceError

ai_bp = Blueprint(
    'ai',
    __name__,
    url_prefix='/api/crm/ai',
)


@ai_bp.route('/health', methods=['GET'])
@login_required
def ai_health():
    """Quick health check — confirms the AI service is configured."""
    import os
    from flask import current_app
    key = os.environ.get('AI_API_KEY') or current_app.config.get('AI_API_KEY', '')
    return jsonify({
        'configured': bool(key),
        'key_preview': (key[:12] + '...' + key[-4:]) if key else None,
        'base_url': os.environ.get('AI_BASE_URL', 'https://openrouter.ai/api/v1'),
    })


@ai_bp.route('/parse', methods=['POST'])
@login_required
def ai_parse():
    """
    POST { text, context? } → parsed JSON.
    Used by the AI Assist modal on the Opportunities page.
    """
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    context = data.get('context', 'opportunity')

    if not text:
        return jsonify({'ok': False, 'error': 'Text is required.'}), 400
    if len(text) > 8000:
        return jsonify({'ok': False, 'error': 'Text too long (max 8000 characters).'}), 400

    try:
        parsed = parse_crm_text(text, context=context)
        return jsonify({'ok': True, 'parsed': parsed})
    except AIServiceError as e:
        return jsonify({'ok': False, 'error': str(e)}), 503
    except Exception as e:
        print(f'[AI] UNEXPECTED: {type(e).__name__}: {e}', flush=True)
        return jsonify({'ok': False, 'error': f'Unexpected error: {e}'}), 500
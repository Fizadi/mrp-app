"""
AI service wrapper — provider-agnostic, with multi-model fallback.

Provider: AvalAI (https://api.avalai.ir/v1)

Primary:   gemini-3.5-flash-lite  (fastest, cheapest)
Fallback1: gemini-3.8-flash       (higher quality, slower)
Fallback2: qwen3.8-flash          (good Persian support)

If a model returns 429 (rate-limited upstream), or an empty response,
or invalid JSON, we try the next one.
If all fail, we raise AIServiceError with a user-friendly message.
"""
import os
import json
import requests
from datetime import datetime
from flask import current_app


class AIServiceError(Exception):
    """Raised when all LLM attempts fail."""
    pass


# ============================================================================
# Model chain — order matters. First try, second try, last resort.
# ============================================================================
MODEL_CHAIN = [
    'gemini-3.5-flash-lite',   # fastest, cheapest
    'gemini-3.8-flash',        # fallback: higher quality
    'qwen3.8-flash',           # fallback: good Persian
]


def _config():
    """
    Read config with sensible defaults.

    PRECEDENCE: current_app.config wins; os.environ is a fallback.
    This makes config.py the single source of truth during development,
    and prevents stale shell env vars from silently overriding it.
    """
    cfg = {
        'provider': current_app.config.get('AI_PROVIDER')
                    or os.environ.get('AI_PROVIDER', 'avalai'),
        'api_key':  current_app.config.get('AI_API_KEY')
                    or os.environ.get('AI_API_KEY', ''),
        'base_url': current_app.config.get('AI_BASE_URL')
                    or os.environ.get('AI_BASE_URL', 'https://api.avalai.ir/v1'),
        'model':    current_app.config.get('AI_MODEL')
                    or os.environ.get('AI_MODEL', 'gemini-3.5-flash-lite'),
        'timeout':  int(current_app.config.get('AI_TIMEOUT')
                        or os.environ.get('AI_TIMEOUT', 60)),
    }
    if not cfg['api_key']:
        raise AIServiceError('AI_API_KEY is not configured on the server.')
    return cfg


# ============================================================================
# Post-processing — fill in safe defaults the model may have omitted
# ============================================================================
def _apply_defaults(parsed, context):
    """
    Mutate `parsed` in-place to add safe defaults:
      - opportunity.stage        → 'Prospecting' if missing
      - opportunity.probability  → 0 if missing
      - activity.activity_date   → today's ISO date if missing (activities need a date)
      - task.due_date            → left as-is (tasks CAN be undated)
    """
    if not isinstance(parsed, dict):
        return parsed
    entities = parsed.get('entities') or {}
    today_iso = datetime.now().strftime('%Y-%m-%d')

    # --- opportunity context ---
    opp = entities.get('opportunity')
    if isinstance(opp, dict):
        if not opp.get('stage'):
            opp['stage'] = 'Prospecting'
        if opp.get('probability') is None:
            opp['probability'] = 0

    # --- activity context ---
    activities = entities.get('activities')
    if isinstance(activities, list):
        for act in activities:
            if isinstance(act, dict) and not act.get('activity_date'):
                act['activity_date'] = today_iso

    # --- single activity (quote context) ---
    activity = entities.get('activity')
    if isinstance(activity, dict) and not activity.get('activity_date'):
        activity['activity_date'] = today_iso

    return parsed


# ============================================================================
# Main entry point
# ============================================================================
def parse_crm_text(user_text, context='opportunity', temperature=0.1):
    """
    Try each model in MODEL_CHAIN until one succeeds.

    Args:
      user_text:  free-form text the user pasted
      context:    'opportunity' | 'customer' | 'activity' | 'task' | 'quote'
      temperature: low = deterministic

    Returns:
      dict — parsed entities with defaults applied.

    Raises:
      AIServiceError — if every model in the chain fails.
    """
    from app.core.ai_prompts import build_prompt, CONTEXT_SCHEMAS

    if context not in CONTEXT_SCHEMAS:
        raise AIServiceError(f'Unknown context: {context}')

    cfg = _config()
    system_prompt, user_prompt = build_prompt(context, user_text)

    headers = {
        'Authorization': f'Bearer {cfg["api_key"]}',
        'Content-Type': 'application/json',
        # AvalAI-specific metadata — optional, informational.
        'HTTP-Referer': 'http://localhost:5001',
        'X-Title': 'Taraz Tamin CRM',
    }

    last_error = None
    for idx, model in enumerate(MODEL_CHAIN, 1):
        print(f'[AI] attempt {idx}/{len(MODEL_CHAIN)} — model={model} '
              f'context={context} text_len={len(user_text)}', flush=True)

        payload = {
            'model': model,
            'temperature': temperature,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            'max_tokens': 1500,
        }

        # Only send response_format for models known to support it.
        # AvalAI proxies Gemini, OpenAI, Anthropic, and Qwen families —
        # these all support JSON mode. Community/experimental models may
        # reject it with HTTP 400, so we gate it here.
        known_json_mode = ('gemini', 'qwen', 'gpt-4', 'gpt-3.5', 'claude')
        if any(s in model.lower() for s in known_json_mode):
            payload['response_format'] = {'type': 'json_object'}

        try:
            resp = requests.post(
                f'{cfg["base_url"]}/chat/completions',
                headers=headers,
                json=payload,
                timeout=cfg['timeout'],
            )
        except requests.exceptions.RequestException as e:
            print(f'[AI] {model} network error: {e}', flush=True)
            last_error = f'Network error: {e}'
            continue

        # --- Handle rate limit: try next model ---
        if resp.status_code == 429:
            print(f'[AI] {model} rate-limited upstream, trying next...', flush=True)
            last_error = f'{model} rate-limited'
            continue

        # --- Handle other HTTP errors ---
        if resp.status_code != 200:
            print(f'[AI] {model} HTTP {resp.status_code}: {resp.text[:300]}', flush=True)
            last_error = f'{model} HTTP {resp.status_code}'
            # 400 usually means the model doesn't support the request — try next
            if resp.status_code in (400, 404):
                continue
            # 401/403 = key problem, don't retry
            if resp.status_code in (401, 403):
                raise AIServiceError(
                    'AI authentication failed. Please check the API key on the server.'
                )
            continue

        # --- Parse successful response ---
        try:
            body = resp.json()
            content = body['choices'][0]['message']['content']
        except (KeyError, IndexError, ValueError) as e:
            print(f'[AI] {model} bad response shape: {e}', flush=True)
            last_error = f'{model} bad response shape'
            continue

        # Guard: some models return None or empty content
        if content is None or not str(content).strip():
            print(f'[AI] {model} returned empty content, trying next...', flush=True)
            last_error = f'{model} returned empty content'
            continue

        # Strip markdown fences if present
        content = str(content).strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[1] if '\n' in content else content
            content = content.rsplit('```', 1)[0]
        content = content.strip()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            print(f'[AI] {model} invalid JSON: {e} — content[:200]={content[:200]}',
                  flush=True)
            last_error = f'{model} returned invalid JSON'
            continue

        print(f'[AI] ✓ {model} succeeded', flush=True)
        return _apply_defaults(parsed, context)

    # Every model failed
    raise AIServiceError(
        f'AI service unavailable after {len(MODEL_CHAIN)} attempts. '
        f'Please try again in a minute. (Last error: {last_error})'
    )
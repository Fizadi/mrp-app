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
# OPPORTUNITY CANDIDATE SEARCH
# ============================================================================
def find_opportunity_candidates(conn, parsed, industry_code, max_results=5):
    """
    Given a parsed AI result, search for matching opportunities in the DB.

    Matching strategy (in priority order, deduplicated):
      1. Customer name (exact or LIKE) — highest weight
      2. Contact person name (exact or LIKE)
      3. Notes keyword match (search_hint)

    Returns list of dicts:
      [{ 'opportunity_id': ..., 'name': ..., 'customer_name': ...,
         'stage': ..., 'status': ..., 'match_reason': ... }, ...]
    """
    from sqlalchemy.sql import text

    ents = (parsed or {}).get('entities', {})
    opp = ents.get('opportunity', {}) or {}
    company = ents.get('company', {}) or {}

    hint = (opp.get('search_hint') or '').strip()
    company_name = (company.get('name') or '').strip()
    contact_person = (opp.get('contact_person') or '').strip()

    # If we have no hints at all, don't search
    if not hint and not company_name and not contact_person:
        return []

    # --- Build query: we UNION three match types and rank by confidence ---
    # Confidence: 3 = company match, 2 = contact match, 1 = notes keyword
    sql = """
    SELECT * FROM (
        SELECT
            o.OpportunityID AS opportunity_id,
            o.OpportunityName AS name,
            o.OpportunityNameFa AS name_fa,
            o.CustomerID,
            c.Name AS customer_name,
            c.NameFa AS customer_name_fa,
            o.Stage,
            o.Status,
            o.ContactPerson,
            o.Notes,
            3 AS confidence,
            N'customer name' AS match_reason
        FROM t_Opportunity o
        LEFT JOIN t_companies c ON c.companyID = o.CustomerID
        WHERE o.DemoIndustryCode = :ind
          AND (o.IsActive IS NULL OR o.IsActive = '1')
          AND (:company_name = N'' OR
               c.Name LIKE N'%' + :company_name + N'%' OR
               c.NameFa LIKE N'%' + :company_name + N'%')

        UNION

        SELECT
            o.OpportunityID, o.OpportunityName, o.OpportunityNameFa,
            o.CustomerID, c.Name, c.NameFa,
            o.Stage, o.Status, o.ContactPerson, o.Notes,
            2 AS confidence,
            N'contact person' AS match_reason
        FROM t_Opportunity o
        LEFT JOIN t_companies c ON c.companyID = o.CustomerID
        WHERE o.DemoIndustryCode = :ind
          AND (o.IsActive IS NULL OR o.IsActive = '1')
          AND (:contact_person = N'' OR o.ContactPerson LIKE N'%' + :contact_person + N'%')

        UNION

        SELECT
            o.OpportunityID, o.OpportunityName, o.OpportunityNameFa,
            o.CustomerID, c.Name, c.NameFa,
            o.Stage, o.Status, o.ContactPerson, o.Notes,
            1 AS confidence,
            N'notes keyword' AS match_reason
        FROM t_Opportunity o
        LEFT JOIN t_companies c ON c.companyID = o.CustomerID
        WHERE o.DemoIndustryCode = :ind
          AND (o.IsActive IS NULL OR o.IsActive = '1')
          AND (:hint = N'' OR o.Notes LIKE N'%' + :hint + N'%'
                        OR o.OpportunityName LIKE N'%' + :hint + N'%')
    ) AS matches
    ORDER BY confidence DESC, name
    """

    try:
        rows = conn.execute(text(sql), {
            'ind': industry_code,
            'company_name': company_name,
            'contact_person': contact_person,
            'hint': hint,
        }).fetchall()
    except Exception as e:
        print(f'[AI] candidate search failed: {e}')
        return []

    # Deduplicate by opportunity_id, keeping highest-confidence match
    seen = {}
    for r in rows:
        m = r._mapping
        oid = m['opportunity_id']
        if oid in seen:
            continue
        seen[oid] = {
            'opportunity_id': str(oid),
            'name': m['name'] or '',
            'name_fa': m['name_fa'] or '',
            'customer_name': m['customer_name'] or '',
            'customer_name_fa': m['customer_name_fa'] or '',
            'stage': m['Stage'] or '',
            'status': m['Status'] or '',
            'contact_person': m['ContactPerson'] or '',
            'match_reason': m['match_reason'],
        }
        if len(seen) >= max_results:
            break

    return list(seen.values())


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
      If context == 'opportunity', also includes 'opportunity_candidates'.

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
        parsed = _apply_defaults(parsed, context)

        # ---- Candidate search (opportunity context only) ----
        if context == 'opportunity':
            try:
                from flask import session
                industry_code = session.get('demo_industry', 'valve')
                engine = current_app.extensions['sqlalchemy'].engine
                with engine.connect() as conn:
                    parsed['opportunity_candidates'] = find_opportunity_candidates(
                        conn, parsed, industry_code
                    )
            except Exception as e:
                print(f'[AI] candidate search error: {e}')
                parsed['opportunity_candidates'] = []
        else:
            parsed['opportunity_candidates'] = []

        return parsed

    # Every model failed
    raise AIServiceError(
        f'AI service unavailable after {len(MODEL_CHAIN)} attempts. '
        f'Please try again in a minute. (Last error: {last_error})'
    )
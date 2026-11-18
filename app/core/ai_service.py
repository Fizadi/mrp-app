"""
AI service wrapper — provider-agnostic, with multi-model fallback.
(Same as v1, plus find_customer_candidates + CANDIDATE_FINDERS registry.)
"""
import os
import json
import requests
from datetime import datetime
from flask import current_app


class AIServiceError(Exception):
    pass


MODEL_CHAIN = [
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
    'qwen3.8-flash',
]


def _config():
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


def _apply_defaults(parsed, context):
    if not isinstance(parsed, dict):
        return parsed
    entities = parsed.get('entities') or {}
    today_iso = datetime.now().strftime('%Y-%m-%d')

    opp = entities.get('opportunity')
    if isinstance(opp, dict):
        if not opp.get('stage'):
            opp['stage'] = 'Prospecting'
        if opp.get('probability') is None:
            opp['probability'] = 0

    activities = entities.get('activities')
    if isinstance(activities, list):
        for act in activities:
            if isinstance(act, dict) and not act.get('activity_date'):
                act['activity_date'] = today_iso

    activity = entities.get('activity')
    if isinstance(activity, dict) and not activity.get('activity_date'):
        activity['activity_date'] = today_iso

    return parsed


# ============================================================================
# OPPORTUNITY CANDIDATES — unchanged from prior version
# ============================================================================
def find_opportunity_candidates(conn, parsed, industry_code, max_results=5):
    from sqlalchemy.sql import text

    ents = (parsed or {}).get('entities', {})
    opp = ents.get('opportunity', {}) or {}
    company = ents.get('company', {}) or {}

    hint = (opp.get('search_hint') or '').strip()
    company_name = (company.get('name') or '').strip()
    contact_person = (opp.get('contact_person') or '').strip()

    if not hint and not company_name and not contact_person:
        return []

    sql = """
    SELECT * FROM (
        SELECT o.OpportunityID AS opportunity_id, o.OpportunityName AS name,
               o.OpportunityNameFa AS name_fa, o.CustomerID,
               c.Name AS customer_name, c.NameFa AS customer_name_fa,
               o.Stage, o.Status, o.ContactPerson, o.Notes,
               3 AS confidence, N'customer name' AS match_reason
        FROM t_Opportunity o
        LEFT JOIN t_companies c ON c.companyID = o.CustomerID
        WHERE o.DemoIndustryCode = :ind
          AND (o.IsActive IS NULL OR o.IsActive = '1')
          AND (:company_name = N'' OR
               c.Name LIKE N'%' + :company_name + N'%' OR
               c.NameFa LIKE N'%' + :company_name + N'%')
        UNION
        SELECT o.OpportunityID, o.OpportunityName, o.OpportunityNameFa,
               o.CustomerID, c.Name, c.NameFa, o.Stage, o.Status,
               o.ContactPerson, o.Notes, 2 AS confidence, N'contact person'
        FROM t_Opportunity o
        LEFT JOIN t_companies c ON c.companyID = o.CustomerID
        WHERE o.DemoIndustryCode = :ind
          AND (o.IsActive IS NULL OR o.IsActive = '1')
          AND (:contact_person = N'' OR o.ContactPerson LIKE N'%' + :contact_person + N'%')
        UNION
        SELECT o.OpportunityID, o.OpportunityName, o.OpportunityNameFa,
               o.CustomerID, c.Name, c.NameFa, o.Stage, o.Status,
               o.ContactPerson, o.Notes, 1 AS confidence, N'notes keyword'
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
            'ind': industry_code, 'company_name': company_name,
            'contact_person': contact_person, 'hint': hint,
        }).fetchall()
    except Exception as e:
        print(f'[AI] opportunity candidate search failed: {e}')
        return []

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
# CUSTOMER CANDIDATES — new
# Matching tiers:
#   Tier 3 (phone):  number appears in t_companies.Telephone or t_CompanyPhone
#   Tier 2 (name):   LIKE on Name / NameFa
#   Tier 1 (address): LIKE on Address / AddressFa
# ============================================================================
def find_customer_candidates(conn, parsed, industry_code, max_results=5):
    from sqlalchemy.sql import text

    ents = (parsed or {}).get('entities', {})
    company = ents.get('company', {}) or {}

    name    = (company.get('name') or '').strip()
    phones  = company.get('phones') or []
    address = (company.get('address') or '').strip()
    hint    = (company.get('search_hint') or '').strip()

    # First phone number only — the primary is usually first
    phone = ''
    for p in phones:
        v = (p.get('value') or '').strip()
        if v:
            phone = v
            break
    if not phone and company.get('telephone'):
        phone = str(company.get('telephone')).strip()

    if not name and not phone and not address and not hint:
        return []

    sql = """
    SELECT * FROM (
        -- Tier 3a: t_companies.Telephone
        SELECT c.companyID AS customer_id,
               c.Name AS name, c.NameFa AS name_fa,
               c.Telephone AS telephone,
               c.Address AS address, c.AddressFa AS address_fa,
               c.IsCustomer AS is_customer, c.IsSupplier AS is_supplier,
               3 AS confidence, N'phone' AS match_reason
        FROM t_companies c
        WHERE (c.DemoIndustryCode = :ind OR c.DemoIndustryCode IS NULL)
          AND c.IsCustomer = '1'
          AND (:phone = N'' OR c.Telephone LIKE N'%' + :phone + N'%')

        UNION

        -- Tier 3b: t_CompanyPhone
        SELECT c.companyID, c.Name, c.NameFa, c.Telephone,
               c.Address, c.AddressFa, c.IsCustomer, c.IsSupplier,
               3 AS confidence, N'phone (directory)'
        FROM t_companies c
        JOIN t_CompanyPhone p ON p.CompanyID = c.companyID
        WHERE (c.DemoIndustryCode = :ind OR c.DemoIndustryCode IS NULL)
          AND c.IsCustomer = '1'
          AND (p.IsActive IS NULL OR p.IsActive = '1')
          AND (:phone = N'' OR p.PhoneNumber LIKE N'%' + :phone + N'%')

        UNION

        -- Tier 2: name
        SELECT c.companyID, c.Name, c.NameFa, c.Telephone,
               c.Address, c.AddressFa, c.IsCustomer, c.IsSupplier,
               2 AS confidence, N'name'
        FROM t_companies c
        WHERE (c.DemoIndustryCode = :ind OR c.DemoIndustryCode IS NULL)
          AND c.IsCustomer = '1'
          AND (:name = N'' OR c.Name LIKE N'%' + :name + N'%'
                          OR c.NameFa LIKE N'%' + :name + N'%')

        UNION

        -- Tier 1: address or search hint
        SELECT c.companyID, c.Name, c.NameFa, c.Telephone,
               c.Address, c.AddressFa, c.IsCustomer, c.IsSupplier,
               1 AS confidence, N'address/hint'
        FROM t_companies c
        WHERE (c.DemoIndustryCode = :ind OR c.DemoIndustryCode IS NULL)
          AND c.IsCustomer = '1'
          AND ((:address <> N'' AND (c.Address LIKE N'%' + :address + N'%'
                                  OR c.AddressFa LIKE N'%' + :address + N'%'))
            OR (:hint <> N'' AND (c.Name LIKE N'%' + :hint + N'%'
                                OR c.Address LIKE N'%' + :hint + N'%')))
    ) AS matches
    ORDER BY confidence DESC, name
    """
    try:
        rows = conn.execute(text(sql), {
            'ind': industry_code, 'name': name, 'phone': phone,
            'address': address, 'hint': hint,
        }).fetchall()
    except Exception as e:
        print(f'[AI] customer candidate search failed: {e}')
        return []

    seen = {}
    for r in rows:
        m = r._mapping
        cid = m['customer_id']
        if cid in seen:
            continue
        seen[cid] = {
            'customer_id': str(cid),
            'name': m['name'] or '',
            'name_fa': m['name_fa'] or '',
            'telephone': m['telephone'] or '',
            'address': m['address'] or '',
            'address_fa': m['address_fa'] or '',
            'is_customer': m['is_customer'] or '',
            'is_supplier': m['is_supplier'] or '',
            'match_reason': m['match_reason'],
        }
        if len(seen) >= max_results:
            break
    return list(seen.values())


# ============================================================================
# CANDIDATE FINDER REGISTRY — extend here when adding AI to new modules
# ============================================================================
CANDIDATE_FINDERS = {
    'opportunity': find_opportunity_candidates,
    'customer':    find_customer_candidates,
    # 'quote':     find_quote_candidates,
    # 'task':      find_task_candidates,
}


# ============================================================================
# Main entry point
# ============================================================================
def parse_crm_text(user_text, context='opportunity', temperature=0.1):
    from app.core.ai_prompts import build_prompt, CONTEXT_SCHEMAS

    if context not in CONTEXT_SCHEMAS:
        raise AIServiceError(f'Unknown context: {context}')

    cfg = _config()
    system_prompt, user_prompt = build_prompt(context, user_text)

    headers = {
        'Authorization': f'Bearer {cfg["api_key"]}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'http://localhost:5001',
        'X-Title': 'Taraz Tamin CRM',
    }

    last_error = None
    for idx, model in enumerate(MODEL_CHAIN, 1):
        print(f'[AI] attempt {idx}/{len(MODEL_CHAIN)} — model={model} '
              f'context={context} text_len={len(user_text)}', flush=True)

        payload = {
            'model': model, 'temperature': temperature,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            'max_tokens': 1500,
        }
        known_json_mode = ('gemini', 'qwen', 'gpt-4', 'gpt-3.5', 'claude')
        if any(s in model.lower() for s in known_json_mode):
            payload['response_format'] = {'type': 'json_object'}

        try:
            resp = requests.post(
                f'{cfg["base_url"]}/chat/completions',
                headers=headers, json=payload, timeout=cfg['timeout'],
            )
        except requests.exceptions.RequestException as e:
            print(f'[AI] {model} network error: {e}', flush=True)
            last_error = f'Network error: {e}'
            continue

        if resp.status_code == 429:
            print(f'[AI] {model} rate-limited, next...', flush=True)
            last_error = f'{model} rate-limited'
            continue

        if resp.status_code != 200:
            print(f'[AI] {model} HTTP {resp.status_code}: {resp.text[:300]}',
                  flush=True)
            last_error = f'{model} HTTP {resp.status_code}'
            if resp.status_code in (400, 404):
                continue
            if resp.status_code in (401, 403):
                raise AIServiceError(
                    'AI authentication failed. Check the API key on the server.')
            continue

        try:
            body = resp.json()
            content = body['choices'][0]['message']['content']
        except (KeyError, IndexError, ValueError) as e:
            print(f'[AI] {model} bad response shape: {e}', flush=True)
            last_error = f'{model} bad response shape'
            continue

        if content is None or not str(content).strip():
            print(f'[AI] {model} empty content, next...', flush=True)
            last_error = f'{model} returned empty content'
            continue

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

        # ---- Candidate search via registry ----
        finder = CANDIDATE_FINDERS.get(context)
        if finder is not None:
            try:
                from flask import session
                industry_code = session.get('demo_industry', 'valve')
                engine = current_app.extensions['sqlalchemy'].engine
                with engine.connect() as conn:
                    parsed[f'{context}_candidates'] = finder(conn, parsed, industry_code)
            except Exception as e:
                print(f'[AI] candidate search error: {e}')
                parsed[f'{context}_candidates'] = []
        else:
            parsed[f'{context}_candidates'] = []

        return parsed

    raise AIServiceError(
        f'AI service unavailable after {len(MODEL_CHAIN)} attempts. '
        f'Please try again in a minute. (Last error: {last_error})'
    )
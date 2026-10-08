from . import crm_api_bp
from flask import request, jsonify, current_app, session
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from datetime import datetime
import csv
from io import StringIO
from flask import Response
from app.core.date_helpers import format_date

# ============================================================================
# BILINGUAL HELPER — v47.1
# ============================================================================
# Companion-column suffix by language code. English is the base column (no suffix).
# Add new languages here — no other code changes are needed.
LANG_SUFFIX = {
    'fa': 'Fa',
    # 'it': 'It',
    # 'ar': 'Ar',
    # 'de': 'De',
}


def _lang():
    """Current UI language, from session."""
    return session.get('lang', 'en')


def pick_localized(row, base_field):
    """
    Return the value of base_field based on the current session language.

    - If lang == 'en', or no localized column exists for this language,
      return the base column value.
    - Otherwise, try f'{base_field}{Suffix}' (e.g. OpportunityNameFa).
    - Fall back to the English base column if the localized value is
      NULL or whitespace-only. Never returns a blank when English exists.

    `row` must be dict-like — pass `row._mapping` for SQLAlchemy rows.
    """
    lang = _lang()
    if lang == 'en':
        return row.get(base_field)

    suffix = LANG_SUFFIX.get(lang)
    if not suffix:
        return row.get(base_field)

    loc = row.get(f'{base_field}{suffix}')
    if loc is not None and str(loc).strip():
        return loc

    return row.get(base_field)


# ============================================================================
# ENGINE / UTILITIES
# ============================================================================
def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def date_to_key(date_str):
    if not date_str:
        return None
    return int(str(date_str).replace('-', '').replace('/', ''))


# --- INDUSTRY FILTER: master toggle + helper ---
ALLOWED_INDUSTRIES_ENABLED = True


def current_industry():
    return session.get('demo_industry', 'valve')


def is_admin_user():
    try:
        roles = getattr(current_user, 'user_roles', None)
        if roles is None:
            return False
        if isinstance(roles, str):
            import json as _json
            roles = _json.loads(roles) if roles else []
        return 'ADMIN' in roles
    except Exception:
        return False


def users_for_current_industry(conn, industry):
    if is_admin_user() or not ALLOWED_INDUSTRIES_ENABLED:
        return conn.execute(text("""
            SELECT UserID, FullName, Username
            FROM t_Users
            WHERE UserType = 'Internal' AND PortalAccess = 1
            ORDER BY FullName
        """)).fetchall()

    try:
        return conn.execute(text("""
            SELECT UserID, FullName, Username
            FROM t_Users
            WHERE UserType = 'Internal' AND PortalAccess = 1
              AND (
                    AllowedIndustries IS NULL
                 OR AllowedIndustries = 'ALL'
                 OR AllowedIndustries = :exact
                 OR AllowedIndustries LIKE :pre
                 OR AllowedIndustries LIKE :mid
                 OR AllowedIndustries LIKE :suf
              )
            ORDER BY FullName
        """), {
            'exact': industry,
            'pre':   f'{industry},%',
            'mid':   f'%,{industry},%',
            'suf':   f'%,{industry}',
        }).fetchall()
    except Exception as e:
        print(f"⚠️ AllowedIndustries filter failed ({e}); returning all users.")
        return conn.execute(text("""
            SELECT UserID, FullName, Username
            FROM t_Users
            WHERE UserType = 'Internal' AND PortalAccess = 1
            ORDER BY FullName
        """)).fetchall()


# ============================================================================
# OPPORTUNITIES — LIST / STATS
# ============================================================================

@crm_api_bp.route('/opportunities/stats')
@login_required
def get_opportunity_stats():
    industry = current_industry()
    project_id = request.args.get('project_id')
    if not project_id:
        current_project = session.get('current_project_id', 'All')
        if current_project != 'All':
            project_id = current_project

    status_filter = request.args.get('status', 'Open')
    stage_filter = request.args.get('stage', '')
    assigned_filter = request.args.get('assigned_to', '')

    engine = get_engine()
    with engine.connect() as conn:
        conditions = [
            "(DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)",
            "(IsActive IS NULL OR IsActive = '1')",
        ]
        params = {'industry': industry}

        if project_id and project_id != 'All':
            conditions.append("(ProjectID = :proj OR ProjectID IS NULL)")
            params['proj'] = project_id

        if status_filter and status_filter != 'All':
            conditions.append("Status = :status")
            params['status'] = status_filter

        if stage_filter:
            conditions.append("Stage = :stage")
            params['stage'] = stage_filter

        if assigned_filter:
            conditions.append("AssignedToUserID = :assigned")
            params['assigned'] = assigned_filter

        where_clause = " AND ".join(conditions)

        rows = conn.execute(text(f"""
            SELECT
                COALESCE(SUM(CAST(Value AS DECIMAL(18,2))), 0) as total_value,
                COALESCE(SUM(CAST(Value AS DECIMAL(18,2)) * CAST(Probability AS INT) / 100), 0) as weighted_value,
                COUNT(*) as total_count
            FROM t_Opportunity
            WHERE {where_clause}
        """), params).fetchone()

        total_value = rows[0] or 0
        weighted_value = rows[1] or 0
        total_count = rows[2] or 0

        won_rows = conn.execute(text("""
            SELECT COUNT(*) as won_count
            FROM t_Opportunity
            WHERE Status = 'Closed Won'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'industry': industry}).fetchone()

        lost_rows = conn.execute(text("""
            SELECT COUNT(*) as lost_count
            FROM t_Opportunity
            WHERE Status = 'Closed Lost'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'industry': industry}).fetchone()

        won_count = won_rows[0] or 0
        lost_count = lost_rows[0] or 0
        total_closed = won_count + lost_count
        win_rate = (won_count / total_closed * 100) if total_closed > 0 else 0

        return jsonify({
            'total_value': round(total_value, 2),
            'weighted_value': round(weighted_value, 2),
            'win_rate': round(win_rate, 1),
            'opportunity_count': total_count,
            'won_count': won_count,
            'lost_count': lost_count
        })


@crm_api_bp.route('/opportunities')
@login_required
def list_opportunities():
    industry = current_industry()
    project_id = request.args.get('project_id')
    if not project_id:
        current_project = session.get('current_project_id', 'All')
        if current_project != 'All':
            project_id = current_project

    stage = request.args.get('stage', '')
    assigned_to = request.args.get('assigned_to', '')
    status = request.args.get('status', 'Open')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    probability_min = request.args.get('probability_min', '')
    probability_max = request.args.get('probability_max', '')
    search = request.args.get('search', '')

    engine = get_engine()
    with engine.connect() as conn:
        conditions = [
            "(o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)",
            "(o.IsActive IS NULL OR o.IsActive = '1')",
        ]
        params = {'industry': industry}

        if project_id and project_id != 'All':
            conditions.append("(o.ProjectID = :proj OR o.ProjectID IS NULL)")
            params['proj'] = project_id

        if stage:
            conditions.append("o.Stage = :stage")
            params['stage'] = stage

        if assigned_to:
            conditions.append("o.AssignedToUserID = :assigned")
            params['assigned'] = assigned_to

        if status and status != 'All':
            conditions.append("o.Status = :status")
            params['status'] = status

        if date_from:
            conditions.append("o.ExpectedCloseDateKey >= :date_from")
            params['date_from'] = date_to_key(date_from)

        if date_to:
            conditions.append("o.ExpectedCloseDateKey <= :date_to")
            params['date_to'] = date_to_key(date_to)

        if probability_min:
            conditions.append("CAST(o.Probability AS INT) >= :prob_min")
            params['prob_min'] = int(probability_min)

        if probability_max:
            conditions.append("CAST(o.Probability AS INT) <= :prob_max")
            params['prob_max'] = int(probability_max)

        if search:
            # Search across both base and localized fields.
            conditions.append("""(
                o.OpportunityName LIKE :search
                OR o.OpportunityNameFa LIKE :search
                OR o.ContactPerson LIKE :search
                OR o.ContactPersonFa LIKE :search
                OR c.Name LIKE :search
                OR c.NameFa LIKE :search
            )""")
            params['search'] = f"%{search}%"

        where_clause = " AND ".join(conditions)

        rows = conn.execute(text(f"""
            SELECT o.OpportunityID,
                   o.OpportunityName,       o.OpportunityNameFa,
                   o.Value, o.Probability,
                   o.Stage,
                   o.ExpectedCloseDateKey,
                   o.Status,
                   o.WinLossReason,         o.WinLossReasonFa,
                   o.ContactPerson,         o.ContactPersonFa,
                   o.ContactEmail, o.ContactPhone,
                   o.Notes,                 o.NotesFa,
                   c.companyID AS CustomerID,
                   c.Name      AS CustomerName,
                   c.NameFa    AS CustomerNameFa,
                   u.UserID    AS AssignedUserID,
                   u.FullName  AS AssignedUserName,
                   o.CreatedDateKey,
                   o.ModifiedDateKey,
                   acts.LastActivityKey,
                   acts.NextFollowupKey
            FROM t_Opportunity o
            LEFT JOIN t_companies c ON o.CustomerID = c.companyID
            LEFT JOIN t_Users u ON o.AssignedToUserID = u.UserID
            LEFT JOIN (
                SELECT
                    OpportunityID,
                    MAX(ActivityDateKey) AS LastActivityKey,
                    MIN(CASE WHEN ReminderDateKey > 0 THEN ReminderDateKey END) AS NextFollowupKey
                FROM t_OpportunityActivity
                GROUP BY OpportunityID
            ) acts ON acts.OpportunityID = o.OpportunityID
            WHERE {where_clause}
            ORDER BY o.ExpectedCloseDateKey ASC, CAST(o.Value AS DECIMAL(18,2)) DESC
        """), params).fetchall()

        opportunities = []
        today_key_int = int(datetime.now().strftime('%Y%m%d'))

        for row in rows:
            r = row._mapping

            def _to_int(v):
                if v is None or v == '':
                    return None
                try:
                    return int(v)
                except (TypeError, ValueError):
                    try:
                        return int(float(v))
                    except (TypeError, ValueError):
                        return None

            def _safe_format(v):
                k = _to_int(v)
                if not k:
                    return ''
                try:
                    return format_date(k)
                except Exception as e:
                    print(f"⚠️ format_date failed for {k}: {e}")
                    return ''

            def _to_float(v):
                if v is None or v == '':
                    return 0.0
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0

            expected_close_date_key = _to_int(r.get('ExpectedCloseDateKey'))
            created_date_key        = _to_int(r.get('CreatedDateKey'))
            modified_date_key       = _to_int(r.get('ModifiedDateKey'))
            last_activity_key       = _to_int(r.get('LastActivityKey'))
            next_followup_key       = _to_int(r.get('NextFollowupKey'))

            value = _to_float(r.get('Value'))
            probability = _to_int(r.get('Probability')) or 0

            opportunities.append({
                'opportunity_id': r.get('OpportunityID'),
                # ---- bilingual text via pick_localized ----
                'name':           pick_localized(r, 'OpportunityName'),
                'win_loss_reason': pick_localized(r, 'WinLossReason'),
                'contact_person': pick_localized(r, 'ContactPerson'),
                'notes':          pick_localized(r, 'Notes'),
                'customer_name':  pick_localized(r, 'CustomerName'),
                # ---- numbers ----
                'value':          value,
                'probability':    probability,
                # ---- enum (raw — UI translates) ----
                'stage':          r.get('Stage'),
                'status':         r.get('Status'),
                # ---- non-translatable / IDs ----
                'contact_email':  r.get('ContactEmail'),
                'contact_phone':  r.get('ContactPhone'),
                'customer_id':    r.get('CustomerID'),
                'assigned_user_id':   r.get('AssignedUserID'),
                'assigned_user_name': r.get('AssignedUserName'),
                # ---- dates: key + formatted twin ----
                'expected_close_date_key':       expected_close_date_key,
                'expected_close_date_formatted': _safe_format(expected_close_date_key),
                'created_date_key':       created_date_key,
                'created_date_formatted': _safe_format(created_date_key),
                'modified_date_key':       modified_date_key,
                'modified_date_formatted': _safe_format(modified_date_key),
                'last_activity_key':       last_activity_key,
                'last_activity_formatted': _safe_format(last_activity_key),
                'next_followup_key':       next_followup_key,
                'next_followup_formatted': _safe_format(next_followup_key),
                'is_followup_overdue':     bool(next_followup_key and next_followup_key < today_key_int),
            })

        return jsonify(opportunities)


# ============================================================================
# OPPORTUNITIES — GET ONE
# ============================================================================

@crm_api_bp.route('/opportunities/<int:opportunity_id>', methods=['GET'])
@login_required
def get_opportunity(opportunity_id):
    """Get a single opportunity, industry-scoped, bilingual."""
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                o.OpportunityID,
                o.OpportunityName,   o.OpportunityNameFa,
                o.Value, o.Probability,
                o.Stage,
                o.ExpectedCloseDateKey,
                o.Status,
                o.WinLossReason,     o.WinLossReasonFa,
                o.ContactPerson,     o.ContactPersonFa,
                o.ContactEmail, o.ContactPhone,
                o.Notes,             o.NotesFa,
                o.CustomerID, o.ProjectID, o.AssignedToUserID,
                o.CreatedDateKey, o.ModifiedDateKey
            FROM t_Opportunity o
            WHERE o.OpportunityID = :id
              AND (o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)
              AND (o.IsActive IS NULL OR o.IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).fetchone()

        if not row:
            return jsonify({'error': 'Opportunity not found'}), 404

        r = row._mapping

        def to_int(v):
            if v is None or v == '': return None
            try: return int(v)
            except (TypeError, ValueError):
                try: return int(float(v))
                except (TypeError, ValueError): return None

        def safe_format(v):
            k = to_int(v)
            if not k: return ''
            try: return format_date(k)
            except Exception: return ''

        def to_float(v):
            if v is None or v == '': return 0.0
            try: return float(v)
            except (TypeError, ValueError): return 0.0

        return jsonify({
            'opportunity_id': r['OpportunityID'],
            'name':           pick_localized(r, 'OpportunityName'),
            'value':          to_float(r['Value']),
            'probability':    to_int(r['Probability']) or 0,
            'stage':          r['Stage'],
            'expected_close_date_key':       to_int(r['ExpectedCloseDateKey']),
            'expected_close_date_formatted': safe_format(r['ExpectedCloseDateKey']),
            'status':         r['Status'],
            'win_loss_reason': pick_localized(r, 'WinLossReason'),
            'contact_person': pick_localized(r, 'ContactPerson'),
            'contact_email':  r['ContactEmail'],
            'contact_phone':  r['ContactPhone'],
            'notes':          pick_localized(r, 'Notes'),
            'customer_id':    r['CustomerID'],
            'project_id':     r['ProjectID'],
            'assigned_user_id': r['AssignedToUserID'],
        })


# ============================================================================
# OPPORTUNITIES — CREATE / UPDATE / DELETE / DUPLICATE
# ============================================================================
# IMPORTANT: The create/update endpoints accept BOTH base and _Fa values.
# The form must submit both (empty string allowed). We write both, and if
# the _Fa value is blank we fall back to writing the same text as English
# (so a Persian-only user isn't left with empty rows).

def _write_pair(data, base_field):
    """Return (base_value, fa_value) with FA falling back to base if blank."""
    base = data.get(base_field)
    fa   = data.get(f'{base_field}Fa') or base
    return base, fa


@crm_api_bp.route('/opportunities', methods=['POST'])
@login_required
def create_opportunity():
    industry = current_industry()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    name, name_fa = _write_pair(data, 'name')
    notes, notes_fa = _write_pair(data, 'notes')
    wlr, wlr_fa = _write_pair(data, 'win_loss_reason')
    cp, cp_fa = _write_pair(data, 'contact_person')

    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            INSERT INTO t_Opportunity (
                OpportunityName, OpportunityNameFa,
                CustomerID, ProjectID, Value, Probability,
                Status, Stage, ExpectedCloseDateKey,
                WinLossReason, WinLossReasonFa,
                ContactPerson, ContactPersonFa,
                ContactEmail, ContactPhone, AssignedToUserID,
                Notes, NotesFa,
                CreatedBy, CreatedDateKey, ModifiedBy, ModifiedDateKey,
                DemoIndustryCode
            ) VALUES (
                :name, :name_fa,
                :customer_id, :project_id, :value, :probability,
                'Open', :stage, :close_date,
                :win_loss_reason, :win_loss_reason_fa,
                :contact_person, :contact_person_fa,
                :contact_email, :contact_phone, :assigned_to,
                :notes, :notes_fa,
                :user_id, :created_date, :user_id, :created_date,
                :industry
            )
        """), {
            'name': name,
            'name_fa': name_fa,
            'customer_id': data.get('customer_id'),
            'project_id': data.get('project_id'),
            'value': data.get('value', 0),
            'probability': data.get('probability', 0),
            'stage': data.get('stage', 'Prospecting'),
            'close_date': date_to_key(data.get('expected_close_date')),
            'win_loss_reason': wlr,
            'win_loss_reason_fa': wlr_fa,
            'contact_person': cp,
            'contact_person_fa': cp_fa,
            'contact_email': data.get('contact_email'),
            'contact_phone': data.get('contact_phone'),
            'assigned_to': data.get('assigned_to_user_id'),
            'notes': notes,
            'notes_fa': notes_fa,
            'user_id': current_user.get_id(),
            'created_date': today_key,
            'industry': industry,
        })
        conn.commit()
        opp_id = result.lastrowid

        return jsonify({'message': 'Opportunity created', 'opportunity_id': opp_id}), 201


@crm_api_bp.route('/opportunities/<int:opportunity_id>', methods=['PUT'])
@login_required
def update_opportunity(opportunity_id):
    industry = current_industry()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    name, name_fa = _write_pair(data, 'name')
    notes, notes_fa = _write_pair(data, 'notes')
    wlr, wlr_fa = _write_pair(data, 'win_loss_reason')
    cp, cp_fa = _write_pair(data, 'contact_person')

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not exists:
            return jsonify({'error': 'Opportunity not found'}), 404

        status = 'Open'
        if data.get('stage') == 'Closed Won':
            status = 'Closed Won'
        elif data.get('stage') == 'Closed Lost':
            status = 'Closed Lost'

        conn.execute(text("""
            UPDATE t_Opportunity SET
                OpportunityName = :name,
                OpportunityNameFa = :name_fa,
                CustomerID = :customer_id,
                ProjectID = :project_id,
                Value = :value,
                Probability = :probability,
                Stage = :stage,
                ExpectedCloseDateKey = :close_date,
                WinLossReason = :win_loss_reason,
                WinLossReasonFa = :win_loss_reason_fa,
                ContactPerson = :contact_person,
                ContactPersonFa = :contact_person_fa,
                ContactEmail = :contact_email,
                ContactPhone = :contact_phone,
                AssignedToUserID = :assigned_to,
                Notes = :notes,
                NotesFa = :notes_fa,
                Status = :status,
                ModifiedDateKey = :modified_date,
                ModifiedBy = :user_id
            WHERE OpportunityID = :id
        """), {
            'id': opportunity_id,
            'name': name,
            'name_fa': name_fa,
            'customer_id': data.get('customer_id'),
            'project_id': data.get('project_id'),
            'value': data.get('value', 0),
            'probability': data.get('probability', 0),
            'stage': data.get('stage'),
            'close_date': date_to_key(data.get('expected_close_date')),
            'win_loss_reason': wlr,
            'win_loss_reason_fa': wlr_fa,
            'contact_person': cp,
            'contact_person_fa': cp_fa,
            'contact_email': data.get('contact_email'),
            'contact_phone': data.get('contact_phone'),
            'assigned_to': data.get('assigned_to_user_id'),
            'notes': notes,
            'notes_fa': notes_fa,
            'status': status,
            'modified_date': today_key,
            'user_id': current_user.get_id()
        })
        conn.commit()
        return jsonify({'message': 'Opportunity updated'})


@crm_api_bp.route('/opportunities/<int:opportunity_id>', methods=['DELETE'])
@login_required
def delete_opportunity(opportunity_id):
    industry = current_industry()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()

        if not exists:
            return jsonify({'error': 'Opportunity not found'}), 404

        try:
            conn.execute(text("""
                UPDATE t_Opportunity
                SET IsActive = '0',
                    ModifiedDateKey = :date,
                    ModifiedBy = :user_id
                WHERE OpportunityID = :id
            """), {'id': opportunity_id, 'date': today_key, 'user_id': current_user.get_id()})
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({'error': f'Delete failed: {str(e)}'}), 500

    return jsonify({'message': 'Opportunity deleted'})


@crm_api_bp.route('/opportunities/<int:opportunity_id>/duplicate', methods=['POST'])
@login_required
def duplicate_opportunity(opportunity_id):
    industry = current_industry()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    engine = get_engine()
    with engine.connect() as conn:
        src = conn.execute(text("""
            SELECT OpportunityName, OpportunityNameFa,
                   CustomerID, ProjectID, Value, Probability,
                   Stage, ExpectedCloseDateKey,
                   WinLossReason, WinLossReasonFa,
                   ContactPerson, ContactPersonFa,
                   ContactEmail, ContactPhone, AssignedToUserID,
                   Notes, NotesFa
            FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).fetchone()

        if not src:
            return jsonify({'error': 'Opportunity not found'}), 404

        m = src._mapping
        new_name    = 'Copy of ' + (m.get('OpportunityName') or '')
        new_name_fa = 'کپی از ' + (m.get('OpportunityNameFa') or m.get('OpportunityName') or '')

        result = conn.execute(text("""
            INSERT INTO t_Opportunity (
                OpportunityName, OpportunityNameFa,
                CustomerID, ProjectID, Value, Probability,
                Status, Stage, ExpectedCloseDateKey,
                WinLossReason, WinLossReasonFa,
                ContactPerson, ContactPersonFa,
                ContactEmail, ContactPhone, AssignedToUserID,
                Notes, NotesFa,
                CreatedBy, CreatedDateKey, ModifiedBy, ModifiedDateKey,
                DemoIndustryCode
            ) VALUES (
                :name, :name_fa,
                :customer_id, :project_id, :value, :probability,
                'Open', :stage, :close_date,
                :win_loss_reason, :win_loss_reason_fa,
                :contact_person, :contact_person_fa,
                :contact_email, :contact_phone, :assigned_to,
                :notes, :notes_fa,
                :user_id, :created_date, :user_id, :created_date,
                :industry
            )
        """), {
            'name': new_name,
            'name_fa': new_name_fa,
            'customer_id': m.get('CustomerID'),
            'project_id': m.get('ProjectID'),
            'value': m.get('Value'),
            'probability': m.get('Probability'),
            'stage': m.get('Stage'),
            'close_date': m.get('ExpectedCloseDateKey'),
            'win_loss_reason': m.get('WinLossReason'),
            'win_loss_reason_fa': m.get('WinLossReasonFa'),
            'contact_person': m.get('ContactPerson'),
            'contact_person_fa': m.get('ContactPersonFa'),
            'contact_email': m.get('ContactEmail'),
            'contact_phone': m.get('ContactPhone'),
            'assigned_to': m.get('AssignedToUserID'),
            'notes': m.get('Notes'),
            'notes_fa': m.get('NotesFa'),
            'user_id': current_user.get_id(),
            'created_date': today_key,
            'industry': industry,
        })
        conn.commit()
        new_id = result.lastrowid

    return jsonify({'message': 'Opportunity duplicated', 'opportunity_id': new_id}), 201


@crm_api_bp.route('/opportunities/<int:opportunity_id>/inline', methods=['PATCH'])
@login_required
def inline_update_opportunity(opportunity_id):
    industry = current_industry()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    allowed_fields = ['probability', 'stage', 'value']
    updates = []
    params = {'id': opportunity_id, 'modified_date': today_key, 'user_id': current_user.get_id()}

    for field in allowed_fields:
        if field in data:
            if field == 'probability':
                updates.append("Probability = :probability")
                params['probability'] = data[field]
            elif field == 'stage':
                updates.append("Stage = :stage")
                params['stage'] = data[field]
                if data[field] == 'Closed Won':
                    updates.append("Status = 'Closed Won'")
                elif data[field] == 'Closed Lost':
                    updates.append("Status = 'Closed Lost'")
                else:
                    updates.append("Status = 'Open'")
            elif field == 'value':
                updates.append("Value = :value")
                params['value'] = data[field]

    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400

    updates.append("ModifiedDateKey = :modified_date")
    updates.append("ModifiedBy = :user_id")

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not exists:
            return jsonify({'error': 'Opportunity not found'}), 404

        conn.execute(text(f"""
            UPDATE t_Opportunity
            SET {', '.join(updates)}
            WHERE OpportunityID = :id
        """), params)
        conn.commit()

    return jsonify({'message': 'Updated successfully'})


# ============================================================================
# ACTIVITY LOG — bilingual
# ============================================================================

@crm_api_bp.route('/opportunities/<int:opportunity_id>/activity', methods=['POST'])
@login_required
def log_activity(opportunity_id):
    industry = current_industry()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    subj, subj_fa = _write_pair(data, 'subject')
    notes, notes_fa = _write_pair(data, 'notes')

    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        conn.execute(text("""
            INSERT INTO t_OpportunityActivity (
                OpportunityID, ActivityType,
                Subject, SubjectFa,
                Notes, NotesFa,
                ActivityDateKey, ReminderDateKey, CreatedBy
            ) VALUES (
                :opp_id, :type,
                :subject, :subject_fa,
                :notes, :notes_fa,
                :activity_date, :reminder_date, :user_id
            )
        """), {
            'opp_id': opportunity_id,
            'type': data.get('activity_type'),
            'subject': subj,
            'subject_fa': subj_fa,
            'notes': notes,
            'notes_fa': notes_fa,
            'activity_date': date_to_key(data.get('activity_date')),
            'reminder_date': date_to_key(data.get('reminder_date')),
            'user_id': current_user.get_id()
        })
        conn.commit()
        return jsonify({'message': 'Activity logged'}), 201


@crm_api_bp.route('/opportunities/<int:opportunity_id>/activities')
@login_required
def get_activities(opportunity_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        rows = conn.execute(text("""
            SELECT ActivityID, ActivityType,
                   Subject, SubjectFa,
                   Notes, NotesFa,
                   ActivityDateKey, ReminderDateKey
            FROM t_OpportunityActivity
            WHERE OpportunityID = :opp_id
            ORDER BY ActivityDateKey DESC
        """), {'opp_id': opportunity_id}).fetchall()

        activities = []
        for row in rows:
            m = row._mapping
            adk = m['ActivityDateKey']
            rdk = m['ReminderDateKey']
            activities.append({
                'activity_id': m['ActivityID'],
                'activity_type': m['ActivityType'],
                'subject': pick_localized(m, 'Subject'),
                'notes':   pick_localized(m, 'Notes'),
                'activity_date_key': adk,
                'activity_date_formatted': format_date(int(adk)) if adk else '',
                'reminder_date_key': rdk,
                'reminder_date_formatted': format_date(int(rdk)) if rdk else '',
            })

        return jsonify(activities)


# ============================================================================
# TASKS — bilingual
# ============================================================================

@crm_api_bp.route('/opportunities/<int:opportunity_id>/tasks', methods=['GET'])
@login_required
def get_opportunity_tasks(opportunity_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        rows = conn.execute(text("""
            SELECT TaskID, OpportunityID, ContactID,
                   Subject, SubjectFa,
                   Notes, NotesFa,
                   DueDateKey, Priority, Status, AssignedToUserID,
                   CompletedDateKey, CreatedDateKey, CreatedBy
            FROM t_OpportunityTask
            WHERE OpportunityID = :opp
            ORDER BY
                CASE Status
                    WHEN 'Open' THEN 1
                    WHEN 'InProgress' THEN 2
                    WHEN 'Done' THEN 3
                    ELSE 4
                END,
                DueDateKey ASC
        """), {'opp': opportunity_id}).fetchall()

        tasks = []
        for row in rows:
            m = row._mapping
            due = m['DueDateKey']
            tasks.append({
                'task_id':         m['TaskID'],
                'opportunity_id':  m['OpportunityID'],
                'contact_id':      m['ContactID'],
                'subject':         pick_localized(m, 'Subject'),
                'notes':           pick_localized(m, 'Notes'),
                'due_date_key':    due,
                'due_date_formatted': format_date(int(due)) if due else '',
                'priority':        m['Priority'],
                'status':          m['Status'],
                'assigned_to_user_id': m['AssignedToUserID'],
                'completed_date_key':  m['CompletedDateKey'],
                'completed_date_formatted': format_date(int(m['CompletedDateKey'])) if m['CompletedDateKey'] else '',
                'created_date_key':    m['CreatedDateKey'],
            })
        return jsonify(tasks)


@crm_api_bp.route('/opportunities/<int:opportunity_id>/tasks', methods=['POST'])
@login_required
def create_opportunity_task(opportunity_id):
    industry = current_industry()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))

    subj, subj_fa = _write_pair(data, 'subject')
    notes, notes_fa = _write_pair(data, 'notes')

    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        new_id = _next_int_id(conn, 't_OpportunityTask', 'TaskID')
        task_id = f"TASK-{new_id:05d}"

        conn.execute(text("""
            INSERT INTO t_OpportunityTask (
                TaskID, OpportunityID, ContactID,
                Subject, SubjectFa,
                Notes, NotesFa,
                DueDateKey, Priority, Status, AssignedToUserID,
                DemoIndustryCode, CreatedDateKey, CreatedBy
            ) VALUES (
                :tid, :opp, :cid,
                :subj, :subj_fa,
                :notes, :notes_fa,
                :due, :pri, 'Open', :assigned,
                :industry, :created, :user
            )
        """), {
            'tid': task_id,
            'opp': opportunity_id,
            'cid': data.get('contact_id'),
            'subj': subj,
            'subj_fa': subj_fa,
            'notes': notes,
            'notes_fa': notes_fa,
            'due': date_to_key(data.get('due_date')),
            'pri': data.get('priority') or 'Medium',
            'assigned': data.get('assigned_to_user_id') or current_user.get_id(),
            'industry': industry,
            'created': today_key,
            'user': current_user.get_id(),
        })
        conn.commit()
        return jsonify({'message': 'Task created', 'task_id': task_id}), 201


@crm_api_bp.route('/tasks/<string:task_id>', methods=['PATCH'])
@login_required
def update_opportunity_task(task_id):
    industry = current_industry()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT t.TaskID
            FROM t_OpportunityTask t
            JOIN t_Opportunity o ON t.OpportunityID = o.OpportunityID
            WHERE t.TaskID = :tid
              AND (o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)
        """), {'tid': task_id, 'industry': industry}).first()
        if not row:
            return jsonify({'error': 'Task not found'}), 404

        updates = []
        params = {'tid': task_id}

        if 'status' in data:
            updates.append("Status = :status")
            params['status'] = data['status']
            if data['status'] == 'Done':
                updates.append("CompletedDateKey = :done_date")
                params['done_date'] = today_key

        if 'subject' in data:
            subj, subj_fa = _write_pair(data, 'subject')
            updates.append("Subject = :subject")
            updates.append("SubjectFa = :subject_fa")
            params['subject'] = subj
            params['subject_fa'] = subj_fa

        if 'notes' in data:
            notes, notes_fa = _write_pair(data, 'notes')
            updates.append("Notes = :notes")
            updates.append("NotesFa = :notes_fa")
            params['notes'] = notes
            params['notes_fa'] = notes_fa

        if 'due_date' in data:
            updates.append("DueDateKey = :due")
            params['due'] = date_to_key(data['due_date'])

        if 'priority' in data:
            updates.append("Priority = :pri")
            params['pri'] = data['priority']

        if not updates:
            return jsonify({'error': 'No fields to update'}), 400

        conn.execute(text(f"""
            UPDATE t_OpportunityTask
            SET {', '.join(updates)}
            WHERE TaskID = :tid
        """), params)
        conn.commit()
        return jsonify({'message': 'Task updated'})


@crm_api_bp.route('/tasks/<string:task_id>', methods=['DELETE'])
@login_required
def delete_opportunity_task(task_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT t.TaskID
            FROM t_OpportunityTask t
            JOIN t_Opportunity o ON t.OpportunityID = o.OpportunityID
            WHERE t.TaskID = :tid
              AND (o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)
        """), {'tid': task_id, 'industry': industry}).first()
        if not row:
            return jsonify({'error': 'Task not found'}), 404

        conn.execute(text("DELETE FROM t_OpportunityTask WHERE TaskID = :tid"),
                     {'tid': task_id})
        conn.commit()
        return jsonify({'message': 'Task deleted'})


def _next_int_id(conn, table, id_col):
    try:
        v = conn.execute(text(f"SELECT MAX(CAST({id_col} AS INT)) FROM {table}")).scalar()
        return (v or 0) + 1
    except Exception:
        return 1


# ============================================================================
# CONTACTS — bilingual
# ============================================================================

@crm_api_bp.route('/opportunities/<int:opportunity_id>/contacts', methods=['GET'])
@login_required
def get_opportunity_contacts(opportunity_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        rows = conn.execute(text("""
            SELECT
                oc.LinkID, oc.ContactID, oc.Role, oc.IsPrimary,
                c.FirstName, c.FirstNameFa,
                c.LastName,  c.LastNameFa,
                c.JobTitle,  c.JobTitleFa,
                c.Department, c.DepartmentFa,
                c.Email, c.Mobile, c.DirectPhone, c.CompanyID
            FROM t_OpportunityContact oc
            LEFT JOIN t_CompanyContact c ON oc.ContactID = c.ContactID
            WHERE oc.OpportunityID = :opp_id
            ORDER BY oc.IsPrimary DESC, c.FirstName
        """), {'opp_id': opportunity_id}).fetchall()

        contacts = []
        for row in rows:
            m = row._mapping
            fn = pick_localized(m, 'FirstName')
            ln = pick_localized(m, 'LastName')
            contacts.append({
                'link_id':     m['LinkID'],
                'contact_id':  m['ContactID'],
                'role':        m['Role'],
                'is_primary':  m['IsPrimary'],
                'first_name':  fn,
                'last_name':   ln,
                'full_name':   ' '.join(filter(None, [fn, ln])),
                'job_title':   pick_localized(m, 'JobTitle'),
                'department':  pick_localized(m, 'Department'),
                'email':       m['Email'],
                'mobile':      m['Mobile'],
                'direct_phone': m['DirectPhone'],
                'company_id':  m['CompanyID'],
            })

        return jsonify(contacts)


@crm_api_bp.route('/contacts/search', methods=['GET'])
@login_required
def search_contacts():
    industry = current_industry()
    q = request.args.get('q', '').strip()
    engine = get_engine()
    with engine.connect() as conn:
        base_sql = """
            SELECT TOP 50 ContactID,
                   FirstName, FirstNameFa,
                   LastName,  LastNameFa,
                   JobTitle,  JobTitleFa,
                   CompanyID
            FROM t_CompanyContact
            WHERE IsActive = '1'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """
        if q:
            rows = conn.execute(text(base_sql + """
                AND (FirstName LIKE :q OR FirstNameFa LIKE :q
                     OR LastName LIKE :q OR LastNameFa LIKE :q
                     OR JobTitle LIKE :q OR JobTitleFa LIKE :q)
                ORDER BY FirstName
            """), {'industry': industry, 'q': f'%{q}%'}).fetchall()
        else:
            rows = conn.execute(text(base_sql + " ORDER BY FirstName"),
                                {'industry': industry}).fetchall()

        contacts = []
        for r in rows:
            m = r._mapping
            fn = pick_localized(m, 'FirstName')
            ln = pick_localized(m, 'LastName')
            contacts.append({
                'contact_id': m['ContactID'],
                'full_name':  ' '.join(filter(None, [fn, ln])),
                'job_title':  pick_localized(m, 'JobTitle'),
                'company_id': m['CompanyID'],
            })
        return jsonify(contacts)


@crm_api_bp.route('/opportunities/<int:opportunity_id>/contacts', methods=['POST'])
@login_required
def link_opportunity_contact(opportunity_id):
    industry = current_industry()
    data = request.get_json() or {}
    contact_id = data.get('contact_id')
    role = data.get('role') or 'Influencer'
    is_primary = data.get('is_primary', '0')

    if not contact_id:
        return jsonify({'error': 'contact_id required'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        existing = conn.execute(text("""
            SELECT LinkID FROM t_OpportunityContact
            WHERE OpportunityID = :opp AND ContactID = :c
        """), {'opp': opportunity_id, 'c': contact_id}).first()
        if existing:
            return jsonify({'error': 'Contact already linked'}), 400

        import uuid
        link_id = f"LINK-{uuid.uuid4().hex[:10].upper()}"

        conn.execute(text("""
            INSERT INTO t_OpportunityContact (LinkID, OpportunityID, ContactID, Role, IsPrimary)
            VALUES (:lid, :opp, :c, :role, :prim)
        """), {
            'lid': link_id, 'opp': opportunity_id, 'c': contact_id,
            'role': role, 'prim': is_primary
        })
        conn.commit()
        return jsonify({'message': 'Contact linked', 'link_id': link_id}), 201


@crm_api_bp.route('/opportunities/contacts/<string:link_id>', methods=['DELETE'])
@login_required
def unlink_opportunity_contact(link_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT oc.OpportunityID
            FROM t_OpportunityContact oc
            JOIN t_Opportunity o ON oc.OpportunityID = o.OpportunityID
            WHERE oc.LinkID = :lid
              AND (o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)
        """), {'lid': link_id, 'industry': industry}).first()
        if not row:
            return jsonify({'error': 'Link not found'}), 404

        conn.execute(text("DELETE FROM t_OpportunityContact WHERE LinkID = :lid"),
                     {'lid': link_id})
        conn.commit()
        return jsonify({'message': 'Contact unlinked'})


# ============================================================================
# DOCUMENTS — bilingual
# ============================================================================

@crm_api_bp.route('/opportunities/<int:opportunity_id>/documents', methods=['GET'])
@login_required
def get_opportunity_documents(opportunity_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        rows = conn.execute(text("""
            SELECT d.DocID, d.ActivityID,
                   d.DocumentName, d.DocumentNameFa,
                   d.DocumentType, d.StorageType, d.Location,
                   d.FileSizeKB, d.Direction, d.Channel,
                   d.Notes, d.NotesFa,
                   d.CreatedDateKey,
                   a.Subject AS ActivitySubject,
                   a.SubjectFa AS ActivitySubjectFa,
                   a.ActivityDateKey
            FROM t_ActivityDocument d
            LEFT JOIN t_OpportunityActivity a ON d.ActivityID = a.ActivityID
            WHERE d.OpportunityID = :oid
            ORDER BY d.CreatedDateKey DESC, d.DocID DESC
        """), {'oid': opportunity_id}).fetchall()

        documents = []
        for r in rows:
            m = r._mapping
            cdk = m['CreatedDateKey']
            documents.append({
                'doc_id':        m['DocID'],
                'activity_id':   m['ActivityID'],
                'document_name': pick_localized(m, 'DocumentName'),
                'document_type': m['DocumentType'],
                'storage_type':  m['StorageType'],
                'location':      m['Location'],
                'file_size_kb':  m['FileSizeKB'],
                'direction':     m['Direction'],
                'channel':       m['Channel'],
                'notes':         pick_localized(m, 'Notes'),
                'created_date_key': cdk,
                'created_date_formatted': format_date(int(cdk)) if cdk else '',
                'activity_subject': pick_localized(m, 'ActivitySubject'),
            })
        return jsonify(documents)


@crm_api_bp.route('/opportunities/<int:opportunity_id>/documents', methods=['POST'])
@login_required
def add_opportunity_document(opportunity_id):
    industry = current_industry()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))

    name, name_fa = _write_pair(data, 'document_name')
    notes, notes_fa = _write_pair(data, 'notes')

    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': opportunity_id, 'industry': industry}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404

        max_id = conn.execute(text(
            "SELECT MAX(CAST(DocID AS INT)) FROM t_ActivityDocument"
        )).scalar() or 0
        new_doc_id = max_id + 1

        conn.execute(text("""
            INSERT INTO t_ActivityDocument (
                DocID, ActivityID, OpportunityID,
                DocumentName, DocumentNameFa,
                DocumentType, StorageType, Location, FileSizeKB,
                Direction, Channel,
                Notes, NotesFa,
                CreatedDateKey, CreatedBy
            ) VALUES (
                :did, :aid, :oid,
                :name, :name_fa,
                :type, :storage, :loc, :size,
                :dir, :chan,
                :notes, :notes_fa,
                :date, :user
            )
        """), {
            'did': new_doc_id,
            'aid': data.get('activity_id'),
            'oid': opportunity_id,
            'name': name,
            'name_fa': name_fa,
            'type': data.get('document_type', 'Other'),
            'storage': data.get('storage_type', 'URL'),
            'loc': data.get('location'),
            'size': data.get('file_size_kb'),
            'dir': data.get('direction', 'Sent'),
            'chan': data.get('channel', 'Email'),
            'notes': notes,
            'notes_fa': notes_fa,
            'date': today_key,
            'user': current_user.get_id(),
        })
        conn.commit()
        return jsonify({'message': 'Document attached', 'doc_id': new_doc_id}), 201


@crm_api_bp.route('/documents/<int:doc_id>', methods=['DELETE'])
@login_required
def delete_activity_document(doc_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT d.DocID
            FROM t_ActivityDocument d
            JOIN t_Opportunity o ON d.OpportunityID = o.OpportunityID
            WHERE d.DocID = :did
              AND (o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)
        """), {'did': doc_id, 'industry': industry}).first()
        if not row:
            return jsonify({'error': 'Document not found'}), 404

        conn.execute(text("DELETE FROM t_ActivityDocument WHERE DocID = :did"),
                     {'did': doc_id})
        conn.commit()
        return jsonify({'message': 'Document removed'})


# ============================================================================
# STAGES / USERS / CUSTOMERS (light bilingual)
# ============================================================================

@crm_api_bp.route('/stages')
@login_required
def get_stages():
    stages = [
        'Prospecting', 'Qualification', 'Proposal', 'Negotiation',
        'Closed Won', 'Closed Lost'
    ]
    return jsonify(stages)


@crm_api_bp.route('/users')
@login_required
def get_crm_users():
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = users_for_current_industry(conn, industry)
        users = [{'user_id': row[0], 'full_name': row[1], 'username': row[2]} for row in rows]
        return jsonify(users)


@crm_api_bp.route('/customers')
@login_required
def get_crm_customers():
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT companyID,
                   Name, NameFa
            FROM t_companies
            WHERE IsCustomer = '1'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            ORDER BY Name
        """), {'industry': industry}).fetchall()

        customers = []
        for row in rows:
            m = row._mapping
            customers.append({
                'customer_id': m['companyID'],
                'name': pick_localized(m, 'Name'),
            })
        return jsonify(customers)


@crm_api_bp.route('/all-customers')
@login_required
def get_all_customers():
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                c.companyID,
                c.Name, c.NameFa,
                c.Telephone,
                c.IsCustomer,
                COUNT(DISTINCT o.OpportunityID) as opportunity_count,
                COALESCE(SUM(CASE WHEN o.Status = 'Open' THEN CAST(o.Value AS DECIMAL(18,2)) ELSE 0 END), 0) as open_pipeline_value,
                COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0) as total_revenue
            FROM t_companies c
            LEFT JOIN t_Opportunity o ON c.companyID = o.CustomerID
            LEFT JOIN t_SalesOrder so ON c.companyID = so.CustomerID AND so.Status = 'Confirmed'
            WHERE c.IsCustomer = '1'
              AND (c.DemoIndustryCode = :industry OR c.DemoIndustryCode IS NULL)
            GROUP BY c.companyID, c.Name, c.NameFa, c.Telephone, c.IsCustomer
            ORDER BY c.Name
        """), {'industry': industry}).fetchall()

        customers = []
        for row in rows:
            m = row._mapping
            customers.append({
                'customer_id': m['companyID'],
                'name': pick_localized(m, 'Name'),
                'telephone': m['Telephone'],
                'is_customer': m['IsCustomer'],
                'opportunity_count': m['opportunity_count'] or 0,
                'open_pipeline_value': float(m['open_pipeline_value'] or 0),
                'total_revenue': float(m['total_revenue'] or 0),
            })
        return jsonify(customers)


@crm_api_bp.route('/customers/<string:customer_id>')
@login_required
def get_customer_detail(customer_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        customer = conn.execute(text("""
            SELECT companyID,
                   Name, NameFa,
                   Address, AddressFa,
                   Telephone, IsCustomer, IsSupplier
            FROM t_companies
            WHERE companyID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': customer_id, 'industry': industry}).fetchone()

        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        c = customer._mapping

        opportunities = conn.execute(text("""
            SELECT OpportunityID,
                   OpportunityName, OpportunityNameFa,
                   Value, Probability, Stage, Status, ExpectedCloseDateKey
            FROM t_Opportunity
            WHERE CustomerID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
            ORDER BY CreatedDateKey DESC
        """), {'id': customer_id, 'industry': industry}).fetchall()

        opp_list = []
        for o in opportunities:
            om = o._mapping
            eck = om['ExpectedCloseDateKey']
            opp_list.append({
                'opportunity_id': om['OpportunityID'],
                'name': pick_localized(om, 'OpportunityName'),
                'value': float(om['Value'] or 0),
                'probability': om['Probability'],
                'stage': om['Stage'],
                'status': om['Status'],
                'expected_close_date_key': eck,
                'expected_close_date_formatted': format_date(int(eck)) if eck else '',
            })

        return jsonify({
            'customer_id': c['companyID'],
            'name':        pick_localized(c, 'Name'),
            'address':     pick_localized(c, 'Address'),
            'telephone':   c['Telephone'],
            'is_customer': c['IsCustomer'],
            'is_supplier': c['IsSupplier'],
            'opportunities': opp_list,
        })


# ============================================================================
# ACTIVITY LOG (all)
# ============================================================================

@crm_api_bp.route('/all-activities')
@login_required
def get_all_activities():
    industry = current_industry()
    limit = request.args.get('limit', 50)
    activity_type = request.args.get('activity_type', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')

    engine = get_engine()
    with engine.connect() as conn:
        conditions = [
            "(o.DemoIndustryCode = :industry OR o.DemoIndustryCode IS NULL)",
            "(o.IsActive IS NULL OR o.IsActive = '1')",
        ]
        params = {'industry': industry}

        if activity_type:
            conditions.append("a.ActivityType = :type")
            params['type'] = activity_type

        if from_date:
            conditions.append("a.ActivityDateKey >= :from_date")
            params['from_date'] = from_date

        if to_date:
            conditions.append("a.ActivityDateKey <= :to_date")
            params['to_date'] = to_date

        where_clause = " AND ".join(conditions)

        rows = conn.execute(text(f"""
            SELECT TOP {int(limit)}
                a.ActivityID, a.OpportunityID, a.ActivityType,
                a.Subject, a.SubjectFa,
                a.Notes, a.NotesFa,
                a.ActivityDateKey, a.CreatedBy,
                o.OpportunityName, o.OpportunityNameFa,
                u.FullName as CreatedByName
            FROM t_OpportunityActivity a
            JOIN t_Opportunity o ON a.OpportunityID = o.OpportunityID
            LEFT JOIN t_Users u ON a.CreatedBy = u.UserID
            WHERE {where_clause}
            ORDER BY a.ActivityDateKey DESC
        """), params).fetchall()

        activities = []
        for row in rows:
            m = row._mapping
            adk = m['ActivityDateKey']
            activities.append({
                'activity_id': m['ActivityID'],
                'opportunity_id': m['OpportunityID'],
                'opportunity_name': pick_localized(m, 'OpportunityName'),
                'activity_type': m['ActivityType'],
                'subject': pick_localized(m, 'Subject'),
                'notes':   pick_localized(m, 'Notes'),
                'activity_date_key': adk,
                'activity_date_formatted': format_date(int(adk)) if adk else '',
                'created_by_name': m['CreatedByName'],
            })
        return jsonify(activities)


# ============================================================================
# QUOTES — bilingual
# ============================================================================

@crm_api_bp.route('/quotes')
@login_required
def list_quotes():
    industry = current_industry()
    opportunity_id = request.args.get('opportunity_id')
    customer_id = request.args.get('customer_id')
    status = request.args.get('status', '')

    engine = get_engine()
    with engine.connect() as conn:
        conditions = ["(q.DemoIndustryCode = :industry OR q.DemoIndustryCode IS NULL)"]
        params = {'industry': industry}

        if opportunity_id:
            conditions.append("q.OpportunityID = :opp_id")
            params['opp_id'] = opportunity_id

        if customer_id:
            conditions.append("q.CustomerID = :cust_id")
            params['cust_id'] = customer_id

        if status:
            conditions.append("q.Status = :status")
            params['status'] = status

        where_clause = " AND ".join(conditions)

        rows = conn.execute(text(f"""
            SELECT q.QuoteID, q.QuoteNumber, q.OpportunityID, q.CustomerID,
                   q.Revision, q.Status,
                   q.Subject, q.SubjectFa,
                   q.ValidUntilDateKey,
                   q.TotalAmount,
                   q.Notes, q.NotesFa,
                   q.ParentQuoteID,
                   o.OpportunityName, o.OpportunityNameFa,
                   c.Name, c.NameFa,
                   u.FullName,
                   q.CreatedDateKey
            FROM t_Quote q
            LEFT JOIN t_Opportunity o ON q.OpportunityID = o.OpportunityID
            LEFT JOIN t_companies c ON q.CustomerID = c.companyID
            LEFT JOIN t_Users u ON q.CreatedBy = u.UserID
            WHERE {where_clause}
            ORDER BY q.CreatedDateKey DESC
        """), params).fetchall()

        quotes = []
        for row in rows:
            m = row._mapping
            vu = m['ValidUntilDateKey']
            cd = m['CreatedDateKey']
            quotes.append({
                'quote_id': m['QuoteID'],
                'quote_number': m['QuoteNumber'],
                'opportunity_id': m['OpportunityID'],
                'opportunity_name': pick_localized(m, 'OpportunityName'),
                'customer_id': m['CustomerID'],
                'customer_name': pick_localized(m, 'Name'),
                'revision': m['Revision'],
                'status': m['Status'],
                'subject': pick_localized(m, 'Subject'),
                'valid_until_date_key': vu,
                'valid_until_date_formatted': format_date(int(vu)) if vu else '',
                'total_amount': float(m['TotalAmount']) if m['TotalAmount'] else 0,
                'notes': pick_localized(m, 'Notes'),
                'parent_quote_id': m['ParentQuoteID'],
                'created_by_name': m['FullName'],
                'created_date_key': cd,
                'created_date_formatted': format_date(int(cd)) if cd else '',
            })
        return jsonify(quotes)


@crm_api_bp.route('/quotes/<string:quote_id>')
@login_required
def get_quote(quote_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        quote_row = conn.execute(text("""
            SELECT q.QuoteID, q.QuoteNumber, q.OpportunityID, q.CustomerID,
                   q.Revision, q.Status,
                   q.Subject, q.SubjectFa,
                   q.ValidUntilDateKey,
                   q.TotalAmount,
                   q.Notes, q.NotesFa,
                   q.ParentQuoteID,
                   q.CreatedDateKey, q.ModifiedDateKey,
                   o.OpportunityName, o.OpportunityNameFa,
                   c.Name, c.NameFa
            FROM t_Quote q
            LEFT JOIN t_Opportunity o ON q.OpportunityID = o.OpportunityID
            LEFT JOIN t_companies c ON q.CustomerID = c.companyID
            WHERE q.QuoteID = :id
              AND (q.DemoIndustryCode = :industry OR q.DemoIndustryCode IS NULL)
        """), {'id': quote_id, 'industry': industry}).fetchone()

        if not quote_row:
            return jsonify({'error': 'Quote not found'}), 404

        m = quote_row._mapping
        vu = m['ValidUntilDateKey']
        cd = m['CreatedDateKey']
        md = m['ModifiedDateKey']

        lines = conn.execute(text("""
            SELECT ql.LineID, ql.ProductID,
                   p.descEnglish AS ProductName,
                   p.descFarsi   AS ProductNameFa,
                   ql.Quantity, ql.UnitPrice, ql.TotalPrice, ql.Notes
            FROM t_QuoteLine ql
            LEFT JOIN t_Product p ON ql.ProductID = p.ProductId
            WHERE ql.QuoteID = :id
        """), {'id': quote_id}).fetchall()

        line_list = []
        for line in lines:
            lm = line._mapping
            line_list.append({
                'line_id': lm['LineID'],
                'product_id': lm['ProductID'],
                # NOTE: t_Product uses the legacy descFarsi suffix — not _Fa.
                # We swap it here explicitly.
                'product_name': (
                    lm['ProductNameFa']
                    if _lang() != 'en' and lm.get('ProductNameFa') and str(lm['ProductNameFa']).strip()
                    else lm['ProductName']
                ),
                'quantity':    float(lm['Quantity']) if lm['Quantity'] else 0,
                'unit_price':  float(lm['UnitPrice']) if lm['UnitPrice'] else 0,
                'total_price': float(lm['TotalPrice']) if lm['TotalPrice'] else 0,
                'notes':       lm['Notes'],
            })

        return jsonify({
            'quote_id':       m['QuoteID'],
            'quote_number':   m['QuoteNumber'],
            'opportunity_id': m['OpportunityID'],
            'opportunity_name': pick_localized(m, 'OpportunityName'),
            'customer_id':    m['CustomerID'],
            'customer_name':  pick_localized(m, 'Name'),
            'revision':       m['Revision'],
            'status':         m['Status'],
            'subject':        pick_localized(m, 'Subject'),
            'valid_until_date_key': vu,
            'valid_until_date_formatted': format_date(int(vu)) if vu else '',
            'total_amount':   float(m['TotalAmount']) if m['TotalAmount'] else 0,
            'notes':          pick_localized(m, 'Notes'),
            'parent_quote_id': m['ParentQuoteID'],
            'created_date_key': cd,
            'created_date_formatted': format_date(int(cd)) if cd else '',
            'modified_date_key': md,
            'modified_date_formatted': format_date(int(md)) if md else '',
            'lines': line_list,
        })


# ============================================================================
# COMPANY PHONES / CONTACT CHANNELS — bilingual label
# ============================================================================

@crm_api_bp.route('/companies/<string:company_id>/phones', methods=['GET'])
@login_required
def get_company_phones(company_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT 1 FROM t_companies
            WHERE companyID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': company_id, 'industry': industry}).first()
        if not row:
            return jsonify({'error': 'Company not found'}), 404

        rows = conn.execute(text("""
            SELECT PhoneID, PhoneNumber, PhoneRangeEnd, PhoneType,
                   Extension, IsPrimary,
                   Label, LabelFa,
                   Notes, IsActive
            FROM t_CompanyPhone
            WHERE CompanyID = :id
            ORDER BY
                CASE WHEN IsPrimary = '1' THEN 1 ELSE 2 END,
                PhoneType, PhoneNumber
        """), {'id': company_id}).fetchall()

        phones = []
        for r in rows:
            m = r._mapping
            phones.append({
                'phone_id':        m['PhoneID'],
                'phone_number':    m['PhoneNumber'],
                'phone_range_end': m['PhoneRangeEnd'],
                'phone_type':      m['PhoneType'],
                'extension':       m['Extension'],
                'is_primary':      m['IsPrimary'],
                'label':           pick_localized(m, 'Label'),
                'notes':           m['Notes'],
                'is_active':       m['IsActive'],
            })
        return jsonify(phones)


@crm_api_bp.route('/contacts/<string:contact_id>/channels', methods=['GET'])
@login_required
def get_contact_channels(contact_id):
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT 1 FROM t_CompanyContact
            WHERE ContactID = :id
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'id': contact_id, 'industry': industry}).first()
        if not row:
            return jsonify({'error': 'Contact not found'}), 404

        rows = conn.execute(text("""
            SELECT ChannelID, ChannelType, Value, Extension,
                   IsPrimary, IsPreferred,
                   Label, LabelFa,
                   IsActive
            FROM t_ContactChannel
            WHERE ContactID = :id AND IsActive = '1'
            ORDER BY
                CASE WHEN IsPreferred = '1' THEN 1 ELSE 2 END,
                ChannelType
        """), {'id': contact_id}).fetchall()

        channels = []
        for r in rows:
            m = r._mapping
            channels.append({
                'channel_id':   m['ChannelID'],
                'channel_type': m['ChannelType'],
                'value':        m['Value'],
                'extension':    m['Extension'],
                'is_primary':   m['IsPrimary'],
                'is_preferred': m['IsPreferred'],
                'label':        pick_localized(m, 'Label'),
                'is_active':    m['IsActive'],
            })
        return jsonify(channels)
        
   
   

# ============================================================================
# SALES DASHBOARD — KPI STATS + CHARTS + FORECAST
# ============================================================================
# These endpoints back /crm/sales-dashboard. They must respect the same
# industry + project filters as the list endpoints (Playbook §4.3).

@crm_api_bp.route('/dashboard/stats')
@login_required
def dashboard_stats():
    """
    Top KPI cards on the Sales Dashboard.
    Returns:
      pipeline_value       — SUM(Value) for open opportunities
      weighted_pipeline    — SUM(Value * Probability / 100) for open opportunities
      win_rate             — won / (won + lost) * 100
      open_opportunities   — count of open opportunities
      total_revenue        — SUM of confirmed sales orders
      won_count, lost_count
    """
    industry = current_industry()
    project_id = request.args.get('project_id')
    if not project_id:
        cp = session.get('current_project_id', 'All')
        if cp != 'All':
            project_id = cp

    engine = get_engine()
    with engine.connect() as conn:
        # ---- Opportunity KPIs ----
        cond = [
            "(DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)",
            "(IsActive IS NULL OR IsActive = '1')",
        ]
        params = {'industry': industry}
        if project_id and project_id != 'All':
            cond.append("(ProjectID = :proj OR ProjectID IS NULL)")
            params['proj'] = project_id

        opp_where = " AND ".join(cond)

        # Open pipeline (Status = Open)
        open_row = conn.execute(text(f"""
            SELECT
                COALESCE(SUM(CAST(Value AS DECIMAL(18,2))), 0) AS pipeline_value,
                COALESCE(SUM(CAST(Value AS DECIMAL(18,2)) * CAST(Probability AS INT) / 100.0), 0) AS weighted_pipeline,
                COUNT(*) AS open_count
            FROM t_Opportunity
            WHERE {opp_where}
              AND Status = 'Open'
        """), params).fetchone()

        pipeline_value    = float(open_row[0] or 0)
        weighted_pipeline = float(open_row[1] or 0)
        open_count        = int(open_row[2] or 0)

        # Won / Lost
        wl_row = conn.execute(text(f"""
            SELECT
                SUM(CASE WHEN Status = 'Closed Won'  THEN 1 ELSE 0 END) AS won_count,
                SUM(CASE WHEN Status = 'Closed Lost' THEN 1 ELSE 0 END) AS lost_count
            FROM t_Opportunity
            WHERE {opp_where}
        """), params).fetchone()
        won_count  = int(wl_row[0] or 0)
        lost_count = int(wl_row[1] or 0)
        total_closed = won_count + lost_count
        win_rate = round((won_count / total_closed * 100), 1) if total_closed > 0 else 0

        # ---- Revenue (confirmed sales orders) ----
        try:
            rev_row = conn.execute(text(f"""
                SELECT COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0)
                FROM t_SalesOrder so
                WHERE so.Status = 'Confirmed'
                  AND (so.DemoIndustryCode = :industry OR so.DemoIndustryCode IS NULL)
            """), {'industry': industry}).fetchone()
            total_revenue = float(rev_row[0] or 0)
        except Exception as e:
            print(f"⚠️ total_revenue query failed: {e}")
            total_revenue = 0.0

        return jsonify({
            'pipeline_value':    round(pipeline_value, 2),
            'weighted_pipeline': round(weighted_pipeline, 2),
            'win_rate':          win_rate,
            'open_opportunities': open_count,
            'total_revenue':     round(total_revenue, 2),
            'won_count':         won_count,
            'lost_count':        lost_count,
        })


@crm_api_bp.route('/dashboard/charts')
@login_required
def dashboard_charts():
    """
    Pipeline by stage + sales trend (last 6 months).
    """
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        # Pipeline by stage
        stage_rows = conn.execute(text("""
            SELECT Stage,
                   COALESCE(SUM(CAST(Value AS DECIMAL(18,2))), 0) AS stage_value
            FROM t_Opportunity
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
              AND Status = 'Open'
            GROUP BY Stage
        """), {'industry': industry}).fetchall()

        stage_order = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation', 'Closed Won', 'Closed Lost']
        stage_map = {r[0]: float(r[1] or 0) for r in stage_rows}
        stages = [s for s in stage_order if s in stage_map]
        stage_values = [stage_map[s] for s in stages]

        # Sales trend — last 6 months, from confirmed sales orders.
        # Uses DateKey integer arithmetic (Playbook §4.4).
        trend_rows = []
        try:
            trend_rows = conn.execute(text("""
                SELECT
                    (YEAR(GETDATE()) * 100 + MONTH(GETDATE())) AS ym,
                    COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0) AS revenue
                FROM t_SalesOrder so
                WHERE so.Status = 'Confirmed'
                  AND (so.DemoIndustryCode = :industry OR so.DemoIndustryCode IS NULL)
                  AND so.OrderDateKey >= (
                      YEAR(DATEADD(MONTH, -5, GETDATE())) * 10000
                    + MONTH(DATEADD(MONTH, -5, GETDATE())) * 100
                    + 1
                  )
                GROUP BY (YEAR(GETDATE()) * 100 + MONTH(GETDATE()))
                ORDER BY ym
            """), {'industry': industry}).fetchall()
        except Exception as e:
            print(f"⚠️ sales trend query failed: {e}")

        months = []
        revenues = []
        for r in trend_rows:
            ym = int(r[0] or 0)
            y, m = divmod(ym, 100)
            months.append(f"{y}-{m:02d}")
            revenues.append(float(r[1] or 0))

        return jsonify({
            'stages': stages,
            'stage_values': stage_values,
            'months': months,
            'revenues': revenues,
        })


@crm_api_bp.route('/dashboard/top-customers')
@login_required
def dashboard_top_customers():
    """
    Top 5 customers by confirmed-order revenue.
    """
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = []
        try:
            rows = conn.execute(text("""
                SELECT TOP 5
                    c.Name AS CustomerName,
                    COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0) AS revenue
                FROM t_SalesOrder so
                JOIN t_companies c ON so.CustomerID = c.companyID
                WHERE so.Status = 'Confirmed'
                  AND (so.DemoIndustryCode = :industry OR so.DemoIndustryCode IS NULL)
                GROUP BY c.Name
                ORDER BY revenue DESC
            """), {'industry': industry}).fetchall()
        except Exception as e:
            print(f"⚠️ top-customers query failed: {e}")

        customers = [r[0] for r in rows]
        revenues  = [float(r[1] or 0) for r in rows]
        return jsonify({'customers': customers, 'revenues': revenues})


@crm_api_bp.route('/dashboard/top-products')
@login_required
def dashboard_top_products():
    """
    Top 5 products by sold quantity.
    """
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = []
        try:
            rows = conn.execute(text("""
                SELECT TOP 5
                    p.descEnglish AS ProductName,
                    COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2))), 0) AS qty
                FROM t_SalesOrder so
                JOIN t_Product p ON so.ProductID = p.ProductId
                WHERE so.Status = 'Confirmed'
                  AND (so.DemoIndustryCode = :industry OR so.DemoIndustryCode IS NULL)
                GROUP BY p.descEnglish
                ORDER BY qty DESC
            """), {'industry': industry}).fetchall()
        except Exception as e:
            print(f"⚠️ top-products query failed: {e}")

        products   = [r[0] for r in rows]
        quantities = [float(r[1] or 0) for r in rows]
        return jsonify({'products': products, 'quantities': quantities})


@crm_api_bp.route('/forecast')
@login_required
def dashboard_forecast():
    """
    Simple forecast: sum of open-opportunity weighted values whose
    ExpectedCloseDateKey falls within the next 30 / 60 / 90 days.
    """
    industry = current_industry()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    # Compute +30 / +60 / +90 day keys in Python (no SQL date math needed).
    from datetime import timedelta
    d30 = int((datetime.now() + timedelta(days=30)).strftime('%Y%m%d'))
    d60 = int((datetime.now() + timedelta(days=60)).strftime('%Y%m%d'))
    d90 = int((datetime.now() + timedelta(days=90)).strftime('%Y%m%d'))

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COALESCE(SUM(CASE
                    WHEN CAST(ExpectedCloseDateKey AS INT) BETWEEN :today AND :d30
                    THEN CAST(Value AS DECIMAL(18,2)) * CAST(Probability AS INT) / 100.0
                    ELSE 0 END), 0) AS f30,
                COALESCE(SUM(CASE
                    WHEN CAST(ExpectedCloseDateKey AS INT) BETWEEN :today AND :d60
                    THEN CAST(Value AS DECIMAL(18,2)) * CAST(Probability AS INT) / 100.0
                    ELSE 0 END), 0) AS f60,
                COALESCE(SUM(CASE
                    WHEN CAST(ExpectedCloseDateKey AS INT) BETWEEN :today AND :d90
                    THEN CAST(Value AS DECIMAL(18,2)) * CAST(Probability AS INT) / 100.0
                    ELSE 0 END), 0) AS f90
            FROM t_Opportunity
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND (IsActive IS NULL OR IsActive = '1')
              AND Status = 'Open'
              AND ExpectedCloseDateKey IS NOT NULL
              AND ExpectedCloseDateKey <> ''
        """), {
            'industry': industry,
            'today': today_key, 'd30': d30, 'd60': d60, 'd90': d90,
        }).fetchone()

        return jsonify({
            'next_30_days': {'total': float(row[0] or 0)},
            'next_60_days': {'total': float(row[1] or 0)},
            'next_90_days': {'total': float(row[2] or 0)},
        })
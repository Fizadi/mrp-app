from . import crm_api_bp
from flask import request, jsonify, current_app, session
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from datetime import datetime
import csv
import uuid as _uuid
from io import StringIO
from flask import Response
from app.core.date_helpers import format_date
from app.modules.auth import verify_password  # noqa: F401
from app.core.permissions import require_permission, require_module

# ============================================================================
# BILINGUAL HELPER
# ============================================================================
LANG_SUFFIX = {'fa': 'Fa'}


def _lang():
    return session.get('lang', 'en')


def pick_localized(row, base_field):
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


def _tenant_id():
    return session.get('tenant_id', 1)


def date_to_key(date_str):
    if not date_str:
        return None
    return int(str(date_str).replace('-', '').replace('/', ''))


def _to_int_or_none(v):
    if v is None or v == '':
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _to_float_or_none(v):
    """§DealSize — safe float conversion that preserves None."""
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
@require_module('crm')
@require_permission('SALES_VIEW')
def get_opportunity_stats():
    tenant_id = _tenant_id()
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
            "(TenantID = :tenant_id OR :tenant_id = 0)",
            "(IsActive IS NULL OR IsActive = '1')",
        ]
        params = {'tenant_id': tenant_id}

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
                COALESCE(SUM(TRY_CAST(Value AS DECIMAL(18,2))), 0) as total_value,
                COALESCE(SUM(TRY_CAST(Value AS DECIMAL(18,2)) * TRY_CAST(Probability AS INT) / 100), 0) as weighted_value,
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
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'tenant_id': tenant_id}).fetchone()

        lost_rows = conn.execute(text("""
            SELECT COUNT(*) as lost_count
            FROM t_Opportunity
            WHERE Status = 'Closed Lost'
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'tenant_id': tenant_id}).fetchone()

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
@require_module('crm')
@require_permission('SALES_VIEW')
def list_opportunities():
    tenant_id = _tenant_id()
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
            "(o.TenantID = :tenant_id OR :tenant_id = 0)",
            "(o.IsActive IS NULL OR o.IsActive = '1')",
        ]
        params = {'tenant_id': tenant_id}

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
            conditions.append("TRY_CAST(o.Probability AS INT) >= :prob_min")
            params['prob_min'] = int(probability_min)
        if probability_max:
            conditions.append("TRY_CAST(o.Probability AS INT) <= :prob_max")
            params['prob_max'] = int(probability_max)
        if search:
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

        # §DealSize — SELECT DealSizeValue + DealSizeUnit
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
                   o.DealSizeValue,         o.DealSizeUnit,
                   c.companyID AS CustomerID,
                   c.Name      AS CustomerName,
                   c.NameFa    AS CustomerNameFa,
                   u.UserID         AS AssignedUserID,
                   u.FullName       AS AssignedUserName,
                   u.FullNameLocal  AS AssignedUserNameFa,
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
            ORDER BY o.ExpectedCloseDateKey ASC,
                     TRY_CAST(o.Value AS DECIMAL(18,2)) DESC
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
            deal_size_value = _to_float_or_none(r.get('DealSizeValue'))  # §DealSize

            opportunities.append({
                'opportunity_id': r.get('OpportunityID'),
                'name':           pick_localized(r, 'OpportunityName'),
                'win_loss_reason': pick_localized(r, 'WinLossReason'),
                'contact_person': pick_localized(r, 'ContactPerson'),
                'notes':          pick_localized(r, 'Notes'),
                'customer_name':  pick_localized(r, 'CustomerName'),
                'value':          value,
                'probability':    probability,
                'stage':          r.get('Stage'),
                'status':         r.get('Status'),
                'contact_email':  r.get('ContactEmail'),
                'contact_phone':  r.get('ContactPhone'),
                'customer_id':    r.get('CustomerID'),
                'assigned_user_id':   r.get('AssignedUserID'),
                'assigned_user_name': pick_localized(r, 'AssignedUserName'),
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
                # §DealSize
                'deal_size_value': deal_size_value,
                'deal_size_unit':  r.get('DealSizeUnit'),
                'deal_size_display': (
                    f"{deal_size_value:g} {r.get('DealSizeUnit') or ''}".strip()
                    if deal_size_value is not None else ''
                ),
            })

        return jsonify(opportunities)


# ============================================================================
# OPPORTUNITIES — GET ONE
# ============================================================================

@crm_api_bp.route('/opportunities/<string:opportunity_id>', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_opportunity(opportunity_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        # §DealSize — SELECT the two new columns
        row = conn.execute(text("""
            SELECT
                o.OpportunityID,
                o.OpportunityName,   o.OpportunityNameFa,
                c.Name  AS CustomerName,  c.NameFa  AS CustomerNameFa,
                o.Value, o.Probability,
                o.Stage,
                o.ExpectedCloseDateKey,
                o.Status,
                o.WinLossReason,     o.WinLossReasonFa,
                o.ContactPerson,     o.ContactPersonFa,
                o.ContactEmail, o.ContactPhone,
                o.Notes,             o.NotesFa,
                o.CustomerID, o.ProjectID, o.AssignedToUserID,
                o.CreatedDateKey, o.ModifiedDateKey,
                o.DealSizeValue, o.DealSizeUnit
            FROM t_Opportunity o
            LEFT JOIN t_companies c ON o.CustomerID = c.companyID
            WHERE o.OpportunityID = :id
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
              AND (o.IsActive IS NULL OR o.IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).fetchone()

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
            'customer_id':    r['CustomerID'],
            'customer_name':  pick_localized(r, 'CustomerName'),
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
            'project_id':     r['ProjectID'],
            'assigned_user_id': r['AssignedToUserID'],
            # §DealSize
            'deal_size_value': _to_float_or_none(r['DealSizeValue']),
            'deal_size_unit':  r['DealSizeUnit'],
        })

# ============================================================================
# OPPORTUNITIES — CREATE / UPDATE / DELETE / DUPLICATE
# ============================================================================

def _write_pair(data, base_field):
    base = data.get(base_field)
    fa   = data.get(f'{base_field}Fa')
    if fa is not None:
        fa = str(fa).strip() or None
    return base, fa


@crm_api_bp.route('/opportunities', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_CREATE')
def create_opportunity():
    tenant_id = _tenant_id()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    name, name_fa = _write_pair(data, 'name')
    notes, notes_fa = _write_pair(data, 'notes')
    wlr, wlr_fa = _write_pair(data, 'win_loss_reason')
    cp, cp_fa = _write_pair(data, 'contact_person')

    new_opp_id = str(_uuid.uuid4()).upper()

    # §DealSize
    deal_size_value = _to_float_or_none(data.get('deal_size_value'))
    deal_size_unit  = (data.get('deal_size_unit') or '').strip() or None

    engine = get_engine()
    try:
        with engine.connect() as conn:
            if deal_size_value is not None and not deal_size_unit:
                try:
                    trow = conn.execute(text("""
                        SELECT DefaultDealSizeUnit FROM t_Tenants
                        WHERE TenantID = :tid
                    """), {'tid': tenant_id}).fetchone()
                    if trow and trow[0]:
                        deal_size_unit = trow[0]
                except Exception as _e:
                    print(f"⚠️ tenant-default fallback failed: {_e}")

            result = conn.execute(text("""
                INSERT INTO t_Opportunity (
                    OpportunityID,
                    OpportunityName, OpportunityNameFa,
                    CustomerID, ProjectID, Value, Probability,
                    Status, Stage, ExpectedCloseDateKey,
                    WinLossReason, WinLossReasonFa,
                    ContactPerson, ContactPersonFa,
                    ContactEmail, ContactPhone, AssignedToUserID,
                    Notes, NotesFa,
                    DealSizeValue, DealSizeUnit,
                    CreatedBy, CreatedDateKey, ModifiedBy, ModifiedDateKey,
                    TenantID
                ) VALUES (
                    :opp_id,
                    :name, :name_fa,
                    :customer_id, :project_id, :value, :probability,
                    'Open', :stage, :close_date,
                    :win_loss_reason, :win_loss_reason_fa,
                    :contact_person, :contact_person_fa,
                    :contact_email, :contact_phone, :assigned_to,
                    :notes, :notes_fa,
                    :deal_size_value, :deal_size_unit,
                    :user_id, :created_date, :user_id, :created_date,
                    :tenant_id
                )
            """), {
                'opp_id': new_opp_id,
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
                'deal_size_value': deal_size_value,
                'deal_size_unit':  deal_size_unit,
                'user_id': current_user.get_id(),
                'created_date': today_key,
                'tenant_id': tenant_id,
            })
            conn.commit()

            return jsonify({'message': 'Opportunity created',
                            'opportunity_id': new_opp_id}), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Create failed: {e}'}), 500


@crm_api_bp.route('/opportunities/<string:opportunity_id>', methods=['PUT'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def update_opportunity(opportunity_id):
    tenant_id = _tenant_id()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))

    name, name_fa = _write_pair(data, 'name')
    notes, notes_fa = _write_pair(data, 'notes')
    wlr, wlr_fa = _write_pair(data, 'win_loss_reason')
    cp, cp_fa = _write_pair(data, 'contact_person')

    # §DealSize
    deal_size_value = _to_float_or_none(data.get('deal_size_value'))
    deal_size_unit  = (data.get('deal_size_unit') or '').strip() or None

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Opportunity not found'}), 404

        if deal_size_value is not None and not deal_size_unit:
            try:
                trow = conn.execute(text("""
                    SELECT DefaultDealSizeUnit FROM t_Tenants
                    WHERE TenantID = :tid
                """), {'tid': tenant_id}).fetchone()
                if trow and trow[0]:
                    deal_size_unit = trow[0]
            except Exception as _e:
                print(f"⚠️ tenant-default fallback failed: {_e}")

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
                DealSizeValue = :deal_size_value,
                DealSizeUnit = :deal_size_unit,
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
            'deal_size_value': deal_size_value,
            'deal_size_unit':  deal_size_unit,
            'status': status,
            'modified_date': today_key,
            'user_id': current_user.get_id()
        })
        conn.commit()
        return jsonify({'message': 'Opportunity updated'})


@crm_api_bp.route('/opportunities/<string:opportunity_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_DELETE')
def delete_opportunity(opportunity_id):
    tenant_id = _tenant_id()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Opportunity not found'}), 404
        try:
            conn.execute(text("""
                UPDATE t_Opportunity
                SET IsActive = '0', ModifiedDateKey = :date, ModifiedBy = :user_id
                WHERE OpportunityID = :id
            """), {'id': opportunity_id, 'date': today_key, 'user_id': current_user.get_id()})
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({'error': f'Delete failed: {str(e)}'}), 500
    return jsonify({'message': 'Opportunity deleted'})


@crm_api_bp.route('/opportunities/<string:opportunity_id>/duplicate', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_CREATE')
def duplicate_opportunity(opportunity_id):
    tenant_id = _tenant_id()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.connect() as conn:
        # §DealSize
        src = conn.execute(text("""
            SELECT OpportunityName, OpportunityNameFa,
                   CustomerID, ProjectID, Value, Probability,
                   Stage, ExpectedCloseDateKey,
                   WinLossReason, WinLossReasonFa,
                   ContactPerson, ContactPersonFa,
                   ContactEmail, ContactPhone, AssignedToUserID,
                   Notes, NotesFa,
                   DealSizeValue, DealSizeUnit
            FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).fetchone()

        if not src:
            return jsonify({'error': 'Opportunity not found'}), 404

        m = src._mapping
        new_name    = 'Copy of ' + (m.get('OpportunityName') or '')
        new_name_fa = 'کپی از ' + (m.get('OpportunityNameFa') or m.get('OpportunityName') or '')
        new_opp_id = str(_uuid.uuid4()).upper()

        conn.execute(text("""
            INSERT INTO t_Opportunity (
                OpportunityID,
                OpportunityName, OpportunityNameFa,
                CustomerID, ProjectID, Value, Probability,
                Status, Stage, ExpectedCloseDateKey,
                WinLossReason, WinLossReasonFa,
                ContactPerson, ContactPersonFa,
                ContactEmail, ContactPhone, AssignedToUserID,
                Notes, NotesFa,
                DealSizeValue, DealSizeUnit,
                CreatedBy, CreatedDateKey, ModifiedBy, ModifiedDateKey,
                TenantID
            ) VALUES (
                :opp_id, :name, :name_fa,
                :customer_id, :project_id, :value, :probability,
                'Open', :stage, :close_date,
                :win_loss_reason, :win_loss_reason_fa,
                :contact_person, :contact_person_fa,
                :contact_email, :contact_phone, :assigned_to,
                :notes, :notes_fa,
                :deal_size_value, :deal_size_unit,
                :user_id, :created_date, :user_id, :created_date,
                :tenant_id
            )
        """), {
            'opp_id': new_opp_id,
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
            'deal_size_value': m.get('DealSizeValue'),
            'deal_size_unit': m.get('DealSizeUnit'),
            'user_id': current_user.get_id(),
            'created_date': today_key,
            'tenant_id': tenant_id,
        })
        conn.commit()
    return jsonify({'message': 'Opportunity duplicated', 'opportunity_id': new_opp_id}), 201


@crm_api_bp.route('/opportunities/<string:opportunity_id>/inline', methods=['PATCH'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def inline_update_opportunity(opportunity_id):
    tenant_id = _tenant_id()
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
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
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

@crm_api_bp.route('/opportunities/<string:opportunity_id>/activity', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def log_activity(opportunity_id):
    tenant_id = _tenant_id()
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    subj, subj_fa = _write_pair(data, 'subject')
    notes, notes_fa = _write_pair(data, 'notes')

    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        activity_id = str(_uuid.uuid4()).upper()
        conn.execute(text("""
            INSERT INTO t_OpportunityActivity (
                ActivityID, OpportunityID, ActivityType,
                Subject, SubjectFa, Notes, NotesFa,
                ActivityDateKey, ReminderDateKey, CreatedBy, TenantID
            ) VALUES (
                :aid, :opp_id, :type,
                :subject, :subject_fa, :notes, :notes_fa,
                :activity_date, :reminder_date, :user_id, :tenant_id
            )
        """), {
            'aid': activity_id,
            'opp_id': opportunity_id,
            'type': data.get('activity_type'),
            'subject': subj,
            'subject_fa': subj_fa,
            'notes': notes,
            'notes_fa': notes_fa,
            'activity_date': date_to_key(data.get('activity_date')),
            'reminder_date': date_to_key(data.get('reminder_date')),
            'user_id': current_user.get_id(),
            'tenant_id': tenant_id,
        })
        conn.commit()
        return jsonify({'message': 'Activity logged', 'activity_id': activity_id}), 201


@crm_api_bp.route('/opportunities/<string:opportunity_id>/activities')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_activities(opportunity_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        rows = conn.execute(text("""
            SELECT ActivityID, ActivityType, Subject, SubjectFa, Notes, NotesFa,
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

@crm_api_bp.route('/opportunities/<string:opportunity_id>/tasks', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_opportunity_tasks(opportunity_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        rows = conn.execute(text("""
            SELECT TaskID, OpportunityID, ContactID,
                   Subject, SubjectFa, Notes, NotesFa,
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
            cdk = m['CreatedDateKey']
            tasks.append({
                'task_id': m['TaskID'],
                'opportunity_id': m['OpportunityID'],
                'contact_id': m['ContactID'],
                'subject': pick_localized(m, 'Subject'),
                'notes': pick_localized(m, 'Notes'),
                'due_date_key': due,
                'due_date_formatted': format_date(int(due)) if due else '',
                'priority': m['Priority'],
                'status': m['Status'],
                'assigned_to_user_id': m['AssignedToUserID'],
                'completed_date_key': m['CompletedDateKey'],
                'completed_date_formatted': format_date(int(m['CompletedDateKey'])) if m['CompletedDateKey'] else '',
                'created_date_key': cdk,
                'created_date_formatted': format_date(int(cdk)) if cdk else '',
            })
        return jsonify(tasks)


@crm_api_bp.route('/opportunities/<string:opportunity_id>/tasks', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def create_opportunity_task(opportunity_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))
    subj, subj_fa = _write_pair(data, 'subject')
    notes, notes_fa = _write_pair(data, 'notes')

    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        task_id = str(_uuid.uuid4()).upper()
        assigned_raw = data.get('assigned_to_user_id') or current_user.get_id()
        assigned_int = _to_int_or_none(assigned_raw)
        created_by_int = _to_int_or_none(current_user.get_id())
        conn.execute(text("""
            INSERT INTO t_OpportunityTask (
                TaskID, OpportunityID, ContactID,
                Subject, SubjectFa, Notes, NotesFa,
                DueDateKey, Priority, Status, AssignedToUserID,
                TenantID, CreatedDateKey, CreatedBy
            ) VALUES (
                :tid, :opp, :cid,
                :subj, :subj_fa, :notes, :notes_fa,
                :due, :pri, 'Open', :assigned,
                :tenant_id, :created, :user
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
            'assigned': assigned_int,
            'tenant_id': tenant_id,
            'created': today_key,
            'user': created_by_int,
        })
        conn.commit()
        return jsonify({'message': 'Task created', 'task_id': task_id}), 201


@crm_api_bp.route('/tasks/<string:task_id>', methods=['PATCH'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def update_opportunity_task(task_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT t.TaskID
            FROM t_OpportunityTask t
            JOIN t_Opportunity o ON t.OpportunityID = o.OpportunityID
            WHERE t.TaskID = :tid
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'tid': task_id, 'tenant_id': tenant_id}).first()
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
@require_module('crm')
@require_permission('SALES_EDIT')
def delete_opportunity_task(task_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT t.TaskID
            FROM t_OpportunityTask t
            JOIN t_Opportunity o ON t.OpportunityID = o.OpportunityID
            WHERE t.TaskID = :tid
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'tid': task_id, 'tenant_id': tenant_id}).first()
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

@crm_api_bp.route('/opportunities/<string:opportunity_id>/contacts')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_opportunity_contacts(opportunity_id):
    tenant_id = _tenant_id()
    lang = _lang()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        rows = conn.execute(text("""
            SELECT oc.LinkID, oc.ContactID, oc.Role, oc.IsPrimary,
                   c.FirstName, c.FirstNameFa, c.LastName, c.LastNameFa,
                   c.JobTitle, c.JobTitleFa, c.Department, c.DepartmentFa,
                   c.Email, c.Mobile, c.DirectPhone, c.Extension
            FROM t_OpportunityContact oc
            JOIN t_CompanyContact   c ON oc.ContactID = c.ContactID
            WHERE oc.OpportunityID = :opp_id
              AND (c.IsActive IS NULL OR c.IsActive = '1')
            ORDER BY
                CASE WHEN oc.IsPrimary = '1' THEN 0 ELSE 1 END,
                c.FirstName
        """), {'opp_id': str(opportunity_id)}).fetchall()
        contacts = []
        for row in rows:
            m = row._mapping
            if lang != 'en':
                fa_first = (m['FirstNameFa'] or '').strip()
                fa_last  = (m['LastNameFa']  or '').strip()
                if fa_first or fa_last:
                    full_name = ' '.join(p for p in [fa_first, fa_last] if p)
                else:
                    full_name = ' '.join(p for p in [(m['FirstName'] or ''), (m['LastName'] or '')] if p)
                job_title = (m['JobTitleFa'] or '').strip() or m['JobTitle']
            else:
                full_name = ' '.join(p for p in [(m['FirstName'] or ''), (m['LastName'] or '')] if p)
                job_title = m['JobTitle']
            contacts.append({
                'link_id': m['LinkID'],
                'contact_id': m['ContactID'],
                'full_name': full_name,
                'job_title': job_title,
                'email': m['Email'],
                'mobile': m['Mobile'],
                'direct_phone': m['DirectPhone'],
                'extension': m['Extension'],
                'role': m['Role'],
                'is_primary': m['IsPrimary'],
            })
        return jsonify(contacts)


@crm_api_bp.route('/contacts/search', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def search_contacts():
    tenant_id = _tenant_id()
    q = request.args.get('q', '').strip()
    engine = get_engine()
    with engine.connect() as conn:
        base_sql = """
            SELECT TOP 50 ContactID, FirstName, FirstNameFa, LastName, LastNameFa,
                   JobTitle, JobTitleFa, CompanyID
            FROM t_CompanyContact
            WHERE IsActive = '1'
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """
        if q:
            rows = conn.execute(text(base_sql + """
                AND (FirstName LIKE :q OR FirstNameFa LIKE :q
                     OR LastName LIKE :q OR LastNameFa LIKE :q
                     OR JobTitle LIKE :q OR JobTitleFa LIKE :q)
                ORDER BY FirstName
            """), {'tenant_id': tenant_id, 'q': f'%{q}%'}).fetchall()
        else:
            rows = conn.execute(text(base_sql + " ORDER BY FirstName"),
                                {'tenant_id': tenant_id}).fetchall()
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


@crm_api_bp.route('/opportunities/<string:opportunity_id>/contacts', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def link_opportunity_contact(opportunity_id):
    tenant_id = _tenant_id()
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
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
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
        """), {'lid': link_id, 'opp': opportunity_id, 'c': contact_id,
                'role': role, 'prim': is_primary})
        conn.commit()
        return jsonify({'message': 'Contact linked', 'link_id': link_id}), 201


@crm_api_bp.route('/opportunities/contacts/<string:link_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def unlink_opportunity_contact(link_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT oc.OpportunityID
            FROM t_OpportunityContact oc
            JOIN t_Opportunity o ON oc.OpportunityID = o.OpportunityID
            WHERE oc.LinkID = :lid
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'lid': link_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Link not found'}), 404
        conn.execute(text("DELETE FROM t_OpportunityContact WHERE LinkID = :lid"),
                     {'lid': link_id})
        conn.commit()
        return jsonify({'message': 'Contact unlinked'})


# ============================================================================
# DOCUMENTS — bilingual
# ============================================================================

@crm_api_bp.route('/opportunities/<string:opportunity_id>/documents')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_opportunity_documents(opportunity_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        rows = conn.execute(text("""
            SELECT d.DocID, d.DocumentName, d.DocumentNameFa,
                   d.DocumentType, d.StorageType, d.Location,
                   d.FileSizeKB, d.Direction, d.Channel,
                   d.Notes, d.NotesFa, d.CreatedDateKey
            FROM t_ActivityDocument d
            WHERE CAST(d.OpportunityID AS NVARCHAR(50)) = :opp_id
            ORDER BY d.CreatedDateKey DESC, d.DocID DESC
        """), {'opp_id': str(opportunity_id)}).fetchall()
        docs = []
        for row in rows:
            m = row._mapping
            cdk = m['CreatedDateKey']
            docs.append({
                'doc_id': m['DocID'],
                'document_name': pick_localized(m, 'DocumentName'),
                'document_type': m['DocumentType'],
                'storage_type': m['StorageType'],
                'location': m['Location'],
                'file_size_kb': m['FileSizeKB'],
                'direction': m['Direction'],
                'channel': m['Channel'],
                'notes': pick_localized(m, 'Notes'),
                'created_date_key': cdk,
                'created_date_formatted': format_date(int(cdk)) if cdk else '',
            })
        return jsonify(docs)


@crm_api_bp.route('/opportunities/<string:opportunity_id>/documents', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def add_opportunity_document(opportunity_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))
    name, name_fa = _write_pair(data, 'document_name')
    notes, notes_fa = _write_pair(data, 'notes')
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT 1 FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).first()
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
                CreatedDateKey, CreatedBy, TenantID
            ) VALUES (
                :did, :aid, :oid,
                :name, :name_fa,
                :type, :storage, :loc, :size,
                :dir, :chan,
                :notes, :notes_fa,
                :date, :user, :tenant_id
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
            'tenant_id': tenant_id,
        })
        conn.commit()
        return jsonify({'message': 'Document attached', 'doc_id': new_doc_id}), 201


@crm_api_bp.route('/documents/<int:doc_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def delete_activity_document(doc_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT d.DocID
            FROM t_ActivityDocument d
            JOIN t_Opportunity o ON d.OpportunityID = o.OpportunityID
            WHERE d.DocID = :did
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'did': doc_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Document not found'}), 404
        conn.execute(text("DELETE FROM t_ActivityDocument WHERE DocID = :did"),
                     {'did': doc_id})
        conn.commit()
        return jsonify({'message': 'Document removed'})


# ============================================================================
# STAGES / USERS / CUSTOMERS
# ============================================================================

@crm_api_bp.route('/stages')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_stages():
    stages = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation',
              'Closed Won', 'Closed Lost']
    return jsonify(stages)


@crm_api_bp.route('/users')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_crm_users():
    industry = current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = users_for_current_industry(conn, industry)
        users = [{'user_id': row[0], 'full_name': row[1], 'username': row[2]} for row in rows]
        return jsonify(users)


@crm_api_bp.route('/customers')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_crm_customers():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT companyID, Name, NameFa
            FROM t_companies
            WHERE IsCustomer = '1'
              AND (TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY Name
        """), {'tenant_id': tenant_id}).fetchall()
        customers = []
        for row in rows:
            m = row._mapping
            customers.append({'customer_id': m['companyID'],
                             'name': pick_localized(m, 'Name')})
        return jsonify(customers)


@crm_api_bp.route('/all-customers')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_all_customers():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.companyID, c.Name, c.NameFa, c.Telephone, c.IsCustomer,
                   c.RahkaranPartyID,
                   COUNT(DISTINCT o.OpportunityID) as opportunity_count,
                   COALESCE(SUM(CASE WHEN o.Status = 'Open' THEN TRY_CAST(o.Value AS DECIMAL(18,2)) ELSE 0 END), 0) as open_pipeline_value,
                   COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0) as total_revenue
            FROM t_companies c
            LEFT JOIN t_Opportunity o ON c.companyID = o.CustomerID
            LEFT JOIN t_SalesOrder so ON c.companyID = so.CustomerID AND so.Status = 'Confirmed'
            WHERE c.IsCustomer = '1'
              AND (c.TenantID = :tenant_id OR :tenant_id = 0)
            GROUP BY c.companyID, c.Name, c.NameFa, c.Telephone, c.IsCustomer, c.RahkaranPartyID
            ORDER BY c.Name
        """), {'tenant_id': tenant_id}).fetchall()
        customers = []
        for row in rows:
            m = row._mapping
            customers.append({
                'customer_id': m['companyID'],
                'name': pick_localized(m, 'Name'),
                'telephone': m['Telephone'],
                'is_customer': m['IsCustomer'],
                'rahkaran_party_id': m['RahkaranPartyID'],
                'opportunity_count': m['opportunity_count'] or 0,
                'open_pipeline_value': float(m['open_pipeline_value'] or 0),
                'total_revenue': float(m['total_revenue'] or 0),
            })
        return jsonify(customers)


@crm_api_bp.route('/customers/<string:customer_id>')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_customer_detail(customer_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        customer = conn.execute(text("""
            SELECT companyID, Name, NameFa, Address, AddressFa,
                   Telephone, IsCustomer, IsSupplier
            FROM t_companies
            WHERE companyID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': customer_id, 'tenant_id': tenant_id}).fetchone()
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404
        c = customer._mapping
        opportunities = conn.execute(text("""
            SELECT OpportunityID, OpportunityName, OpportunityNameFa,
                   Value, Probability, Stage, Status, ExpectedCloseDateKey
            FROM t_Opportunity
            WHERE CustomerID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
            ORDER BY CreatedDateKey DESC
        """), {'id': customer_id, 'tenant_id': tenant_id}).fetchall()
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
            'name': pick_localized(c, 'Name'),
            'address': pick_localized(c, 'Address'),
            'telephone': c['Telephone'],
            'is_customer': c['IsCustomer'],
            'is_supplier': c['IsSupplier'],
            'opportunities': opp_list,
        })


# ============================================================================
# ACTIVITY LOG (all)
# ============================================================================

@crm_api_bp.route('/all-activities')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_all_activities():
    tenant_id = _tenant_id()
    limit = request.args.get('limit', 50)
    activity_type = request.args.get('activity_type', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    engine = get_engine()
    with engine.connect() as conn:
        conditions = [
            "(o.TenantID = :tenant_id OR :tenant_id = 0)",
            "(o.IsActive IS NULL OR o.IsActive = '1')",
        ]
        params = {'tenant_id': tenant_id}
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
                a.Subject, a.SubjectFa, a.Notes, a.NotesFa,
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
                'notes': pick_localized(m, 'Notes'),
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
@require_module('crm')
@require_permission('SALES_VIEW')
def list_quotes():
    tenant_id = _tenant_id()
    opportunity_id = request.args.get('opportunity_id')
    customer_id = request.args.get('customer_id')
    status = request.args.get('status', '')
    engine = get_engine()
    with engine.connect() as conn:
        conditions = ["(q.TenantID = :tenant_id OR :tenant_id = 0)"]
        params = {'tenant_id': tenant_id}
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
                   q.Revision, q.Status, q.Subject, q.SubjectFa,
                   q.ValidUntilDateKey, q.TotalAmount, q.Currency,
                   q.TaxRate, q.TaxAmount, q.TotalWithTax,
                   q.Notes, q.NotesFa, q.ParentQuoteID,
                   o.OpportunityName, o.OpportunityNameFa,
                   c.Name AS CustomerName, c.NameFa AS CustomerNameFa,
                   u.FullName AS CreatedByName, u.FullNameLocal AS CreatedByNameFa,
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

            def _to_int(v):
                if v is None or v == '': return None
                try: return int(v)
                except (TypeError, ValueError):
                    try: return int(float(v))
                    except (TypeError, ValueError): return None

            def _safe_format(v):
                k = _to_int(v)
                if not k: return ''
                try: return format_date(k)
                except Exception: return ''

            quotes.append({
                'quote_id': m['QuoteID'],
                'quote_number': m['QuoteNumber'],
                'opportunity_id': m['OpportunityID'],
                'opportunity_name': pick_localized(m, 'OpportunityName'),
                'customer_id': m['CustomerID'],
                'customer_name': pick_localized(m, 'CustomerName'),
                'revision': m['Revision'],
                'status': m['Status'],
                'subject': pick_localized(m, 'Subject'),
                'valid_until_date_key': _to_int(vu),
                'valid_until_date_formatted': _safe_format(vu),
                'total_amount': float(m['TotalAmount']) if m['TotalAmount'] else 0,
                'currency': m['Currency'] or 'USD',
                'tax_rate': float(m['TaxRate']) if m['TaxRate'] is not None else 0,
                'tax_amount': float(m['TaxAmount']) if m['TaxAmount'] is not None else 0,
                'total_with_tax': float(m['TotalWithTax']) if m['TotalWithTax'] is not None else 0,
                'notes': pick_localized(m, 'Notes'),
                'parent_quote_id': m['ParentQuoteID'],
                'created_by_name': pick_localized(m, 'CreatedByName'),
                'created_date_key': _to_int(cd),
                'created_date_formatted': _safe_format(cd),
            })
        return jsonify(quotes)


@crm_api_bp.route('/quotes/<string:quote_id>')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_quote(quote_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        quote_row = conn.execute(text("""
            SELECT q.QuoteID, q.QuoteNumber, q.OpportunityID, q.CustomerID,
                   q.Revision, q.Status, q.Subject, q.SubjectFa,
                   q.ValidUntilDateKey, q.TotalAmount,
                   q.Currency, q.TaxRate, q.TaxAmount, q.TotalWithTax,
                   q.Notes, q.NotesFa, q.ParentQuoteID,
                   q.CreatedDateKey, q.ModifiedDateKey,
                   o.OpportunityName, o.OpportunityNameFa,
                   c.Name, c.NameFa
            FROM t_Quote q
            LEFT JOIN t_Opportunity o ON q.OpportunityID = o.OpportunityID
            LEFT JOIN t_companies c ON q.CustomerID = c.companyID
            WHERE q.QuoteID = :id
              AND (q.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': quote_id, 'tenant_id': tenant_id}).fetchone()
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
                   ql.Quantity, ql.UnitPrice, ql.TotalPrice, ql.Notes, ql.Currency
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
                'product_name': (
                    lm['ProductNameFa']
                    if _lang() != 'en' and lm.get('ProductNameFa') and str(lm['ProductNameFa']).strip()
                    else lm['ProductName']
                ),
                'quantity': float(lm['Quantity']) if lm['Quantity'] else 0,
                'unit_price': float(lm['UnitPrice']) if lm['UnitPrice'] else 0,
                'total_price': float(lm['TotalPrice']) if lm['TotalPrice'] else 0,
                'notes': lm['Notes'],
                'currency': lm['Currency'] or 'USD',
            })
        return jsonify({
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
            'currency': m['Currency'] or 'USD',
            'tax_rate': float(m['TaxRate']) if m['TaxRate'] is not None else 0,
            'tax_amount': float(m['TaxAmount']) if m['TaxAmount'] is not None else 0,
            'total_with_tax': float(m['TotalWithTax']) if m['TotalWithTax'] is not None else 0,
            'notes': pick_localized(m, 'Notes'),
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
@require_module('crm')
@require_permission('SALES_VIEW')
def get_company_phones(company_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT 1 FROM t_companies
            WHERE companyID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': company_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Company not found'}), 404
        rows = conn.execute(text("""
            SELECT PhoneID, PhoneNumber, PhoneRangeEnd, PhoneType,
                   Extension, IsPrimary, Label, LabelFa, Notes, IsActive
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
                'phone_id': m['PhoneID'],
                'phone_number': m['PhoneNumber'],
                'phone_range_end': m['PhoneRangeEnd'],
                'phone_type': m['PhoneType'],
                'extension': m['Extension'],
                'is_primary': m['IsPrimary'],
                'label': pick_localized(m, 'Label'),
                'notes': m['Notes'],
                'is_active': m['IsActive'],
            })
        return jsonify(phones)


@crm_api_bp.route('/contacts/<string:contact_id>/channels', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_contact_channels(contact_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT 1 FROM t_CompanyContact
            WHERE ContactID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': contact_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Contact not found'}), 404
        rows = conn.execute(text("""
            SELECT ChannelID, ChannelType, Value, Extension,
                   IsPrimary, IsPreferred, Label, LabelFa, IsActive
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
                'channel_id': m['ChannelID'],
                'channel_type': m['ChannelType'],
                'value': m['Value'],
                'extension': m['Extension'],
                'is_primary': m['IsPrimary'],
                'is_preferred': m['IsPreferred'],
                'label': pick_localized(m, 'Label'),
                'is_active': m['IsActive'],
            })
        return jsonify(channels)


# ============================================================================
# SALES DASHBOARD
# ============================================================================

@crm_api_bp.route('/dashboard/stats')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_stats():
    tenant_id = _tenant_id()
    project_id = request.args.get('project_id')
    if not project_id:
        cp = session.get('current_project_id', 'All')
        if cp != 'All':
            project_id = cp
    engine = get_engine()
    with engine.connect() as conn:
        cond = [
            "(TenantID = :tenant_id OR :tenant_id = 0)",
            "(IsActive IS NULL OR IsActive = '1')",
        ]
        params = {'tenant_id': tenant_id}
        if project_id and project_id != 'All':
            cond.append("(ProjectID = :proj OR ProjectID IS NULL)")
            params['proj'] = project_id
        opp_where = " AND ".join(cond)
        open_row = conn.execute(text(f"""
            SELECT
                COALESCE(SUM(TRY_CAST(Value AS DECIMAL(18,2))), 0) AS pipeline_value,
                COALESCE(SUM(TRY_CAST(Value AS DECIMAL(18,2)) * TRY_CAST(Probability AS INT) / 100.0), 0) AS weighted_pipeline,
                COUNT(*) AS open_count
            FROM t_Opportunity
            WHERE {opp_where}
              AND Status = 'Open'
        """), params).fetchone()
        pipeline_value    = float(open_row[0] or 0)
        weighted_pipeline = float(open_row[1] or 0)
        open_count        = int(open_row[2] or 0)
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
        try:
            rev_row = conn.execute(text(f"""
                SELECT COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0)
                FROM t_SalesOrder so
                WHERE so.Status = 'Confirmed'
                  AND (so.TenantID = :tenant_id OR :tenant_id = 0)
            """), {'tenant_id': tenant_id}).fetchone()
            total_revenue = float(rev_row[0] or 0)
        except Exception as e:
            print(f"⚠️ total_revenue query failed: {e}")
            total_revenue = 0.0
        return jsonify({
            'pipeline_value': round(pipeline_value, 2),
            'weighted_pipeline': round(weighted_pipeline, 2),
            'win_rate': win_rate,
            'open_opportunities': open_count,
            'total_revenue': round(total_revenue, 2),
            'won_count': won_count,
            'lost_count': lost_count,
        })


@crm_api_bp.route('/dashboard/charts')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_charts():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        stage_rows = conn.execute(text("""
            SELECT Stage, COALESCE(SUM(TRY_CAST(Value AS DECIMAL(18,2))), 0) AS stage_value
            FROM t_Opportunity
            WHERE (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
              AND Status = 'Open'
            GROUP BY Stage
        """), {'tenant_id': tenant_id}).fetchall()
        stage_order = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation', 'Closed Won', 'Closed Lost']
        stage_map = {r[0]: float(r[1] or 0) for r in stage_rows}
        stages = [s for s in stage_order if s in stage_map]
        stage_values = [stage_map[s] for s in stages]
        trend_rows = []
        try:
            trend_rows = conn.execute(text("""
                SELECT
                    (YEAR(GETDATE()) * 100 + MONTH(GETDATE())) AS ym,
                    COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0) AS revenue
                FROM t_SalesOrder so
                WHERE so.Status = 'Confirmed'
                  AND (so.TenantID = :tenant_id OR :tenant_id = 0)
                  AND so.OrderDateKey >= (
                      YEAR(DATEADD(MONTH, -5, GETDATE())) * 10000
                    + MONTH(DATEADD(MONTH, -5, GETDATE())) * 100
                    + 1
                  )
                GROUP BY (YEAR(GETDATE()) * 100 + MONTH(GETDATE()))
                ORDER BY ym
            """), {'tenant_id': tenant_id}).fetchall()
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
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_top_customers():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = []
        try:
            rows = conn.execute(text("""
                SELECT TOP 5 c.Name AS CustomerName,
                    COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2)) * CAST(so.Unitprice AS DECIMAL(18,2))), 0) AS revenue
                FROM t_SalesOrder so
                JOIN t_companies c ON so.CustomerID = c.companyID
                WHERE so.Status = 'Confirmed'
                  AND (so.TenantID = :tenant_id OR :tenant_id = 0)
                GROUP BY c.Name
                ORDER BY revenue DESC
            """), {'tenant_id': tenant_id}).fetchall()
        except Exception as e:
            print(f"⚠️ top-customers query failed: {e}")
        customers = [r[0] for r in rows]
        revenues  = [float(r[1] or 0) for r in rows]
        return jsonify({'customers': customers, 'revenues': revenues})


@crm_api_bp.route('/dashboard/top-products')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_top_products():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = []
        try:
            rows = conn.execute(text("""
                SELECT TOP 5 p.descEnglish AS ProductName,
                    COALESCE(SUM(CAST(so.Qty AS DECIMAL(18,2))), 0) AS qty
                FROM t_SalesOrder so
                JOIN t_Product p ON so.ProductID = p.ProductId
                WHERE so.Status = 'Confirmed'
                  AND (so.TenantID = :tenant_id OR :tenant_id = 0)
                GROUP BY p.descEnglish
                ORDER BY qty DESC
            """), {'tenant_id': tenant_id}).fetchall()
        except Exception as e:
            print(f"⚠️ top-products query failed: {e}")
        products   = [r[0] for r in rows]
        quantities = [float(r[1] or 0) for r in rows]
        return jsonify({'products': products, 'quantities': quantities})


@crm_api_bp.route('/forecast')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_forecast():
    tenant_id = _tenant_id()
    today_key = int(datetime.now().strftime('%Y%m%d'))
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
                    THEN TRY_CAST(Value AS DECIMAL(18,2)) * TRY_CAST(Probability AS INT) / 100.0
                    ELSE 0 END), 0) AS f30,
                COALESCE(SUM(CASE
                    WHEN CAST(ExpectedCloseDateKey AS INT) BETWEEN :today AND :d60
                    THEN TRY_CAST(Value AS DECIMAL(18,2)) * TRY_CAST(Probability AS INT) / 100.0
                    ELSE 0 END), 0) AS f60,
                COALESCE(SUM(CASE
                    WHEN CAST(ExpectedCloseDateKey AS INT) BETWEEN :today AND :d90
                    THEN TRY_CAST(Value AS DECIMAL(18,2)) * TRY_CAST(Probability AS INT) / 100.0
                    ELSE 0 END), 0) AS f90
            FROM t_Opportunity
            WHERE (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
              AND Status = 'Open'
              AND ExpectedCloseDateKey IS NOT NULL
              AND ExpectedCloseDateKey <> ''
        """), {
            'tenant_id': tenant_id,
            'today': today_key, 'd30': d30, 'd60': d60, 'd90': d90,
        }).fetchone()
        return jsonify({
            'next_30_days': {'total': float(row[0] or 0)},
            'next_60_days': {'total': float(row[1] or 0)},
            'next_90_days': {'total': float(row[2] or 0)},
        })


# ============================================================================
# §DealSize — DASHBOARD: FUNNEL (group by stage + unit)
# ============================================================================

@crm_api_bp.route('/dashboard/funnel')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_funnel():
    """Pipeline funnel — one row per stage. Groups by DealSizeUnit so a
    mixed-unit tenant sees '500 kW + 3 tons', not a corrupted sum."""
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT Stage, DealSizeUnit,
                   COUNT(*) AS deal_count,
                   COALESCE(SUM(TRY_CAST(Value AS DECIMAL(18,2))), 0) AS total_value,
                   COALESCE(SUM(DealSizeValue), 0) AS total_size
            FROM t_Opportunity
            WHERE (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
              AND Status = 'Open'
            GROUP BY Stage, DealSizeUnit
            ORDER BY Stage, DealSizeUnit
        """), {'tenant_id': tenant_id}).fetchall()

        stage_order = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation']
        stage_map = {}
        for r in rows:
            stage = r[0] or ''
            unit  = r[1] or ''
            n     = int(r[2] or 0)
            value = float(r[3] or 0)
            size  = float(r[4] or 0)
            if stage not in stage_map:
                stage_map[stage] = {
                    'stage': stage, 'count': 0, 'value': 0.0,
                    'size_by_unit': {},
                }
            stage_map[stage]['count'] += n
            stage_map[stage]['value'] += value
            if size > 0:
                stage_map[stage]['size_by_unit'][unit] = (
                    stage_map[stage]['size_by_unit'].get(unit, 0.0) + size
                )

        stages = []
        for stage in stage_order:
            info = stage_map.get(stage, {'stage': stage, 'count': 0, 'value': 0.0, 'size_by_unit': {}})
            parts = []
            for unit, total in sorted(info['size_by_unit'].items(),
                                      key=lambda kv: kv[0] or 'zzz'):
                if total > 0:
                    parts.append(f"{total:g} {unit}".strip())
            stages.append({
                'stage': stage,
                'count': info['count'],
                'value': round(info['value'], 2),
                'size_display': ' + '.join(parts) if parts else '—',
            })
        return jsonify({'stages': stages})


# ============================================================================
# §DealSize — DASHBOARD: TOP PROJECTS BY DEAL SIZE
# ============================================================================

@crm_api_bp.route('/dashboard/top-projects-by-size')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def dashboard_top_projects_by_size():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TOP 5
                o.OpportunityID, o.OpportunityName, o.OpportunityNameFa,
                o.DealSizeValue, o.DealSizeUnit, o.Value,
                c.Name AS CustomerName, c.NameFa AS CustomerNameFa
            FROM t_Opportunity o
            LEFT JOIN t_companies c ON o.CustomerID = c.companyID
            WHERE (o.TenantID = :tenant_id OR :tenant_id = 0)
              AND (o.IsActive IS NULL OR o.IsActive = '1')
              AND o.Status = 'Open'
              AND o.DealSizeValue IS NOT NULL
              AND o.DealSizeValue > 0
            ORDER BY o.DealSizeValue DESC
        """), {'tenant_id': tenant_id}).fetchall()
        items = []
        for r in rows:
            m = r._mapping
            size = float(m['DealSizeValue'] or 0)
            unit = m['DealSizeUnit'] or ''
            items.append({
                'opportunity_id': m['OpportunityID'],
                'name':           pick_localized(m, 'OpportunityName'),
                'customer_name':  pick_localized(m, 'CustomerName'),
                'deal_size_value': size,
                'deal_size_unit':  unit,
                'deal_size_display': f"{size:g} {unit}".strip(),
                'value':          float(m['Value'] or 0),
            })
        return jsonify({'projects': items})


# ============================================================================
# §DealSize — TENANT DEFAULTS
# ============================================================================

@crm_api_bp.route('/tenant-defaults')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def tenant_defaults():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        try:
            row = conn.execute(text("""
                SELECT DefaultDealSizeUnit FROM t_Tenants
                WHERE TenantID = :tid
            """), {'tid': tenant_id}).fetchone()
            unit = row[0] if row and row[0] else None
        except Exception as e:
            print(f"⚠️ tenant-defaults lookup failed: {e}")
            unit = None
        return jsonify({
            'tenant_id': tenant_id,
            'default_deal_size_unit': unit,
        })


# ============================================================================
# CUSTOMER PRICE OVERRIDES — LIST
# ============================================================================

@crm_api_bp.route('/customer-pricing')
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def list_customer_price_overrides():
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT o.OverrideID, o.CustomerID,
                   c.Name AS CustomerName, c.NameFa AS CustomerNameFa,
                   o.ProductID,
                   p.descEnglish AS ProductName,
                   p.ProductNameLocal AS ProductNameFa,
                   o.OverridePrice, o.Currency, o.MinOrderQty,
                   o.ValidFromDateKey, o.ValidToDateKey,
                   o.Reason, o.ReasonLocal AS ReasonFa,
                   o.IsActive, o.CreatedDateKey
            FROM t_CustomerPriceOverride o
            LEFT JOIN t_companies c ON o.CustomerID = c.companyID
            LEFT JOIN t_Product   p ON o.ProductID  = p.ProductId
            WHERE (o.TenantID = :tenant_id OR :tenant_id = 0)
              AND (o.IsActive IS NULL OR o.IsActive = '1')
            ORDER BY o.CreatedDateKey DESC, o.OverrideID DESC
        """), {'tenant_id': tenant_id}).fetchall()
        overrides = []
        for row in rows:
            m = row._mapping

            def _to_int(v):
                if v is None or v == '': return None
                try: return int(v)
                except (TypeError, ValueError):
                    try: return int(float(v))
                    except (TypeError, ValueError): return None

            def _safe_format(v):
                k = _to_int(v)
                if not k: return ''
                try: return format_date(k)
                except Exception: return ''

            currency = m['Currency'] or 'USD'
            override_price = m['OverridePrice']
            try:
                override_price = float(override_price) if override_price is not None else 0.0
            except (TypeError, ValueError):
                override_price = 0.0

            overrides.append({
                'override_id': m['OverrideID'],
                'customer_id': m['CustomerID'],
                'customer_name': pick_localized(m, 'CustomerName'),
                'product_id': m['ProductID'],
                'product_name': pick_localized(m, 'ProductName'),
                'override_price': override_price,
                'currency': currency,
                'min_order_qty': _to_int(m['MinOrderQty']) or 1,
                'valid_from_date_key': _to_int(m['ValidFromDateKey']),
                'valid_from_date_formatted': _safe_format(m['ValidFromDateKey']),
                'valid_to_date_key': _to_int(m['ValidToDateKey']),
                'valid_to_date_formatted': _safe_format(m['ValidToDateKey']),
                'reason': pick_localized(m, 'Reason'),
                'created_date_key': _to_int(m['CreatedDateKey']),
                'created_date_formatted': _safe_format(m['CreatedDateKey']),
            })
        return jsonify(overrides)


# ============================================================================
# CUSTOMERS — CREATE / UPDATE / DELETE / PHONES
# ============================================================================

import uuid as _uuid

_COMPANY_OPTIONAL_COLS = None


def _get_company_columns(conn):
    global _COMPANY_OPTIONAL_COLS
    if _COMPANY_OPTIONAL_COLS is not None:
        return _COMPANY_OPTIONAL_COLS
    try:
        rows = conn.execute(text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 't_companies'
        """)).fetchall()
        _COMPANY_OPTIONAL_COLS = {r[0] for r in rows}
    except Exception as e:
        print(f"⚠️ t_companies column introspection failed: {e}")
        _COMPANY_OPTIONAL_COLS = set()
    return _COMPANY_OPTIONAL_COLS


def _new_guid():
    return str(_uuid.uuid4()).upper()


def _next_phone_seq(conn):
    return _new_guid()


def _insert_customer(conn, data, tenant_id):
    cols = _get_company_columns(conn)
    missing = []
    company_id = _new_guid()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    user_id_str = current_user.get_id()
    try:
        user_id_int = int(user_id_str)
    except (TypeError, ValueError):
        user_id_int = None
    name, name_fa = _write_pair(data, 'name')
    address, address_fa = _write_pair(data, 'address')
    if not name and not name_fa:
        raise ValueError('Customer name is required')
    insert_cols = ['companyID', 'Name', 'NameFa', 'Address', 'AddressFa',
                   'Telephone', 'Role', 'IsCustomer', 'IsSupplier', 'TenantID']
    insert_vals = {
        'companyID': company_id, 'Name': name, 'NameFa': name_fa,
        'Address': address, 'AddressFa': address_fa,
        'Telephone': data.get('telephone'), 'Role': data.get('role'),
        'IsCustomer': '1',
        'IsSupplier': '1' if str(data.get('is_supplier', '')).strip() in ('1', 'true', 'yes', 'on') else '0',
        'TenantID': tenant_id,
    }
    optional_map = {
        'Website': data.get('website'), 'Email': data.get('email'),
        'CreatedBy': user_id_int, 'CreatedDateKey': today_key,
        'ModifiedBy': user_id_int, 'ModifiedDateKey': today_key,
    }
    for col, val in optional_map.items():
        if col in cols:
            insert_cols.append(col)
            insert_vals[col] = val
        else:
            missing.append(col)
    sql = ("INSERT INTO t_companies ("
           + ", ".join(insert_cols) + ") VALUES ("
           + ", ".join(':' + c for c in insert_cols) + ")")
    conn.execute(text(sql), insert_vals)
    return company_id, missing


def _insert_phones(conn, company_id, phones):
    if not phones:
        return 0
    count = 0
    today_key = int(datetime.now().strftime('%Y%m%d'))
    user_id_str = current_user.get_id()
    try:
        user_id_int = int(user_id_str)
    except (TypeError, ValueError):
        user_id_int = None
    try:
        cols = {r[0] for r in conn.execute(text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 't_CompanyPhone'
        """)).fetchall()}
    except Exception:
        cols = set()
    for p in phones:
        if not p or not p.get('value'):
            continue
        payload = {
            'PhoneID': _new_guid(), 'CompanyID': company_id,
            'PhoneNumber': str(p.get('value')).strip(),
            'PhoneRangeEnd': p.get('range_end'),
            'PhoneType': p.get('type') or 'Main',
            'Extension': p.get('extension'),
            'IsPrimary': '1' if str(p.get('is_primary', '')).strip() in ('1', 'true', 'yes') else '0',
            'Label': p.get('label'), 'IsActive': '1',
            'CreatedDateKey': today_key, 'CreatedBy': user_id_int,
        }
        if 'LabelFa' in cols:
            payload['LabelFa'] = p.get('label_fa')
        use_cols = [c for c in payload.keys() if c in cols or c in
                    ('PhoneID', 'CompanyID', 'PhoneNumber')]
        sql = ("INSERT INTO t_CompanyPhone ("
               + ", ".join(use_cols) + ") VALUES ("
               + ", ".join(':' + c for c in use_cols) + ")")
        conn.execute(text(sql), {c: payload[c] for c in use_cols})
        count += 1
    return count


@crm_api_bp.route('/customers', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_CREATE')
def create_crm_customer():
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    phones = data.get('phones') or []
    engine = get_engine()
    try:
        with engine.connect() as conn:
            company_id, missing = _insert_customer(conn, data, tenant_id)
            phones_added = _insert_phones(conn, company_id, phones)
            conn.commit()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Create failed: {e}'}), 500
    resp = {'message': 'Customer created',
            'customer_id': company_id, 'phones_added': phones_added}
    if missing:
        resp['warnings'] = [f'Column not in DB, value skipped: {c}' for c in missing]
    return jsonify(resp), 201


@crm_api_bp.route('/customers/<string:customer_id>', methods=['PUT'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def update_crm_customer(customer_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_companies
            WHERE companyID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': customer_id, 'tenant_id': tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Customer not found'}), 404
        cols = _get_company_columns(conn)
        today_key = int(datetime.now().strftime('%Y%m%d'))
        try:
            uid = int(current_user.get_id())
        except (TypeError, ValueError):
            uid = None
        name, name_fa = _write_pair(data, 'name')
        address, address_fa = _write_pair(data, 'address')
        updates = []
        params = {'id': customer_id}
        missing = []
        def _put(field, value, always=False):
            if field in cols or always:
                updates.append(f"{field} = :{field}")
                params[field] = value
            else:
                missing.append(field)
        _put('Name', name, always=True)
        _put('NameFa', name_fa, always=True)
        _put('Address', address, always=True)
        _put('AddressFa', address_fa, always=True)
        _put('Telephone', data.get('telephone'), always=True)
        _put('Role', data.get('role'), always=True)
        _put('IsSupplier',
             '1' if str(data.get('is_supplier', '')).strip() in ('1', 'true', 'yes', 'on') else '0',
             always=True)
        _put('Website', data.get('website'))
        _put('Email', data.get('email'))
        _put('ModifiedBy', uid)
        _put('ModifiedDateKey', today_key)
        if not updates:
            return jsonify({'error': 'No fields to update'}), 400
        conn.execute(text("UPDATE t_companies SET " + ", ".join(updates)
                          + " WHERE companyID = :id"), params)
        conn.commit()
    resp = {'message': 'Customer updated'}
    if missing:
        resp['warnings'] = [f'Column not in DB, value skipped: {c}' for c in missing]
    return jsonify(resp)


@crm_api_bp.route('/customers/<string:customer_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_DELETE')
def delete_crm_customer(customer_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_companies
            WHERE companyID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': customer_id, 'tenant_id': tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Customer not found'}), 404
        opp_count = conn.execute(text("""
            SELECT COUNT(*) FROM t_Opportunity
            WHERE CustomerID = :id
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': customer_id}).scalar() or 0
        if opp_count > 0:
            return jsonify({
                'error': f'Cannot delete: customer has {opp_count} opportunity(ies). Archive or reassign them first.',
                'opportunity_count': int(opp_count),
            }), 409
        try:
            conn.execute(text("DELETE FROM t_CompanyPhone WHERE CompanyID = :id"),
                         {'id': customer_id})
        except Exception as e:
            print(f"⚠️ phone cascade warning: {e}")
        conn.execute(text("DELETE FROM t_companies WHERE companyID = :id"),
                     {'id': customer_id})
        conn.commit()
    return jsonify({'message': 'Customer deleted'})


@crm_api_bp.route('/customers/<string:customer_id>/full', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def get_crm_customer_full(customer_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT companyID, Name, NameFa, Address, AddressFa,
                   Telephone, Role, IsCustomer, IsSupplier, TenantID,
                   Website, Email, CreatedDateKey, ModifiedDateKey,
                   RahkaranPartyID
            FROM t_companies
            WHERE companyID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': customer_id, 'tenant_id': tenant_id}).fetchone()
        if not row:
            return jsonify({'error': 'Customer not found'}), 404
        m = row._mapping
        phones = []
        try:
            prow = conn.execute(text("""
                SELECT PhoneID, PhoneNumber, PhoneRangeEnd, PhoneType,
                       Extension, IsPrimary, Label, IsActive
                FROM t_CompanyPhone
                WHERE CompanyID = :id
                ORDER BY CASE WHEN IsPrimary='1' THEN 0 ELSE 1 END, PhoneType
            """), {'id': customer_id}).fetchall()
            for p in prow:
                pm = p._mapping
                phones.append({
                    'phone_id': pm['PhoneID'],
                    'value': pm['PhoneNumber'],
                    'range_end': pm['PhoneRangeEnd'],
                    'type': pm['PhoneType'],
                    'extension': pm['Extension'],
                    'is_primary': pm['IsPrimary'],
                    'label': pm['Label'],
                    'is_active': pm['IsActive'],
                })
        except Exception as e:
            print(f"⚠️ phones load warning: {e}")
        cdk = m.get('CreatedDateKey')
        mdk = m.get('ModifiedDateKey')
        return jsonify({
            'customer_id': m['companyID'],
            'name': pick_localized(m, 'Name'),
            'name_fa': m.get('NameFa'),
            'address': pick_localized(m, 'Address'),
            'address_fa': m.get('AddressFa'),
            'telephone': m['Telephone'],
            'role': m['Role'],
            'is_customer': m['IsCustomer'],
            'is_supplier': m['IsSupplier'],
            'website': m.get('Website'),
            'email': m.get('Email'),
            'rahkaran_party_id': m.get('RahkaranPartyID'),
            'created_date_key': cdk,
            'created_date_formatted': format_date(int(cdk)) if cdk else '',
            'modified_date_key': mdk,
            'modified_date_formatted': format_date(int(mdk)) if mdk else '',
            'phones': phones,
        })


@crm_api_bp.route('/customers/<string:customer_id>/phones', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def add_crm_customer_phone(customer_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    if not data.get('value'):
        return jsonify({'error': 'value is required'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_companies
            WHERE companyID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': customer_id, 'tenant_id': tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Customer not found'}), 404
        n = _insert_phones(conn, customer_id, [data])
        conn.commit()
    if n == 0:
        return jsonify({'error': 'Phone not inserted'}), 500
    return jsonify({'message': 'Phone added'}), 201


@crm_api_bp.route('/companies/<string:company_id>/phones', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def add_company_phone(company_id):
    return add_crm_customer_phone(company_id)


@crm_api_bp.route('/phones/<string:phone_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def delete_crm_phone(phone_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT p.PhoneID
            FROM t_CompanyPhone p
            JOIN t_companies c ON p.CompanyID = c.companyID
            WHERE p.PhoneID = :id
              AND (c.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': phone_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Phone not found'}), 404
        conn.execute(text("DELETE FROM t_CompanyPhone WHERE PhoneID = :id"),
                     {'id': phone_id})
        conn.commit()
    return jsonify({'message': 'Phone deleted'})


# ============================================================================
# OPPORTUNITY PRODUCTS (CPQ)
# ============================================================================

def _resolve_price(conn, product_id, customer_id, date_key, currency=None, tenant_id=None):
    if not product_id:
        return {'source': 'none', 'price': None, 'currency': None}
    if not date_key:
        from datetime import datetime as _dt
        date_key = int(_dt.now().strftime('%Y%m%d'))
    cur_filter = " AND (Currency = :cur OR Currency IS NULL)" if currency else ""
    cur_param = {'cur': currency} if currency else {}
    tenant_filter = " AND (TenantID = :tenant_id OR :tenant_id = 0)" if tenant_id is not None else ""
    tenant_param = {'tenant_id': tenant_id} if tenant_id is not None else {}
    if customer_id:
        try:
            row = conn.execute(text(f"""
                SELECT TOP 1
                       CAST(OverridePrice AS DECIMAL(18,4)) AS Price, Currency
                FROM t_CustomerPriceOverride
                WHERE CustomerID  = :cid
                  AND ProductID   = :pid
                  AND (IsActive IS NULL OR IsActive = '1')
                  AND (ValidFromDateKey IS NULL OR ValidFromDateKey <= :d)
                  AND (ValidToDateKey   IS NULL OR ValidToDateKey   >= :d)
                  AND OverridePrice IS NOT NULL
                  {cur_filter}
                  {tenant_filter}
                ORDER BY ValidFromDateKey DESC, CreatedDateKey DESC
            """), {'cid': customer_id, 'pid': product_id, 'd': date_key, **cur_param, **tenant_param}).fetchone()
            if row and row[0] is not None:
                return {'source': 'override', 'price': float(row[0]),
                        'currency': row[1] or currency or 'USD'}
        except Exception as e:
            print(f"⚠️ price override lookup failed: {e}")
    for price_type, source in (('Standard', 'standard'), ('List', 'list')):
        try:
            row = conn.execute(text(f"""
                SELECT TOP 1
                       CAST(Price AS DECIMAL(18,4)) AS Price, Currency
                FROM t_ProductPriceHistory
                WHERE ProductID = :pid
                  AND PriceType = :pt
                  AND EffectiveDateKey <= :d
                  AND Price IS NOT NULL
                  {cur_filter}
                ORDER BY EffectiveDateKey DESC
            """), {'pid': product_id, 'pt': price_type, 'd': date_key, **cur_param}).fetchone()
            if row and row[0] is not None:
                return {'source': source, 'price': float(row[0]),
                        'currency': row[1] or currency or 'USD'}
        except Exception as e:
            print(f"⚠️ price history lookup failed ({price_type}): {e}")
    return {'source': 'none', 'price': None, 'currency': currency}


@crm_api_bp.route('/opportunities/<string:opportunity_id>/products', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def list_opportunity_products(opportunity_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT CustomerID FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).fetchone()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        customer_id = parent[0]
        rows = conn.execute(text("""
            SELECT op.LinkID, op.OpportunityID, op.ProductID,
                   op.Quantity, op.Currency,
                   op.EstimatedUnitPrice, op.OverridePrice,
                   op.OverrideReason, op.OverrideReasonFa,
                   op.Notes, op.NotesFa,
                   op.CreatedDateKey, op.ModifiedDateKey,
                   p.descEnglish      AS ProductName,
                   p.ProductNameLocal AS ProductNameFa,
                   p.descFarsi        AS ProductNameFaAlt,
                   p.StandardCost, p.RahkaranPartID
            FROM t_OpportunityProduct op
            LEFT JOIN t_Product p ON op.ProductID = p.ProductId
            WHERE op.OpportunityID = :opp
              AND (op.TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY op.CreatedDateKey DESC, op.LinkID
        """), {'opp': opportunity_id, 'tenant_id': tenant_id}).fetchall()
        items = []
        for r in rows:
            m = r._mapping
            qty = float(m['Quantity']) if m['Quantity'] is not None else 0.0
            est = float(m['EstimatedUnitPrice']) if m['EstimatedUnitPrice'] is not None else None
            ovr = float(m['OverridePrice']) if m['OverridePrice'] is not None else None
            unit = ovr if ovr is not None else (est or 0.0)
            lang = _lang()
            if lang != 'en':
                name = (m['ProductNameFa'] or '').strip() or (m['ProductNameFaAlt'] or '').strip() or m['ProductName'] or ''
            else:
                name = m['ProductName'] or ''
            items.append({
                'link_id': m['LinkID'],
                'opportunity_id': m['OpportunityID'],
                'product_id': m['ProductID'],
                'product_name': name,
                'quantity': qty,
                'currency': m['Currency'] or 'USD',
                'estimated_unit_price': est,
                'override_price': ovr,
                'effective_unit_price': unit,
                'line_total': round(qty * unit, 4),
                'override_reason': pick_localized(m, 'OverrideReason'),
                'notes': pick_localized(m, 'Notes'),
                'rahkaran_part_id': m['RahkaranPartID'],
                'created_date_key': _to_int_or_none(m['CreatedDateKey']),
                'created_date_formatted': format_date(int(m['CreatedDateKey'])) if m['CreatedDateKey'] else '',
            })
        return jsonify(items)


@crm_api_bp.route('/opportunities/<string:opportunity_id>/products', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def attach_opportunity_product(opportunity_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400
    today_key = int(datetime.now().strftime('%Y%m%d'))
    notes, notes_fa = _write_pair(data, 'notes')
    o_reason, o_reason_fa = _write_pair(data, 'override_reason')
    engine = get_engine()
    with engine.connect() as conn:
        parent = conn.execute(text("""
            SELECT CustomerID, TenantID FROM t_Opportunity
            WHERE OpportunityID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND (IsActive IS NULL OR IsActive = '1')
        """), {'id': opportunity_id, 'tenant_id': tenant_id}).fetchone()
        if not parent:
            return jsonify({'error': 'Opportunity not found'}), 404
        customer_id = parent[0]
        opp_tenant = parent[1] if parent[1] is not None else tenant_id
        currency = data.get('currency') or 'USD'
        est_price = data.get('estimated_unit_price')
        if est_price is None:
            resolved = _resolve_price(conn, product_id, customer_id, today_key, currency, tenant_id=opp_tenant)
            est_price = resolved['price']
        link_id = str(_uuid.uuid4()).upper()
        conn.execute(text("""
            INSERT INTO t_OpportunityProduct (
                LinkID, OpportunityID, ProductID,
                Quantity, Currency, EstimatedUnitPrice, OverridePrice,
                OverrideReason, OverrideReasonFa,
                Notes, NotesFa,
                CreatedDateKey, CreatedBy, TenantID
            ) VALUES (
                :lid, :opp, :pid,
                :qty, :cur, :est, :ovr,
                :or, :orfa,
                :notes, :notes_fa,
                :cdk, :cby, :tid
            )
        """), {
            'lid': link_id, 'opp': opportunity_id, 'pid': product_id,
            'qty': data.get('quantity') or 1,
            'cur': currency, 'est': est_price,
            'ovr': data.get('override_price'),
            'or': o_reason, 'orfa': o_reason_fa,
            'notes': notes, 'notes_fa': notes_fa,
            'cdk': today_key,
            'cby': _to_int_or_none(current_user.get_id()),
            'tid': opp_tenant,
        })
        conn.commit()
    return jsonify({'message': 'Product attached', 'link_id': link_id}), 201


@crm_api_bp.route('/opportunities/<string:opportunity_id>/products/<string:link_id>', methods=['PUT'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def update_opportunity_product(opportunity_id, link_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT op.LinkID
            FROM t_OpportunityProduct op
            JOIN t_Opportunity o ON op.OpportunityID = o.OpportunityID
            WHERE op.LinkID = :lid AND op.OpportunityID = :opp
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
              AND (o.IsActive IS NULL OR o.IsActive = '1')
        """), {'lid': link_id, 'opp': opportunity_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Product link not found'}), 404
        updates = []
        params = {'lid': link_id, 'mby': _to_int_or_none(current_user.get_id()), 'mdk': today_key}
        for fld in ('quantity', 'currency', 'estimated_unit_price', 'override_price'):
            if fld in data:
                col = {'quantity': 'Quantity', 'currency': 'Currency',
                       'estimated_unit_price': 'EstimatedUnitPrice',
                       'override_price': 'OverridePrice'}[fld]
                updates.append(f"{col} = :{fld}")
                params[fld] = data[fld]
        if 'notes' in data or 'notesFa' in data:
            notes, notes_fa = _write_pair(data, 'notes')
            updates.append("Notes = :notes")
            updates.append("NotesFa = :notes_fa")
            params['notes'] = notes; params['notes_fa'] = notes_fa
        if 'override_reason' in data or 'override_reasonFa' in data:
            r, r_fa = _write_pair(data, 'override_reason')
            updates.append("OverrideReason = :or")
            updates.append("OverrideReasonFa = :orfa")
            params['or'] = r; params['orfa'] = r_fa
        if not updates:
            return jsonify({'error': 'No fields to update'}), 400
        updates.append("ModifiedBy = :mby")
        updates.append("ModifiedDateKey = :mdk")
        conn.execute(text(f"""
            UPDATE t_OpportunityProduct
            SET {', '.join(updates)}
            WHERE LinkID = :lid
        """), params)
        conn.commit()
    return jsonify({'message': 'Product updated'})


@crm_api_bp.route('/opportunities/<string:opportunity_id>/products/<string:link_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def detach_opportunity_product(opportunity_id, link_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT op.LinkID
            FROM t_OpportunityProduct op
            JOIN t_Opportunity o ON op.OpportunityID = o.OpportunityID
            WHERE op.LinkID = :lid AND op.OpportunityID = :opp
              AND (o.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'lid': link_id, 'opp': opportunity_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Product link not found'}), 404
        conn.execute(text("DELETE FROM t_OpportunityProduct WHERE LinkID = :lid"),
                     {'lid': link_id})
        conn.commit()
    return jsonify({'message': 'Product detached'})


@crm_api_bp.route('/products/catalog', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def list_product_catalog():
    tenant_id = _tenant_id()
    q = request.args.get('q', '').strip()
    engine = get_engine()
    with engine.connect() as conn:
        sql = """
            SELECT TOP 200 ProductId, descEnglish, descFarsi, ProductNameLocal, StandardCost
            FROM t_Product
            WHERE (TenantID = :tenant_id OR :tenant_id = 0)
        """
        params = {'tenant_id': tenant_id}
        if q:
            sql += " AND (descEnglish LIKE :q OR descFarsi LIKE :q OR ProductNameLocal LIKE :q)"
            params['q'] = f'%{q}%'
        sql += " ORDER BY descEnglish"
        rows = conn.execute(text(sql), params).fetchall()
        items = []
        for r in rows:
            m = r._mapping
            lang = _lang()
            if lang != 'en':
                name = (m['ProductNameLocal'] or '').strip() or (m['descFarsi'] or '').strip() or m['descEnglish'] or ''
            else:
                name = m['descEnglish'] or ''
            items.append({
                'product_id': m['ProductId'],
                'name': name,
                'name_en': m['descEnglish'],
                'name_fa': m['descFarsi'] or m['ProductNameLocal'],
                'standard_cost': float(m['StandardCost']) if m['StandardCost'] is not None else None,
            })
        return jsonify(items)


@crm_api_bp.route('/products/<string:product_id>/price-for/<string:customer_id>', methods=['GET'])
@login_required
@require_module('crm')
@require_permission('SALES_VIEW')
def product_price_for_customer(product_id, customer_id):
    tenant_id = _tenant_id()
    currency = request.args.get('currency') or None
    date_key = request.args.get('date_key')
    date_key = _to_int_or_none(date_key)
    engine = get_engine()
    with engine.connect() as conn:
        result = _resolve_price(conn, product_id, customer_id, date_key, currency, tenant_id=tenant_id)
    return jsonify(result)


# ============================================================================
# QUOTES — WRITE ENDPOINTS
# ============================================================================

def _next_quote_number(conn, tenant_id=1):
    year = datetime.now().year
    if tenant_id and tenant_id != 0:
        prefix = f"Q{tenant_id}-{year}-"
    else:
        prefix = f"Q-{year}-"
    row = conn.execute(text("""
        SELECT MAX(QuoteNumber) FROM t_Quote
        WHERE QuoteNumber LIKE :pfx
    """), {'pfx': prefix + '%'}).scalar()
    if not row:
        return f"{prefix}0001"
    try:
        n = int(str(row).split('-')[-1]) + 1
    except (ValueError, IndexError):
        n = 1
    return f"{prefix}{n:04d}"


def _copy_opportunity_products_to_quote(conn, opportunity_id, quote_id, default_currency, tenant_id):
    rows = conn.execute(text("""
        SELECT ProductID, Quantity, Currency, EstimatedUnitPrice, OverridePrice
        FROM t_OpportunityProduct
        WHERE OpportunityID = :opp
    """), {'opp': opportunity_id}).fetchall()
    if not rows:
        return 0
    count = 0
    for r in rows:
        m = r._mapping
        qty = float(m['Quantity']) if m['Quantity'] is not None else 0.0
        est = float(m['EstimatedUnitPrice']) if m['EstimatedUnitPrice'] is not None else None
        ovr = float(m['OverridePrice']) if m['OverridePrice'] is not None else None
        unit = ovr if ovr is not None else (est or 0.0)
        line_total = round(qty * unit, 4)
        conn.execute(text("""
            INSERT INTO t_QuoteLine (
                LineID, QuoteID, ProductID,
                Quantity, UnitPrice, TotalPrice,
                Currency, BasePrice,
                Notes, NotesLocal, TenantID
            ) VALUES (
                :lid, :qid, :pid,
                :qty, :unit, :total,
                :cur, :base,
                :notes, :notes_local, :tenant_id
            )
        """), {
            'lid': str(_uuid.uuid4()).upper(),
            'qid': quote_id,
            'pid': m['ProductID'],
            'qty': qty,
            'unit': unit,
            'total': line_total,
            'cur': m['Currency'] or default_currency,
            'base': est,
            'notes': None,
            'notes_local': None,
            'tenant_id': tenant_id,
        })
        count += 1
    return count


@crm_api_bp.route('/quotes', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_CREATE')
def create_quote():
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    opp_id = data.get('opportunity_id')
    if not opp_id:
        return jsonify({'error': 'opportunity_id is required'}), 400
    today_key = int(datetime.now().strftime('%Y%m%d'))
    subj, subj_fa = _write_pair(data, 'subject')
    notes, notes_fa = _write_pair(data, 'notes')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            opp = conn.execute(text("""
                SELECT CustomerID, TenantID FROM t_Opportunity
                WHERE OpportunityID = :id
                  AND (TenantID = :tenant_id OR :tenant_id = 0)
                  AND (IsActive IS NULL OR IsActive = '1')
            """), {'id': opp_id, 'tenant_id': tenant_id}).fetchone()
            if not opp:
                return jsonify({'error': 'Opportunity not found'}), 404
            customer_id = data.get('customer_id') or opp[0]
            opp_tenant = opp[1] if opp[1] is not None else tenant_id
            currency = data.get('currency') or 'USD'
            tax_rate = data.get('tax_rate')
            if tax_rate is None:
                tax_rate = 0
            quote_id = str(_uuid.uuid4()).upper()
            qnum = _next_quote_number(conn, opp_tenant)
            valid_until_key = date_to_key(data.get('valid_until'))
            conn.execute(text("""
                INSERT INTO t_Quote (
                    QuoteID, QuoteNumber, OpportunityID, CustomerID,
                    Revision, Status, Subject, SubjectFa, ValidUntilDateKey,
                    TotalAmount, Currency, TaxRate, TaxAmount, TotalWithTax,
                    Notes, NotesFa,
                    CreatedBy, CreatedDateKey, TenantID
                ) VALUES (
                    :qid, :qnum, :opp, :cust,
                    1, 'Draft',
                    :subj, :subj_fa, :valid,
                    0, :cur, :tax_rate, 0, 0,
                    :notes, :notes_fa,
                    :cby, :cdk, :tid
                )
            """), {
                'qid': quote_id, 'qnum': qnum, 'opp': opp_id, 'cust': customer_id,
                'subj': subj, 'subj_fa': subj_fa,
                'valid': valid_until_key,
                'cur': currency, 'tax_rate': tax_rate,
                'notes': notes, 'notes_fa': notes_fa,
                'cby': _to_int_or_none(current_user.get_id()),
                'cdk': today_key,
                'tid': opp_tenant,
            })
            copied = 0
            if data.get('copy_opportunity_products', True):
                copied = _copy_opportunity_products_to_quote(conn, opp_id, quote_id, currency, opp_tenant)
            _recalc_quote_totals(conn, quote_id)
            conn.commit()
        return jsonify({
            'message': 'Quote created',
            'quote_id': quote_id,
            'quote_number': qnum,
            'lines_copied': copied,
        }), 201
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'Create failed: {e}'}), 500


def _recalc_quote_totals(conn, quote_id):
    q = conn.execute(text("""
        SELECT Currency, TaxRate FROM t_Quote WHERE QuoteID = :id
    """), {'id': quote_id}).fetchone()
    if not q:
        return
    tax_rate = float(q[1]) if q[1] is not None else 0.0
    sub = conn.execute(text("""
        SELECT COALESCE(SUM(CAST(TotalPrice AS DECIMAL(18,4))), 0)
        FROM t_QuoteLine WHERE QuoteID = :id
    """), {'id': quote_id}).scalar() or 0
    sub = float(sub)
    tax = round(sub * tax_rate / 100.0, 4)
    tot = round(sub + tax, 4)
    conn.execute(text("""
        UPDATE t_Quote
        SET TotalAmount = :sub, TaxAmount = :tax, TotalWithTax = :tot,
            ModifiedDateKey = :mdk
        WHERE QuoteID = :id
    """), {
        'sub': sub, 'tax': tax, 'tot': tot,
        'mdk': int(datetime.now().strftime('%Y%m%d')),
        'id': quote_id,
    })


@crm_api_bp.route('/quotes/<string:quote_id>', methods=['PUT'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def update_quote(quote_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM t_Quote
            WHERE QuoteID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
              AND Status IN ('Draft', 'Sent')
        """), {'id': quote_id, 'tenant_id': tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Quote not found or not editable'}), 404
        updates = []
        params = {'id': quote_id, 'mdk': today_key}
        if 'subject' in data or 'subjectFa' in data:
            s, s_fa = _write_pair(data, 'subject')
            updates += ["Subject = :subj", "SubjectFa = :subj_fa"]
            params['subj'] = s; params['subj_fa'] = s_fa
        if 'notes' in data or 'notesFa' in data:
            n, n_fa = _write_pair(data, 'notes')
            updates += ["Notes = :notes", "NotesFa = :notes_fa"]
            params['notes'] = n; params['notes_fa'] = n_fa
        if 'currency' in data:
            updates.append("Currency = :cur"); params['cur'] = data['currency']
        if 'tax_rate' in data:
            updates.append("TaxRate = :tr"); params['tr'] = data['tax_rate']
        if 'valid_until' in data:
            updates.append("ValidUntilDateKey = :vu")
            params['vu'] = date_to_key(data['valid_until'])
        if 'status' in data and data['status'] in ('Draft', 'Sent', 'Accepted', 'Rejected'):
            updates.append("Status = :st"); params['st'] = data['status']
        if not updates:
            return jsonify({'error': 'No fields to update'}), 400
        updates.append("ModifiedDateKey = :mdk")
        conn.execute(text("UPDATE t_Quote SET " + ", ".join(updates)
                          + " WHERE QuoteID = :id"), params)
        _recalc_quote_totals(conn, quote_id)
        conn.commit()
    return jsonify({'message': 'Quote updated'})


@crm_api_bp.route('/quotes/<string:quote_id>/lines', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def add_quote_line(quote_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    pid = data.get('product_id')
    if not pid:
        return jsonify({'error': 'product_id is required'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        q = conn.execute(text("""
            SELECT Currency, TenantID FROM t_Quote
            WHERE QuoteID = :id AND Status IN ('Draft', 'Sent')
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': quote_id, 'tenant_id': tenant_id}).fetchone()
        if not q:
            return jsonify({'error': 'Quote not found or not editable'}), 404
        header_currency = q[0] or 'USD'
        line_tenant = q[1] if q[1] is not None else tenant_id
        qty = float(data.get('quantity') or 0)
        if qty <= 0:
            return jsonify({'error': 'quantity must be > 0'}), 400
        unit = data.get('unit_price')
        if unit is None:
            unit = 0.0
        unit = float(unit)
        total = round(qty * unit, 4)
        currency = data.get('currency') or header_currency
        notes, notes_fa = _write_pair(data, 'notes')
        line_id = str(_uuid.uuid4()).upper()
        conn.execute(text("""
            INSERT INTO t_QuoteLine (
                LineID, QuoteID, ProductID,
                Quantity, UnitPrice, TotalPrice,
                Currency, BasePrice, Notes, NotesLocal, TenantID
            ) VALUES (
                :lid, :qid, :pid,
                :qty, :unit, :total,
                :cur, :base, :notes, :notes_local, :tenant_id
            )
        """), {
            'lid': line_id, 'qid': quote_id, 'pid': pid,
            'qty': qty, 'unit': unit, 'total': total,
            'cur': currency, 'base': unit,
            'notes': notes, 'notes_local': notes_fa,
            'tenant_id': line_tenant,
        })
        _recalc_quote_totals(conn, quote_id)
        conn.commit()
    return jsonify({'message': 'Line added', 'line_id': line_id}), 201


@crm_api_bp.route('/quote-lines/<string:line_id>', methods=['PUT'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def update_quote_line(line_id):
    tenant_id = _tenant_id()
    data = request.get_json() or {}
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT ql.QuoteID, ql.Quantity, ql.UnitPrice
            FROM t_QuoteLine ql
            JOIN t_Quote q ON ql.QuoteID = q.QuoteID
            WHERE ql.LineID = :lid
              AND q.Status IN ('Draft', 'Sent')
              AND (q.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'lid': line_id, 'tenant_id': tenant_id}).fetchone()
        if not row:
            return jsonify({'error': 'Line not found or quote not editable'}), 404
        quote_id = row[0]
        qty  = float(data.get('quantity',   row[1] or 0))
        unit = float(data.get('unit_price', row[2] or 0))
        total = round(qty * unit, 4)
        updates = ["Quantity = :qty", "UnitPrice = :unit", "TotalPrice = :total"]
        params = {'lid': line_id, 'qty': qty, 'unit': unit, 'total': total}
        if 'notes' in data or 'notesFa' in data:
            n, n_fa = _write_pair(data, 'notes')
            updates += ["Notes = :notes", "NotesLocal = :notes_local"]
            params['notes'] = n; params['notes_local'] = n_fa
        conn.execute(text("UPDATE t_QuoteLine SET " + ", ".join(updates)
                          + " WHERE LineID = :lid"), params)
        _recalc_quote_totals(conn, quote_id)
        conn.commit()
    return jsonify({'message': 'Line updated'})


@crm_api_bp.route('/quote-lines/<string:line_id>', methods=['DELETE'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def delete_quote_line(line_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT ql.QuoteID
            FROM t_QuoteLine ql
            JOIN t_Quote q ON ql.QuoteID = q.QuoteID
            WHERE ql.LineID = :lid
              AND q.Status IN ('Draft', 'Sent')
              AND (q.TenantID = :tenant_id OR :tenant_id = 0)
        """), {'lid': line_id, 'tenant_id': tenant_id}).first()
        if not row:
            return jsonify({'error': 'Line not found or quote not editable'}), 404
        quote_id = row[0]
        conn.execute(text("DELETE FROM t_QuoteLine WHERE LineID = :lid"),
                     {'lid': line_id})
        _recalc_quote_totals(conn, quote_id)
        conn.commit()
    return jsonify({'message': 'Line deleted'})


@crm_api_bp.route('/quotes/<string:quote_id>/revise', methods=['POST'])
@login_required
@require_module('crm')
@require_permission('SALES_EDIT')
def revise_quote(quote_id):
    tenant_id = _tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        src = conn.execute(text("""
            SELECT * FROM t_Quote
            WHERE QuoteID = :id
              AND (TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': quote_id, 'tenant_id': tenant_id}).fetchone()
        if not src:
            return jsonify({'error': 'Quote not found'}), 404
        m = src._mapping
        if m['Status'] not in ('Draft', 'Sent', 'Rejected'):
            return jsonify({'error': f"Cannot revise a {m['Status']} quote"}), 400
        new_id = str(_uuid.uuid4()).upper()
        new_rev = int(m['Revision'] or 1) + 1
        today_key = int(datetime.now().strftime('%Y%m%d'))
        conn.execute(text("""
            INSERT INTO t_Quote (
                QuoteID, QuoteNumber, OpportunityID, CustomerID,
                Revision, Status, Subject, SubjectFa, ValidUntilDateKey,
                TotalAmount, Currency, TaxRate, TaxAmount, TotalWithTax,
                Notes, NotesFa,
                CreatedBy, CreatedDateKey, TenantID
            ) VALUES (
                :qid, :qnum, :opp, :cust,
                :rev, 'Draft',
                :subj, :subj_fa, :valid,
                0, :cur, :tax_rate, 0, 0,
                :notes, :notes_fa,
                :cby, :cdk, :tid
            )
        """), {
            'qid': new_id, 'qnum': m['QuoteNumber'], 'opp': m['OpportunityID'],
            'cust': m['CustomerID'], 'rev': new_rev,
            'subj': m['Subject'], 'subj_fa': m['SubjectFa'],
            'valid': m['ValidUntilDateKey'],
            'cur': m['Currency'] or 'USD',
            'tax_rate': m['TaxRate'] or 0,
            'notes': m['Notes'], 'notes_fa': m['NotesFa'],
            'cby': _to_int_or_none(current_user.get_id()),
            'cdk': today_key,
            'tid': m['TenantID'] if m['TenantID'] is not None else tenant_id,
        })
        lines = conn.execute(text("""
            SELECT ProductID, Quantity, UnitPrice, TotalPrice,
                   Currency, BasePrice, Notes, NotesLocal AS NotesFa
            FROM t_QuoteLine WHERE QuoteID = :id
        """), {'id': quote_id}).fetchall()
        for ln in lines:
            lm = ln._mapping
            conn.execute(text("""
                INSERT INTO t_QuoteLine (
                    LineID, QuoteID, ProductID,
                    Quantity, UnitPrice, TotalPrice,
                    Currency, BasePrice, Notes, NotesLocal, TenantID
                ) VALUES (
                    :lid, :qid, :pid,
                    :qty, :unit, :total,
                    :cur, :base, :notes, :notes_local, :tenant_id
                )
            """), {
                'lid': str(_uuid.uuid4()).upper(), 'qid': new_id,
                'pid': lm['ProductID'],
                'qty': lm['Quantity'], 'unit': lm['UnitPrice'],
                'total': lm['TotalPrice'],
                'cur': lm['Currency'], 'base': lm['BasePrice'],
                'notes': lm['Notes'], 'notes_local': lm.get('NotesFa'),
                'tenant_id': m['TenantID'] if m['TenantID'] is not None else tenant_id,
            })
        _recalc_quote_totals(conn, new_id)
        conn.execute(text("""
            UPDATE t_Quote
            SET Status = 'Superseded',
                ParentQuoteID = COALESCE(ParentQuoteID, :parent_hint),
                ModifiedDateKey = :mdk
            WHERE QuoteID = :id
        """), {'id': quote_id, 'parent_hint': new_id, 'mdk': today_key})
        conn.commit()
    return jsonify({
        'message': 'Quote revised',
        'quote_id': new_id,
        'revision': new_rev,
    }), 201
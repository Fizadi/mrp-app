# app/modules/finance/api.py
from flask import jsonify, request, session, current_app
from sqlalchemy.sql import text
from . import finance_api_bp
from app.core.auth import login_required, permission_required
from app.modules.auth import verify_password  # noqa: F401 — Playbook G5
import logging
import uuid
from datetime import datetime

# ============================================================================
# HELPERS
# ============================================================================

def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def _new_id():
    return str(uuid.uuid4())


def _today_key():
    return int(datetime.now().strftime('%Y%m%d'))


def get_current_industry():
    """D3=a — fallback to 'valve' like every other module."""
    return session.get('demo_industry') or 'valve'


def _industry_filter(col='DemoIndustryCode'):
    """Standard industry filter for tables that have DemoIndustryCode."""
    return f"({col} = :industry OR {col} IS NULL)"


def _float(v, default=0.0):
    if v is None or v == '':
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0):
    if v is None or v == '':
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ============================================================================
# SALES FORECAST
# ============================================================================

@finance_api_bp.route('/sales-forecast/stats', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_sales_forecast_stats():
    industry = get_current_industry()
    engine = get_engine()
    try:
        with engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM t_SalesForecastHeader WHERE {_industry_filter()}"),
                {'industry': industry}
            ).scalar() or 0

            approved = conn.execute(
                text(f"SELECT COUNT(*) FROM t_SalesForecastHeader WHERE {_industry_filter()} AND Status = 'Approved'"),
                {'industry': industry}
            ).scalar() or 0

            draft = conn.execute(
                text(f"SELECT COUNT(*) FROM t_SalesForecastHeader WHERE {_industry_filter()} AND Status = 'Draft'"),
                {'industry': industry}
            ).scalar() or 0

            total_value = conn.execute(
                text(f"""
                    SELECT COALESCE(SUM(CAST(sfl.TotalAmount AS DECIMAL(18,4))), 0)
                    FROM t_SalesForecastHeader sfh
                    JOIN t_SalesForecastLine sfl ON sfh.ForecastID = sfl.ForecastID
                    WHERE {_industry_filter('sfh.DemoIndustryCode')}
                      AND sfh.Status = 'Approved'
                """),
                {'industry': industry}
            ).scalar() or 0

            return jsonify({
                'success': True,
                'stats': {
                    'total': total,
                    'approved': approved,
                    'draft': draft,
                    'total_value': _float(total_value)
                }
            })
    except Exception as e:
        logging.error(f"Error getting sales forecast stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/sales-forecast', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_sales_forecasts():
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    year = request.args.get('year')
    season = request.args.get('season')
    status_filter = request.args.get('status')

    engine = get_engine()
    try:
        query = f"""
            SELECT 
                sfh.ForecastID,
                sfh.ForecastCode,
                sfh.ForecastName,
                sfh.ForecastNameLocal,
                sfh.PeriodStartDateKey,
                sfh.PeriodEndDateKey,
                sfh.PeriodPersianYear,
                sfh.PeriodPersianSeason,
                sfh.Status,
                sfh.CreatedBy,
                sfh.CreatedDateKey,
                sfh.DemoIndustryCode,
                sfh.CurrencyCode,
                u.FullName AS CreatedByName,
                COALESCE(
                    (SELECT SUM(CAST(TotalAmount AS DECIMAL(18,4))) FROM t_SalesForecastLine WHERE ForecastID = sfh.ForecastID),
                    0
                ) AS TotalAmount,
                COALESCE(
                    (SELECT SUM(CAST(TotalQuantity AS DECIMAL(18,4))) FROM t_SalesForecastLine WHERE ForecastID = sfh.ForecastID),
                    0
                ) AS TotalQuantity
            FROM t_SalesForecastHeader sfh
            LEFT JOIN t_Users u ON sfh.CreatedBy = u.UserID
            WHERE {_industry_filter('sfh.DemoIndustryCode')}
        """
        params = {'industry': industry}

        if year:
            query += " AND sfh.PeriodPersianYear = :year"
            params['year'] = str(year)
        if season:
            query += " AND sfh.PeriodPersianSeason = :season"
            params['season'] = season
        if status_filter:
            query += " AND sfh.Status = :status"
            params['status'] = status_filter

        query += " ORDER BY sfh.PeriodStartDateKey DESC, sfh.CreatedDateKey DESC"

        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()

        result = []
        for r in rows:
            m = r._mapping
            forecast_name = m['ForecastName']
            if lang == 'fa' and m['ForecastNameLocal'] and str(m['ForecastNameLocal']).strip():
                forecast_name = m['ForecastNameLocal']

            result.append({
                'ForecastID': m['ForecastID'],
                'ForecastCode': m['ForecastCode'],
                'ForecastName': forecast_name,
                'ForecastNameLocal': m['ForecastNameLocal'],
                'PeriodStartDateKey': m['PeriodStartDateKey'],
                'PeriodEndDateKey': m['PeriodEndDateKey'],
                'PeriodPersianYear': m['PeriodPersianYear'],
                'PeriodPersianSeason': m['PeriodPersianSeason'],
                'Status': m['Status'],
                'TotalAmount': _float(m['TotalAmount']),
                'TotalQuantity': _float(m['TotalQuantity']),
                'CreatedByName': m['CreatedByName'] or 'Unknown',
                'CurrencyCode': m['CurrencyCode'] or 'IRR',
                'DemoIndustryCode': m['DemoIndustryCode']
            })

        return jsonify({'success': True, 'forecasts': result})
    except Exception as e:
        logging.error(f"Error getting sales forecasts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/sales-forecast/<string:forecast_id>', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_sales_forecast(forecast_id):
    industry = get_current_industry()
    lang = session.get('lang', 'en')

    engine = get_engine()
    try:
        with engine.connect() as conn:
            header_row = conn.execute(
                text(f"SELECT * FROM t_SalesForecastHeader WHERE ForecastID = :fid AND {_industry_filter()}"),
                {'fid': forecast_id, 'industry': industry}
            ).fetchone()

            if not header_row:
                return jsonify({'success': False, 'error': 'Forecast not found'}), 404

            header_dict = dict(header_row._mapping)

            lines = conn.execute(
                text("""
                    SELECT 
                        sfl.LineID, sfl.LineNumber, sfl.ProductCategoryID,
                        sfl.Month1Quantity, sfl.Month1Price, sfl.Month1Amount,
                        sfl.Month2Quantity, sfl.Month2Price, sfl.Month2Amount,
                        sfl.Month3Quantity, sfl.Month3Price, sfl.Month3Amount,
                        sfl.TotalQuantity, sfl.TotalAmount,
                        sfl.CashPct, sfl.Days30Pct, sfl.Days60Pct,
                        sfl.Days90Pct, sfl.Days120Pct, sfl.Days150Pct,
                        pc.CategoryName, pc.CategoryNameLocal
                    FROM t_SalesForecastLine sfl
                    LEFT JOIN t_ProductCategory pc ON sfl.ProductCategoryID = pc.CategoryID
                    WHERE sfl.ForecastID = :fid
                    ORDER BY TRY_CAST(sfl.LineNumber AS INT)
                """),
                {'fid': forecast_id}
            ).fetchall()

        lines_result = []
        for line in lines:
            m = line._mapping
            category_name = m['CategoryName'] or ''
            category_name_local = m['CategoryNameLocal'] or ''
            if lang == 'fa' and category_name_local.strip():
                category_name = category_name_local

            lines_result.append({
                'LineID': m['LineID'],
                'LineNumber': m['LineNumber'],
                'ProductCategoryID': m['ProductCategoryID'],
                'CategoryName': category_name,
                'CategoryNameLocal': category_name_local,
                'Month1Quantity': _float(m['Month1Quantity']),
                'Month1Price': _float(m['Month1Price']),
                'Month1Amount': _float(m['Month1Amount']),
                'Month2Quantity': _float(m['Month2Quantity']),
                'Month2Price': _float(m['Month2Price']),
                'Month2Amount': _float(m['Month2Amount']),
                'Month3Quantity': _float(m['Month3Quantity']),
                'Month3Price': _float(m['Month3Price']),
                'Month3Amount': _float(m['Month3Amount']),
                'TotalQuantity': _float(m['TotalQuantity']),
                'TotalAmount': _float(m['TotalAmount']),
                'CashPct': _float(m['CashPct']),
                'Days30Pct': _float(m['Days30Pct']),
                'Days60Pct': _float(m['Days60Pct']),
                'Days90Pct': _float(m['Days90Pct']),
                'Days120Pct': _float(m['Days120Pct']),
                'Days150Pct': _float(m['Days150Pct'])
            })

        return jsonify({'success': True, 'header': header_dict, 'lines': lines_result})
    except Exception as e:
        logging.error(f"Error getting sales forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/sales-forecast', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_CREATE')
def create_sales_forecast():
    data = request.get_json() or {}
    user_id = session.get('user_id')
    industry = session.get('demo_industry')
    if not industry:
        return jsonify({'success': False, 'error': 'No industry selected. Please select an industry first.'}), 400

    for field in ['ForecastName', 'PeriodPersianYear', 'PeriodPersianSeason']:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing field: {field}'}), 400

    engine = get_engine()
    try:
        year = data['PeriodPersianYear']
        season = data['PeriodPersianSeason']
        season_code = {
            'بهار': 1, 'تابستان': 2, 'پاییز': 3, 'زمستان': 4,
            'Spring': 1, 'Summer': 2, 'Autumn': 3, 'Fall': 3, 'Winter': 4,
        }.get(season, 1)

        with engine.begin() as conn:
            seq = conn.execute(
                text("""
                    SELECT COUNT(*) + 1 FROM t_SalesForecastHeader 
                    WHERE PeriodPersianYear = :year AND PeriodPersianSeason = :season
                """),
                {'year': str(year), 'season': season}
            ).scalar() or 1

            forecast_code = f"SALES-{year}-{season_code:02d}-{seq:03d}"
            new_id = _new_id()

            conn.execute(text("""
                INSERT INTO t_SalesForecastHeader (
                    ForecastID, ForecastCode, ForecastName, PeriodPersianYear, PeriodPersianSeason,
                    PeriodPersianSeasonCode, Status, CreatedBy, CreatedDateKey,
                    DemoIndustryCode, CurrencyCode, Description
                ) VALUES (
                    :fid, :code, :name, :year, :season,
                    :scode, 'Draft', :user, :datekey,
                    :industry, :currency, :desc
                )
            """), {
                'fid': new_id,
                'code': forecast_code,
                'name': data['ForecastName'],
                'year': str(year),
                'season': season,
                'scode': str(season_code),
                'user': user_id,
                'datekey': str(_today_key()),
                'industry': industry,
                'currency': data.get('CurrencyCode', 'IRR'),
                'desc': data.get('Description', '')
            })

        return jsonify({'success': True, 'ForecastID': new_id, 'ForecastCode': forecast_code})
    except Exception as e:
        logging.error(f"Error creating sales forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/sales-forecast/<string:forecast_id>/approve', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_APPROVE')
def approve_sales_forecast(forecast_id):
    industry = get_current_industry()
    engine = get_engine()
    try:
        with engine.begin() as conn:
            status = conn.execute(
                text(f"SELECT Status FROM t_SalesForecastHeader WHERE ForecastID = :fid AND {_industry_filter()}"),
                {'fid': forecast_id, 'industry': industry}
            ).scalar()

            if status is None:
                return jsonify({'success': False, 'error': 'Forecast not found'}), 404
            if status != 'Draft':
                return jsonify({'success': False, 'error': 'Only Draft forecasts can be approved'}), 400

            conn.execute(text("""
                UPDATE t_SalesForecastHeader 
                SET Status = 'Approved',
                    ApprovedBy = :user,
                    ApprovedDateKey = :datekey
                WHERE ForecastID = :fid
            """), {
                'user': session.get('user_id'),
                'datekey': str(_today_key()),
                'fid': forecast_id
            })

        return jsonify({'success': True, 'message': 'Forecast approved successfully'})
    except Exception as e:
        logging.error(f"Error approving sales forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# PURCHASE FORECAST
# ============================================================================

@finance_api_bp.route('/purchase-forecast/stats', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_purchase_forecast_stats():
    industry = get_current_industry()
    engine = get_engine()
    try:
        with engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM t_PurchaseForecastHeader WHERE {_industry_filter()}"),
                {'industry': industry}
            ).scalar() or 0
            approved = conn.execute(
                text(f"SELECT COUNT(*) FROM t_PurchaseForecastHeader WHERE {_industry_filter()} AND Status = 'Approved'"),
                {'industry': industry}
            ).scalar() or 0
            draft = conn.execute(
                text(f"SELECT COUNT(*) FROM t_PurchaseForecastHeader WHERE {_industry_filter()} AND Status = 'Draft'"),
                {'industry': industry}
            ).scalar() or 0
            total_value = conn.execute(
                text(f"""
                    SELECT COALESCE(SUM(CAST(pfl.TotalPurchaseAmount AS DECIMAL(18,4))), 0)
                    FROM t_PurchaseForecastHeader pfh
                    JOIN t_PurchaseForecastLine pfl ON pfh.ForecastID = pfl.ForecastID
                    WHERE {_industry_filter('pfh.DemoIndustryCode')}
                      AND pfh.Status = 'Approved'
                """),
                {'industry': industry}
            ).scalar() or 0

            return jsonify({
                'success': True,
                'stats': {
                    'total': total,
                    'approved': approved,
                    'draft': draft,
                    'total_value': _float(total_value)
                }
            })
    except Exception as e:
        logging.error(f"Error getting purchase forecast stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/purchase-forecast', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_purchase_forecasts():
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    year = request.args.get('year')
    season = request.args.get('season')
    status_filter = request.args.get('status')

    engine = get_engine()
    try:
        query = f"""
            SELECT 
                pfh.ForecastID, pfh.ForecastCode, pfh.ForecastName, pfh.ForecastNameLocal,
                pfh.PeriodPersianYear, pfh.PeriodPersianSeason,
                pfh.Status, pfh.CreatedBy, pfh.CreatedDateKey,
                u.FullName AS CreatedByName,
                COALESCE((SELECT SUM(CAST(TotalPurchaseAmount AS DECIMAL(18,4))) FROM t_PurchaseForecastLine WHERE ForecastID = pfh.ForecastID), 0) AS TotalAmount,
                COALESCE((SELECT SUM(CAST(ExpectedConsumption AS DECIMAL(18,4))) FROM t_PurchaseForecastLine WHERE ForecastID = pfh.ForecastID), 0) AS TotalConsumption
            FROM t_PurchaseForecastHeader pfh
            LEFT JOIN t_Users u ON pfh.CreatedBy = u.UserID
            WHERE {_industry_filter('pfh.DemoIndustryCode')}
        """
        params = {'industry': industry}
        if year:
            query += " AND pfh.PeriodPersianYear = :year"
            params['year'] = str(year)
        if season:
            query += " AND pfh.PeriodPersianSeason = :season"
            params['season'] = season
        if status_filter:
            query += " AND pfh.Status = :status"
            params['status'] = status_filter
        query += " ORDER BY pfh.PeriodStartDateKey DESC, pfh.CreatedDateKey DESC"

        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()

        result = []
        for r in rows:
            m = r._mapping
            forecast_name = m['ForecastName']
            if lang == 'fa' and m['ForecastNameLocal'] and str(m['ForecastNameLocal']).strip():
                forecast_name = m['ForecastNameLocal']
            result.append({
                'ForecastID': m['ForecastID'],
                'ForecastCode': m['ForecastCode'],
                'ForecastName': forecast_name,
                'ForecastNameLocal': m['ForecastNameLocal'],
                'PeriodPersianYear': m['PeriodPersianYear'],
                'PeriodPersianSeason': m['PeriodPersianSeason'],
                'Status': m['Status'],
                'TotalAmount': _float(m['TotalAmount']),
                'TotalConsumption': _float(m['TotalConsumption']),
                'CreatedByName': m['CreatedByName'] or 'Unknown'
            })
        return jsonify({'success': True, 'forecasts': result})
    except Exception as e:
        logging.error(f"Error getting purchase forecasts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/purchase-forecast/<string:forecast_id>', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_purchase_forecast(forecast_id):
    industry = get_current_industry()
    lang = session.get('lang', 'en')

    engine = get_engine()
    try:
        with engine.connect() as conn:
            header_row = conn.execute(
                text(f"SELECT * FROM t_PurchaseForecastHeader WHERE ForecastID = :fid AND {_industry_filter()}"),
                {'fid': forecast_id, 'industry': industry}
            ).fetchone()
            if not header_row:
                return jsonify({'success': False, 'error': 'Forecast not found'}), 404

            header_dict = dict(header_row._mapping)

            lines = conn.execute(text("""
                SELECT 
                    pfl.LineID, pfl.LineNumber,
                    pfl.ProductID, pfl.ProductCategoryID,
                    pfl.ExpectedConsumption,
                    pfl.BeginningInventoryRate, pfl.EstimatedPurchaseRate,
                    pfl.TotalPurchaseAmount,
                    pfl.CashPct, pfl.Days30Pct, pfl.Days60Pct,
                    pfl.Days90Pct, pfl.Days120Pct, pfl.Days150Pct,
                    p.ProductId AS ProductIdJoined,
                    p.descEnglish AS ProductName,
                    p.descFarsi AS ProductNameFarsi,
                    pc.CategoryName
                FROM t_PurchaseForecastLine pfl
                LEFT JOIN t_Product p ON pfl.ProductID = p.ProductId
                LEFT JOIN t_ProductCategory pc ON pfl.ProductCategoryID = pc.CategoryID
                WHERE pfl.ForecastID = :fid
                ORDER BY TRY_CAST(pfl.LineNumber AS INT)
            """), {'fid': forecast_id}).fetchall()

        lines_result = []
        for line in lines:
            m = line._mapping
            product_name = m['ProductName'] or ''
            if lang == 'fa' and m['ProductNameFarsi'] and str(m['ProductNameFarsi']).strip():
                product_name = m['ProductNameFarsi']
            lines_result.append({
                'LineID': m['LineID'],
                'LineNumber': m['LineNumber'],
                'ProductID': m['ProductID'],
                'ProductName': product_name,
                'CategoryName': m['CategoryName'] or '',
                'ExpectedConsumption': _float(m['ExpectedConsumption']),
                'BeginningInventoryRate': _float(m['BeginningInventoryRate']),
                'EstimatedPurchaseRate': _float(m['EstimatedPurchaseRate']),
                'TotalPurchaseAmount': _float(m['TotalPurchaseAmount']),
                'CashPct': _float(m['CashPct']),
                'Days30Pct': _float(m['Days30Pct']),
                'Days60Pct': _float(m['Days60Pct']),
                'Days90Pct': _float(m['Days90Pct']),
                'Days120Pct': _float(m['Days120Pct']),
                'Days150Pct': _float(m['Days150Pct'])
            })

        return jsonify({'success': True, 'header': header_dict, 'lines': lines_result})
    except Exception as e:
        logging.error(f"Error getting purchase forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/purchase-forecast', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_CREATE')
def create_purchase_forecast():
    data = request.get_json() or {}
    user_id = session.get('user_id')
    industry = session.get('demo_industry')
    if not industry:
        return jsonify({'success': False, 'error': 'No industry selected. Please select an industry first.'}), 400

    for field in ['ForecastName', 'PeriodPersianYear', 'PeriodPersianSeason']:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing field: {field}'}), 400

    engine = get_engine()
    try:
        year = data['PeriodPersianYear']
        season = data['PeriodPersianSeason']
        season_code = {
            'بهار': 1, 'تابستان': 2, 'پاییز': 3, 'زمستان': 4,
            'Spring': 1, 'Summer': 2, 'Autumn': 3, 'Fall': 3, 'Winter': 4,
        }.get(season, 1)

        with engine.begin() as conn:
            seq = conn.execute(
                text("""
                    SELECT COUNT(*) + 1 FROM t_PurchaseForecastHeader 
                    WHERE PeriodPersianYear = :year AND PeriodPersianSeason = :season
                """),
                {'year': str(year), 'season': season}
            ).scalar() or 1

            forecast_code = f"PURCHASE-{year}-{season_code:02d}-{seq:03d}"
            new_id = _new_id()

            conn.execute(text("""
                INSERT INTO t_PurchaseForecastHeader (
                    ForecastID, ForecastCode, ForecastName, PeriodPersianYear, PeriodPersianSeason,
                    PeriodPersianSeasonCode, Status, CreatedBy, CreatedDateKey,
                    DemoIndustryCode, CurrencyCode, Description
                ) VALUES (
                    :fid, :code, :name, :year, :season,
                    :scode, 'Draft', :user, :datekey,
                    :industry, :currency, :desc
                )
            """), {
                'fid': new_id,
                'code': forecast_code,
                'name': data['ForecastName'],
                'year': str(year),
                'season': season,
                'scode': str(season_code),
                'user': user_id,
                'datekey': str(_today_key()),
                'industry': industry,
                'currency': data.get('CurrencyCode', 'IRR'),
                'desc': data.get('Description', '')
            })

        return jsonify({'success': True, 'ForecastID': new_id, 'ForecastCode': forecast_code})
    except Exception as e:
        logging.error(f"Error creating purchase forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/purchase-forecast/<string:forecast_id>/approve', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_APPROVE')
def approve_purchase_forecast(forecast_id):
    industry = get_current_industry()
    engine = get_engine()
    try:
        with engine.begin() as conn:
            status = conn.execute(
                text(f"SELECT Status FROM t_PurchaseForecastHeader WHERE ForecastID = :fid AND {_industry_filter()}"),
                {'fid': forecast_id, 'industry': industry}
            ).scalar()

            if status is None:
                return jsonify({'success': False, 'error': 'Forecast not found'}), 404
            if status != 'Draft':
                return jsonify({'success': False, 'error': 'Only Draft forecasts can be approved'}), 400

            conn.execute(text("""
                UPDATE t_PurchaseForecastHeader 
                SET Status = 'Approved',
                    ApprovedBy = :user,
                    ApprovedDateKey = :datekey
                WHERE ForecastID = :fid
            """), {
                'user': session.get('user_id'),
                'datekey': str(_today_key()),
                'fid': forecast_id
            })

        return jsonify({'success': True, 'message': 'Forecast approved successfully'})
    except Exception as e:
        logging.error(f"Error approving purchase forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# DROPDOWNS
# ============================================================================

@finance_api_bp.route('/product-categories', methods=['GET'])
@login_required
def get_product_categories():
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT 
                    CategoryID, CategoryCode, CategoryName, CategoryNameLocal, DemoIndustryCode
                FROM t_ProductCategory
                WHERE {_industry_filter()}
                  AND IsActive = '1'
                ORDER BY CategoryName
            """), {'industry': industry}).fetchall()

        result = []
        for row in rows:
            m = row._mapping
            display_name = m['CategoryNameLocal'] if lang == 'fa' and m['CategoryNameLocal'] else m['CategoryName']
            result.append({
                'CategoryID': m['CategoryID'],
                'CategoryCode': m['CategoryCode'],
                'CategoryName': m['CategoryName'],
                'CategoryNameLocal': m['CategoryNameLocal'],
                'DisplayName': display_name
            })
        return jsonify({'success': True, 'categories': result})
    except Exception as e:
        logging.error(f"Error getting categories: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/raw-materials', methods=['GET'])
@login_required
def get_raw_materials():
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT ProductId, descEnglish, descFarsi, ProductCategoryCode, UOM
                FROM t_Product
                WHERE {_industry_filter()}
                  AND ProcurementType = 'Purchased'
                  AND IsManufactured = '0'
                ORDER BY descEnglish
            """), {'industry': industry}).fetchall()

        result = []
        for row in rows:
            m = row._mapping
            display_name = m['descFarsi'] if lang == 'fa' and m['descFarsi'] else m['descEnglish']
            result.append({
                'ProductId': m['ProductId'],
                'Name': display_name,
                'UOM': m['UOM']
            })
        return jsonify({'success': True, 'materials': result})
    except Exception as e:
        logging.error(f"Error getting raw materials: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# CASH FLOW
# ============================================================================

@finance_api_bp.route('/cash-flow/list', methods=['GET'])
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def get_cash_flow_list():
    industry = get_current_industry()
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT 
                    cfp.ProjectionID, cfp.ProjectionCode, cfp.ProjectionName,
                    cfp.PeriodPersianYear, cfp.PeriodPersianSeason,
                    cfp.Status, cfp.CreatedBy, cfp.CreatedDateKey,
                    cfp.OpeningBalanceInitial, cfp.DemoIndustryCode, cfp.CurrencyCode,
                    cfp.Description, cfp.PeriodType,
                    u.FullName AS CreatedByName,
                    sfh.ForecastCode AS SalesForecastCode,
                    pfh.ForecastCode AS PurchaseForecastCode,
                    COALESCE((SELECT SUM(CAST(TotalReceipts AS DECIMAL(18,4))) FROM t_CashFlowLine WHERE ProjectionID = cfp.ProjectionID), 0) AS TotalReceipts,
                    COALESCE((SELECT SUM(CAST(TotalPayments AS DECIMAL(18,4))) FROM t_CashFlowLine WHERE ProjectionID = cfp.ProjectionID), 0) AS TotalPayments,
                    COALESCE((SELECT SUM(CAST(NetCashFlow AS DECIMAL(18,4))) FROM t_CashFlowLine WHERE ProjectionID = cfp.ProjectionID), 0) AS TotalNetCash,
                    (SELECT TOP 1 CAST(ClosingBalance AS DECIMAL(18,4)) FROM t_CashFlowLine 
                     WHERE ProjectionID = cfp.ProjectionID 
                     ORDER BY TRY_CAST(MonthNumber AS INT) DESC) AS ClosingBalance
                FROM t_CashFlowProjection cfp
                LEFT JOIN t_Users u ON cfp.CreatedBy = u.UserID
                LEFT JOIN t_SalesForecastHeader sfh ON cfp.SalesForecastID = sfh.ForecastID
                LEFT JOIN t_PurchaseForecastHeader pfh ON cfp.PurchaseForecastID = pfh.ForecastID
                WHERE {_industry_filter('cfp.DemoIndustryCode')}
                ORDER BY cfp.CreatedDateKey DESC, cfp.ProjectionID DESC
            """), {'industry': industry}).fetchall()

        result = []
        for row in rows:
            m = row._mapping
            result.append({
                'ProjectionID': m['ProjectionID'],
                'ProjectionCode': m['ProjectionCode'],
                'ProjectionName': m['ProjectionName'] or 'Unnamed Projection',
                'PeriodPersianYear': m['PeriodPersianYear'],
                'PeriodPersianSeason': m['PeriodPersianSeason'],
                'PeriodType': m['PeriodType'] or 'season',
                'Status': m['Status'] or 'Draft',
                'CreatedByName': m['CreatedByName'] or 'Unknown',
                'SalesForecastCode': m['SalesForecastCode'] or '-',
                'PurchaseForecastCode': m['PurchaseForecastCode'] or '-',
                'OpeningBalanceInitial': _float(m['OpeningBalanceInitial']),
                'TotalReceipts': _float(m['TotalReceipts']),
                'TotalPayments': _float(m['TotalPayments']),
                'TotalNetCash': _float(m['TotalNetCash']),
                'ClosingBalance': _float(m['ClosingBalance']),
                'CurrencyCode': m['CurrencyCode'] or 'IRR',
                'Description': m['Description'] or ''
            })
        return jsonify({'success': True, 'projections': result})
    except Exception as e:
        logging.error(f"Error getting cash flow list: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/cash-flow/<string:projection_id>', methods=['GET'])
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def get_cash_flow(projection_id):
    industry = get_current_industry()
    engine = get_engine()
    try:
        with engine.connect() as conn:
            header_row = conn.execute(
                text(f"SELECT * FROM t_CashFlowProjection WHERE ProjectionID = :pid AND {_industry_filter()}"),
                {'pid': projection_id, 'industry': industry}
            ).fetchone()
            if not header_row:
                return jsonify({'success': False, 'error': 'Projection not found'}), 404
            header_dict = dict(header_row._mapping)

            lines = conn.execute(text("""
                SELECT * FROM t_CashFlowLine
                WHERE ProjectionID = :pid
                ORDER BY TRY_CAST(MonthNumber AS INT)
            """), {'pid': projection_id}).fetchall()

        lines_result = [dict(line._mapping) for line in lines]
        return jsonify({'success': True, 'header': header_dict, 'lines': lines_result})
    except Exception as e:
        logging.error(f"Error getting cash flow: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/cash-flow/calculate', methods=['POST'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def calculate_cash_flow():
    data = request.get_json() or {}
    sales_forecast_id = data.get('sales_forecast_id')
    purchase_forecast_id = data.get('purchase_forecast_id')
    opening_balance = _float(data.get('opening_balance', 0))
    bank_credit = _float(data.get('bank_credit', 0))
    shareholder_contributions = _float(data.get('shareholder_contributions', 0))
    period_type = data.get('period_type', 'season')

    engine = get_engine()
    try:
        with engine.connect() as conn:
            sales_lines = conn.execute(text("""
                SELECT 
                    Month1Quantity, Month1Price,
                    Month2Quantity, Month2Price,
                    Month3Quantity, Month3Price,
                    CashPct, Days30Pct, Days60Pct,
                    Days90Pct, Days120Pct, Days150Pct
                FROM t_SalesForecastLine
                WHERE ForecastID = :fid
            """), {'fid': sales_forecast_id}).fetchall()

            purchase_lines = conn.execute(text("""
                SELECT TotalPurchaseAmount,
                       CashPct, Days30Pct, Days60Pct,
                       Days90Pct, Days120Pct, Days150Pct
                FROM t_PurchaseForecastLine
                WHERE ForecastID = :fid
            """), {'fid': purchase_forecast_id}).fetchall()

        num_months = 3
        month_names = ['فروردین', 'اردیبهشت', 'خرداد']

        result = []
        current_balance = opening_balance

        for i in range(num_months):
            month_num = i + 1
            qty_key = f'Month{month_num}Quantity'
            price_key = f'Month{month_num}Price'

            sales_receipts = {'cash': 0.0, 'days30': 0.0, 'days60': 0.0,
                             'days90': 0.0, 'days120': 0.0, 'days150': 0.0}

            for row in sales_lines:
                m = row._mapping
                qty = _float(m.get(qty_key))
                price = _float(m.get(price_key))
                amount = qty * price
                if amount > 0:
                    sales_receipts['cash']    += amount * _float(m['CashPct'])    / 100
                    sales_receipts['days30']  += amount * _float(m['Days30Pct'])  / 100
                    sales_receipts['days60']  += amount * _float(m['Days60Pct'])  / 100
                    sales_receipts['days90']  += amount * _float(m['Days90Pct'])  / 100
                    sales_receipts['days120'] += amount * _float(m['Days120Pct']) / 100
                    sales_receipts['days150'] += amount * _float(m['Days150Pct']) / 100

            total_sales_receipts = sum(sales_receipts.values())

            purchase_payments = {'cash': 0.0, 'days30': 0.0, 'days60': 0.0,
                                 'days90': 0.0, 'days120': 0.0, 'days150': 0.0}

            for row in purchase_lines:
                m = row._mapping
                total_amount = _float(m['TotalPurchaseAmount'])
                if total_amount > 0:
                    purchase_payments['cash']    += total_amount * _float(m['CashPct'])    / 100
                    purchase_payments['days30']  += total_amount * _float(m['Days30Pct'])  / 100
                    purchase_payments['days60']  += total_amount * _float(m['Days60Pct'])  / 100
                    purchase_payments['days90']  += total_amount * _float(m['Days90Pct'])  / 100
                    purchase_payments['days120'] += total_amount * _float(m['Days120Pct']) / 100
                    purchase_payments['days150'] += total_amount * _float(m['Days150Pct']) / 100

            total_purchase_payments = sum(purchase_payments.values())

            loan_repayments = _float(data.get('loan_repayment_monthly', 0))
            bank_credit_month = bank_credit if month_num == 1 else 0
            shareholder_month = shareholder_contributions if month_num == 1 else 0
            mandatory_payments = _float(data.get('mandatory_payment_monthly', 0))

            total_receipts = total_sales_receipts + bank_credit_month + shareholder_month
            total_payments = total_purchase_payments + loan_repayments + mandatory_payments
            net_cash_flow = total_receipts - total_payments
            closing_balance = current_balance + net_cash_flow

            result.append({
                'month': month_num,
                'name': month_names[i] if i < len(month_names) else f'Month {month_num}',
                'opening_balance': current_balance,
                'sales_receipts': sales_receipts,
                'total_sales_receipts': total_sales_receipts,
                'purchase_payments': purchase_payments,
                'total_purchase_payments': total_purchase_payments,
                'loan_repayments': loan_repayments,
                'mandatory_payments': mandatory_payments,
                'bank_credit': bank_credit_month,
                'shareholder_contributions': shareholder_month,
                'total_receipts': total_receipts,
                'total_payments': total_payments,
                'net_cash_flow': net_cash_flow,
                'closing_balance': closing_balance
            })

            current_balance = closing_balance

        return jsonify({'success': True, 'cash_flow': result})
    except Exception as e:
        logging.error(f"Error calculating cash flow: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/cash-flow', methods=['POST'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def save_cash_flow():
    """D4=a — persist projection AND lines to t_CashFlowLine."""
    data = request.get_json() or {}
    user_id = session.get('user_id')
    industry = session.get('demo_industry')
    if not industry:
        return jsonify({'success': False, 'error': 'No industry selected'}), 400

    cash_flow_rows = data.get('cash_flow') or data.get('rows') or []
    if not isinstance(cash_flow_rows, list):
        return jsonify({'success': False, 'error': 'cash_flow must be an array'}), 400

    engine = get_engine()
    try:
        with engine.begin() as conn:
            projection_name = data.get('projection_name') or f"Cash Flow - {datetime.now().strftime('%Y-%m-%d')}"
            projection_code = f"CF-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            new_proj_id = _new_id()
            today_str = str(_today_key())

            conn.execute(text("""
                INSERT INTO t_CashFlowProjection (
                    ProjectionID, ProjectionCode, ProjectionName, SalesForecastID, PurchaseForecastID,
                    PeriodPersianYear, PeriodPersianSeason, Status,
                    CreatedBy, CreatedDateKey, DemoIndustryCode,
                    OpeningBalanceInitial, Description, PeriodType
                ) VALUES (
                    :pid, :code, :name, :sfid, :pfid,
                    :year, :season, 'Draft',
                    :user, :datekey, :industry,
                    :opening, :desc, :ptype
                )
            """), {
                'pid': new_proj_id,
                'code': projection_code,
                'name': projection_name,
                'sfid': data.get('sales_forecast_id'),
                'pfid': data.get('purchase_forecast_id'),
                'year': data.get('PeriodPersianYear'),
                'season': data.get('PeriodPersianSeason'),
                'user': user_id,
                'datekey': today_str,
                'industry': industry,
                'opening': str(_float(data.get('opening_balance', 0))),
                'desc': data.get('Description', ''),
                'ptype': data.get('period_type', 'season')
            })

            for row in cash_flow_rows:
                sr = row.get('sales_receipts') or {}
                pp = row.get('purchase_payments') or {}
                conn.execute(text("""
                    INSERT INTO t_CashFlowLine (
                        LineID, ProjectionID, MonthNumber, MonthPersianName, MonthPersianYear,
                        OpeningBalance,
                        SalesReceiptsCash, SalesReceipts30, SalesReceipts60, SalesReceipts90, SalesReceipts120, SalesReceipts150,
                        TotalSalesReceipts,
                        PurchasePaymentsCash, PurchasePayments30, PurchasePayments60, PurchasePayments90, PurchasePayments120, PurchasePayments150,
                        TotalPurchasePayments,
                        LoanRepayments, MandatoryPayments, TotalOtherPayments,
                        ShareholderContributions, BankCreditReceived, TotalOtherReceipts,
                        TotalReceipts, TotalPayments, NetCashFlow, ClosingBalance,
                        CreatedDateKey, IsManual
                    ) VALUES (
                        :lid, :pid, :mon, :mname, :myear,
                        :opening,
                        :src, :sr30, :sr60, :sr90, :sr120, :sr150,
                        :tsr,
                        :ppc, :pp30, :pp60, :pp90, :pp120, :pp150,
                        :tpp,
                        :loan, :mand, :tother_pay,
                        :shc, :bcr, :tother_rec,
                        :trec, :tpay, :net, :close,
                        :datekey, '0'
                    )
                """), {
                    'lid': _new_id(),
                    'pid': new_proj_id,
                    'mon': str(row.get('month', 0)),
                    'mname': row.get('name') or '',
                    'myear': str(data.get('PeriodPersianYear') or ''),
                    'opening': str(_float(row.get('opening_balance'))),
                    'src':   str(_float(sr.get('cash'))),
                    'sr30':  str(_float(sr.get('days30'))),
                    'sr60':  str(_float(sr.get('days60'))),
                    'sr90':  str(_float(sr.get('days90'))),
                    'sr120': str(_float(sr.get('days120'))),
                    'sr150': str(_float(sr.get('days150'))),
                    'tsr':   str(_float(row.get('total_sales_receipts'))),
                    'ppc':   str(_float(pp.get('cash'))),
                    'pp30':  str(_float(pp.get('days30'))),
                    'pp60':  str(_float(pp.get('days60'))),
                    'pp90':  str(_float(pp.get('days90'))),
                    'pp120': str(_float(pp.get('days120'))),
                    'pp150': str(_float(pp.get('days150'))),
                    'tpp':   str(_float(row.get('total_purchase_payments'))),
                    'loan':  str(_float(row.get('loan_repayments'))),
                    'mand':  str(_float(row.get('mandatory_payments'))),
                    'tother_pay': str(_float(row.get('loan_repayments')) + _float(row.get('mandatory_payments'))),
                    'shc':   str(_float(row.get('shareholder_contributions'))),
                    'bcr':   str(_float(row.get('bank_credit'))),
                    'tother_rec': str(_float(row.get('shareholder_contributions')) + _float(row.get('bank_credit'))),
                    'trec':  str(_float(row.get('total_receipts'))),
                    'tpay':  str(_float(row.get('total_payments'))),
                    'net':   str(_float(row.get('net_cash_flow'))),
                    'close': str(_float(row.get('closing_balance'))),
                    'datekey': today_str
                })

        return jsonify({'success': True, 'ProjectionID': new_proj_id, 'ProjectionCode': projection_code})
    except Exception as e:
        logging.error(f"Error saving cash flow: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/cash-flow/<string:projection_id>/delete', methods=['DELETE'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def delete_cash_flow(projection_id):
    engine = get_engine()
    try:
        with engine.begin() as conn:
            exists = conn.execute(
                text("SELECT ProjectionID FROM t_CashFlowProjection WHERE ProjectionID = :pid"),
                {'pid': projection_id}
            ).scalar()
            if not exists:
                return jsonify({'success': False, 'error': 'Projection not found'}), 404

            conn.execute(text("DELETE FROM t_CashFlowLine WHERE ProjectionID = :pid"), {'pid': projection_id})
            conn.execute(text("DELETE FROM t_CashFlowProjection WHERE ProjectionID = :pid"), {'pid': projection_id})

        return jsonify({'success': True, 'message': 'Projection deleted successfully'})
    except Exception as e:
        logging.error(f"Error deleting cash flow: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/cash-flow/<string:projection_id>/duplicate', methods=['POST'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def duplicate_cash_flow(projection_id):
    industry = get_current_industry()
    user_id = session.get('user_id')

    engine = get_engine()
    try:
        with engine.begin() as conn:
            original = conn.execute(
                text("SELECT * FROM t_CashFlowProjection WHERE ProjectionID = :pid"),
                {'pid': projection_id}
            ).fetchone()
            if not original:
                return jsonify({'success': False, 'error': 'Projection not found'}), 404
            om = original._mapping

            new_id = _new_id()
            new_code = f"CF-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            new_name = f"{om['ProjectionName'] or 'Cash Flow'} (Copy)"

            conn.execute(text("""
                INSERT INTO t_CashFlowProjection (
                    ProjectionID, ProjectionCode, ProjectionName, SalesForecastID, PurchaseForecastID,
                    PeriodPersianYear, PeriodPersianSeason, Status,
                    CreatedBy, CreatedDateKey, DemoIndustryCode,
                    OpeningBalanceInitial, Description, PeriodType
                ) VALUES (
                    :pid, :code, :name, :sfid, :pfid,
                    :year, :season, 'Draft',
                    :user, :datekey, :industry,
                    :opening, :desc, :ptype
                )
            """), {
                'pid': new_id,
                'code': new_code,
                'name': new_name,
                'sfid': om['SalesForecastID'],
                'pfid': om['PurchaseForecastID'],
                'year': om['PeriodPersianYear'],
                'season': om['PeriodPersianSeason'],
                'user': user_id,
                'datekey': str(_today_key()),
                'industry': industry,
                'opening': om['OpeningBalanceInitial'] or '0',
                'desc': f"Duplicate of {om['ProjectionName']}",
                'ptype': om['PeriodType'] or 'season'
            })

        return jsonify({'success': True, 'ProjectionID': new_id, 'message': 'Projection duplicated'})
    except Exception as e:
        logging.error(f"Error duplicating cash flow: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@finance_api_bp.route('/cash-flow/<string:projection_id>/chart-data', methods=['GET'])
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def get_cash_flow_chart_data(projection_id):
    industry = get_current_industry()
    lang = session.get('lang', 'en')

    engine = get_engine()
    try:
        with engine.connect() as conn:
            header = conn.execute(
                text(f"SELECT 1 FROM t_CashFlowProjection WHERE ProjectionID = :pid AND {_industry_filter()}"),
                {'pid': projection_id, 'industry': industry}
            ).first()
            if not header:
                return jsonify({'success': False, 'error': 'Projection not found'}), 404

            lines = conn.execute(text("""
                SELECT MonthNumber, TotalReceipts, TotalPayments, NetCashFlow, ClosingBalance
                FROM t_CashFlowLine
                WHERE ProjectionID = :pid
                ORDER BY TRY_CAST(MonthNumber AS INT)
            """), {'pid': projection_id}).fetchall()

        persian_months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                          'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
        if lang == 'en':
            persian_months = [f'Month {i+1}' for i in range(12)]

        chart_data = {'labels': [], 'receipts': [], 'payments': [],
                      'net_cash': [], 'closing_balance': []}

        for line in lines:
            m = line._mapping
            month_num = _int(m['MonthNumber'], 1)
            month_name = persian_months[month_num - 1] if 1 <= month_num <= 12 else f"Month {month_num}"
            chart_data['labels'].append(month_name)
            chart_data['receipts'].append(_float(m['TotalReceipts']))
            chart_data['payments'].append(_float(m['TotalPayments']))
            chart_data['net_cash'].append(_float(m['NetCashFlow']))
            chart_data['closing_balance'].append(_float(m['ClosingBalance']))

        return jsonify({'success': True, 'chart_data': chart_data})
    except Exception as e:
        logging.error(f"Error getting chart data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
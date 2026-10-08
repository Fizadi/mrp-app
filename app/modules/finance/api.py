# app/modules/finance/api.py
from flask import jsonify, request, session
from . import finance_api_bp
from app.core.auth import login_required, permission_required
from app.core.database import get_db_connection, execute_query, fetch_all, fetch_one, get_last_insert_id
import logging
from datetime import datetime

# ============================================================================
# HELPER
# ============================================================================

def get_current_industry():
    """
    Get current industry from session.
    Returns None if no industry has been selected yet.
    """
    return session.get('demo_industry')


# ============================================================================
# SALES FORECAST API ENDPOINTS
# ============================================================================

@finance_api_bp.route('/sales-forecast/stats', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_sales_forecast_stats():
    """Get KPI statistics for sales forecasts."""
    logging.info("=== get_sales_forecast_stats called ===")
    industry = get_current_industry()
    logging.info(f"Current industry: {industry}")
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        # If industry is None, show all. Otherwise filter.
        total = fetch_one(conn, """
            SELECT COUNT(*) FROM t_SalesForecastHeader
            WHERE (? IS NULL OR DemoIndustryCode = ?)
        """, [industry, industry])[0]
        
        approved = fetch_one(conn, """
            SELECT COUNT(*) FROM t_SalesForecastHeader
            WHERE (? IS NULL OR DemoIndustryCode = ?)
            AND Status = 'Approved'
        """, [industry, industry])[0]
        
        draft = fetch_one(conn, """
            SELECT COUNT(*) FROM t_SalesForecastHeader
            WHERE (? IS NULL OR DemoIndustryCode = ?)
            AND Status = 'Draft'
        """, [industry, industry])[0]
        
        total_value = fetch_one(conn, """
            SELECT COALESCE(SUM(CAST(sfl.TotalAmount AS DECIMAL(18,4))), 0) 
            FROM t_SalesForecastHeader sfh
            JOIN t_SalesForecastLine sfl ON sfh.ForecastID = sfl.ForecastID
            WHERE (? IS NULL OR sfh.DemoIndustryCode = ?)
            AND sfh.Status = 'Approved'
        """, [industry, industry])[0]
        
        return jsonify({
            'success': True,
            'stats': {
                'total': total or 0,
                'approved': approved or 0,
                'draft': draft or 0,
                'total_value': float(total_value) if total_value else 0
            }
        })
    except Exception as e:
        logging.error(f"Error getting sales forecast stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/sales-forecast', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_sales_forecasts():
    """Get list of sales forecasts with industry filtering."""
    logging.info("=== get_sales_forecasts called ===")
    industry = get_current_industry()
    logging.info(f"Current industry: {industry}")
    lang = session.get('lang', 'en')
    
    year = request.args.get('year')
    season = request.args.get('season')
    status_filter = request.args.get('status')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        query = """
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
                u.FullName as CreatedByName,
                COALESCE(
                    (SELECT SUM(CAST(TotalAmount AS DECIMAL(18,4))) FROM t_SalesForecastLine WHERE ForecastID = sfh.ForecastID),
                    0
                ) as TotalAmount,
                COALESCE(
                    (SELECT SUM(CAST(TotalQuantity AS DECIMAL(18,4))) FROM t_SalesForecastLine WHERE ForecastID = sfh.ForecastID),
                    0
                ) as TotalQuantity
            FROM t_SalesForecastHeader sfh
            LEFT JOIN t_Users u ON sfh.CreatedBy = u.UserID
            WHERE (? IS NULL OR sfh.DemoIndustryCode = ?)
        """
        params = [industry, industry]
        
        if year:
            query += " AND sfh.PeriodPersianYear = ?"
            params.append(int(year))
        if season:
            query += " AND sfh.PeriodPersianSeason = ?"
            params.append(season)
        if status_filter:
            query += " AND sfh.Status = ?"
            params.append(status_filter)
        
        query += " ORDER BY sfh.PeriodStartDateKey DESC, sfh.CreatedDateKey DESC"
        
        rows = fetch_all(conn, query, params)
        
        result = []
        for row in rows:
            forecast_name = row[2]
            if lang == 'fa' and row[3] and str(row[3]).strip():
                forecast_name = row[3]
            
            result.append({
                'ForecastID': row[0],
                'ForecastCode': row[1],
                'ForecastName': forecast_name,
                'ForecastNameLocal': row[3],
                'PeriodStartDateKey': row[4],
                'PeriodEndDateKey': row[5],
                'PeriodPersianYear': row[6],
                'PeriodPersianSeason': row[7],
                'Status': row[8],
                'TotalAmount': float(row[14]) if row[14] else 0,
                'TotalQuantity': float(row[15]) if row[15] else 0,
                'CreatedByName': row[13] or 'Unknown',
                'CurrencyCode': row[12] or 'IRR',
                'DemoIndustryCode': row[11]
            })
        
        return jsonify({'success': True, 'forecasts': result})
    except Exception as e:
        logging.error(f"Error getting sales forecasts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/sales-forecast/<int:forecast_id>', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_sales_forecast(forecast_id):
    """Get sales forecast details with lines."""
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        header = fetch_one(conn, """
            SELECT * FROM t_SalesForecastHeader
            WHERE ForecastID = ? AND (? IS NULL OR DemoIndustryCode = ?)
        """, [forecast_id, industry, industry])
        
        if not header:
            return jsonify({'success': False, 'error': 'Forecast not found'}), 404
        
        lines = fetch_all(conn, """
            SELECT 
                sfl.*,
                pc.CategoryName,
                pc.CategoryNameLocal
            FROM t_SalesForecastLine sfl
            JOIN t_ProductCategory pc ON sfl.ProductCategoryID = pc.CategoryID
            WHERE sfl.ForecastID = ?
            ORDER BY sfl.LineNumber
        """, [forecast_id])
        
        header_dict = {}
        for key in header.cursor_description:
            header_dict[key[0]] = getattr(header, key[0])
        
        lines_result = []
        for line in lines:
            category_name = line[11] if len(line) > 11 else ''
            category_name_local = line[12] if len(line) > 12 else ''
            
            if lang == 'fa' and category_name_local and str(category_name_local).strip():
                category_name = category_name_local
            
            lines_result.append({
                'LineID': line[0],
                'LineNumber': line[3],
                'ProductCategoryID': line[2],
                'CategoryName': category_name,
                'CategoryNameLocal': category_name_local,
                'Month1Quantity': line[4] or 0,
                'Month1Price': line[5] or 0,
                'Month1Amount': line[6] or 0,
                'Month2Quantity': line[7] or 0,
                'Month2Price': line[8] or 0,
                'Month2Amount': line[9] or 0,
                'Month3Quantity': line[10] or 0,
                'Month3Price': line[11] if len(line) > 11 else 0,
                'Month3Amount': line[12] if len(line) > 12 else 0,
                'TotalQuantity': line[13] if len(line) > 13 else 0,
                'TotalAmount': line[14] if len(line) > 14 else 0,
                'CashPct': line[15] if len(line) > 15 else 0,
                'Days30Pct': line[16] if len(line) > 16 else 0,
                'Days60Pct': line[17] if len(line) > 17 else 0,
                'Days90Pct': line[18] if len(line) > 18 else 0,
                'Days120Pct': line[19] if len(line) > 19 else 0,
                'Days150Pct': line[20] if len(line) > 20 else 0
            })
        
        return jsonify({
            'success': True,
            'header': header_dict,
            'lines': lines_result
        })
    except Exception as e:
        logging.error(f"Error getting sales forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/sales-forecast', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_CREATE')
def create_sales_forecast():
    """Create a new sales forecast."""
    data = request.get_json()
    user_id = session.get('user_id')
    industry = get_current_industry()
    
    if not industry:
        return jsonify({'success': False, 'error': 'No industry selected. Please select an industry first.'}), 400
    
    required = ['ForecastName', 'PeriodPersianYear', 'PeriodPersianSeason']
    for field in required:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing field: {field}'}), 400
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        year = data['PeriodPersianYear']
        season = data['PeriodPersianSeason']
        season_code = {'بهار': 1, 'تابستان': 2, 'پاییز': 3, 'زمستان': 4}.get(season, 1)
        
        seq = fetch_one(conn, """
            SELECT COUNT(*) + 1 FROM t_SalesForecastHeader 
            WHERE PeriodPersianYear = ? AND PeriodPersianSeason = ?
        """, [year, season])[0]
        
        forecast_code = f"SALES-{year}-{season_code:02d}-{seq:03d}"
        
        execute_query(conn, """
            INSERT INTO t_SalesForecastHeader (
                ForecastCode, ForecastName, PeriodPersianYear, PeriodPersianSeason,
                Status, CreatedBy, CreatedDateKey, DemoIndustryCode, CurrencyCode,
                Description
            ) VALUES (?, ?, ?, ?, 'Draft', ?, ?, ?, ?, ?)
        """, [
            forecast_code,
            data['ForecastName'],
            year,
            season,
            user_id,
            int(datetime.now().strftime('%Y%m%d')),
            industry,
            data.get('CurrencyCode', 'IRR'),
            data.get('Description', '')
        ])
        
        forecast_id = get_last_insert_id(conn)
        conn.commit()
        
        return jsonify({'success': True, 'ForecastID': forecast_id, 'ForecastCode': forecast_code})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error creating sales forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/sales-forecast/<int:forecast_id>/approve', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_APPROVE')
def approve_sales_forecast(forecast_id):
    """Approve a sales forecast."""
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        status = fetch_one(conn, """
            SELECT Status FROM t_SalesForecastHeader WHERE ForecastID = ?
        """, [forecast_id])
        
        if not status:
            return jsonify({'success': False, 'error': 'Forecast not found'}), 404
        if status[0] != 'Draft':
            return jsonify({'success': False, 'error': 'Only Draft forecasts can be approved'}), 400
        
        execute_query(conn, """
            UPDATE t_SalesForecastHeader 
            SET Status = 'Approved',
                ApprovedBy = ?,
                ApprovedDateKey = ?
            WHERE ForecastID = ?
        """, [session.get('user_id'), int(datetime.now().strftime('%Y%m%d')), forecast_id])
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Forecast approved successfully'})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error approving sales forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ============================================================================
# PURCHASE FORECAST API ENDPOINTS
# ============================================================================

@finance_api_bp.route('/purchase-forecast/stats', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_purchase_forecast_stats():
    """Get KPI statistics for purchase forecasts."""
    logging.info("=== get_purchase_forecast_stats called ===")
    industry = get_current_industry()
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        total = fetch_one(conn, """
            SELECT COUNT(*) FROM t_PurchaseForecastHeader
            WHERE (? IS NULL OR DemoIndustryCode = ?)
        """, [industry, industry])[0]
        
        approved = fetch_one(conn, """
            SELECT COUNT(*) FROM t_PurchaseForecastHeader
            WHERE (? IS NULL OR DemoIndustryCode = ?)
            AND Status = 'Approved'
        """, [industry, industry])[0]
        
        draft = fetch_one(conn, """
            SELECT COUNT(*) FROM t_PurchaseForecastHeader
            WHERE (? IS NULL OR DemoIndustryCode = ?)
            AND Status = 'Draft'
        """, [industry, industry])[0]
        
        total_value = fetch_one(conn, """
            SELECT COALESCE(SUM(CAST(pfl.TotalPurchaseAmount AS DECIMAL(18,4))), 0) 
            FROM t_PurchaseForecastHeader pfh
            JOIN t_PurchaseForecastLine pfl ON pfh.ForecastID = pfl.ForecastID
            WHERE (? IS NULL OR pfh.DemoIndustryCode = ?)
            AND pfh.Status = 'Approved'
        """, [industry, industry])[0]
        
        return jsonify({
            'success': True,
            'stats': {
                'total': total or 0,
                'approved': approved or 0,
                'draft': draft or 0,
                'total_value': float(total_value) if total_value else 0
            }
        })
    except Exception as e:
        logging.error(f"Error getting purchase forecast stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/purchase-forecast', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_purchase_forecasts():
    """Get list of purchase forecasts with industry filtering."""
    logging.info("=== get_purchase_forecasts called ===")
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    year = request.args.get('year')
    season = request.args.get('season')
    status_filter = request.args.get('status')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        query = """
            SELECT 
                pfh.ForecastID,
                pfh.ForecastCode,
                pfh.ForecastName,
                pfh.ForecastNameLocal,
                pfh.PeriodPersianYear,
                pfh.PeriodPersianSeason,
                pfh.Status,
                pfh.CreatedBy,
                pfh.CreatedDateKey,
                u.FullName as CreatedByName,
                COALESCE(
                    (SELECT SUM(CAST(TotalPurchaseAmount AS DECIMAL(18,4))) FROM t_PurchaseForecastLine WHERE ForecastID = pfh.ForecastID),
                    0
                ) as TotalAmount,
                COALESCE(
                    (SELECT SUM(CAST(ExpectedConsumption AS DECIMAL(18,4))) FROM t_PurchaseForecastLine WHERE ForecastID = pfh.ForecastID),
                    0
                ) as TotalConsumption
            FROM t_PurchaseForecastHeader pfh
            LEFT JOIN t_Users u ON pfh.CreatedBy = u.UserID
            WHERE (? IS NULL OR pfh.DemoIndustryCode = ?)
        """
        params = [industry, industry]
        
        if year:
            query += " AND pfh.PeriodPersianYear = ?"
            params.append(int(year))
        if season:
            query += " AND pfh.PeriodPersianSeason = ?"
            params.append(season)
        if status_filter:
            query += " AND pfh.Status = ?"
            params.append(status_filter)
        
        query += " ORDER BY pfh.PeriodStartDateKey DESC, pfh.CreatedDateKey DESC"
        
        rows = fetch_all(conn, query, params)
        
        result = []
        for row in rows:
            forecast_name = row[2]
            if lang == 'fa' and row[3] and str(row[3]).strip():
                forecast_name = row[3]
            
            result.append({
                'ForecastID': row[0],
                'ForecastCode': row[1],
                'ForecastName': forecast_name,
                'ForecastNameLocal': row[3],
                'PeriodPersianYear': row[4],
                'PeriodPersianSeason': row[5],
                'Status': row[6],
                'TotalAmount': float(row[10]) if row[10] else 0,
                'TotalConsumption': float(row[11]) if row[11] else 0,
                'CreatedByName': row[9] or 'Unknown'
            })
        
        return jsonify({'success': True, 'forecasts': result})
    except Exception as e:
        logging.error(f"Error getting purchase forecasts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/purchase-forecast/<int:forecast_id>', methods=['GET'])
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def get_purchase_forecast(forecast_id):
    """Get purchase forecast details with lines."""
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        header = fetch_one(conn, """
            SELECT * FROM t_PurchaseForecastHeader
            WHERE ForecastID = ? AND (? IS NULL OR DemoIndustryCode = ?)
        """, [forecast_id, industry, industry])
        
        if not header:
            return jsonify({'success': False, 'error': 'Forecast not found'}), 404
        
        lines = fetch_all(conn, """
            SELECT 
                pfl.*,
                p.ProductId,
                p.descEnglish,
                p.descFarsi,
                pc.CategoryName
            FROM t_PurchaseForecastLine pfl
            JOIN t_Product p ON pfl.ProductID = p.ProductId
            JOIN t_ProductCategory pc ON pfl.ProductCategoryID = pc.CategoryID
            WHERE pfl.ForecastID = ?
            ORDER BY pfl.LineNumber
        """, [forecast_id])
        
        header_dict = {}
        for key in header.cursor_description:
            header_dict[key[0]] = getattr(header, key[0])
        
        lines_result = []
        for line in lines:
            lines_result.append({
                'LineID': line[0],
                'LineNumber': line[3],
                'ProductID': line[11] if len(line) > 11 else None,
                'ProductName': line[12] if len(line) > 12 else '',
                'CategoryName': line[14] if len(line) > 14 else '',
                'ExpectedConsumption': float(line[4]) if line[4] else 0,
                'BeginningInventoryRate': float(line[5]) if line[5] else 0,
                'EstimatedPurchaseRate': float(line[6]) if line[6] else 0,
                'TotalPurchaseAmount': float(line[7]) if line[7] else 0,
                'CashPct': float(line[8]) if line[8] else 0,
                'Days30Pct': float(line[9]) if line[9] else 0,
                'Days60Pct': float(line[10]) if line[10] else 0,
                'Days90Pct': float(line[15]) if len(line) > 15 else 0,
                'Days120Pct': float(line[16]) if len(line) > 16 else 0,
                'Days150Pct': float(line[17]) if len(line) > 17 else 0
            })
        
        return jsonify({
            'success': True,
            'header': header_dict,
            'lines': lines_result
        })
    except Exception as e:
        logging.error(f"Error getting purchase forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/purchase-forecast', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_CREATE')
def create_purchase_forecast():
    """Create a new purchase forecast."""
    data = request.get_json()
    user_id = session.get('user_id')
    industry = get_current_industry()
    
    if not industry:
        return jsonify({'success': False, 'error': 'No industry selected. Please select an industry first.'}), 400
    
    required = ['ForecastName', 'PeriodPersianYear', 'PeriodPersianSeason']
    for field in required:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing field: {field}'}), 400
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        year = data['PeriodPersianYear']
        season = data['PeriodPersianSeason']
        season_code = {'بهار': 1, 'تابستان': 2, 'پاییز': 3, 'زمستان': 4}.get(season, 1)
        
        forecast_code = f"PURCHASE-{year}-{season_code:02d}"
        
        execute_query(conn, """
            INSERT INTO t_PurchaseForecastHeader (
                ForecastCode, ForecastName, PeriodPersianYear, PeriodPersianSeason,
                Status, CreatedBy, CreatedDateKey, DemoIndustryCode, CurrencyCode,
                Description
            ) VALUES (?, ?, ?, ?, 'Draft', ?, ?, ?, ?, ?)
        """, [
            forecast_code,
            data['ForecastName'],
            year,
            season,
            user_id,
            int(datetime.now().strftime('%Y%m%d')),
            industry,
            data.get('CurrencyCode', 'IRR'),
            data.get('Description', '')
        ])
        
        forecast_id = get_last_insert_id(conn)
        conn.commit()
        
        return jsonify({'success': True, 'ForecastID': forecast_id, 'ForecastCode': forecast_code})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error creating purchase forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/purchase-forecast/<int:forecast_id>/approve', methods=['POST'])
@login_required
@permission_required('FINANCE_FORECAST_APPROVE')
def approve_purchase_forecast(forecast_id):
    """Approve a purchase forecast."""
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        status = fetch_one(conn, """
            SELECT Status FROM t_PurchaseForecastHeader WHERE ForecastID = ?
        """, [forecast_id])
        
        if not status:
            return jsonify({'success': False, 'error': 'Forecast not found'}), 404
        if status[0] != 'Draft':
            return jsonify({'success': False, 'error': 'Only Draft forecasts can be approved'}), 400
        
        execute_query(conn, """
            UPDATE t_PurchaseForecastHeader 
            SET Status = 'Approved',
                ApprovedBy = ?,
                ApprovedDateKey = ?
            WHERE ForecastID = ?
        """, [session.get('user_id'), int(datetime.now().strftime('%Y%m%d')), forecast_id])
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Forecast approved successfully'})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error approving purchase forecast: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ============================================================================
# DROPDOWNS
# ============================================================================

@finance_api_bp.route('/product-categories', methods=['GET'])
@login_required
def get_product_categories():
    """Get product categories for dropdown (filtered by industry)."""
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        rows = fetch_all(conn, """
            SELECT 
                CategoryID,
                CategoryCode,
                CategoryName,
                CategoryNameLocal,
                DemoIndustryCode
            FROM t_ProductCategory
            WHERE (? IS NULL OR DemoIndustryCode = ?)
            AND IsActive = 1
            ORDER BY CategoryName
        """, [industry, industry])
        
        result = []
        for row in rows:
            display_name = row[3] if lang == 'fa' and row[3] else row[2]
            result.append({
                'CategoryID': row[0],
                'CategoryCode': row[1],
                'CategoryName': row[2],
                'CategoryNameLocal': row[3],
                'DisplayName': display_name
            })
        
        return jsonify({'success': True, 'categories': result})
    except Exception as e:
        logging.error(f"Error getting categories: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/raw-materials', methods=['GET'])
@login_required
def get_raw_materials():
    """Get raw materials for dropdown (filtered by industry)."""
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        rows = fetch_all(conn, """
            SELECT 
                ProductId,
                descEnglish,
                descFarsi,
                ProductCategoryCode,
                UOM
            FROM t_Product
            WHERE (? IS NULL OR DemoIndustryCode = ?)
            AND ProcurementType = 'Purchased'
            AND IsManufactured = 0
            ORDER BY descEnglish
        """, [industry, industry])
        
        result = []
        for row in rows:
            display_name = row[2] if lang == 'fa' and row[2] else row[1]
            result.append({
                'ProductId': row[0],
                'Name': display_name,
                'UOM': row[4]
            })
        
        return jsonify({'success': True, 'materials': result})
    except Exception as e:
        logging.error(f"Error getting raw materials: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()
        
   
# ============================================================================
# CASH FLOW API ENDPOINTS
# ============================================================================

@finance_api_bp.route('/cash-flow/list', methods=['GET'])
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def get_cash_flow_list():
    """Get list of all cash flow projections."""
    logging.info("=== get_cash_flow_list called ===")
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        rows = fetch_all(conn, """
            SELECT 
                cfp.ProjectionID,
                cfp.ProjectionCode,
                cfp.ProjectionName,
                cfp.PeriodPersianYear,
                cfp.PeriodPersianSeason,
                cfp.Status,
                cfp.CreatedBy,
                cfp.CreatedDateKey,
                cfp.OpeningBalanceInitial,
                cfp.DemoIndustryCode,
                cfp.CurrencyCode,
                cfp.Description,
                cfp.PeriodType,
                u.FullName as CreatedByName,
                sfh.ForecastCode as SalesForecastCode,
                pfh.ForecastCode as PurchaseForecastCode,
                COALESCE(
                    (SELECT SUM(CAST(TotalReceipts AS DECIMAL(18,4))) FROM t_CashFlowLine WHERE ProjectionID = cfp.ProjectionID),
                    0
                ) as TotalReceipts,
                COALESCE(
                    (SELECT SUM(CAST(TotalPayments AS DECIMAL(18,4))) FROM t_CashFlowLine WHERE ProjectionID = cfp.ProjectionID),
                    0
                ) as TotalPayments,
                COALESCE(
                    (SELECT SUM(CAST(NetCashFlow AS DECIMAL(18,4))) FROM t_CashFlowLine WHERE ProjectionID = cfp.ProjectionID),
                    0
                ) as TotalNetCash,
                (SELECT TOP 1 CAST(ClosingBalance AS DECIMAL(18,4)) FROM t_CashFlowLine 
                 WHERE ProjectionID = cfp.ProjectionID 
                 ORDER BY MonthNumber DESC) as ClosingBalance
            FROM t_CashFlowProjection cfp
            LEFT JOIN t_Users u ON cfp.CreatedBy = u.UserID
            LEFT JOIN t_SalesForecastHeader sfh ON cfp.SalesForecastID = sfh.ForecastID
            LEFT JOIN t_PurchaseForecastHeader pfh ON cfp.PurchaseForecastID = pfh.ForecastID
            WHERE (? IS NULL OR cfp.DemoIndustryCode = ?)
            ORDER BY cfp.CreatedDateKey DESC, cfp.ProjectionID DESC
        """, [industry, industry])
        
        result = []
        for row in rows:
            result.append({
                'ProjectionID': row[0],
                'ProjectionCode': row[1],
                'ProjectionName': row[2] or 'Unnamed Projection',
                'PeriodPersianYear': row[3],
                'PeriodPersianSeason': row[4],
                'PeriodType': row[12] or 'season',
                'Status': row[5] or 'Draft',
                'CreatedByName': row[13] or 'Unknown',
                'SalesForecastCode': row[14] or '-',
                'PurchaseForecastCode': row[15] or '-',
                'OpeningBalanceInitial': float(row[8]) if row[8] else 0,
                'TotalReceipts': float(row[16]) if row[16] else 0,
                'TotalPayments': float(row[17]) if row[17] else 0,
                'TotalNetCash': float(row[18]) if row[18] else 0,
                'ClosingBalance': float(row[19]) if row[19] else 0,
                'CurrencyCode': row[10] or 'IRR',
                'Description': row[11] or ''
            })
        
        return jsonify({'success': True, 'projections': result})
    except Exception as e:
        logging.error(f"Error getting cash flow list: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/cash-flow/<int:projection_id>', methods=['GET'])
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def get_cash_flow(projection_id):
    """Get cash flow projection details."""
    industry = get_current_industry()
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        header = fetch_one(conn, """
            SELECT * FROM t_CashFlowProjection
            WHERE ProjectionID = ? AND (? IS NULL OR DemoIndustryCode = ?)
        """, [projection_id, industry, industry])
        
        if not header:
            return jsonify({'success': False, 'error': 'Projection not found'}), 404
        
        lines = fetch_all(conn, """
            SELECT * FROM t_CashFlowLine
            WHERE ProjectionID = ?
            ORDER BY MonthNumber
        """, [projection_id])
        
        header_dict = {}
        for key in header.cursor_description:
            header_dict[key[0]] = getattr(header, key[0])
        
        lines_result = []
        for line in lines:
            line_dict = {}
            for key in line.cursor_description:
                line_dict[key[0]] = getattr(line, key[0])
            lines_result.append(line_dict)
        
        return jsonify({
            'success': True,
            'header': header_dict,
            'lines': lines_result
        })
    except Exception as e:
        logging.error(f"Error getting cash flow: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/cash-flow/calculate', methods=['POST'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def calculate_cash_flow():
    """
    Calculate cash flow projection from sales and purchase forecasts.
    """
    data = request.get_json()
    sales_forecast_id = data.get('sales_forecast_id')
    purchase_forecast_id = data.get('purchase_forecast_id')
    opening_balance = data.get('opening_balance', 0)
    bank_credit = data.get('bank_credit', 0)
    shareholder_contributions = data.get('shareholder_contributions', 0)
    
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    period_type = data.get('period_type', 'season')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        # Get sales forecast with lines
        sales_lines = fetch_all(conn, """
            SELECT 
                sfl.*,
                pc.CategoryName,
                pc.CategoryNameLocal
            FROM t_SalesForecastLine sfl
            JOIN t_ProductCategory pc ON sfl.ProductCategoryID = pc.CategoryID
            WHERE sfl.ForecastID = ?
        """, [sales_forecast_id])
        
        # Get purchase forecast with lines
        purchase_lines = fetch_all(conn, """
            SELECT *
            FROM t_PurchaseForecastLine
            WHERE ForecastID = ?
        """, [purchase_forecast_id])
        
        num_months = 3
        month_names = ['فروردین', 'اردیبهشت', 'خرداد']
        
        months = []
        for i in range(num_months):
            months.append({
                'month': i + 1,
                'name': month_names[i] if i < len(month_names) else f'Month {i+1}'
            })
        
        result = []
        current_balance = opening_balance
        
        for month_data in months:
            month_num = month_data['month']
            
            # Calculate sales receipts for this month
            sales_receipts = {
                'cash': 0, 'days30': 0, 'days60': 0,
                'days90': 0, 'days120': 0, 'days150': 0
            }
            
            for line in sales_lines:
                qty = line[3 + month_num] if len(line) > 3 + month_num else 0
                price = line[3 + month_num + 3] if len(line) > 3 + month_num + 3 else 0
                amount = float(qty or 0) * float(price or 0)
                
                if amount > 0:
                    cash_idx = 15 + (month_num - 1) * 5
                    d30_idx = 16 + (month_num - 1) * 5
                    d60_idx = 17 + (month_num - 1) * 5
                    d90_idx = 18 + (month_num - 1) * 5
                    d120_idx = 19 + (month_num - 1) * 5
                    d150_idx = 20 + (month_num - 1) * 5
                    
                    sales_receipts['cash'] += amount * float(line[cash_idx] if len(line) > cash_idx else 0 or 0) / 100
                    sales_receipts['days30'] += amount * float(line[d30_idx] if len(line) > d30_idx else 0 or 0) / 100
                    sales_receipts['days60'] += amount * float(line[d60_idx] if len(line) > d60_idx else 0 or 0) / 100
                    sales_receipts['days90'] += amount * float(line[d90_idx] if len(line) > d90_idx else 0 or 0) / 100
                    sales_receipts['days120'] += amount * float(line[d120_idx] if len(line) > d120_idx else 0 or 0) / 100
                    sales_receipts['days150'] += amount * float(line[d150_idx] if len(line) > d150_idx else 0 or 0) / 100
            
            total_sales_receipts = sum(sales_receipts.values())
            
            # Calculate purchase payments
            purchase_payments = {
                'cash': 0, 'days30': 0, 'days60': 0,
                'days90': 0, 'days120': 0, 'days150': 0
            }
            
            for line in purchase_lines:
                total_amount = float(line[7] or 0) if len(line) > 7 else 0
                if total_amount > 0:
                    purchase_payments['cash'] += total_amount * float(line[8] or 0 if len(line) > 8 else 0) / 100
                    purchase_payments['days30'] += total_amount * float(line[9] or 0 if len(line) > 9 else 0) / 100
                    purchase_payments['days60'] += total_amount * float(line[10] or 0 if len(line) > 10 else 0) / 100
                    purchase_payments['days90'] += total_amount * float(line[15] or 0 if len(line) > 15 else 0) / 100
                    purchase_payments['days120'] += total_amount * float(line[16] or 0 if len(line) > 16 else 0) / 100
                    purchase_payments['days150'] += total_amount * float(line[17] or 0 if len(line) > 17 else 0) / 100
            
            total_purchase_payments = sum(purchase_payments.values())
            
            loan_repayments = data.get('loan_repayment_monthly', 0)
            bank_credit_month = bank_credit if month_num == 1 else 0
            shareholder_month = shareholder_contributions if month_num == 1 else 0
            mandatory_payments = data.get('mandatory_payment_monthly', 0)
            
            total_receipts = total_sales_receipts + bank_credit_month + shareholder_month
            total_payments = total_purchase_payments + loan_repayments + mandatory_payments
            
            net_cash_flow = total_receipts - total_payments
            closing_balance = current_balance + net_cash_flow
            
            result.append({
                'month': month_num,
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
    finally:
        conn.close()


@finance_api_bp.route('/cash-flow', methods=['POST'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def save_cash_flow():
    """Save cash flow projection."""
    data = request.get_json()
    user_id = session.get('user_id')
    industry = get_current_industry()
    
    if not industry:
        return jsonify({'success': False, 'error': 'No industry selected'}), 400
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        projection_name = data.get('projection_name') or f"Cash Flow - {datetime.now().strftime('%Y-%m-%d')}"
        projection_code = f"CF-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        execute_query(conn, """
            INSERT INTO t_CashFlowProjection (
                ProjectionCode, ProjectionName, SalesForecastID, PurchaseForecastID,
                PeriodPersianYear, PeriodPersianSeason, Status,
                CreatedBy, CreatedDateKey, DemoIndustryCode,
                OpeningBalanceInitial, Description, PeriodType
            ) VALUES (?, ?, ?, ?, ?, ?, 'Draft', ?, ?, ?, ?, ?, ?)
        """, [
            projection_code,
            projection_name,
            data.get('sales_forecast_id'),
            data.get('purchase_forecast_id'),
            data.get('PeriodPersianYear'),
            data.get('PeriodPersianSeason'),
            user_id,
            int(datetime.now().strftime('%Y%m%d')),
            industry,
            data.get('opening_balance', 0),
            data.get('Description', ''),
            data.get('period_type', 'season')
        ])
        
        projection_id = get_last_insert_id(conn)
        conn.commit()
        
        return jsonify({'success': True, 'ProjectionID': projection_id, 'ProjectionCode': projection_code})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error saving cash flow: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/cash-flow/<int:projection_id>/delete', methods=['DELETE'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def delete_cash_flow(projection_id):
    """Delete a cash flow projection."""
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        check = fetch_one(conn, """
            SELECT ProjectionID FROM t_CashFlowProjection WHERE ProjectionID = ?
        """, [projection_id])
        
        if not check:
            return jsonify({'success': False, 'error': 'Projection not found'}), 404
        
        execute_query(conn, "DELETE FROM t_CashFlowLine WHERE ProjectionID = ?", [projection_id])
        execute_query(conn, "DELETE FROM t_CashFlowProjection WHERE ProjectionID = ?", [projection_id])
        conn.commit()
        
        return jsonify({'success': True, 'message': 'Projection deleted successfully'})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error deleting cash flow: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/cash-flow/<int:projection_id>/duplicate', methods=['POST'])
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def duplicate_cash_flow(projection_id):
    """Duplicate a cash flow projection."""
    industry = get_current_industry()
    user_id = session.get('user_id')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        original = fetch_one(conn, """
            SELECT * FROM t_CashFlowProjection WHERE ProjectionID = ?
        """, [projection_id])
        
        if not original:
            return jsonify({'success': False, 'error': 'Projection not found'}), 404
        
        new_code = f"CF-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        new_name = f"{original[2]} (Copy)"
        
        execute_query(conn, """
            INSERT INTO t_CashFlowProjection (
                ProjectionCode, ProjectionName, SalesForecastID, PurchaseForecastID,
                PeriodPersianYear, PeriodPersianSeason, Status,
                CreatedBy, CreatedDateKey, DemoIndustryCode,
                OpeningBalanceInitial, Description, PeriodType
            ) VALUES (?, ?, ?, ?, ?, ?, 'Draft', ?, ?, ?, ?, ?, ?)
        """, [
            new_code,
            new_name,
            original[2],
            original[3],
            original[6],
            original[7],
            user_id,
            int(datetime.now().strftime('%Y%m%d')),
            industry,
            original[12] if len(original) > 12 else 0,
            f"Duplicate of {original[2]}",
            original[14] if len(original) > 14 else 'season'
        ])
        
        new_id = get_last_insert_id(conn)
        conn.commit()
        
        return jsonify({'success': True, 'ProjectionID': new_id, 'message': 'Projection duplicated'})
    except Exception as e:
        conn.rollback()
        logging.error(f"Error duplicating cash flow: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@finance_api_bp.route('/cash-flow/<int:projection_id>/chart-data', methods=['GET'])
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def get_cash_flow_chart_data(projection_id):
    """Get chart data for cash flow visualization."""
    industry = get_current_industry()
    lang = session.get('lang', 'en')
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    
    try:
        header = fetch_one(conn, """
            SELECT * FROM t_CashFlowProjection WHERE ProjectionID = ?
        """, [projection_id])
        
        if not header:
            return jsonify({'success': False, 'error': 'Projection not found'}), 404
        
        lines = fetch_all(conn, """
            SELECT * FROM t_CashFlowLine
            WHERE ProjectionID = ?
            ORDER BY MonthNumber
        """, [projection_id])
        
        persian_months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                          'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
        if lang == 'en':
            persian_months = ['Month 1', 'Month 2', 'Month 3', 'Month 4', 'Month 5', 'Month 6',
                              'Month 7', 'Month 8', 'Month 9', 'Month 10', 'Month 11', 'Month 12']
        
        chart_data = {
            'labels': [],
            'receipts': [],
            'payments': [],
            'net_cash': [],
            'closing_balance': []
        }
        
        for line in lines:
            month_num = line[1]  # MonthNumber
            month_name = persian_months[month_num - 1] if month_num <= len(persian_months) else f"Month {month_num}"
            chart_data['labels'].append(month_name)
            chart_data['receipts'].append(float(line[25] or 0))
            chart_data['payments'].append(float(line[26] or 0))
            chart_data['net_cash'].append(float(line[27] or 0))
            chart_data['closing_balance'].append(float(line[28] or 0))
        
        return jsonify({
            'success': True,
            'chart_data': chart_data
        })
    except Exception as e:
        logging.error(f"Error getting chart data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()
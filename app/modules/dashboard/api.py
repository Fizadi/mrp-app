# app/modules/dashboard/api.py
from flask import jsonify, session
from . import dashboard_api_bp
from app.core.auth import login_required
from app.core.database import get_db_connection, fetch_one
import logging

@dashboard_api_bp.route('/kpis')
@login_required
def get_kpis():
    """Get KPI data for dashboard."""
    logging.info("=== get_kpis called ===")
    
    conn = get_db_connection(use_teardown=False)
    if not conn:
        logging.error("Database connection failed for KPIs")
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        # Initialize with zeros
        data = {
            'open_production_orders': 0,
            'open_purchase_orders': 0,
            'open_ncrs': 0,
            'active_projects': 0
        }
        
        # Try to get real data - these queries may fail if tables don't exist
        try:
            # Check if t_ProductionOrder exists by trying to query it
            result = fetch_one(conn, """
                SELECT COUNT(*) FROM t_ProductionOrder 
                WHERE Status NOT IN ('Completed', 'Cancelled')
            """)
            if result:
                data['open_production_orders'] = result[0] or 0
        except Exception as e:
            logging.warning(f"t_ProductionOrder query failed: {e}")
        
        try:
            result = fetch_one(conn, """
                SELECT COUNT(*) FROM t_PurchaseOrder 
                WHERE Status NOT IN ('Closed', 'Cancelled')
            """)
            if result:
                data['open_purchase_orders'] = result[0] or 0
        except Exception as e:
            logging.warning(f"t_PurchaseOrder query failed: {e}")
        
        try:
            result = fetch_one(conn, """
                SELECT COUNT(*) FROM t_NCR 
                WHERE Status NOT IN ('Closed', 'Cancelled')
            """)
            if result:
                data['open_ncrs'] = result[0] or 0
        except Exception as e:
            logging.warning(f"t_NCR query failed: {e}")
        
        try:
            result = fetch_one(conn, """
                SELECT COUNT(*) FROM t_Project 
                WHERE Status = 'Active'
            """)
            if result:
                data['active_projects'] = result[0] or 0
        except Exception as e:
            logging.warning(f"t_Project query failed: {e}")
        
        return jsonify(data)
    except Exception as e:
        logging.error(f"Error in get_kpis: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
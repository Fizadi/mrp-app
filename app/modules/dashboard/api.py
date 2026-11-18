# app/modules/dashboard/api.py
from flask import jsonify, session, current_app
from sqlalchemy.sql import text
from . import dashboard_api_bp
from app.core.auth import login_required
import logging


def _get_engine():
    return current_app.extensions['sqlalchemy'].engine


def _current_industry():
    return session.get('demo_industry', 'valve')


@dashboard_api_bp.route('/kpis')
@login_required
def get_kpis():
    """Get KPI data for dashboard (industry-scoped)."""
    logging.info("=== get_kpis called ===")
    industry = _current_industry()
    engine = _get_engine()

    data = {
        'open_production_orders': 0,
        'open_purchase_orders': 0,
        'open_ncrs': 0,
        'active_projects': 0,
    }

    try:
        with engine.connect() as conn:
            # Open production orders
            row = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM t_ProductionOrder
                    WHERE Status NOT IN ('Completed', 'Closed', 'Canceled')
                      AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                """),
                {'industry': industry}
            ).fetchone()
            data['open_production_orders'] = (row[0] or 0) if row else 0

            # Open purchase orders
            row = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM t_PurchaseOrder
                    WHERE Status NOT IN ('Closed', 'Cancelled')
                      AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                """),
                {'industry': industry}
            ).fetchone()
            data['open_purchase_orders'] = (row[0] or 0) if row else 0

            # Open NCRs — real table is t_NonConformance
            row = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM t_NonConformance
                    WHERE Status NOT IN ('Closed', 'Cancelled')
                      AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                """),
                {'industry': industry}
            ).fetchone()
            data['open_ncrs'] = (row[0] or 0) if row else 0

            # Active projects
            row = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM t_Project
                    WHERE Status = 'Active'
                      AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                """),
                {'industry': industry}
            ).fetchone()
            data['active_projects'] = (row[0] or 0) if row else 0

        return jsonify(data)

    except Exception as e:
        logging.error(f"Error in get_kpis: {e}")
        return jsonify({'error': str(e)}), 500
# app/modules/quality/__init__.py

from flask import Blueprint

# Create the quality blueprint
quality_bp = Blueprint('quality', __name__, 
                      template_folder='../../../templates/quality',
                      static_folder='../../../static/quality')

# Import routes AFTER creating the blueprint
from app.modules.quality import routes

# Don't import api here - let app/__init__.py handle it
# This avoids circular imports


# In your quality blueprint (app/modules/quality/__init__.py or similar)

@quality_bp.route('/operations/')
def operations():
    """Operations page."""
    from app.core.database import get_db_connection
    import sqlite3
    
    # Get products and work centers
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Get products
    cur.execute("""
        SELECT DISTINCT ProductFamily as code, ProductFamily as name 
        FROM t_Operations 
        WHERE ProductFamily IS NOT NULL AND ProductFamily != ''
        ORDER BY ProductFamily
    """)
    products = [dict(row) for row in cur.fetchall()]
    
    # Get work centers
    cur.execute("""
        SELECT DISTINCT WorkCenterID as code, WorkCenterID as name 
        FROM t_Operations 
        WHERE WorkCenterID IS NOT NULL AND WorkCenterID != ''
        ORDER BY WorkCenterID
    """)
    work_centers = [dict(row) for row in cur.fetchall()]
    
    conn.close()
    
    return render_template('operations.html', 
                         products=products, 
                         work_centers=work_centers)

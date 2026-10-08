from flask import render_template
from app.modules.inventory import inventory_bp

@inventory_bp.route('/')
def inventory():
    return render_template('inventory/inventory.html')

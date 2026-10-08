from flask import Blueprint

# Page blueprint
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

# API blueprint
dashboard_api_bp = Blueprint('dashboard_api', __name__, url_prefix='/api/dashboard')

# Import routes and API
from app.modules.dashboard import routes, api
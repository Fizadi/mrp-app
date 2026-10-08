from flask import Blueprint

# HTML blueprint (page views)
manufacturing_bp = Blueprint('manufacturing', __name__, url_prefix='/manufacturing', template_folder='../../../templates/manufacturing')

# API blueprint
api_bp = Blueprint('manufacturing_api', __name__, url_prefix='/api')

# Import routes and API endpoints
from . import routes, api
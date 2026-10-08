from flask import Blueprint

# Page routes blueprint
workflow_bp = Blueprint('workflow', __name__,
                        template_folder='templates',
                        static_folder='static',
                        url_prefix='/workflow')

# API routes blueprint - MATCH THE MANUFACTURING PATTERN
workflow_api_bp = Blueprint('workflow_api', __name__,
                            url_prefix='/api/workflow')   # <-- Changed from '/api' to '/api/workflow'

# Import routes and api
from . import routes, api
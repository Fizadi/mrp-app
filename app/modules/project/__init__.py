# app/modules/project/__init__.py
from flask import Blueprint

# HTML blueprint (page views) - template_folder relative to this file
project_bp = Blueprint(
    'project',
    __name__,
    url_prefix='/project',
    template_folder='../../../templates/project'  # Relative to this file's location
)

# API blueprint
api_bp = Blueprint('project_api', __name__, url_prefix='/api/project')

# Import routes and API endpoints
from . import routes, api
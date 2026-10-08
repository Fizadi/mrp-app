from flask import Blueprint

reports_bp = Blueprint('reports', __name__, url_prefix='/reports', template_folder='../../../templates/reports')
api_bp = Blueprint('reports_api', __name__, url_prefix='/api')

from . import routes, api
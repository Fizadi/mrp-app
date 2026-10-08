from flask import Blueprint

crm_bp = Blueprint('crm', __name__, url_prefix='/crm', template_folder='../../../templates/crm')
crm_api_bp = Blueprint('crm_api', __name__, url_prefix='/api/crm')  # Following pattern: /api/crm

from . import routes, api
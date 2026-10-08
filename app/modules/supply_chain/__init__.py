from flask import Blueprint

supply_chain_bp = Blueprint('supply_chain', __name__, url_prefix='/supply-chain', 
                            template_folder='../../../templates/supply_chain')
api_bp = Blueprint('supply_chain_api', __name__, url_prefix='/api')

from . import routes, api
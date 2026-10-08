# app/modules/finance/__init__.py
from flask import Blueprint

# Page routes blueprint
finance_bp = Blueprint('finance', __name__,
                       template_folder='templates',
                       static_folder='static',
                       url_prefix='/finance')

# API routes blueprint
finance_api_bp = Blueprint('finance_api', __name__,
                           url_prefix='/api/finance')

# Import routes and api
from . import routes, api
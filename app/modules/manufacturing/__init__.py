# app/modules/manufacturing/__init__.py
from flask import Blueprint

# HTML blueprint (page views)
manufacturing_bp = Blueprint('manufacturing', __name__, url_prefix='/manufacturing')

# API blueprint - WITH /manufacturing prefix
api_bp = Blueprint('manufacturing_api', __name__, url_prefix='/api/manufacturing')

# Import routes and API endpoints
from . import routes, api
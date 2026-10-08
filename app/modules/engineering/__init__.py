# ================================================================
# Engineering Module — Package Initializer
# ================================================================
# All routes are defined in routes.py. This file only exports the
# blueprint so that app/__init__.py can register it once.
# ================================================================

from app.modules.engineering.routes import engineering_bp

__all__ = ['engineering_bp']
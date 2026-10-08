"""
Document Storage Handler
"""

import os
import hashlib
from datetime import datetime
from flask import current_app
from werkzeug.utils import secure_filename


class DocumentStorage:
    ALLOWED_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff',
        'dwg', 'dxf', 'step', 'stp', 'igs', 'iges',
        'txt', 'csv', 'xml', 'json'
    }
    
    CATEGORIES = {
        'drawing': 'drawings',
        'plan': 'plans',
        'certificate': 'certificates',
        'letter': 'letters',
        'report': 'reports',
        'manual': 'manuals',
        'specification': 'specifications',
        'other': 'other'
    }
    
    @staticmethod
    def get_base_path():
        return current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
    
    @staticmethod
    def save_file(file, storage_type='project', project_id=None, 
                  company_project_id=None, category='other'):
        # ... implementation
        pass
    
    @staticmethod
    def get_file(file_path):
        # ... implementation
        pass
    
    @staticmethod
    def delete_file(file_path):
        # ... implementation
        pass
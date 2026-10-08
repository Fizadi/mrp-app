# Menu structure: each entry has a unique key, URL, and a permission code required to see it.
# If permission_code is None, the menu item is visible to all authenticated users.
# If permission_code is a string, the user must have that permission in any of their roles.

MENU_ITEMS = {
    'dashboard': {
        'url': '/dashboard',
        'permission_code': None,
        'icon': 'fas fa-tachometer-alt',
        'label': 'Dashboard'
    },
    'manufacturing': {
        'url': '#manufacturingSubmenu',
        'permission_code': 'MANUFACTURING_VIEW',
        'icon': 'fas fa-industry',
        'label': 'Manufacturing',
        'children': {
            'production_orders': {'url': '/manufacturing/production-orders', 'permission_code': 'PRODUCTION_ORDER_VIEW', 'label': 'Production Orders'},
            'shop_floor': {'url': '/manufacturing/shop-floor', 'permission_code': 'SHOP_FLOOR_VIEW', 'label': 'Shop Floor Control'},
            'routing': {'url': '/manufacturing/work-center-routing', 'permission_code': 'ROUTING_VIEW', 'label': 'Routing'},
            'capacity': {'url': '/manufacturing/capacity-planning', 'permission_code': 'CAPACITY_VIEW', 'label': 'Capacity Planning'},
            'mrp': {
                'url': '#mrpSubmenu',
                'permission_code': 'MRP_VIEW',
                'label': 'MRP',
                'children': {
                    'mrp_dashboard': {'url': '/manufacturing/mrp-run-dashboard', 'permission_code': 'MRP_VIEW', 'label': 'MRP Run Dashboard'},
                    'planned_orders': {'url': '/manufacturing/mrp/planned-orders', 'permission_code': 'MRP_VIEW', 'label': 'Planned Orders'},
                    'exceptions': {'url': '/manufacturing/mrp/exceptions', 'permission_code': 'MRP_VIEW', 'label': 'MRP Exceptions'},
                    'what_if': {'url': '/manufacturing/what-if', 'permission_code': 'MRP_VIEW', 'label': 'What-If Scenarios'}
                }
            },
            'subcontract': {'url': '/manufacturing/subcontract-dashboard', 'permission_code': 'SUBCONTRACT_VIEW', 'label': 'Subcontract Dashboard'},
            'maintenance': {'url': '/manufacturing/maintenance', 'permission_code': 'MAINTENANCE_VIEW', 'label': 'Maintenance'},
            'barcode': {'url': '/manufacturing/shop-floor-barcode', 'permission_code': 'SHOP_FLOOR_VIEW', 'label': 'Shop Floor Barcode'}
        }
    },
    'supply_chain': {
        'url': '#supplyChainSubmenu',
        'permission_code': 'SUPPLY_CHAIN_VIEW',  # Only admin has this
        'icon': 'fas fa-truck',
        'label': 'Supply Chain',
        'children': {
            'purchase_orders': {'url': '/supply-chain/purchase-orders', 'permission_code': 'PO_VIEW', 'label': 'Purchase Orders'},
            'subcontract_orders': {'url': '/supply-chain/subcontract-orders', 'permission_code': 'SUBCONTRACT_VIEW', 'label': 'Subcontract POs'},
            'rfq': {'url': '/supply-chain/rfq', 'permission_code': 'RFQ_VIEW', 'label': 'RFQ Management'},
            'suppliers': {'url': '/supply-chain/suppliers', 'permission_code': 'SUPPLIER_VIEW', 'label': 'Supplier Management'},
            'stock_overview': {'url': '/supply-chain/stock-overview', 'permission_code': 'INVENTORY_VIEW', 'label': 'Stock Overview'},
            'warehouses': {'url': '/supply-chain/warehouses', 'permission_code': 'INVENTORY_VIEW', 'label': 'Warehouse Management'},
            'stock_movements': {'url': '/supply-chain/stock-movements', 'permission_code': 'INVENTORY_VIEW', 'label': 'Stock Movements'},
            'inventory_valuation': {'url': '/supply-chain/inventory-valuation', 'permission_code': 'INVENTORY_VIEW', 'label': 'Inventory Valuation'},
            'receiving': {'url': '/supply-chain/receiving', 'permission_code': 'RECEIVING_VIEW', 'label': 'Receiving'},
            'shipping': {'url': '/supply-chain/shipping', 'permission_code': 'SHIPPING_VIEW', 'label': 'Shipping'}
        }
    },
    'quality': {
        'url': '#qualitySubmenu',
        'permission_code': 'QUALITY_VIEW',
        'icon': 'fas fa-clipboard-check',
        'label': 'Quality',
        'children': {
            'templates': {'url': '/quality/quality_templates', 'permission_code': 'QUALITY_TEMPLATE_VIEW', 'label': 'Quality Templates'},
            'operations': {'url': '/quality/operations_library', 'permission_code': 'QUALITY_OPERATION_VIEW', 'label': 'Operation Library'},
            'inspection_workbench': {'url': '/quality/inspection_workbench', 'permission_code': 'QUALITY_INSPECTION_VIEW', 'label': 'Inspection Workbench'},
            'supplier_portal': {'url': '/quality/supplier_portal', 'permission_code': 'QUALITY_SUPPLIER_VIEW', 'label': 'Supplier Inspection Portal'},
            'serial_numbers': {'url': '/quality/serial_numbers', 'permission_code': 'QUALITY_SERIAL_VIEW', 'label': 'Serial Numbers'},
            'defect_management': {'url': '/quality/defect_management', 'permission_code': 'QUALITY_NCR_VIEW', 'label': 'Defect Management'},
            'ncr_register': {'url': '/quality/ncr_register', 'permission_code': 'QUALITY_NCR_VIEW', 'label': 'NCR Register'},
            'supplier_ncr': {'url': '/quality/supplier_ncr', 'permission_code': 'QUALITY_NCR_VIEW', 'label': 'Supplier NCR'},
            'capa': {'url': '/quality/capa', 'permission_code': 'QUALITY_NCR_VIEW', 'label': 'CAPA'},
            'supplier_scorecard': {'url': '/quality/supplier_scorecard', 'permission_code': 'QUALITY_SUPPLIER_VIEW', 'label': 'Supplier Scorecard'},
            'certificates': {'url': '/quality/certificates', 'permission_code': 'QUALITY_CERT_VIEW', 'label': 'Certificate Management'},
            'audit_management': {'url': '/quality/audit_management', 'permission_code': 'QUALITY_AUDIT_VIEW', 'label': 'Audit Management'},
            'calibration': {'url': '/quality/calibration_management', 'permission_code': 'QUALITY_CALIBRATION_VIEW', 'label': 'Calibration Management'},
            'signature_audit': {'url': '/quality/signature_audit', 'permission_code': 'QUALITY_SIGNATURE_VIEW', 'label': 'Signature Audit Log'}
        }
    },
    'engineering': {
        'url': '#engineeringSubmenu',
        'permission_code': 'ENGINEERING_VIEW',
        'icon': 'fas fa-file-alt',
        'label': 'Engineering & Documents',
        'children': {
            'products': {'url': '/engineering/products', 'permission_code': 'ENGINEERING_VIEW', 'label': 'Product Management'},
            'bom': {'url': '/engineering/bom', 'permission_code': 'BOM_VIEW', 'label': 'BOM Management'},
            'documents': {'url': '/engineering/documents', 'permission_code': 'DOCUMENT_VIEW', 'label': 'Engineering Documents'},
            'document_control': {'url': '/engineering/document-control', 'permission_code': 'DOCUMENT_VIEW', 'label': 'Document Control'},
            'external_share': {'url': '/engineering/external-share', 'permission_code': 'DOCUMENT_VIEW', 'label': 'External Document Sharing'},
            'handover': {'url': '/engineering/handover', 'permission_code': 'ENGINEERING_VIEW', 'label': 'Engineering Handover'}
        }
    },
    'projects': {
        'url': '#projectsSubmenu',
        'permission_code': None,
        'icon': 'fas fa-tasks',
        'label': 'Projects',
        'children': {
            'portfolio': {'url': '/project/project_portfolio', 'permission_code': None, 'label': 'Project Portfolio'},
            'customer_portal': {'url': '/project/customer_portal', 'permission_code': None, 'label': 'Customer Portal'},
            'customer_projects': {'url': '/project/customer_projects', 'permission_code': None, 'label': 'Customer Project Management'}
        }
    },
    'finance': {
        'url': '#financeSubmenu',
        'permission_code': 'FINANCE_VIEW',
        'icon': 'fas fa-dollar-sign',
        'label': 'Finance',
        'children': {
            'product_costing': {'url': '#', 'permission_code': 'FINANCE_VIEW', 'label': 'Product Costing'},
            'project_financials': {'url': '#', 'permission_code': 'FINANCE_VIEW', 'label': 'Project Financials'},
            'inventory_valuation': {'url': '#', 'permission_code': 'FINANCE_VIEW', 'label': 'Inventory Valuation'},
            'currency': {'url': '#', 'permission_code': 'FINANCE_VIEW', 'label': 'Currency Management'},
            'supplier_invoices': {'url': '#', 'permission_code': 'FINANCE_VIEW', 'label': 'Supplier Invoices'},
            'customer_invoices': {'url': '#', 'permission_code': 'FINANCE_VIEW', 'label': 'Customer Invoices'}
        }
    },
    'hr': {
        'url': '#hrSubmenu',
        'permission_code': 'HR_VIEW',
        'icon': 'fas fa-users',
        'label': 'Human Resources',
        'children': {
            'employee_management': {'url': '#', 'permission_code': 'HR_VIEW', 'label': 'Employee Management'},
            'skill_matrix': {'url': '#', 'permission_code': 'HR_VIEW', 'label': 'Skill Matrix'},
            'training': {'url': '#', 'permission_code': 'HR_VIEW', 'label': 'Training'},
            'work_schedule': {'url': '#', 'permission_code': 'HR_VIEW', 'label': 'Work Schedule'}
        }
    },
    'administration': {
        'url': '#adminSubmenu',
        'permission_code': 'ADMIN',
        'icon': 'fas fa-cog',
        'label': 'Administration',
        'children': {
            'internal_users': {'url': '/admin/internal-users', 'permission_code': 'ADMIN', 'label': 'Internal Users'},
            'external_users': {'url': '/admin/external-users', 'permission_code': 'ADMIN', 'label': 'External Users'},
            'role_permissions': {'url': '/admin/role-permissions', 'permission_code': 'ADMIN', 'label': 'Roles & Permissions'},
            'project_roles': {'url': '/admin/project-roles', 'permission_code': 'ADMIN', 'label': 'Project Roles & Permissions'},
            'workflow_designer': {'url': '/admin/workflow-designer', 'permission_code': 'ADMIN', 'label': 'Workflow Designer'},
            'system_config': {'url': '/admin/system-config', 'permission_code': 'ADMIN', 'label': 'System Configuration'},
            'audit_log': {'url': '/admin/audit-log', 'permission_code': 'ADMIN', 'label': 'Audit Log'},
            'portal_settings': {'url': '/admin/portal-settings', 'permission_code': 'ADMIN', 'label': 'Portal Settings'}
        }
    },
    'reports': {
        'url': '#reportsSubmenu',
        'permission_code': 'REPORT_VIEW',
        'icon': 'fas fa-chart-line',
        'label': 'Reports & Analytics',
        'children': {
            'operational': {'url': '#', 'permission_code': 'REPORT_VIEW', 'label': 'Operational Reports'},
            'management': {'url': '#', 'permission_code': 'REPORT_VIEW', 'label': 'Management Reports'},
            'supplier_performance': {'url': '#', 'permission_code': 'REPORT_VIEW', 'label': 'Supplier Performance'},
            'customer_performance': {'url': '#', 'permission_code': 'REPORT_VIEW', 'label': 'Customer Performance'},
            'subcontracting_analysis': {'url': '#', 'permission_code': 'REPORT_VIEW', 'label': 'Subcontracting Analysis'}
        }
    }
}


def get_user_permission_set(user_roles):
    """Get permission set for a user based on their roles - SQL Server compatible."""
    from app import db
    from sqlalchemy.sql import text
    import json
    if not user_roles:
        return set()
    permission_set = set()
    with db.engine.connect() as conn:
        # Build numbered parameters like :role0, :role1, ...
        placeholders = ','.join([f':role_{i}' for i in range(len(user_roles))])
        params = {f'role_{i}': role for i, role in enumerate(user_roles)}
        query = f"SELECT Permissions FROM t_Roles WHERE RoleCode IN ({placeholders})"
        rows = conn.execute(text(query), params).fetchall()
        for row in rows:
            if row[0]:
                perms = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                def flatten(obj, prefix=''):
                    for k, v in obj.items():
                        if isinstance(v, dict):
                            flatten(v, f"{prefix}{k}." if prefix else f"{k}.")
                        elif v is True or v == 1:
                            permission_set.add(f"{prefix}{k}".upper())
                flatten(perms)
    
    print("DEBUG get_user_permission_set: extracted permission_set =", permission_set)
    return permission_set


def has_permission(required_permission, user_permission_set):
    """Check if a user has a specific permission."""
    if required_permission is None:
        return True
    if required_permission in user_permission_set:
        return True
    # Check for wildcard: e.g., 'QUALITY_VIEW' also allows 'QUALITY_*'?
    # For now, exact match only
    return False
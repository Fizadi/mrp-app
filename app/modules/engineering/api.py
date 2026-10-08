# app/modules/engineering/api.py
from flask import Blueprint, request, jsonify, Response, current_app, send_file, session
from sqlalchemy.sql import text
from flask import send_file
import csv
import io
import os
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import json
import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

api_bp = Blueprint('bom_api', __name__, url_prefix='/api/bom')



def get_engine():
    return current_app.extensions['sqlalchemy'].engine

# ==================== HELPER FUNCTIONS ====================

def get_today_key():
    """Return today's DateKey as integer (YYYYMMDD)."""
    return int(datetime.now().strftime('%Y%m%d'))


def get_industry_and_project(req):
    """Get industry and project from session/request for consistent filtering."""
    industry = session.get('demo_industry', 'valve')
    project_id = req.args.get('project_id', '')
    if not project_id or project_id == '':
        project_id = session.get('current_project_id', 'All')
    return industry, project_id




def get_products_for_selector(req):
    industry = session.get('demo_industry', 'valve')
    project_id = req.args.get('project_id') or session.get('current_project_id', 'All')

    engine = get_engine()
    query = """
        SELECT DISTINCT p.ProductId, p.descEnglish,
               CASE WHEN EXISTS (SELECT 1 FROM t_BOM WHERE ParentProductID = p.ProductId) THEN 1 ELSE 0 END as hasBom
        FROM t_Product p
        WHERE p.DemoIndustryCode = :industry
    """
    params = {'industry': industry}

    if project_id and project_id not in ('All', ''):
        project_list = [x.strip() for x in project_id.split(',') if x.strip()]
        placeholders = ','.join([f':p{i}' for i in range(len(project_list))])
        query += f" AND p.ProductId IN (SELECT ProductID FROM t_ProjectProducts WHERE ProjectID IN ({placeholders}))"
        for i, pid in enumerate(project_list):
            params[f'p{i}'] = pid

    query += " ORDER BY p.descEnglish"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        return [{'ProductID': r[0], 'ProductName': r[1], 'hasBom': bool(r[2])} for r in rows]



def get_bom_stats():
    """Get BOM statistics without project filter (global stats)."""
    engine = get_engine()
    stats = {}
    with engine.connect() as conn:
        try:
            total = conn.execute(text('SELECT COUNT(*) FROM t_BOM WHERE ObsoleteDateKey IS NULL')).fetchone()[0]
        except:
            total = 0
        stats['totalComponents'] = total
        try:
            where_used = conn.execute(text('SELECT COUNT(DISTINCT ParentProductID) FROM t_BOM')).fetchone()[0]
        except:
            where_used = 0
        stats['whereUsedRefs'] = where_used
        try:
            pending_ecos = conn.execute(text("SELECT COUNT(*) FROM t_ChangeOrder WHERE Status = 'Pending'")).fetchone()[0]
        except:
            pending_ecos = 0
        stats['pendingECOs'] = pending_ecos
        try:
            locked_docs = conn.execute(text('SELECT COUNT(*) FROM t_EngineeringDocument WHERE CheckoutStatus = 1')).fetchone()[0]
        except:
            locked_docs = 0
        stats['lockedDocuments'] = locked_docs
    return stats


# ==================== PRODUCT CATEGORIES ====================

@api_bp.route('/categories')
def get_categories():
    industry = session.get('demo_industry', 'valve')
    active_only = request.args.get('active_only') == 'true'
    
    engine = get_engine()
    query = """
        SELECT CategoryID, CategoryCode, CategoryName, Description,
               HasEngineeringParameters, ParameterTableSuffix, IsActive,
               CategoryNameLocal, DescriptionLocal
        FROM t_ProductCategory
        WHERE DemoIndustryCode = :industry
    """
    params = {'industry': industry}
    if active_only:
        query += " AND IsActive = 1"
    query += " ORDER BY CategoryCode"
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        categories = [{
            'categoryId': r[0],
            'categoryCode': r[1],
            'categoryName': r[2],
            'description': r[3],
            'hasParameters': bool(r[4]),
            'parameterTableSuffix': r[5],
            'isActive': bool(r[6]),
            'categoryNameLocal': r[7],
            'descriptionLocal': r[8]
        } for r in rows]
    return jsonify(categories)


@api_bp.route('/categories/stats')
def category_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_ProductCategory WHERE DemoIndustryCode = :industry"), {'industry': industry}).fetchone()[0]
        active = conn.execute(text("SELECT COUNT(*) FROM t_ProductCategory WHERE DemoIndustryCode = :industry AND IsActive = 1"), {'industry': industry}).fetchone()[0]
        with_params = conn.execute(text("SELECT COUNT(*) FROM t_ProductCategory WHERE DemoIndustryCode = :industry AND HasEngineeringParameters = 1"), {'industry': industry}).fetchone()[0]
    return jsonify({'totalCategories': total, 'activeCategories': active, 'withParameters': with_params})


@api_bp.route('/categories', methods=['POST'])
def create_category():
    data = request.json
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ProductCategory (
                    CategoryCode, CategoryName, Description, HasEngineeringParameters,
                    ParameterTableSuffix, IsActive, DemoIndustryCode,
                    CategoryNameLocal, DescriptionLocal, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :code, :name, :desc, :has_params,
                    :suffix, :active, :industry,
                    :name_local, :desc_local, :datekey, :datekey
                )
            """), {
                'code': data['categoryCode'],
                'name': data['categoryName'],
                'desc': data.get('description', ''),
                'has_params': 1 if data.get('hasEngineeringParameters') else 0,
                'suffix': data.get('parameterTableSuffix', ''),
                'active': 1 if data.get('isActive') else 0,
                'industry': industry,
                'name_local': data.get('categoryNameLocal', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/categories/<string:code>', methods=['PUT'])
def update_category(code):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE t_ProductCategory SET
                    CategoryName = :name,
                    Description = :desc,
                    HasEngineeringParameters = :has_params,
                    ParameterTableSuffix = :suffix,
                    IsActive = :active,
                    CategoryNameLocal = :name_local,
                    DescriptionLocal = :desc_local,
                    ModifiedDateKey = :datekey
                WHERE CategoryCode = :code
            """), {
                'code': code,
                'name': data['categoryName'],
                'desc': data.get('description', ''),
                'has_params': 1 if data.get('hasEngineeringParameters') else 0,
                'suffix': data.get('parameterTableSuffix', ''),
                'active': 1 if data.get('isActive') else 0,
                'name_local': data.get('categoryNameLocal', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/categories/<string:code>', methods=['DELETE'])
def delete_category(code):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM t_ProductCategory WHERE CategoryCode = :code"), {'code': code})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/categories/import', methods=['POST'])
def import_categories_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    with engine.connect() as conn:
        for row in reader:
            conn.execute(text("""
                INSERT OR IGNORE INTO t_ProductCategory (
                    CategoryCode, CategoryName, Description, HasEngineeringParameters,
                    ParameterTableSuffix, IsActive, DemoIndustryCode,
                    CategoryNameLocal, DescriptionLocal, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :code, :name, :desc, :has_params,
                    :suffix, :active, :industry,
                    :name_local, :desc_local, :datekey, :datekey
                )
            """), {
                'code': row.get('CategoryCode', ''),
                'name': row.get('CategoryName', ''),
                'desc': row.get('Description', ''),
                'has_params': 1 if row.get('HasEngineeringParameters', '0').lower() in ('1', 'true', 'yes') else 0,
                'suffix': row.get('ParameterTableSuffix', ''),
                'active': 1 if row.get('IsActive', '1').lower() in ('1', 'true', 'yes') else 0,
                'industry': industry,
                'name_local': row.get('CategoryNameLocal', ''),
                'desc_local': row.get('DescriptionLocal', ''),
                'datekey': get_today_key()
            })
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/categories/export')
def export_categories_csv():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT CategoryCode, CategoryName, Description, HasEngineeringParameters,
                   ParameterTableSuffix, IsActive, CategoryNameLocal, DescriptionLocal
            FROM t_ProductCategory
            WHERE DemoIndustryCode = :industry
            ORDER BY CategoryCode
        """), {'industry': industry}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['CategoryCode', 'CategoryName', 'Description', 'HasEngineeringParameters',
                     'ParameterTableSuffix', 'IsActive', 'CategoryNameLocal', 'DescriptionLocal'])
    for r in rows:
        writer.writerow(list(r))
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=product_categories.csv'
    return response


# ==================== ENGINEERING PARAMETERS ====================

@api_bp.route('/parameters')
def get_parameters():
    industry = session.get('demo_industry', 'valve')
    category = request.args.get('category')
    param_type = request.args.get('type')
    status = request.args.get('status')
    required = request.args.get('required')
    
    engine = get_engine()
    query = """
        SELECT DefinitionID, CategoryCode, ParameterCode, ParameterName,
               ParameterType, Unit, IsClientInput, IsCalculated,
               DefaultValue, ValidationRules, DisplayOrder, IsActive,
               ParameterNameLocal
        FROM t_EngineeringParameterDefinition
        WHERE DemoIndustryCode = :industry
    """
    params = {'industry': industry}
    
    if category:
        query += " AND CategoryCode = :category"
        params['category'] = category
    if param_type:
        query += " AND ParameterType = :type"
        params['type'] = param_type
    if status == 'Active':
        query += " AND IsActive = 1"
    elif status == 'Inactive':
        query += " AND IsActive = 0"
    if required == 'true':
        query += " AND IsClientInput = 1"
    elif required == 'false':
        query += " AND IsClientInput = 0"
    
    query += " ORDER BY CategoryCode, DisplayOrder"
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        parameters = [{
            'definitionId': r[0],
            'categoryCode': r[1],
            'parameterCode': r[2],
            'parameterName': r[3],
            'parameterType': r[4],
            'unit': r[5],
            'isClientInput': bool(r[6]),
            'isCalculated': bool(r[7]),
            'defaultValue': r[8],
            'validationRules': r[9],
            'displayOrder': r[10],
            'isActive': bool(r[11]),
            'parameterNameLocal': r[12]
        } for r in rows]
    return jsonify(parameters)


@api_bp.route('/parameters/stats')
def parameter_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_EngineeringParameterDefinition WHERE DemoIndustryCode = :industry"), {'industry': industry}).fetchone()[0]
        active = conn.execute(text("SELECT COUNT(*) FROM t_EngineeringParameterDefinition WHERE DemoIndustryCode = :industry AND IsActive = 1"), {'industry': industry}).fetchone()[0]
        by_category = conn.execute(text("""
            SELECT CategoryCode, COUNT(*) as count 
            FROM t_EngineeringParameterDefinition 
            WHERE DemoIndustryCode = :industry 
            GROUP BY CategoryCode
        """), {'industry': industry}).fetchall()
        by_type = conn.execute(text("""
            SELECT ParameterType, COUNT(*) as count 
            FROM t_EngineeringParameterDefinition 
            WHERE DemoIndustryCode = :industry 
            GROUP BY ParameterType
        """), {'industry': industry}).fetchall()
    return jsonify({
        'totalParameters': total,
        'activeParameters': active,
        'byCategory': [{'category': r[0], 'count': r[1]} for r in by_category],
        'byType': [{'type': r[0], 'count': r[1]} for r in by_type]
    })


@api_bp.route('/parameters', methods=['POST'])
def create_parameter():
    data = request.json
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_EngineeringParameterDefinition (
                    CategoryCode, ParameterCode, ParameterName, ParameterType,
                    Unit, IsClientInput, IsCalculated, DefaultValue,
                    ValidationRules, DisplayOrder, IsActive,
                    ParameterNameLocal, DemoIndustryCode, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :category, :code, :name, :type,
                    :unit, :client, :calc, :default_val,
                    :validation, :order, :active,
                    :name_local, :industry, :datekey, :datekey
                )
            """), {
                'category': data['categoryCode'],
                'code': data['parameterCode'],
                'name': data['parameterName'],
                'type': data['parameterType'],
                'unit': data.get('unit', ''),
                'client': 1 if data.get('isClientInput') else 0,
                'calc': 1 if data.get('isCalculated') else 0,
                'default_val': data.get('defaultValue', ''),
                'validation': data.get('validationRules', ''),
                'order': data.get('displayOrder', 0),
                'active': 1 if data.get('isActive') else 0,
                'name_local': data.get('parameterNameLocal', ''),
                'industry': industry,
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/parameters/<int:def_id>', methods=['PUT'])
def update_parameter(def_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE t_EngineeringParameterDefinition SET
                    CategoryCode = :category,
                    ParameterName = :name,
                    ParameterType = :type,
                    Unit = :unit,
                    IsClientInput = :client,
                    IsCalculated = :calc,
                    DefaultValue = :default_val,
                    ValidationRules = :validation,
                    DisplayOrder = :order,
                    IsActive = :active,
                    ParameterNameLocal = :name_local,
                    ModifiedDateKey = :datekey
                WHERE DefinitionID = :id
            """), {
                'id': def_id,
                'category': data['categoryCode'],
                'name': data['parameterName'],
                'type': data['parameterType'],
                'unit': data.get('unit', ''),
                'client': 1 if data.get('isClientInput') else 0,
                'calc': 1 if data.get('isCalculated') else 0,
                'default_val': data.get('defaultValue', ''),
                'validation': data.get('validationRules', ''),
                'order': data.get('displayOrder', 0),
                'active': 1 if data.get('isActive') else 0,
                'name_local': data.get('parameterNameLocal', ''),
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/parameters/<int:def_id>', methods=['DELETE'])
def delete_parameter(def_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            # Check if parameter is used in any value
            used = conn.execute(text("SELECT COUNT(*) FROM t_EngineeringParameterValue WHERE ParameterCode = (SELECT ParameterCode FROM t_EngineeringParameterDefinition WHERE DefinitionID = :id)"), {'id': def_id}).fetchone()[0]
            if used > 0:
                return jsonify({'error': 'Parameter is in use and cannot be deleted'}), 400
            conn.execute(text("DELETE FROM t_EngineeringParameterDefinition WHERE DefinitionID = :id"), {'id': def_id})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/parameters/types')
def get_parameter_types():
    return jsonify(['String', 'Number', 'Boolean', 'Date', 'List'])


@api_bp.route('/parameters/by-category/<string:category_code>')
def get_parameters_by_category(category_code):
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ParameterCode, ParameterName, ParameterType, Unit, DisplayOrder, IsActive
            FROM t_EngineeringParameterDefinition
            WHERE CategoryCode = :category AND DemoIndustryCode = :industry
            ORDER BY DisplayOrder
        """), {'category': category_code, 'industry': industry}).fetchall()
        return jsonify([{
            'parameterCode': r[0],
            'parameterName': r[1],
            'parameterType': r[2],
            'unit': r[3],
            'displayOrder': r[4],
            'isActive': bool(r[5])
        } for r in rows])


# ==================== ROUTINGS & WORK INSTRUCTIONS ====================



@api_bp.route('/routings')
def get_routings():
    industry, project_id = get_industry_and_project(request)
    product_id = request.args.get('product_id')
    status = request.args.get('status')
    
    engine = get_engine()
    query = """
        SELECT r.rowid as RoutingID, r.ProductID, p.descEnglish as ProductName,
               r.WorkCenterID, wc.Name as WorkCenterName,
               r.OperationSequence, r.SetupTimeHours,
               r.RunTimeHoursPerUnit, r.QueueTimeHours, r.MoveTimeHours,
               r.EffectivityDateKey, r.ObsoleteDateKey, r.IsExternal,
               r.SourcingType, r.IsActive,
               COUNT(i.InstructionID) as InstructionCount,
               (r.SetupTimeHours + r.RunTimeHoursPerUnit + r.QueueTimeHours + r.MoveTimeHours) as TotalTime
        FROM t_WorkCenterRouting r
        JOIN t_Product p ON r.ProductID = p.ProductId
        LEFT JOIN t_WorkCenter wc ON r.WorkCenterID = wc.WorkCenterID
        LEFT JOIN t_WorkInstructions i ON r.rowid = i.RoutingID AND i.IsActive = 1
        WHERE p.DemoIndustryCode = :industry
    """
    params = {'industry': industry}
    
    if product_id:
        query += " AND r.ProductID = :product"
        params['product'] = product_id
    if status == 'Active':
        query += " AND r.IsActive = 1"
    elif status == 'Inactive':
        query += " AND r.IsActive = 0"
    
    # SQL Server: include ALL non-aggregated columns in GROUP BY
    query += """
        GROUP BY 
            r.rowid, r.ProductID, p.descEnglish,
            r.WorkCenterID, wc.Name,
            r.OperationSequence, r.SetupTimeHours,
            r.RunTimeHoursPerUnit, r.QueueTimeHours, r.MoveTimeHours,
            r.EffectivityDateKey, r.ObsoleteDateKey, r.IsExternal,
            r.SourcingType, r.IsActive
        ORDER BY p.descEnglish, r.OperationSequence
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        routings = [{
            'routingId': r[0],
            'productId': r[1],
            'productName': r[2],
            'workCenterId': r[3],
            'workCenterName': r[4],
            'operationSequence': r[5],
            'setupTimeHours': float(r[6]) if r[6] else 0,
            'runTimeHoursPerUnit': float(r[7]) if r[7] else 0,
            'queueTimeHours': float(r[8]) if r[8] else 0,
            'moveTimeHours': float(r[9]) if r[9] else 0,
            'effectivityDateKey': r[10],
            'obsoleteDateKey': r[11],
            'isExternal': bool(r[12]),
            'sourcingType': r[13],
            'isActive': bool(r[14]),
            'instructionCount': r[15] or 0,
            'totalTime': float(r[16]) if r[16] else 0
        } for r in rows]
    return jsonify(routings)

@api_bp.route('/routings/<int:routing_id>')
def get_routing_detail(routing_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT r.rowid, r.ProductID, p.descEnglish, r.WorkCenterID, wc.Name,
                   r.OperationSequence, r.SetupTimeHours, r.RunTimeHoursPerUnit,
                   r.QueueTimeHours, r.MoveTimeHours, r.SourcingType, r.IsActive
            FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            LEFT JOIN t_WorkCenter wc ON r.WorkCenterID = wc.WorkCenterID
            WHERE r.rowid = :id
        """), {'id': routing_id}).fetchone()
        if not row:
            return jsonify({'error': 'Routing not found'}), 404
        return jsonify({
            'routingId': row[0],
            'productId': row[1],
            'productName': row[2],
            'workCenterId': row[3],
            'workCenterName': row[4],
            'operationSequence': row[5],
            'setupTimeHours': float(row[6]) if row[6] else 0,
            'runTimeHoursPerUnit': float(row[7]) if row[7] else 0,
            'queueTimeHours': float(row[8]) if row[8] else 0,
            'moveTimeHours': float(row[9]) if row[9] else 0,
            'sourcingType': row[10],
            'isActive': bool(row[11])
        })


@api_bp.route('/routings/stats')
def routing_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("""
            SELECT COUNT(*) FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE p.DemoIndustryCode = :industry
        """), {'industry': industry}).fetchone()[0]
        active = conn.execute(text("""
            SELECT COUNT(*) FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE p.DemoIndustryCode = :industry AND r.IsActive = 1
        """), {'industry': industry}).fetchone()[0]
        inactive = conn.execute(text("""
            SELECT COUNT(*) FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE p.DemoIndustryCode = :industry AND r.IsActive = 0
        """), {'industry': industry}).fetchone()[0]
    return jsonify({
        'totalRoutings': total,
        'activeRoutings': active,
        'inactiveRoutings': inactive
    })

@api_bp.route('/routings', methods=['POST'])
def create_routing():
    data = request.json
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_WorkCenterRouting (
                    ProductID, WorkCenterID, OperationSequence,
                    SetupTimeHours, RunTimeHoursPerUnit, QueueTimeHours, MoveTimeHours,
                    SourcingType, IsActive, EffectiveDateKey
                ) VALUES (
                    :product, :workcenter, :seq,
                    :setup, :run, :queue, :move,
                    :sourcing, :active, :datekey
                )
            """), {
                'product': data['productId'],
                'workcenter': data['workCenterId'],
                'seq': data.get('operationSequence', 10),
                'setup': data.get('setupTimeHours', 0),
                'run': data.get('runTimeHoursPerUnit', 0),
                'queue': data.get('queueTimeHours', 0),
                'move': data.get('moveTimeHours', 0),
                'sourcing': data.get('sourcingType', 'Make'),
                'active': 1 if data.get('isActive') else 0,
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/<int:routing_id>', methods=['PUT'])
def update_routing(routing_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE t_WorkCenterRouting SET
                    ProductID = :product,
                    WorkCenterID = :workcenter,
                    OperationSequence = :seq,
                    SetupTimeHours = :setup,
                    RunTimeHoursPerUnit = :run,
                    QueueTimeHours = :queue,
                    MoveTimeHours = :move,
                    SourcingType = :sourcing,
                    IsActive = :active
                WHERE rowid = :id
            """), {
                'id': routing_id,
                'product': data['productId'],
                'workcenter': data['workCenterId'],
                'seq': data.get('operationSequence', 10),
                'setup': data.get('setupTimeHours', 0),
                'run': data.get('runTimeHoursPerUnit', 0),
                'queue': data.get('queueTimeHours', 0),
                'move': data.get('moveTimeHours', 0),
                'sourcing': data.get('sourcingType', 'Make'),
                'active': 1 if data.get('isActive') else 0
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/<int:routing_id>', methods=['DELETE'])
def delete_routing(routing_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM t_WorkCenterRouting WHERE rowid = :id"), {'id': routing_id})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/<int:routing_id>/instructions')
def get_routing_instructions(routing_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT InstructionID, StepNumber, Description, DescriptionLocal,
                   SetupInstructions, SetupInstructionsLocal,
                   OperatingInstructions, OperatingInstructionsLocal,
                   QualityCheckpoints, QualityCheckpointsLocal,
                   SafetyNotes, SafetyNotesLocal,
                   ImagePath, VideoPath, DisplayOrder, IsActive
            FROM t_WorkInstructions
            WHERE RoutingID = :id AND IsActive = 1
            ORDER BY StepNumber, DisplayOrder
        """), {'id': routing_id}).fetchall()
        instructions = [{
            'instructionId': r[0],
            'stepNumber': r[1],
            'description': r[2],
            'descriptionLocal': r[3],
            'setupInstructions': r[4],
            'setupInstructionsLocal': r[5],
            'operatingInstructions': r[6],
            'operatingInstructionsLocal': r[7],
            'qualityCheckpoints': r[8],
            'qualityCheckpointsLocal': r[9],
            'safetyNotes': r[10],
            'safetyNotesLocal': r[11],
            'imagePath': r[12],
            'videoPath': r[13],
            'displayOrder': r[14],
            'isActive': bool(r[15])
        } for r in rows]
    return jsonify(instructions)


@api_bp.route('/routings/<int:routing_id>/instructions', methods=['POST'])
def add_routing_instruction(routing_id):
    data = request.json
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_WorkInstructions (
                    RoutingID, StepNumber, Description, DescriptionLocal,
                    SetupInstructions, SetupInstructionsLocal,
                    OperatingInstructions, OperatingInstructionsLocal,
                    QualityCheckpoints, QualityCheckpointsLocal,
                    SafetyNotes, SafetyNotesLocal,
                    ImagePath, VideoPath, DisplayOrder, IsActive,
                    DemoIndustryCode, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :routing, :step, :desc, :desc_local,
                    :setup, :setup_local,
                    :operating, :operating_local,
                    :quality, :quality_local,
                    :safety, :safety_local,
                    :image, :video, :order, :active,
                    :industry, :datekey, :datekey
                )
            """), {
                'routing': routing_id,
                'step': data.get('stepNumber', 1),
                'desc': data.get('description', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'setup': data.get('setupInstructions', ''),
                'setup_local': data.get('setupInstructionsLocal', ''),
                'operating': data.get('operatingInstructions', ''),
                'operating_local': data.get('operatingInstructionsLocal', ''),
                'quality': data.get('qualityCheckpoints', ''),
                'quality_local': data.get('qualityCheckpointsLocal', ''),
                'safety': data.get('safetyNotes', ''),
                'safety_local': data.get('safetyNotesLocal', ''),
                'image': data.get('imagePath', ''),
                'video': data.get('videoPath', ''),
                'order': data.get('displayOrder', 0),
                'active': 1,
                'industry': industry,
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/instructions/<int:instruction_id>', methods=['PUT'])
def update_routing_instruction(instruction_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE t_WorkInstructions SET
                    StepNumber = :step,
                    Description = :desc,
                    DescriptionLocal = :desc_local,
                    SetupInstructions = :setup,
                    SetupInstructionsLocal = :setup_local,
                    OperatingInstructions = :operating,
                    OperatingInstructionsLocal = :operating_local,
                    QualityCheckpoints = :quality,
                    QualityCheckpointsLocal = :quality_local,
                    SafetyNotes = :safety,
                    SafetyNotesLocal = :safety_local,
                    ImagePath = :image,
                    VideoPath = :video,
                    DisplayOrder = :order,
                    ModifiedDateKey = :datekey
                WHERE InstructionID = :id
            """), {
                'id': instruction_id,
                'step': data.get('stepNumber', 1),
                'desc': data.get('description', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'setup': data.get('setupInstructions', ''),
                'setup_local': data.get('setupInstructionsLocal', ''),
                'operating': data.get('operatingInstructions', ''),
                'operating_local': data.get('operatingInstructionsLocal', ''),
                'quality': data.get('qualityCheckpoints', ''),
                'quality_local': data.get('qualityCheckpointsLocal', ''),
                'safety': data.get('safetyNotes', ''),
                'safety_local': data.get('safetyNotesLocal', ''),
                'image': data.get('imagePath', ''),
                'video': data.get('videoPath', ''),
                'order': data.get('displayOrder', 0),
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/instructions/<int:instruction_id>', methods=['DELETE'])
def delete_routing_instruction(instruction_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM t_WorkInstructions WHERE InstructionID = :id"), {'id': instruction_id})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/export')
def export_routings():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.ProductID, p.descEnglish, r.WorkCenterID, r.OperationSequence,
                   r.SetupTimeHours, r.RunTimeHoursPerUnit, r.QueueTimeHours, r.MoveTimeHours,
                   r.SourcingType, r.IsActive            FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE p.DemoIndustryCode = :industry
            ORDER BY p.descEnglish, r.OperationSequence
        """), {'industry': industry}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ProductID', 'ProductName', 'WorkCenterID', 'OperationSequence',
                     'SetupTimeHours', 'RunTimeHoursPerUnit', 'QueueTimeHours', 'MoveTimeHours',
                     'SourcingType', 'IsActive'])
    for r in rows:
        writer.writerow(list(r))
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=routings.csv'
    return response


# ==================== BOM FOR JOIN MAP ====================

@api_bp.route('/bom')
def get_bom_for_product():
    """Get BOM components for a product (used by Join Map)."""
    product_id = request.args.get('product_id')
    if not product_id:
        return jsonify([])
    
    engine = get_engine()
    today_key = get_today_key()
    try:
        with engine.connect() as conn:
            # FIX: Use integer comparison via :today parameter (SQL Server compatible)
            rows = conn.execute(text('''
                SELECT b.ComponentProductID, p.descEnglish as ComponentName, 
                       b.Quantity, b.OperationSequence
                FROM t_BOM b
                JOIN t_Product p ON b.ComponentProductID = p.ProductId
                WHERE b.ParentProductID = :pid
                  AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey >= :today)
                ORDER BY b.OperationSequence
            '''), {'pid': product_id, 'today': today_key}).fetchall()
            
            return jsonify([{
                'componentProductId': r[0],
                'componentName': r[1],
                'quantity': r[2],
                'operationSequence': r[3] or 0
            } for r in rows])
    except Exception as e:
        print(f"BOM API error: {e}")
        return jsonify([])


# ==================== ENGINEERING CHANGE ORDER (ECO) ====================

# Helper to generate ECO number
def generate_eco_number():
    from datetime import datetime
    year = datetime.now().year
    engine = get_engine()
    with engine.connect() as conn:
        # Count ECOs for this year
        count = conn.execute(text("""
            SELECT COUNT(*) FROM t_ECO 
            WHERE ECONumber LIKE :pattern
        """), {'pattern': f'ECO-{year}-%'}).fetchone()[0]
        next_num = count + 1
        return f"ECO-{year}-{str(next_num).zfill(3)}"


@api_bp.route('/eco')
def get_eco_list():
    industry, project_id = get_industry_and_project(request)
    status = request.args.get('status')
    priority = request.args.get('priority')
    product_id = request.args.get('product_id')
    
    engine = get_engine()
    query = """
        SELECT ECOID, ECONumber, Title, Description, AffectedProductID,
               Reason, Priority, Status, RequestedBy, RequestedDateKey,
               ApprovedBy, ApprovedDateKey, ImplementedBy, ImplementedDateKey,
               ImpactAnalysis, RiskAssessment, ProjectID
        FROM t_ECO
        WHERE DemoIndustryCode = :industry
    """
    params = {'industry': industry}
    
    if project_id and project_id != 'All' and project_id != '':
        query += " AND ProjectID = :project"
        params['project'] = project_id
    if status:
        query += " AND Status = :status"
        params['status'] = status
    if priority:
        query += " AND Priority = :priority"
        params['priority'] = priority
    if product_id:
        query += " AND AffectedProductID = :product"
        params['product'] = product_id
    
    query += " ORDER BY RequestedDateKey DESC, ECOID DESC"
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        eco_list = [{
            'ecoId': r[0],
            'ecoNumber': r[1],
            'title': r[2],
            'description': r[3],
            'affectedProductId': r[4],
            'reason': r[5],
            'priority': r[6],
            'status': r[7],
            'requestedBy': r[8],
            'requestedDateKey': r[9],
            'approvedBy': r[10],
            'approvedDateKey': r[11],
            'implementedBy': r[12],
            'implementedDateKey': r[13],
            'impactAnalysis': r[14],
            'riskAssessment': r[15],
            'projectId': r[16]
        } for r in rows]
    return jsonify(eco_list)


@api_bp.route('/eco/<int:eco_id>')
def get_eco_detail(eco_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT ECOID, ECONumber, Title, TitleLocal, Description, DescriptionLocal,
                   AffectedProductID, Reason, Priority, Status, RequestedBy, RequestedDateKey,
                   ApprovedBy, ApprovedDateKey, ImplementedBy, ImplementedDateKey,
                   ImpactAnalysis, RiskAssessment, ProjectID
            FROM t_ECO
            WHERE ECOID = :id
        """), {'id': eco_id}).fetchone()
        if not row:
            return jsonify({'error': 'ECO not found'}), 404
        return jsonify({
            'ecoId': row[0],
            'ecoNumber': row[1],
            'title': row[2],
            'titleLocal': row[3],
            'description': row[4],
            'descriptionLocal': row[5],
            'affectedProductId': row[6],
            'reason': row[7],
            'priority': row[8],
            'status': row[9],
            'requestedBy': row[10],
            'requestedDateKey': row[11],
            'approvedBy': row[12],
            'approvedDateKey': row[13],
            'implementedBy': row[14],
            'implementedDateKey': row[15],
            'impactAnalysis': row[16],
            'riskAssessment': row[17],
            'projectId': row[18]
        })


@api_bp.route('/eco/stats')
def eco_stats():
    industry, project_id = get_industry_and_project(request)
    engine = get_engine()
    query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN Status = 'Draft' THEN 1 ELSE 0 END) as draft,
            SUM(CASE WHEN Status = 'Review' THEN 1 ELSE 0 END) as review,
            SUM(CASE WHEN Status = 'Approved' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN Status = 'Implemented' THEN 1 ELSE 0 END) as implemented,
            SUM(CASE WHEN Status = 'Rejected' THEN 1 ELSE 0 END) as rejected
        FROM t_ECO
        WHERE DemoIndustryCode = :industry
    """
    params = {'industry': industry}
    if project_id and project_id != 'All' and project_id != '':
        query += " AND ProjectID = :project"
        params['project'] = project_id
    
    with engine.connect() as conn:
        row = conn.execute(text(query), params).fetchone()
    return jsonify({
        'total': row[0] or 0,
        'draft': row[1] or 0,
        'review': row[2] or 0,
        'approved': row[3] or 0,
        'implemented': row[4] or 0,
        'rejected': row[5] or 0
    })


@api_bp.route('/eco', methods=['POST'])
def create_eco():
    data = request.json
    industry = session.get('demo_industry', 'valve')
    user_id = session.get('user_id', 1)
    today_key = get_today_key()
    
    eco_number = generate_eco_number()
    
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ECO (
                    ECONumber, Title, TitleLocal, Description, DescriptionLocal,
                    AffectedProductID, Reason, Priority, Status,
                    RequestedBy, RequestedDateKey, DemoIndustryCode, ProjectID
                ) VALUES (
                    :number, :title, :title_local, :desc, :desc_local,
                    :product, :reason, :priority, 'Draft',
                    :requested_by, :datekey, :industry, :project
                )
            """), {
                'number': eco_number,
                'title': data['title'],
                'title_local': data.get('titleLocal', ''),
                'desc': data.get('description', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'product': data['affectedProductId'],
                'reason': data.get('reason', ''),
                'priority': data.get('priority', 'Medium'),
                'requested_by': user_id,
                'datekey': today_key,
                'industry': industry,
                'project': data.get('projectId', '')
            })
            conn.commit()
        return jsonify({'success': True, 'ecoNumber': eco_number})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/eco/<int:eco_id>', methods=['PUT'])
def update_eco(eco_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            # Check if ECO is in Draft status (only editable)
            status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
            if not status_row:
                return jsonify({'error': 'ECO not found'}), 404
            if status_row[0] != 'Draft':
                return jsonify({'error': 'ECO cannot be edited in current status'}), 400
            
            conn.execute(text("""
                UPDATE t_ECO SET
                    Title = :title,
                    TitleLocal = :title_local,
                    Description = :desc,
                    DescriptionLocal = :desc_local,
                    AffectedProductID = :product,
                    Reason = :reason,
                    Priority = :priority,
                    ProjectID = :project
                WHERE ECOID = :id
            """), {
                'id': eco_id,
                'title': data['title'],
                'title_local': data.get('titleLocal', ''),
                'desc': data.get('description', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'product': data['affectedProductId'],
                'reason': data.get('reason', ''),
                'priority': data.get('priority', 'Medium'),
                'project': data.get('projectId', '')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/eco/<int:eco_id>/submit', methods=['POST'])
def submit_eco(eco_id):
    engine = get_engine()
    with engine.connect() as conn:
        status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
        if not status_row:
            return jsonify({'error': 'ECO not found'}), 404
        if status_row[0] != 'Draft':
            return jsonify({'error': 'ECO cannot be submitted'}), 400
        
        conn.execute(text("UPDATE t_ECO SET Status = 'Review' WHERE ECOID = :id"), {'id': eco_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'ECO submitted for review'})


@api_bp.route('/eco/<int:eco_id>/approve', methods=['POST'])
def approve_eco(eco_id):
    user_id = session.get('user_id', 1)
    today_key = get_today_key()
    engine = get_engine()
    with engine.connect() as conn:
        status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
        if not status_row:
            return jsonify({'error': 'ECO not found'}), 404
        if status_row[0] != 'Review':
            return jsonify({'error': 'ECO must be in Review status to approve'}), 400
        
        conn.execute(text("""
            UPDATE t_ECO SET 
                Status = 'Approved',
                ApprovedBy = :user,
                ApprovedDateKey = :datekey
            WHERE ECOID = :id
        """), {'id': eco_id, 'user': user_id, 'datekey': today_key})
        conn.commit()
    return jsonify({'success': True, 'message': 'ECO approved'})


@api_bp.route('/eco/<int:eco_id>/reject', methods=['POST'])
def reject_eco(eco_id):
    engine = get_engine()
    with engine.connect() as conn:
        status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
        if not status_row:
            return jsonify({'error': 'ECO not found'}), 404
        if status_row[0] != 'Review':
            return jsonify({'error': 'ECO must be in Review status to reject'}), 400
        
        conn.execute(text("UPDATE t_ECO SET Status = 'Rejected' WHERE ECOID = :id"), {'id': eco_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'ECO rejected'})


@api_bp.route('/eco/<int:eco_id>/implement', methods=['POST'])
def implement_eco(eco_id):
    user_id = session.get('user_id', 1)
    today_key = get_today_key()
    engine = get_engine()
    with engine.connect() as conn:
        status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
        if not status_row:
            return jsonify({'error': 'ECO not found'}), 404
        if status_row[0] != 'Approved':
            return jsonify({'error': 'ECO must be Approved status to implement'}), 400
        
        conn.execute(text("""
            UPDATE t_ECO SET 
                Status = 'Implemented',
                ImplementedBy = :user,
                ImplementedDateKey = :datekey
            WHERE ECOID = :id
        """), {'id': eco_id, 'user': user_id, 'datekey': today_key})
        conn.commit()
    return jsonify({'success': True, 'message': 'ECO implemented'})


@api_bp.route('/eco/<int:eco_id>/cancel', methods=['POST'])
def cancel_eco(eco_id):
    engine = get_engine()
    with engine.connect() as conn:
        status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
        if not status_row:
            return jsonify({'error': 'ECO not found'}), 404
        if status_row[0] in ('Implemented', 'Rejected'):
            return jsonify({'error': 'ECO cannot be cancelled'}), 400
        
        conn.execute(text("UPDATE t_ECO SET Status = 'Cancelled' WHERE ECOID = :id"), {'id': eco_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'ECO cancelled'})


@api_bp.route('/eco/<int:eco_id>/delete', methods=['DELETE'])
def delete_eco(eco_id):
    engine = get_engine()
    with engine.connect() as conn:
        status_row = conn.execute(text("SELECT Status FROM t_ECO WHERE ECOID = :id"), {'id': eco_id}).fetchone()
        if not status_row:
            return jsonify({'error': 'ECO not found'}), 404
        if status_row[0] not in ('Draft', 'Rejected', 'Cancelled'):
            return jsonify({'error': 'ECO cannot be deleted'}), 400
        
        conn.execute(text("DELETE FROM t_ECO WHERE ECOID = :id"), {'id': eco_id})
        conn.commit()
    return jsonify({'success': True})


# ==================== BOM ENDPOINTS (FIXED WITH PROJECT FILTER) ====================

@api_bp.route('/tree/<string:product_id>')
def bom_tree(product_id):
    industry, project_id = get_industry_and_project(request)
    effectivity_date = request.args.get('effectivity_date')
    show_locked_only = request.args.get('show_locked_only') == 'true'
    engine = get_engine()
    today_key = str(int(datetime.now().strftime('%Y%m%d')))

    with engine.connect() as conn:
        # Project check only if a specific project is selected
        if project_id and project_id not in ('All', ''):
            project_list = [x.strip() for x in project_id.split(',') if x.strip()]
            placeholders = ','.join([f':p{i}' for i in range(len(project_list))])
            params = {'pid': product_id, 'industry': industry}
            for i, pid in enumerate(project_list):
                params[f'p{i}'] = pid

            root_ok = conn.execute(text(f"""
                SELECT COUNT(*) FROM t_Product p
                WHERE p.ProductId = :pid
                  AND p.DemoIndustryCode = :industry
                  AND p.ProductId IN (
                      SELECT ProductID FROM t_ProjectProducts WHERE ProjectID IN ({placeholders})
                  )
            """), params).fetchone()[0]

            if not root_ok:
                return jsonify([])

        # Build tree — industry filter only
        def build_tree(conn, parent_id):
            rows = conn.execute(text("""
                SELECT b.ComponentProductID, p.descEnglish, b.Quantity, b.ScrapFactor,
                       b.EffectivityDateKey, b.ObsoleteDateKey, b.BOMId
                FROM t_BOM b
                JOIN t_Product p ON b.ComponentProductID = p.ProductId
                WHERE b.ParentProductID = :parent
                  AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey >= :today)
                  AND p.DemoIndustryCode = :industry
            """), {'parent': parent_id, 'today': today_key, 'industry': industry}).fetchall()

            children = []
            for row in rows:
                if effectivity_date and row[4] and str(row[4]) > effectivity_date:
                    continue
                children.append({
                    'id': row[6],
                    'component_id': row[0],
                    'name': row[1],
                    'quantity': row[2],
                    'scrap_factor': row[3],
                    'effectivity': row[4],
                    'obsolete': row[5],
                    'locked': False,
                    'children': build_tree(conn, row[0])
                })
            return children

        tree = build_tree(conn, product_id)

    return jsonify(tree)

@api_bp.route('/component', methods=['POST'])
def add_component():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            INSERT INTO t_BOM (ParentProductID, ComponentProductID, Quantity, ScrapFactor, EffectivityDateKey, ObsoleteDateKey)
            VALUES (:parent, :component, :qty, :scrap, :eff, :obs)
        '''), {
            'parent': data['parent_id'],
            'component': data['component_id'],
            'qty': data['quantity'],
            'scrap': data.get('scrap_factor', 0),
            'eff': data.get('effectivity_date'),
            'obs': data.get('obsolete_date')
        })
        conn.commit()
    return jsonify({'success': True, 'message': 'Component added'})


@api_bp.route('/component/<int:bom_id>', methods=['PUT'])
def update_component(bom_id):
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            UPDATE t_BOM SET Quantity=:qty, ScrapFactor=:scrap, EffectivityDateKey=:eff, ObsoleteDateKey=:obs
            WHERE BOMId = :id
        '''), {
            'qty': data['quantity'],
            'scrap': data.get('scrap_factor', 0),
            'eff': data.get('effectivity_date'),
            'obs': data.get('obsolete_date'),
            'id': bom_id
        })
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/component/<int:bom_id>', methods=['DELETE'])
def delete_component(bom_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM t_BOM WHERE BOMId = :id'), {'id': bom_id})
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/where-used/<string:component_id>')
def where_used(component_id):
    industry, project_id = get_industry_and_project(request)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT p.ProductId, p.descEnglish, b.Quantity
            FROM t_BOM b
            JOIN t_Product p ON b.ParentProductID = p.ProductId
            WHERE b.ComponentProductID = :comp
              AND p.DemoIndustryCode = :industry
        '''), {'comp': component_id, 'industry': industry}).fetchall()
    return jsonify([{'ProductID': r[0], 'ProductName': r[1], 'Quantity': r[2]} for r in rows])


@api_bp.route('/import', methods=['POST'])
def import_bom_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    csv_input = csv.DictReader(stream)
    engine = get_engine()
    with engine.connect() as conn:
        for row in csv_input:
            conn.execute(text('''
                INSERT OR IGNORE INTO t_BOM (ParentProductID, ComponentProductID, Quantity, ScrapFactor, EffectivityDateKey, ObsoleteDateKey)
                VALUES (:parent, :component, :qty, :scrap, :eff, :obs)
            '''), {
                'parent': row['ParentProductID'],
                'component': row['ComponentProductID'],
                'qty': row['Quantity'],
                'scrap': row.get('ScrapFactor', 0),
                'eff': row.get('EffectivityDateKey'),
                'obs': row.get('ObsoleteDateKey')
            })
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/export/<string:product_id>')
def export_bom_csv(product_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT ParentProductID, ComponentProductID, Quantity, ScrapFactor, EffectivityDateKey, ObsoleteDateKey
            FROM t_BOM
            WHERE ParentProductID = :prod
        '''), {'prod': product_id}).fetchall()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['ParentProductID', 'ComponentProductID', 'Quantity',
                                                'ScrapFactor', 'EffectivityDateKey', 'ObsoleteDateKey'])
    writer.writeheader()
    for r in rows:
        writer.writerow({
            'ParentProductID': r[0],
            'ComponentProductID': r[1],
            'Quantity': r[2],
            'ScrapFactor': r[3],
            'EffectivityDateKey': r[4],
            'ObsoleteDateKey': r[5]
        })
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=bom_{product_id}.csv'
    return response




@api_bp.route('/stats')
def bom_stats():
    industry, project_id = get_industry_and_project(request)
    engine = get_engine()
    today_key = str(int(datetime.now().strftime('%Y%m%d')))

    # Build project filter only when a project is selected
    project_filter = ""
    params = {'today': today_key, 'industry': industry}
    if project_id and project_id not in ('All', ''):
        project_list = [x.strip() for x in project_id.split(',') if x.strip()]
        placeholders = ','.join([f':p{i}' for i in range(len(project_list))])
        project_filter = f"""
            AND b.ParentProductID IN (
                SELECT ProductID FROM t_ProjectProducts WHERE ProjectID IN ({placeholders})
            )
        """
        for i, pid in enumerate(project_list):
            params[f'p{i}'] = pid

    with engine.connect() as conn:
        total = conn.execute(text(f"""
            SELECT COUNT(*) FROM t_BOM b
            JOIN t_Product p ON b.ComponentProductID = p.ProductId
            WHERE (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey >= :today)
              AND p.DemoIndustryCode = :industry
              {project_filter}
        """), params).fetchone()[0]

        where_used = conn.execute(text(f"""
            SELECT COUNT(DISTINCT b.ParentProductID) FROM t_BOM b
            JOIN t_Product p ON b.ComponentProductID = p.ProductId
            WHERE p.DemoIndustryCode = :industry
              {project_filter}
        """), params).fetchone()[0]

        try:
            pending_ecos = conn.execute(text("SELECT COUNT(*) FROM t_ChangeOrder WHERE Status = 'Pending'")).fetchone()[0]
        except:
            pending_ecos = 0

        locked_docs = conn.execute(text("""
            SELECT COUNT(*) FROM t_EngineeringDocument
            WHERE CheckedOutBy IS NOT NULL AND DemoIndustryCode = :industry
        """), {'industry': industry}).fetchone()[0]

    return jsonify({
        'totalComponents': total,
        'whereUsedRefs': where_used,
        'pendingECOs': pending_ecos,
        'lockedDocuments': locked_docs
    })


@api_bp.route('/component/<string:component_id>/summary')
def component_summary(component_id):
    """
    Get comprehensive summary for a component including inventory, quality,
    purchasing, production, non-conformance, engineering, and sales data.
    """
    industry, project_id = get_industry_and_project(request)
    engine = get_engine()
    summary = {
        'component_id': component_id,
        'name': '',
        'inventory': {'on_hand': 0, 'reserved': 0, 'available': 0, 'low_stock_threshold': 0},
        'quality': {'assigned_operations_count': 0, 'last_inspection_result': 'N/A', 'last_inspection_date': None},
        'purchasing': {'open_pos_count': 0, 'overdue_pos_count': 0, 'preferred_supplier': '', 'standard_lead_time_days': 0},
        'production': {'active_orders_using_count': 0, 'required_next_30_days': 0},
        'non_conformance': {'open_ncrs_count': 0, 'closed_ncrs_count': 0},
        'engineering': {'pending_ecos_count': 0, 'document_count': 0},
        'sales': {'sold_as_spare_ytd': 0, 'service_orders_count': 0}
    }

    with engine.connect() as conn:
        # Get product name and safety stock
        row = conn.execute(text('''
            SELECT descEnglish, SafetyStock FROM t_Product 
            WHERE ProductId = :pid AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()
        if row:
            summary['name'] = row[0] or ''
            summary['inventory']['low_stock_threshold'] = row[1] or 0

        # Inventory on hand
        inv = conn.execute(text('''
            SELECT SUM(Qty) FROM t_InventoryOnHand 
            WHERE ProductID = :pid AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()
        on_hand = inv[0] or 0
        summary['inventory']['on_hand'] = on_hand
        summary['inventory']['available'] = on_hand

        # Quality - assigned operations count
        qual_count = conn.execute(text('''
            SELECT COUNT(*) FROM t_QualityOperationAssignment
            WHERE SourceType = 'Product' AND SourceID = :pid 
              AND IsActive = 1 AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['quality']['assigned_operations_count'] = qual_count

        # ============================================================================
        # FIX: Use TOP 1 with proper SQL Server syntax
        # ============================================================================
        # SQL Server uses SELECT TOP 1 for fetching a single row.
        # The original query used "ORDER BY InspectionDateKey DESC LIMIT 1" which
        # failed on SQL Server. The corrected query uses TOP 1.
        # ============================================================================
        last_batch = conn.execute(text('''
            SELECT TOP 1 OverallResult, InspectionDateKey
            FROM t_QualityInspectionBatch
            WHERE ProductID = :product_id AND DemoIndustryCode = :industry
              AND OverallResult IS NOT NULL
            ORDER BY InspectionDateKey DESC
        '''), {'product_id': component_id, 'industry': industry}).fetchone()
        if last_batch:
            summary['quality']['last_inspection_result'] = last_batch[0]
            if last_batch[1]:
                dk = last_batch[1]
                summary['quality']['last_inspection_date'] = f"{dk//10000}-{(dk%10000)//100:02d}-{dk%100:02d}"

        # Purchasing - open POs
        open_pos = conn.execute(text('''
            SELECT COUNT(DISTINCT po.PurchaseOrderID) FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE pod.ProductID = :pid AND po.Status NOT IN ('Completed', 'Cancelled')
              AND po.DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['purchasing']['open_pos_count'] = open_pos

        # ============================================================================
        # FIX: Preferred supplier query - replaced LIMIT 1 with TOP 1 for SQL Server
        # ============================================================================
        # SQL Server does not support "LIMIT 1" syntax. Changed to "SELECT TOP 1 ..."
        # and moved the ORDER BY clause to the outer query for correct grouping.
        # ============================================================================
        pref = conn.execute(text('''
            SELECT TOP 1 s.SupplierName, prod.LeadTime
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
            JOIN t_Product prod ON pod.ProductID = prod.ProductId
            WHERE pod.ProductID = :pid AND po.DemoIndustryCode = :industry
            GROUP BY s.SupplierName, prod.LeadTime, po.SupplierID
            ORDER BY COUNT(*) DESC
        '''), {'pid': component_id, 'industry': industry}).fetchone()
        if pref:
            summary['purchasing']['preferred_supplier'] = pref[0] or ''
            summary['purchasing']['standard_lead_time_days'] = pref[1] or 0
        else:
            lt = conn.execute(text('''
                SELECT LeadTime FROM t_Product 
                WHERE ProductId = :pid AND DemoIndustryCode = :industry
            '''), {'pid': component_id, 'industry': industry}).fetchone()
            if lt:
                summary['purchasing']['standard_lead_time_days'] = lt[0] or 0

        # Production - active orders
        active_orders = conn.execute(text('''
            SELECT COUNT(*) FROM t_ProductionOrder
            WHERE ProductId = :pid AND Status NOT IN ('Completed', 'Cancelled')
              AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['production']['active_orders_using_count'] = active_orders

        # Production - required next 30 days
        today_key = get_today_key()
        future_key = int((datetime.now() + timedelta(days=30)).strftime('%Y%m%d'))
        req_30 = conn.execute(text('''
            SELECT SUM(OrderQuantity) FROM t_ProductionOrder
            WHERE ProductId = :pid AND Status NOT IN ('Completed', 'Cancelled')
              AND StartDateKey BETWEEN :today AND :future
              AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry, 'today': today_key, 'future': future_key}).fetchone()[0] or 0
        summary['production']['required_next_30_days'] = req_30

        # ============================================================================
        # FIX: Non-conformance queries - replaced double quotes with single quotes
        # ============================================================================
        # SQL Server requires single quotes for string comparisons, not double quotes.
        # The original query used Status = "Open" which failed on SQL Server.
        # ============================================================================
        open_ncrs = conn.execute(text('''
            SELECT COUNT(*) FROM t_NonConformance 
            WHERE ProductID = :pid AND Status = 'Open' AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        closed_ncrs = conn.execute(text('''
            SELECT COUNT(*) FROM t_NonConformance 
            WHERE ProductID = :pid AND Status = 'Closed' AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['non_conformance']['open_ncrs_count'] = open_ncrs
        summary['non_conformance']['closed_ncrs_count'] = closed_ncrs

        # Engineering - document count
        doc_count = conn.execute(text('''
            SELECT COUNT(*) FROM t_EngineeringDocument
            WHERE (ProjectID IN (SELECT ProjectID FROM t_ProjectProducts WHERE ProductID = :pid)
               OR DocumentNumber LIKE :pat)
              AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'pat': f'%{component_id}%', 'industry': industry}).fetchone()[0]
        summary['engineering']['document_count'] = doc_count

        # Sales - sold as spare YTD
        year_start = int(f"{datetime.now().year}0101")
        sold_ytd = conn.execute(text('''
            SELECT SUM(Qty) FROM t_SalesOrder
            WHERE ProductID = :pid AND OrderDateKey >= :start
              AND DemoIndustryCode = :industry
        '''), {'pid': component_id, 'industry': industry, 'start': year_start}).fetchone()[0] or 0
        summary['sales']['sold_as_spare_ytd'] = sold_ytd

    return jsonify(summary)


# ==================== PRODUCT ENDPOINTS (EXISTING - UPDATED) ====================

@api_bp.route('/products')
def get_products():
    industry, project_id = get_industry_and_project(request)
    procurement = request.args.get('procurement_type')
    
    engine = get_engine()
    query = """
        SELECT ProductId, descEnglish, UOM, StandardCost, ProcurementType
        FROM t_Product
        WHERE DemoIndustryCode = :industry
    """
    params = {'industry': industry}
    
    if procurement:
        query += " AND ProcurementType = :proc"
        params['proc'] = procurement
    if project_id and project_id != 'All' and project_id != '':
        # Split comma-separated project IDs and filter via t_ProjectProducts
        project_list = [p.strip() for p in project_id.split(',') if p.strip()]
        if project_list:
            named_placeholders = ','.join([f':proj_{i}' for i in range(len(project_list))])
            query += f" AND ProductId IN (SELECT ProductID FROM t_ProjectProducts WHERE ProjectID IN ({named_placeholders}))"
            for i, proj in enumerate(project_list):
                params[f'proj_{i}'] = proj
    
    query += " ORDER BY ProductId"
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        products = [{
            'ProductId': r[0],
            'ProductName': r[1],
            'UOM': r[2],
            'StandardCost': float(r[3]) if r[3] else 0.0,
            'ProcurementType': r[4],
            'WarrantyMonths': 0
        } for r in rows]
    return jsonify(products)


@api_bp.route('/products/<string:product_id>')
def get_product(product_id):
    industry, project_id = get_industry_and_project(request)
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT ProductId, descEnglish, UOM, StandardCost, ProcurementType,
                   LeadTime, MinOrderQty, SafetyStock, IsManufactured, BatchSize, TechnicalDrawing
            FROM t_Product
            WHERE ProductId = :pid AND DemoIndustryCode = :industry
        '''), {'pid': product_id, 'industry': industry}).fetchone()
        if not row:
            return jsonify({'error': 'Product not found'}), 404
        product = {
            'ProductId': row[0],
            'descEnglish': row[1],
            'UOM': row[2],
            'StandardCost': float(row[3]) if row[3] else 0,
            'ProcurementType': row[4],
            'LeadTime': row[5],
            'MinOrderQty': row[6],
            'SafetyStock': row[7],
            'IsManufactured': bool(row[8]),
            'BatchSize': row[9],
            'TechnicalDrawing': row[10],
            'WarrantyMonths': 0
        }
    return jsonify(product)




@api_bp.route('/products/stats')
def product_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()

    with engine.connect() as conn:
        total = conn.execute(text("""
            SELECT COUNT(*) FROM t_Product
            WHERE DemoIndustryCode = :industry
        """), {'industry': industry}).fetchone()[0]

        manufactured = conn.execute(text("""
            SELECT COUNT(*) FROM t_Product
            WHERE DemoIndustryCode = :industry
              AND ProcurementType = 'Manufactured'
        """), {'industry': industry}).fetchone()[0]

        purchased = conn.execute(text("""
            SELECT COUNT(*) FROM t_Product
            WHERE DemoIndustryCode = :industry
              AND ProcurementType = 'Purchased'
        """), {'industry': industry}).fetchone()[0]

    return jsonify({
        'totalProducts': total,
        'manufactured': manufactured,
        'purchased': purchased,
        'activeProducts': total   # no Status column → count all
    })

@api_bp.route('/products', methods=['POST'])
def create_product():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO t_Product (
                    ProductId, descEnglish, UOM, StandardCost, ProcurementType,
                    LeadTime, MinOrderQty, SafetyStock, IsManufactured, BatchSize, TechnicalDrawing,
                    DemoIndustryCode
                ) VALUES (
                    :pid, :name, :uom, :cost, :proc,
                    :lead, :minqty, :safety, :isman, :batch, :drawing,
                    :industry
                )
            '''), {
                'pid': data['ProductId'],
                'name': data['descEnglish'],
                'uom': data.get('UOM', 'EA'),
                'cost': data.get('StandardCost', 0),
                'proc': data.get('ProcurementType', 'Manufactured'),
                'lead': data.get('LeadTime', 0),
                'minqty': data.get('MinOrderQty', 1),
                'safety': data.get('SafetyStock', 0),
                'isman': 1 if data.get('ProcurementType') == 'Manufactured' else 0,
                'batch': data.get('BatchSize', 1),
                'drawing': data.get('TechnicalDrawing', ''),
                'industry': session.get('demo_industry', 'valve')
            })
            conn.commit()
        return jsonify({'success': True, 'message': 'Product created'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/products/<string:product_id>', methods=['PUT'])
def update_product(product_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                UPDATE t_Product SET
                    descEnglish = :name,
                    UOM = :uom,
                    StandardCost = :cost,
                    ProcurementType = :proc,
                    LeadTime = :lead,
                    MinOrderQty = :minqty,
                    SafetyStock = :safety,
                    IsManufactured = :isman,
                    BatchSize = :batch,
                    TechnicalDrawing = :drawing
                WHERE ProductId = :pid AND DemoIndustryCode = :industry
            '''), {
                'pid': product_id,
                'name': data.get('descEnglish'),
                'uom': data.get('UOM', 'EA'),
                'cost': data.get('StandardCost', 0),
                'proc': data.get('ProcurementType', 'Manufactured'),
                'lead': data.get('LeadTime', 0),
                'minqty': data.get('MinOrderQty', 1),
                'safety': data.get('SafetyStock', 0),
                'isman': 1 if data.get('ProcurementType') == 'Manufactured' else 0,
                'batch': data.get('BatchSize', 1),
                'drawing': data.get('TechnicalDrawing', ''),
                'industry': session.get('demo_industry', 'valve')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/products/<string:product_id>', methods=['DELETE'])
def delete_product(product_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text('DELETE FROM t_Product WHERE ProductId = :pid'), {'pid': product_id})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/products/import', methods=['POST'])
def import_products_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    with engine.connect() as conn:
        for row in reader:
            conn.execute(text('''
                INSERT OR REPLACE INTO t_Product (
                    ProductId, descEnglish, UOM, StandardCost, ProcurementType,
                    LeadTime, MinOrderQty, SafetyStock, IsManufactured, BatchSize, TechnicalDrawing,
                    DemoIndustryCode
                ) VALUES (
                    :pid, :name, :uom, :cost, :proc,
                    :lead, :minqty, :safety, :isman, :batch, :drawing,
                    :industry
                )
            '''), {
                'pid': row['ProductId'],
                'name': row.get('descEnglish', ''),
                'uom': row.get('UOM', 'EA'),
                'cost': float(row.get('StandardCost', 0)),
                'proc': row.get('ProcurementType', 'Manufactured'),
                'lead': int(row.get('LeadTime', 0)),
                'minqty': int(row.get('MinOrderQty', 1)),
                'safety': int(row.get('SafetyStock', 0)),
                'isman': 1 if row.get('ProcurementType') == 'Manufactured' else 0,
                'batch': int(row.get('BatchSize', 1)),
                'drawing': row.get('TechnicalDrawing', ''),
                'industry': industry
            })
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/products/export')
def export_products_csv():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT ProductId, descEnglish, UOM, StandardCost, ProcurementType 
            FROM t_Product 
            WHERE DemoIndustryCode = :industry
        '''), {'industry': industry}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ProductId', 'descEnglish', 'UOM', 'StandardCost', 'ProcurementType'])
    writer.writerows(rows)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=products.csv'
    return response


# ==================== ENGINEERING DOCUMENTS ENDPOINTS (EXISTING - UPDATED) ====================

def get_upload_folder():
    upload_path = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_path, exist_ok=True)
    return upload_path


def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'jpg', 'png', 'dwg', 'dxf'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@api_bp.route('/documents/stats')
def document_stats():
    industry, project_id = get_industry_and_project(request)
    doc_type = request.args.get('doc_type')
    status = request.args.get('status')
    checkout = request.args.get('checkout_status')
    project = request.args.get('project')
    engine = get_engine()
    
    def count_by_status(stat):
        q = "SELECT COUNT(*) FROM t_EngineeringDocument WHERE Status = :stat AND DemoIndustryCode = :industry"
        params = {'stat': stat, 'industry': industry}
        if doc_type:
            q += " AND DocumentType = :type"
            params['type'] = doc_type
        if project:
            q += " AND ProjectID = :proj"
            params['proj'] = project
        if checkout == 'checked_out':
            q += " AND CheckedOutBy IS NOT NULL"
        elif checkout == 'available':
            q += " AND CheckedOutBy IS NULL"
        if project_id and project_id != 'All' and project_id != '':
            q += " AND ProjectID = :proj"
            params['proj'] = project_id
        return conn.execute(text(q), params).fetchone()[0]
    
    with engine.connect() as conn:
        draft = count_by_status('Draft')
        under_review = count_by_status('Under Review')
        approved = count_by_status('Approved')
        obsolete = count_by_status('Obsolete')
        checked_out = conn.execute(text('''
            SELECT COUNT(*) FROM t_EngineeringDocument
            WHERE CheckedOutBy IS NOT NULL AND DemoIndustryCode = :industry
        '''), {'industry': industry}).fetchone()[0]
    return jsonify({'draft': draft, 'underReview': under_review, 'approved': approved, 'obsolete': obsolete, 'checkedOut': checked_out})


@api_bp.route('/documents')
def get_documents():
    industry, project_id = get_industry_and_project(request)
    doc_type = request.args.get('doc_type')
    status = request.args.get('status')
    checkout = request.args.get('checkout_status')
    project = request.args.get('project')

    query = '''
        SELECT DocumentID, DocumentNumber, Title, Revision, Status,
               CheckedOutBy, CheckedOutDate, LastModified, DocumentType, ProjectID
        FROM t_EngineeringDocument
        WHERE DemoIndustryCode = :industry
    '''
    params = {'industry': industry}
    if doc_type:
        query += ' AND DocumentType = :type'
        params['type'] = doc_type
    if status:
        query += ' AND Status = :status'
        params['status'] = status
    if checkout == 'checked_out':
        query += ' AND CheckedOutBy IS NOT NULL'
    elif checkout == 'available':
        query += ' AND CheckedOutBy IS NULL'
    if project:
        query += ' AND ProjectID = :proj'
        params['proj'] = project
    if project_id and project_id != 'All' and project_id != '':
        query += ' AND ProjectID = :proj'
        params['proj'] = project_id

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        docs = []
        for r in rows:
            docs.append({
                'id': r[0],
                'docNumber': r[1],
                'title': r[2],
                'revision': r[3],
                'status': r[4],
                'checkedOutBy': r[5],
                'checkedOutDate': r[6],
                'lastModified': r[7],
                'docType': r[8],
                'projectId': r[9]
            })
    return jsonify(docs)


# FIX: Changed <int:doc_id> to <string:doc_id> so that text DocumentIDs
# (e.g., "DOC-001") are matched instead of only numeric IDs.
@api_bp.route('/documents/<string:doc_id>')
def get_document(doc_id):
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT * FROM t_EngineeringDocument 
            WHERE DocumentID = :id AND DemoIndustryCode = :industry
        '''), {'id': doc_id, 'industry': industry}).fetchone()
        if not row:
            return jsonify({'error': 'Document not found'}), 404
        doc = {
            'id': row[0],
            'docNumber': row[1],
            'revision': row[16] if len(row) > 16 else row[2],
            'title': row[15] if len(row) > 15 else '',
            'status': row[8],
            'checkedOutBy': row[17] if len(row) > 17 else None,
            'checkedOutDate': row[18] if len(row) > 18 else None,
            'fileName': row[19] if len(row) > 19 else '',
            'filePath': row[7],
            'docType': row[4],
            'projectId': row[21] if len(row) > 21 else None,
            'notes': row[14]
        }
    return jsonify(doc)


@api_bp.route('/documents', methods=['POST'])
def create_document():
    data = request.form
    file = request.files.get('file')
    if not file or not allowed_file(file.filename):
        return jsonify({'error': 'Invalid or missing file'}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    upload_folder = get_upload_folder()
    file_path = os.path.join('static', 'uploads', unique_name)
    full_path = os.path.join(current_app.root_path, file_path)
    file.save(full_path)

    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    with engine.connect() as conn:
        conn.execute(text('''
            INSERT INTO t_EngineeringDocument (
                DocumentNumber, Title, Revision, Status, DocumentType, ProjectID,
                FileName, FilePath, LastModified, CreatedDateKey, DemoIndustryCode
            ) VALUES (
                :num, :title, '1', 'Draft', :type, :proj,
                :fname, :fpath, :modified, :datekey, :industry
            )
        '''), {
            'num': data['docNumber'],
            'title': data['title'],
            'type': data.get('docType', 'Drawing'),
            'proj': data.get('projectId'),
            'fname': filename,
            'fpath': file_path,
            'modified': datetime.now().isoformat(),
            'datekey': get_today_key(),
            'industry': industry
        })
        conn.commit()
    return jsonify({'success': True, 'message': 'Document created'})


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/documents/<string:doc_id>/checkout', methods=['POST'])
def checkout_document(doc_id):
    user = request.json.get('user', 'unknown')
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT CheckedOutBy FROM t_EngineeringDocument 
            WHERE DocumentID = :id
        '''), {'id': doc_id}).fetchone()
        if row and row[0]:
            return jsonify({'error': f'Document already checked out by {row[0]}'}), 409
        conn.execute(text('''
            UPDATE t_EngineeringDocument SET CheckedOutBy = :user, CheckedOutDate = :date
            WHERE DocumentID = :id
        '''), {'user': user, 'date': datetime.now().isoformat(), 'id': doc_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'Checked out'})


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/documents/<string:doc_id>/checkin', methods=['POST'])
def checkin_document(doc_id):
    user = request.form.get('user', 'unknown')
    notes = request.form.get('notes', '')
    file = request.files.get('file')
    engine = get_engine()
    with engine.connect() as conn:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder = get_upload_folder()
            file_path = os.path.join('static', 'uploads', unique_name)
            full_path = os.path.join(current_app.root_path, file_path)
            file.save(full_path)

            curr = conn.execute(text('SELECT Revision FROM t_EngineeringDocument WHERE DocumentID = :id'), {'id': doc_id}).fetchone()[0]
            new_rev = str(int(curr) + 1)
            conn.execute(text('''
                UPDATE t_EngineeringDocument SET
                    Revision = :new_rev, FileName = :fname, FilePath = :fpath,
                    LastModified = :date, CheckedOutBy = NULL, CheckedOutDate = NULL
                WHERE DocumentID = :id
            '''), {'new_rev': new_rev, 'fname': filename, 'fpath': file_path, 'date': datetime.now().isoformat(), 'id': doc_id})
            try:
                conn.execute(text('''
                    INSERT INTO t_DocumentRevisions (DocumentID, Revision, FilePath, ChangeNotes, CreatedDate, CreatedBy)
                    VALUES (:did, :rev, :path, :notes, :date, :user)
                '''), {'did': doc_id, 'rev': new_rev, 'path': file_path, 'notes': notes, 'date': datetime.now().isoformat(), 'user': user})
            except:
                pass
        else:
            conn.execute(text('''
                UPDATE t_EngineeringDocument SET CheckedOutBy = NULL, CheckedOutDate = NULL
                WHERE DocumentID = :id
            '''), {'id': doc_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'Checked in'})


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/documents/<string:doc_id>/share', methods=['POST'])
def share_document(doc_id):
    email = request.json.get('email')
    return jsonify({'success': True, 'message': f'External share link sent to {email}'})


@api_bp.route('/documents/<int:doc_id>/subscribe', methods=['POST'])
def subscribe_document(doc_id):
    user_id = request.json.get('userId', 1)
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            INSERT OR IGNORE INTO t_NotificationSettings (UserID, DocumentID, NotifyOnChange)
            VALUES (:uid, :did, 1)
        '''), {'uid': user_id, 'did': doc_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'Subscribed to document changes'})


@api_bp.route('/documents/<int:doc_id>/unsubscribe', methods=['POST'])
def unsubscribe_document(doc_id):
    user_id = request.json.get('userId', 1)
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM t_NotificationSettings WHERE UserID = :uid AND DocumentID = :did'), {'uid': user_id, 'did': doc_id})
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/documents/import', methods=['POST'])
def import_documents_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    with engine.connect() as conn:
        for row in reader:
            conn.execute(text('''
                INSERT OR IGNORE INTO t_EngineeringDocument (
                    DocumentNumber, Title, Revision, Status, DocumentType, ProjectID,
                    DemoIndustryCode
                ) VALUES (
                    :num, :title, '1', 'Draft', :type, :proj,
                    :industry
                )
            '''), {
                'num': row['DocumentNumber'],
                'title': row['Title'],
                'type': row.get('DocumentType', 'Drawing'),
                'proj': row.get('ProjectID'),
                'industry': industry
            })
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/documents/export')
def export_documents_csv():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT DocumentNumber, Title, Revision, Status, DocumentType, ProjectID 
            FROM t_EngineeringDocument 
            WHERE DemoIndustryCode = :industry
        '''), {'industry': industry}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['DocumentNumber', 'Title', 'Revision', 'Status', 'DocumentType', 'ProjectID'])
    writer.writerows(rows)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=engineering_documents.csv'
    return response


# ==================== DOCUMENT CONTROL (WORKFLOW) ENDPOINTS ====================

@api_bp.route('/document-control/stats')
def doc_control_stats():
    current_user = request.args.get('user', 'Current User')
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    # FIX: Use today_key parameter instead of date('now') for SQL Server compatibility
    today_key = get_today_key()
    with engine.connect() as conn:
        pending = conn.execute(text('''
            SELECT COUNT(*) FROM t_WorkflowInstances w
            JOIN t_EngineeringDocument d ON w.DocumentID = d.DocumentID
            WHERE w.AssignedTo = :user AND w.CurrentState = 'Pending Approval'
              AND d.DemoIndustryCode = :industry
        '''), {'user': current_user, 'industry': industry}).fetchone()[0]
        # FIX: Replaced w.DueDate < date('now') with integer DateKey comparison.
        # If DueDate is a DATE column, use CAST(GETDATE() AS DATE); if it's an INT DateKey,
        # use :today_key. The safer cross-type version is provided via SQL Server cast.
        overdue = conn.execute(text('''
            SELECT COUNT(*) FROM t_WorkflowInstances w
            JOIN t_EngineeringDocument d ON w.DocumentID = d.DocumentID
            WHERE w.CurrentState != 'Approved' AND w.CurrentState != 'Rejected'
              AND w.DueDate < CAST(GETDATE() AS DATE)
              AND d.DemoIndustryCode = :industry
        '''), {'industry': industry}).fetchone()[0]
    return jsonify({'pendingMyApproval': pending, 'overdueReviews': overdue})


@api_bp.route('/document-control/tasks')
def get_document_tasks():
    industry = session.get('demo_industry', 'valve')
    doc_type = request.args.get('doc_type')
    pending_action = request.args.get('pending_action')
    assignee = request.args.get('assignee')

    query = '''
        SELECT w.InstanceID, w.DocumentID, d.DocumentNumber, d.Title, d.Revision,
               w.CurrentState, w.AssignedTo, w.DueDate
        FROM t_WorkflowInstances w
        JOIN t_EngineeringDocument d ON w.DocumentID = d.DocumentID
        WHERE d.DemoIndustryCode = :industry
    '''
    params = {'industry': industry}
    if doc_type:
        query += ' AND d.DocumentType = :type'
        params['type'] = doc_type
    if pending_action:
        query += ' AND w.CurrentState = :state'
        params['state'] = pending_action
    if assignee:
        query += ' AND w.AssignedTo = :assignee'
        params['assignee'] = assignee
    query += ' ORDER BY w.DueDate ASC'

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        tasks = []
        for r in rows:
            tasks.append({
                'instanceId': r[0],
                'docId': r[1],
                'docNumber': r[2],
                'title': r[3],
                'revision': r[4],
                'pendingAction': r[5],
                'assignedTo': r[6],
                'dueDate': r[7]
            })
    return jsonify(tasks)


@api_bp.route('/document-control/approve/<int:instance_id>', methods=['POST'])
def approve_document(instance_id):
    comments = request.json.get('comments', '')
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            UPDATE t_WorkflowInstances
            SET CurrentState = 'Approved', CompletedDate = :date, Comments = :comments
            WHERE InstanceID = :id
        '''), {'id': instance_id, 'date': datetime.now().isoformat(), 'comments': comments})
        row = conn.execute(text('SELECT DocumentID FROM t_WorkflowInstances WHERE InstanceID = :id'), {'id': instance_id}).fetchone()
        if row:
            doc_id = row[0]
            conn.execute(text('''
                UPDATE t_EngineeringDocument SET Status = 'Approved'
                WHERE DocumentID = :did
            '''), {'did': doc_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'Document approved'})


# ==================== EXTERNAL DOCUMENT SHARING ENDPOINTS ====================

@api_bp.route('/external/documents')
def external_documents():
    user_id = request.args.get('user_id')
    engine = get_engine()
    # FIX: Use today_key parameter for expiry comparison (SQL Server compatible).
    # If ExternalExpiryDate is a DateKey (integer) column, use :today_key.
    # If it's a DATE column, use CAST(GETDATE() AS DATE).
    today_key = get_today_key()
    if not user_id:
        query = '''
            SELECT DocumentID, DocumentNumber, Title, Revision, ExternalExpiryDate
            FROM t_EngineeringDocument
            WHERE IsExternallyShared = 1
              AND (ExternalExpiryDate IS NULL OR ExternalExpiryDate >= :today)
        '''
        params = {'today': today_key}
    else:
        with engine.connect() as conn:
            user_row = conn.execute(text('''
                SELECT CompanyID, DefaultProjectID FROM t_Users WHERE UserID = :uid
            '''), {'uid': user_id}).fetchone()
            if not user_row:
                return jsonify({'error': 'User not found'}), 404
            company_id = user_row[0]
            project_id = user_row[1]
            query = '''
                SELECT DocumentID, DocumentNumber, Title, Revision, ExternalExpiryDate
                FROM t_EngineeringDocument
                WHERE IsExternallyShared = 1
                  AND (ExternalExpiryDate IS NULL OR ExternalExpiryDate >= :today)
                  AND (ProjectID = :proj OR ProjectID IN (
                      SELECT ProjectID FROM t_Project WHERE CompanyID = :comp
                  ))
            '''
            params = {'today': today_key, 'proj': project_id, 'comp': company_id}
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        docs = []
        for r in rows:
            docs.append({
                'docId': r[0],
                'docNumber': r[1],
                'title': r[2],
                'revision': r[3],
                'expiryDate': r[4]
            })
    return jsonify(docs)


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/external/documents/<string:doc_id>/download')
def external_download(doc_id):
    engine = get_engine()
    # FIX: Use today_key parameter for expiry comparison (SQL Server compatible).
    today_key = get_today_key()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT FilePath FROM t_EngineeringDocument
            WHERE DocumentID = :id AND IsExternallyShared = 1
              AND (ExternalExpiryDate IS NULL OR ExternalExpiryDate >= :today)
        '''), {'id': doc_id, 'today': today_key}).fetchone()
        if not row:
            return jsonify({'error': 'Document not available'}), 404
        file_path = row[0]
        if not file_path:
            return jsonify({'error': 'File not found'}), 404
        full_path = os.path.join(current_app.root_path, file_path)
        if not os.path.exists(full_path):
            return jsonify({'error': 'File missing'}), 404
        return send_file(full_path, as_attachment=True)


@api_bp.route('/external/request-access', methods=['POST'])
def request_external_access():
    data = request.json
    email = data.get('email')
    doc_id = data.get('docId')
    print(f"Access request for document {doc_id} from {email}")
    engine = get_engine()
    with engine.connect() as conn:
        user = conn.execute(text('SELECT UserID FROM t_Users WHERE Email = :email'), {'email': email}).fetchone()
        if user:
            pass
    return jsonify({'success': True, 'message': 'Request sent. You will be notified when access is granted.'})


# ==================== DOCUMENT EXCHANGE & APPROVAL ====================

@api_bp.route('/documents/approval/tasks')
def approval_tasks():
    user_id = request.args.get('user_id', type=int) or 1
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    today_key = get_today_key()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT w.WorkflowID, w.EntityID as DocumentID, d.DocumentNumber, d.Title, d.Revision,
                   w.CurrentStep, w.Status, w.DueDateKey, w.AssignedTo,
                   wf.Steps
            FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :uid
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND d.DemoIndustryCode = :industry
            ORDER BY w.DueDateKey ASC
        '''), {'uid': user_id, 'industry': industry}).fetchall()
        tasks = []
        for r in rows:
            steps = json.loads(r[8]) if r[8] else []
            step_name = ''
            for s in steps:
                if s.get('step') == r[4]:
                    step_name = s.get('name', '')
                    break
            tasks.append({
                'workflowId': r[0],
                'docId': r[1],
                'docNumber': r[2],
                'title': r[3],
                'revision': r[4],
                'currentStep': r[5],
                'stepName': step_name,
                'status': r[6],
                'dueDateKey': r[7],
                'assignedTo': r[8],
                'overdue': r[7] is not None and r[7] < today_key
            })
    return jsonify(tasks)


@api_bp.route('/documents/approval/stats')
def approval_stats():
    user_id = request.args.get('user_id', type=int) or 1
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    today_key = get_today_key()
    with engine.connect() as conn:
        pending = conn.execute(text('''
            SELECT COUNT(*) FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :uid
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND d.DemoIndustryCode = :industry
        '''), {'uid': user_id, 'industry': industry}).fetchone()[0]
        overdue = conn.execute(text('''
            SELECT COUNT(*) FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :uid
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND w.DueDateKey IS NOT NULL AND w.DueDateKey < :today
              AND d.DemoIndustryCode = :industry
        '''), {'uid': user_id, 'today': today_key, 'industry': industry}).fetchone()[0]
    return jsonify({'pendingMyApproval': pending, 'overdueTasks': overdue})


@api_bp.route('/documents/approval/list')
def approval_document_list():
    industry, project_id = get_industry_and_project(request)
    doc_type = request.args.get('doc_type')
    status = request.args.get('status')
    assigned_to = request.args.get('assigned_to', type=int)

    query = '''
        SELECT d.DocumentID, d.DocumentNumber, d.Title, d.Revision, d.DocumentType,
               d.Status as DocStatus, w.Status as WorkflowStatus, w.CurrentStep,
               d.PreparedBy, d.CheckedBy, d.ApprovedBy, w.WorkflowID,
               wf.Steps
        FROM t_EngineeringDocument d
        LEFT JOIN t_Workflow w ON d.DocumentID = w.EntityID AND w.EntityType = 'Document'
        LEFT JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
        WHERE d.DemoIndustryCode = :industry
    '''
    params = {'industry': industry}
    if project_id and project_id != 'All' and project_id != '':
        query += ' AND d.ProjectID = :proj'
        params['proj'] = project_id
    if doc_type:
        query += ' AND d.DocumentType = :type'
        params['type'] = doc_type
    if status:
        query += ' AND (d.Status = :stat OR w.Status = :stat)'
        params['stat'] = status
    if assigned_to:
        query += ' AND (d.PreparedBy = :ass OR d.CheckedBy = :ass OR d.ApprovedBy = :ass OR w.AssignedTo = :ass)'
        params['ass'] = assigned_to

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        docs = []
        for r in rows:
            steps_json = r[11] if len(r) > 11 else '[]'
            steps = json.loads(steps_json) if steps_json else []
            step_name = ''
            if r[7] is not None:
                for s in steps:
                    if s.get('step') == r[7]:
                        step_name = s.get('name', '')
                        break
            docs.append({
                'docId': r[0],
                'docNumber': r[1],
                'title': r[2],
                'revision': r[3],
                'docType': r[4],
                'docStatus': r[5],
                'workflowStatus': r[6],
                'currentStep': r[7],
                'stepName': step_name,
                'preparedBy': r[8],
                'checkedBy': r[9],
                'approvedBy': r[10],
                'workflowId': r[11] if len(r) > 11 else None
            })
    return jsonify(docs)


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/documents/approval/start-workflow/<string:doc_id>', methods=['POST'])
def start_document_workflow(doc_id):
    engine = get_engine()
    with engine.connect() as conn:
        # FIX: Replace "LIMIT 1" with "SELECT TOP 1" for SQL Server
        def_row = conn.execute(text('''
            SELECT TOP 1 WorkflowID, Steps FROM t_WorkflowDefinitions
            WHERE EntityType = 'Document' AND IsActive = 1
        ''')).fetchone()
        if not def_row:
            return jsonify({'error': 'No active workflow definition for Document'}), 400
        template_id = def_row[0]
        steps_json = def_row[1]
        steps = json.loads(steps_json)
        first_step = steps[0]['step'] if steps else 1
        current_user = request.json.get('user_id', 1)
        today_key = get_today_key()
        conn.execute(text('''
            INSERT INTO t_Workflow (
                WorkflowTemplateID, EntityType, EntityID, CurrentStep, Status,
                AssignedTo, AssignedToType, CreatedBy, CreatedDateKey, LastUpdatedDateKey, IsActive
            ) VALUES (
                :tid, 'Document', :eid, :step, 'InProgress',
                :assigned, 'User', :creator, :datekey, :datekey, 1
            )
        '''), {
            'tid': template_id,
            'eid': doc_id,
            'step': first_step,
            'assigned': current_user,
            'creator': current_user,
            'datekey': today_key
        })
        workflow_id = conn.execute(text("SELECT last_insert_rowid()")).fetchone()[0]
        conn.execute(text('UPDATE t_EngineeringDocument SET WorkflowID = :wid WHERE DocumentID = :did'),
                     {'wid': workflow_id, 'did': doc_id})
        conn.commit()
    return jsonify({'success': True, 'workflowId': workflow_id})


@api_bp.route('/documents/approval/action/<int:workflow_id>', methods=['POST'])
def approval_action(workflow_id):
    data = request.json
    action = data.get('action')
    comments = data.get('comments', '')
    user_id = data.get('user_id', 1)

    engine = get_engine()
    with engine.connect() as conn:
        wf = conn.execute(text('''
            SELECT w.WorkflowTemplateID, w.EntityID, w.CurrentStep, w.Status,
                   wf.Steps
            FROM t_Workflow w
            JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
            WHERE w.WorkflowID = :wid
        '''), {'wid': workflow_id}).fetchone()
        if not wf:
            return jsonify({'error': 'Workflow not found'}), 404
        doc_id = wf[1]
        current_step = wf[2]
        steps_json = wf[4]
        steps = json.loads(steps_json)
        step_index = None
        for i, s in enumerate(steps):
            if s['step'] == current_step:
                step_index = i
                break
        if action == 'reject':
            if step_index == 0:
                new_status = 'Rejected'
                new_step = current_step
                new_assigned = None
            else:
                new_step = steps[step_index - 1]['step']
                new_status = 'InProgress'
                new_assigned = None
            conn.execute(text('''
                UPDATE t_Workflow SET Status = :stat, CurrentStep = :step, AssignedTo = :ass, LastUpdatedDateKey = :datekey
                WHERE WorkflowID = :wid
            '''), {'stat': new_status, 'step': new_step, 'ass': new_assigned, 'datekey': get_today_key(), 'wid': workflow_id})
            conn.execute(text('UPDATE t_EngineeringDocument SET Notes = COALESCE(Notes, '') || :note WHERE DocumentID = :did'),
                         {'note': f'\nRejected at step {current_step} by user {user_id}: {comments}', 'did': doc_id})
            conn.commit()
            return jsonify({'success': True, 'message': f'Document rejected at step {current_step}'})
        else:
            if step_index + 1 >= len(steps):
                new_status = 'Completed'
                new_step = current_step
                new_assigned = None
                conn.execute(text('UPDATE t_EngineeringDocument SET Status = "Approved" WHERE DocumentID = :did'), {'did': doc_id})
            else:
                new_step = steps[step_index + 1]['step']
                new_status = 'InProgress'
                next_role = steps[step_index + 1].get('role')
                new_assigned = None
            conn.execute(text('''
                UPDATE t_Workflow SET Status = :stat, CurrentStep = :step, AssignedTo = :ass, LastUpdatedDateKey = :datekey
                WHERE WorkflowID = :wid
            '''), {'stat': new_status, 'step': new_step, 'ass': new_assigned, 'datekey': get_today_key(), 'wid': workflow_id})
            if current_step == 2:
                conn.execute(text('UPDATE t_EngineeringDocument SET CheckedBy = :uid WHERE DocumentID = :did'), {'uid': user_id, 'did': doc_id})
            elif current_step == 3:
                conn.execute(text('UPDATE t_EngineeringDocument SET ApprovedBy = :uid WHERE DocumentID = :did'), {'uid': user_id, 'did': doc_id})
            conn.commit()
            return jsonify({'success': True, 'message': f'Step {current_step} approved'})


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/documents/exchange/thread/<string:doc_id>')
def get_exchange_thread(doc_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT ThreadID, Subject, CreatedAt, CustomerProjectID, InternalProjectID
            FROM t_DocumentExchangeThread
            WHERE InternalProjectID = (SELECT ProjectID FROM t_EngineeringDocument WHERE DocumentID = :did)
               OR CustomerProjectID = (SELECT ProjectID FROM t_EngineeringDocument WHERE DocumentID = :did)
        '''), {'did': doc_id}).fetchall()
        threads = [{'threadId': r[0], 'subject': r[1], 'createdAt': r[2], 'customerProject': r[3], 'internalProject': r[4]} for r in rows]
    return jsonify(threads)


@api_bp.route('/documents/exchange/thread', methods=['POST'])
def create_exchange_thread():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            INSERT INTO t_DocumentExchangeThread (CustomerProjectID, InternalProjectID, Subject)
            VALUES (:cust, :int, :subj)
        '''), {'cust': data.get('customerProjectId'), 'int': data.get('internalProjectId'), 'subj': data.get('subject')})
        conn.commit()
    return jsonify({'success': True})


# ==================== ENGINEERING HANDOVER (EXISTING - UPDATED) ====================

@api_bp.route('/handover/items')
def handover_items():
    industry, project_id = get_industry_and_project(request)
    status = request.args.get('status')

    query = '''
        SELECT p.ProductId, p.descEnglish, p.UOM, p.QuickNotes,
               p.IsEngineeringItem,
               CASE WHEN EXISTS (SELECT 1 FROM t_BOM WHERE ParentProductID = p.ProductId AND ComponentProductID != ParentProductID) THEN 1 ELSE 0 END as HasBOM
        FROM t_Product p
        WHERE p.IsEngineeringItem = 1
          AND p.DemoIndustryCode = :industry
    '''
    params = {'industry': industry}
    if project_id and project_id != 'All' and project_id != '':
        query += ' AND EXISTS (SELECT 1 FROM t_ProjectProducts WHERE ProductID = p.ProductId AND ProjectID = :proj)'
        params['proj'] = project_id
    if status == 'pending':
        query += ' AND p.IsEngineeringItem = 1'
    elif status == 'product_created':
        query += ' AND p.IsEngineeringItem = 0'
    elif status == 'bom_created':
        query += ' AND HasBOM = 1'

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        items = []
        for r in rows:
            quick_notes = r[3] or '{}'
            eng_data = json.loads(quick_notes)
            items.append({
                'productId': r[0],
                'description': r[1],
                'uom': r[2],
                'tagRange': eng_data.get('tagRange', ''),
                'pid': eng_data.get('pid', ''),
                'lineNumber': eng_data.get('lineNumber', ''),
                'hasBOM': bool(r[5])
            })
    return jsonify(items)


@api_bp.route('/handover/stats')
def handover_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        finalized = conn.execute(text('''
            SELECT COUNT(*) FROM t_Product 
            WHERE IsEngineeringItem = 1 AND DemoIndustryCode = :industry
        '''), {'industry': industry}).fetchone()[0]
        products_created = conn.execute(text('''
            SELECT COUNT(*) FROM t_Product 
            WHERE IsEngineeringItem = 0 
              AND DemoIndustryCode = :industry
        ''')).fetchone()[0]
        bom_pending = conn.execute(text('''
            SELECT COUNT(*) FROM t_Product 
            WHERE IsEngineeringItem = 1 
              AND DemoIndustryCode = :industry
              AND NOT EXISTS (SELECT 1 FROM t_BOM WHERE ParentProductID = t_Product.ProductId AND ComponentProductID != ParentProductID)
        '''), {'industry': industry}).fetchone()[0]
    return jsonify({
        'finalizedItems': finalized,
        'productsCreated': products_created,
        'bomsPending': bom_pending
    })


@api_bp.route('/handover/create-product/<string:item_id>', methods=['POST'])
def handover_create_product(item_id):
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            UPDATE t_Product SET
                IsEngineeringItem = 0,
                ProcurementType = :proc,
                StandardCost = :cost,
                LeadTime = :lead,
                UOM = :uom
            WHERE ProductId = :pid
        '''), {
            'pid': item_id,
            'proc': data.get('ProcurementType', 'Manufactured'),
            'cost': data.get('StandardCost', 0),
            'lead': data.get('LeadTime', 0),
            'uom': data.get('UOM', 'EA')
        })
        conn.commit()
    return jsonify({'success': True, 'message': 'Product created'})


@api_bp.route('/handover/link-product/<string:item_id>', methods=['POST'])
def handover_link_product(item_id):
    data = request.json
    target_product_id = data.get('targetProductId')
    if not target_product_id:
        return jsonify({'error': 'Target product ID required'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        current_notes = conn.execute(text('SELECT Notes FROM t_Product WHERE ProductId = :pid'), {'pid': item_id}).fetchone()
        notes = json.loads(current_notes[0]) if current_notes and current_notes[0] else {}
        notes['linkedToProduct'] = target_product_id
        conn.execute(text('UPDATE t_Product SET Notes = :notes WHERE ProductId = :pid'),
                     {'notes': json.dumps(notes), 'pid': item_id})
        conn.commit()
    return jsonify({'success': True, 'message': f'Linked to product {target_product_id}'})


@api_bp.route('/handover/release/<string:product_id>', methods=['POST'])
def handover_release(product_id):
    engine = get_engine()
    with engine.connect() as conn:
        bom_ok = conn.execute(text('''
            SELECT COUNT(*) FROM t_BOM WHERE ParentProductID = :pid AND ComponentProductID != ParentProductID
        '''), {'pid': product_id}).fetchone()[0]
        if bom_ok == 0:
            return jsonify({'error': 'BOM must have at least one valid component'}), 400
        conn.commit()
    return jsonify({'success': True, 'message': 'Released to manufacturing'})


# FIX: Changed <int:doc_id> to <string:doc_id>
@api_bp.route('/documents/<string:doc_id>/revisions')
def document_revisions(doc_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT Revision, FilePath, ChangeNotes, CreatedDate, CreatedBy
            FROM t_DocumentRevisions
            WHERE DocumentID = :did
            ORDER BY CreatedDate DESC
        '''), {'did': doc_id}).fetchall()
        revisions = [{
            'revision': r[0],
            'filePath': r[1],
            'changeNotes': r[2],
            'createdDate': r[3],
            'createdBy': r[4]
        } for r in rows]
    return jsonify(revisions)
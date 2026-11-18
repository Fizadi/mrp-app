# app/modules/engineering/api.py
from flask import Blueprint, request, jsonify, Response, current_app, send_file, session
from sqlalchemy.sql import text
import csv
import io
import os
import json
import logging
import uuid
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from app.core.context_processors import INDUSTRY_DATA
from app.modules.auth import verify_password  # noqa: F401 — Playbook G5 (uniform import)

logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

api_bp = Blueprint('bom_api', __name__, url_prefix='/api/bom')


def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def _new_id():
    return str(uuid.uuid4()).upper()


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
        WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
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
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    stats = {}
    with engine.connect() as conn:
        # D1 fix: query t_ECO (t_ChangeOrder does not exist); no try/except swallow
        total = conn.execute(
            text("SELECT COUNT(*) FROM t_BOM WHERE ObsoleteDateKey IS NULL "
                 "AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).fetchone()[0]
        stats['totalComponents'] = total

        where_used = conn.execute(
            text("SELECT COUNT(DISTINCT ParentProductID) FROM t_BOM "
                 "WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).fetchone()[0]
        stats['whereUsedRefs'] = where_used

        pending_ecos = conn.execute(
            text("SELECT COUNT(*) FROM t_ECO WHERE Status IN ('Draft', 'Review') "
                 "AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).fetchone()[0]
        stats['pendingECOs'] = pending_ecos

        # D3 fix: use CheckedOutBy (CheckoutStatus does not exist)
        locked_docs = conn.execute(
            text("SELECT COUNT(*) FROM t_EngineeringDocument "
                 "WHERE CheckedOutBy IS NOT NULL "
                 "AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).fetchone()[0]
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
        query += " AND IsActive = '1'"
    query += " ORDER BY CategoryCode"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        categories = [{
            'categoryId': r[0],
            'categoryCode': r[1],
            'categoryName': r[2],
            'description': r[3],
            'hasParameters': str(r[4]) == '1' if r[4] is not None else False,
            'parameterTableSuffix': r[5],
            'isActive': str(r[6]) == '1' if r[6] is not None else False,
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
        active = conn.execute(text("SELECT COUNT(*) FROM t_ProductCategory WHERE DemoIndustryCode = :industry AND IsActive = '1'"), {'industry': industry}).fetchone()[0]
        with_params = conn.execute(text("SELECT COUNT(*) FROM t_ProductCategory WHERE DemoIndustryCode = :industry AND HasEngineeringParameters = '1'"), {'industry': industry}).fetchone()[0]
    return jsonify({'totalCategories': total, 'activeCategories': active, 'withParameters': with_params})


@api_bp.route('/categories', methods=['POST'])
def create_category():
    data = request.json
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            new_id = _new_id()
            today_key = str(get_today_key())
            conn.execute(text("""
                INSERT INTO t_ProductCategory (
                    CategoryID, CategoryCode, CategoryName, Description, HasEngineeringParameters,
                    ParameterTableSuffix, IsActive, DemoIndustryCode,
                    CategoryNameLocal, DescriptionLocal, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :cid, :code, :name, :desc, :has_params,
                    :suffix, :active, :industry,
                    :name_local, :desc_local, :datekey, :datekey
                )
            """), {
                'cid': new_id,
                'code': data['categoryCode'],
                'name': data['categoryName'],
                'desc': data.get('description', ''),
                'has_params': '1' if data.get('hasEngineeringParameters') else '0',
                'suffix': data.get('parameterTableSuffix', ''),
                'active': '1' if data.get('isActive') else '0',
                'industry': industry,
                'name_local': data.get('categoryNameLocal', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'datekey': today_key
            })
            conn.commit()
        return jsonify({'success': True, 'categoryId': new_id})
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
                'has_params': '1' if data.get('hasEngineeringParameters') else '0',
                'suffix': data.get('parameterTableSuffix', ''),
                'active': '1' if data.get('isActive') else '0',
                'name_local': data.get('categoryNameLocal', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'datekey': str(get_today_key())
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
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    today_key = str(get_today_key())
    with engine.connect() as conn:
        for row in reader:
            code = row.get('CategoryCode', '')
            if not code:
                continue
            exists = conn.execute(
                text("SELECT CategoryID FROM t_ProductCategory WHERE CategoryCode = :code"),
                {'code': code}
            ).fetchone()
            if exists:
                continue
            conn.execute(text("""
                INSERT INTO t_ProductCategory (
                    CategoryID, CategoryCode, CategoryName, Description, HasEngineeringParameters,
                    ParameterTableSuffix, IsActive, DemoIndustryCode,
                    CategoryNameLocal, DescriptionLocal, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :cid, :code, :name, :desc, :has_params,
                    :suffix, :active, :industry,
                    :name_local, :desc_local, :datekey, :datekey
                )
            """), {
                'cid': _new_id(),
                'code': code,
                'name': row.get('CategoryName', ''),
                'desc': row.get('Description', ''),
                'has_params': '1' if str(row.get('HasEngineeringParameters', '0')).lower() in ('1', 'true', 'yes') else '0',
                'suffix': row.get('ParameterTableSuffix', ''),
                'active': '1' if str(row.get('IsActive', '1')).lower() in ('1', 'true', 'yes') else '0',
                'industry': industry,
                'name_local': row.get('CategoryNameLocal', ''),
                'desc_local': row.get('DescriptionLocal', ''),
                'datekey': today_key
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
    lang = session.get('lang', 'en') # <--- ADD THIS
    
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
        query += " AND IsActive = '1'"
    elif status == 'Inactive':
        query += " AND IsActive = '0'"
    if required == 'true':
        query += " AND IsClientInput = '1'"
    elif required == 'false':
        query += " AND IsClientInput = '0'"
    
    query += " ORDER BY CategoryCode, DisplayOrder"
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        parameters = []
        for r in rows:
            # ✅ THE FIX: Swap English with Farsi if in FA mode
            display_name = r[3]
            if lang == 'fa' and r[12] and str(r[12]).strip():
                display_name = r[12]

            parameters.append({
                'definitionId': r[0],
                'categoryCode': r[1],
                'parameterCode': r[2],
                'parameterName': display_name, 
                'parameterType': r[4],
                'unit': r[5],
                'isClientInput': str(r[6]) == '1' if r[6] is not None else False,
                'isCalculated': str(r[7]) == '1' if r[7] is not None else False,
                'defaultValue': r[8],
                'validationRules': r[9],
                'displayOrder': r[10],
                'isActive': str(r[11]) == '1' if r[11] is not None else False,
                'parameterNameLocal': r[12]
            })
    return jsonify(parameters)


@api_bp.route('/parameters/stats')
def parameter_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_EngineeringParameterDefinition WHERE DemoIndustryCode = :industry"), {'industry': industry}).fetchone()[0]
        active = conn.execute(text("SELECT COUNT(*) FROM t_EngineeringParameterDefinition WHERE DemoIndustryCode = :industry AND IsActive = '1'"), {'industry': industry}).fetchone()[0]
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
            new_id = _new_id()
            today_key = str(get_today_key())
            conn.execute(text("""
                INSERT INTO t_EngineeringParameterDefinition (
                    DefinitionID, CategoryCode, ParameterCode, ParameterName, ParameterType,
                    Unit, IsClientInput, IsCalculated, DefaultValue,
                    ValidationRules, DisplayOrder, IsActive,
                    ParameterNameLocal, DemoIndustryCode, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :did, :category, :code, :name, :type,
                    :unit, :client, :calc, :default_val,
                    :validation, :order, :active,
                    :name_local, :industry, :datekey, :datekey
                )
            """), {
                'did': new_id,
                'category': data['categoryCode'],
                'code': data['parameterCode'],
                'name': data['parameterName'],
                'type': data['parameterType'],
                'unit': data.get('unit', ''),
                'client': '1' if data.get('isClientInput') else '0',
                'calc': '1' if data.get('isCalculated') else '0',
                'default_val': data.get('defaultValue', ''),
                'validation': data.get('validationRules', ''),
                'order': str(data.get('displayOrder', 0)),
                'active': '1' if data.get('isActive') else '0',
                'name_local': data.get('parameterNameLocal', ''),
                'industry': industry,
                'datekey': today_key
            })
            conn.commit()
        return jsonify({'success': True, 'definitionId': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/parameters/<string:def_id>', methods=['PUT'])
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
                'client': '1' if data.get('isClientInput') else '0',
                'calc': '1' if data.get('isCalculated') else '0',
                'default_val': data.get('defaultValue', ''),
                'validation': data.get('validationRules', ''),
                'order': str(data.get('displayOrder', 0)),
                'active': '1' if data.get('isActive') else '0',
                'name_local': data.get('parameterNameLocal', ''),
                'datekey': str(get_today_key())
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/parameters/<string:def_id>', methods=['DELETE'])
def delete_parameter(def_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            used = conn.execute(
                text("SELECT COUNT(*) FROM t_EngineeringParameterValue WHERE ParameterCode = "
                     "(SELECT ParameterCode FROM t_EngineeringParameterDefinition WHERE DefinitionID = :id)"),
                {'id': def_id}
            ).fetchone()[0]
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
            'isActive': str(r[5]) == '1' if r[5] is not None else False
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
               (CAST(r.SetupTimeHours AS DECIMAL(18,4))
                + CAST(r.RunTimeHoursPerUnit AS DECIMAL(18,4))
                + CAST(r.QueueTimeHours AS DECIMAL(18,4))
                + CAST(r.MoveTimeHours AS DECIMAL(18,4))) as TotalTime
        FROM t_WorkCenterRouting r
        JOIN t_Product p ON r.ProductID = p.ProductId
        LEFT JOIN t_WorkCenter wc ON r.WorkCenterID = wc.WorkCenterID
        LEFT JOIN t_WorkInstructions i ON r.rowid = i.RoutingID AND i.IsActive = '1'
        WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
    """
    params = {'industry': industry}

    if product_id:
        query += " AND r.ProductID = :product"
        params['product'] = product_id
    if status == 'Active':
        query += " AND r.IsActive = '1'"
    elif status == 'Inactive':
        query += " AND r.IsActive = '0'"

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
            'isExternal': str(r[12]) == '1' if r[12] is not None else False,
            'sourcingType': r[13],
            'isActive': str(r[14]) == '1' if r[14] is not None else False,
            'instructionCount': r[15] or 0,
            'totalTime': float(r[16]) if r[16] else 0
        } for r in rows]
    return jsonify(routings)


@api_bp.route('/routings/<string:routing_id>')
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
            'isActive': str(row[11]) == '1' if row[11] is not None else False
        })


@api_bp.route('/routings/stats')
def routing_stats():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("""
            SELECT COUNT(*) FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
        """), {'industry': industry}).fetchone()[0]
        active = conn.execute(text("""
            SELECT COUNT(*) FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL) AND r.IsActive = '1'
        """), {'industry': industry}).fetchone()[0]
        inactive = conn.execute(text("""
            SELECT COUNT(*) FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL) AND r.IsActive = '0'
        """), {'industry': industry}).fetchone()[0]
    return jsonify({
        'totalRoutings': total,
        'activeRoutings': active,
        'inactiveRoutings': inactive
    })


@api_bp.route('/routings', methods=['POST'])
def create_routing():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            new_id = _new_id()
            conn.execute(text("""
                INSERT INTO t_WorkCenterRouting (
                    rowid, ProductID, WorkCenterID, OperationSequence,
                    SetupTimeHours, RunTimeHoursPerUnit, QueueTimeHours, MoveTimeHours,
                    SourcingType, IsActive, EffectiveDateKey
                ) VALUES (
                    :rid, :product, :workcenter, :seq,
                    :setup, :run, :queue, :move,
                    :sourcing, :active, :datekey
                )
            """), {
                'rid': new_id,
                'product': data['productId'],
                'workcenter': data['workCenterId'],
                'seq': int(data.get('operationSequence', 10)),
                'setup': float(data.get('setupTimeHours', 0)),
                'run': float(data.get('runTimeHoursPerUnit', 0)),
                'queue': float(data.get('queueTimeHours', 0)),
                'move': float(data.get('moveTimeHours', 0)),
                'sourcing': data.get('sourcingType', 'Make'),
                'active': '1' if data.get('isActive') else '0',
                'datekey': get_today_key()
            })
            conn.commit()
        return jsonify({'success': True, 'routingId': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/<string:routing_id>', methods=['PUT'])
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
                'seq': int(data.get('operationSequence', 10)),
                'setup': float(data.get('setupTimeHours', 0)),
                'run': float(data.get('runTimeHoursPerUnit', 0)),
                'queue': float(data.get('queueTimeHours', 0)),
                'move': float(data.get('moveTimeHours', 0)),
                'sourcing': data.get('sourcingType', 'Make'),
                'active': '1' if data.get('isActive') else '0'
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/<string:routing_id>', methods=['DELETE'])
def delete_routing(routing_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM t_WorkCenterRouting WHERE rowid = :id"), {'id': routing_id})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/<string:routing_id>/instructions')
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
            WHERE RoutingID = :id AND IsActive = '1'
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
            'isActive': str(r[15]) == '1' if r[15] is not None else False
        } for r in rows]
    return jsonify(instructions)


@api_bp.route('/routings/<string:routing_id>/instructions', methods=['POST'])
def add_routing_instruction(routing_id):
    data = request.json
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    try:
        with engine.connect() as conn:
            new_id = _new_id()
            today_key = str(get_today_key())
            conn.execute(text("""
                INSERT INTO t_WorkInstructions (
                    InstructionID, RoutingID, StepNumber, Description, DescriptionLocal,
                    SetupInstructions, SetupInstructionsLocal,
                    OperatingInstructions, OperatingInstructionsLocal,
                    QualityCheckpoints, QualityCheckpointsLocal,
                    SafetyNotes, SafetyNotesLocal,
                    ImagePath, VideoPath, DisplayOrder, IsActive,
                    DemoIndustryCode, CreatedDateKey, ModifiedDateKey
                ) VALUES (
                    :iid, :routing, :step, :desc, :desc_local,
                    :setup, :setup_local,
                    :operating, :operating_local,
                    :quality, :quality_local,
                    :safety, :safety_local,
                    :image, :video, :order, '1',
                    :industry, :datekey, :datekey
                )
            """), {
                'iid': new_id,
                'routing': routing_id,
                'step': str(data.get('stepNumber', 1)),
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
                'order': str(data.get('displayOrder', 0)),
                'industry': industry,
                'datekey': today_key
            })
            conn.commit()
        return jsonify({'success': True, 'instructionId': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/instructions/<string:instruction_id>', methods=['PUT'])
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
                'step': str(data.get('stepNumber', 1)),
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
                'order': str(data.get('displayOrder', 0)),
                'datekey': str(get_today_key())
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/routings/instructions/<string:instruction_id>', methods=['DELETE'])
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
                   r.SourcingType, r.IsActive
            FROM t_WorkCenterRouting r
            JOIN t_Product p ON r.ProductID = p.ProductId
            WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
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
    today_key = str(get_today_key())
    try:
        with engine.connect() as conn:
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

def generate_eco_number():
    year = datetime.now().year
    engine = get_engine()
    with engine.connect() as conn:
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
        WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
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


@api_bp.route('/eco/<string:eco_id>')
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
        WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
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
    today_key = str(get_today_key())

    eco_number = generate_eco_number()

    engine = get_engine()
    try:
        with engine.connect() as conn:
            new_id = _new_id()
            conn.execute(text("""
                INSERT INTO t_ECO (
                    ECOID, ECONumber, Title, TitleLocal, Description, DescriptionLocal,
                    AffectedProductID, Reason, Priority, Status,
                    RequestedBy, RequestedDateKey, DemoIndustryCode, ProjectID
                ) VALUES (
                    :eid, :number, :title, :title_local, :desc, :desc_local,
                    :product, :reason, :priority, 'Draft',
                    :requested_by, :datekey, :industry, :project
                )
            """), {
                'eid': new_id,
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
        return jsonify({'success': True, 'ecoNumber': eco_number, 'ecoId': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/eco/<string:eco_id>', methods=['PUT'])
def update_eco(eco_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
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


@api_bp.route('/eco/<string:eco_id>/submit', methods=['POST'])
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


@api_bp.route('/eco/<string:eco_id>/approve', methods=['POST'])
def approve_eco(eco_id):
    user_id = session.get('user_id', 1)
    today_key = str(get_today_key())
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


@api_bp.route('/eco/<string:eco_id>/reject', methods=['POST'])
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


@api_bp.route('/eco/<string:eco_id>/implement', methods=['POST'])
def implement_eco(eco_id):
    user_id = session.get('user_id', 1)
    today_key = str(get_today_key())
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


@api_bp.route('/eco/<string:eco_id>/cancel', methods=['POST'])
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


@api_bp.route('/eco/<string:eco_id>/delete', methods=['DELETE'])
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


# ==================== BOM ENDPOINTS ====================

@api_bp.route('/tree/<string:product_id>')
def bom_tree(product_id):
    industry, project_id = get_industry_and_project(request)
    effectivity_date = request.args.get('effectivity_date')
    engine = get_engine()
    today_key = str(int(datetime.now().strftime('%Y%m%d')))
    lang = session.get('lang', 'en') # <--- Get language HERE

    with engine.connect() as conn:
        # (Project check logic remains exactly the same)
        if project_id and project_id not in ('All', ''):
            project_list = [x.strip() for x in project_id.split(',') if x.strip()]
            placeholders = ','.join([f':p{i}' for i in range(len(project_list))])
            params = {'pid': product_id, 'industry': industry}
            for i, pid in enumerate(project_list):
                params[f'p{i}'] = pid

            root_ok = conn.execute(text(f"""
                SELECT COUNT(*) FROM t_Product p
                WHERE p.ProductId = :pid
                  AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
                  AND p.ProductId IN (
                      SELECT ProductID FROM t_ProjectProducts WHERE ProjectID IN ({placeholders})
                  )
            """), params).fetchone()[0]

            if not root_ok:
                return jsonify([])

        def build_tree(conn, parent_id):
            rows = conn.execute(text("""
                SELECT b.ComponentProductID, p.descEnglish, p.descFarsi, b.Quantity, b.ScrapFactor,
                       b.EffectivityDateKey, b.ObsoleteDateKey, b.BOMId
                FROM t_BOM b
                JOIN t_Product p ON b.ComponentProductID = p.ProductId
                WHERE b.ParentProductID = :parent
                  AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey >= :today)
                  AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
            """), {'parent': parent_id, 'today': today_key, 'industry': industry}).fetchall()

            children = []
            for row in rows:
                if effectivity_date and row[5] and str(row[5]) > effectivity_date:
                    continue
                
                # ✅ THE FIX: Strict check
                display_name = row[1] # English default
                if lang == 'fa' and row[2] and str(row[2]).strip() != '':
                    display_name = row[2]

                children.append({
                    'id': row[7],
                    'component_id': row[0],
                    'name': display_name, # Use the swapped name
                    'quantity': float(row[3]) if row[3] else 0,
                    'scrap_factor': row[4],
                    'effectivity': row[5],
                    'obsolete': row[6],
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
        new_id = _new_id()
        conn.execute(text('''
            INSERT INTO t_BOM (BOMId, ParentProductID, ComponentProductID, Quantity, ScrapFactor, EffectivityDateKey, ObsoleteDateKey)
            VALUES (:bid, :parent, :component, :qty, :scrap, :eff, :obs)
        '''), {
            'bid': new_id,
            'parent': data['parent_id'],
            'component': data['component_id'],
            'qty': data['quantity'],
            'scrap': str(data.get('scrap_factor', 0)),
            'eff': str(data.get('effectivity_date')) if data.get('effectivity_date') else None,
            'obs': str(data.get('obsolete_date')) if data.get('obsolete_date') else None
        })
        conn.commit()
    return jsonify({'success': True, 'bomId': new_id, 'message': 'Component added'})


@api_bp.route('/component/<string:bom_id>', methods=['PUT'])
def update_component(bom_id):
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            UPDATE t_BOM SET Quantity=:qty, ScrapFactor=:scrap, EffectivityDateKey=:eff, ObsoleteDateKey=:obs
            WHERE BOMId = :id
        '''), {
            'qty': data['quantity'],
            'scrap': str(data.get('scrap_factor', 0)),
            'eff': str(data.get('effectivity_date')) if data.get('effectivity_date') else None,
            'obs': str(data.get('obsolete_date')) if data.get('obsolete_date') else None,
            'id': bom_id
        })
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/component/<string:bom_id>', methods=['DELETE'])
def delete_component(bom_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('DELETE FROM t_BOM WHERE BOMId = :id'), {'id': bom_id})
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/where-used/<string:component_id>')
def where_used(component_id):
    industry, _project_id = get_industry_and_project(request)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT p.ProductId, p.descEnglish, b.Quantity
            FROM t_BOM b
            JOIN t_Product p ON b.ParentProductID = p.ProductId
            WHERE b.ComponentProductID = :comp
              AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
        '''), {'comp': component_id, 'industry': industry}).fetchall()
    return jsonify([{'ProductID': r[0], 'ProductName': r[1], 'Quantity': r[2]} for r in rows])


@api_bp.route('/import', methods=['POST'])
def import_bom_csv():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    csv_input = csv.DictReader(stream)
    engine = get_engine()
    with engine.connect() as conn:
        for row in csv_input:
            parent = row.get('ParentProductID')
            component = row.get('ComponentProductID')
            if not parent or not component:
                continue
            exists = conn.execute(
                text("SELECT BOMId FROM t_BOM WHERE ParentProductID = :parent AND ComponentProductID = :comp"),
                {'parent': parent, 'comp': component}
            ).fetchone()
            if exists:
                continue
            conn.execute(text('''
                INSERT INTO t_BOM (BOMId, ParentProductID, ComponentProductID, Quantity, ScrapFactor, EffectivityDateKey, ObsoleteDateKey)
                VALUES (:bid, :parent, :component, :qty, :scrap, :eff, :obs)
            '''), {
                'bid': _new_id(),
                'parent': parent,
                'component': component,
                'qty': row.get('Quantity', 0),
                'scrap': row.get('ScrapFactor', '0') or '0',
                'eff': row.get('EffectivityDateKey') or None,
                'obs': row.get('ObsoleteDateKey') or None
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
              AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
              {project_filter}
        """), params).fetchone()[0]

        where_used = conn.execute(text(f"""
            SELECT COUNT(DISTINCT b.ParentProductID) FROM t_BOM b
            JOIN t_Product p ON b.ComponentProductID = p.ProductId
            WHERE (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
              {project_filter}
        """), params).fetchone()[0]

        # D1 fix — t_ECO instead of t_ChangeOrder
        pending_ecos = conn.execute(
            text("SELECT COUNT(*) FROM t_ECO WHERE Status IN ('Draft', 'Review') "
                 "AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).fetchone()[0]

        # D3 fix — CheckedOutBy
        locked_docs = conn.execute(text("""
            SELECT COUNT(*) FROM t_EngineeringDocument
            WHERE CheckedOutBy IS NOT NULL
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'industry': industry}).fetchone()[0]

    return jsonify({
        'totalComponents': total,
        'whereUsedRefs': where_used,
        'pendingECOs': pending_ecos,
        'lockedDocuments': locked_docs
    })


@api_bp.route('/component/<string:component_id>/summary')
def component_summary(component_id):
    industry, _project_id = get_industry_and_project(request)
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
        row = conn.execute(text('''
            SELECT descEnglish, TRY_CAST(SafetyStock AS DECIMAL(18,4)) FROM t_Product 
            WHERE ProductId = :pid AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()
        if row:
            summary['name'] = row[0] or ''
            summary['inventory']['low_stock_threshold'] = float(row[1]) if row[1] is not None else 0

        inv = conn.execute(text('''
            SELECT SUM(Qty) FROM t_InventoryOnHand 
            WHERE ProductID = :pid AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()
        on_hand = float(inv[0]) if inv and inv[0] is not None else 0
        summary['inventory']['on_hand'] = on_hand
        summary['inventory']['available'] = on_hand

        qual_count = conn.execute(text('''
            SELECT COUNT(*) FROM t_QualityOperationAssignment
            WHERE SourceType = 'Product' AND SourceID = :pid 
              AND IsActive = '1'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['quality']['assigned_operations_count'] = qual_count

        last_batch = conn.execute(text('''
            SELECT TOP 1 OverallResult, InspectionDateKey
            FROM t_QualityInspectionBatch
            WHERE ProductID = :product_id AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND OverallResult IS NOT NULL
            ORDER BY InspectionDateKey DESC
        '''), {'product_id': component_id, 'industry': industry}).fetchone()
        if last_batch:
            summary['quality']['last_inspection_result'] = last_batch[0]
            if last_batch[1]:
                dk = int(last_batch[1])
                summary['quality']['last_inspection_date'] = f"{dk//10000}-{(dk%10000)//100:02d}-{dk%100:02d}"

        # t_PurchaseOrderDetail EXISTS (schema confirms). Keep detail join.
        open_pos = conn.execute(text('''
            SELECT COUNT(DISTINCT po.PurchaseOrderID) FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE pod.ProductID = :pid AND po.Status NOT IN ('Completed', 'Cancelled')
              AND (po.DemoIndustryCode = :industry OR po.DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['purchasing']['open_pos_count'] = open_pos

        pref = conn.execute(text('''
            SELECT TOP 1 s.SupplierName, prod.LeadTime
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
            JOIN t_Product prod ON pod.ProductID = prod.ProductId
            WHERE pod.ProductID = :pid AND (po.DemoIndustryCode = :industry OR po.DemoIndustryCode IS NULL)
            GROUP BY s.SupplierName, prod.LeadTime, po.SupplierID
            ORDER BY COUNT(*) DESC
        '''), {'pid': component_id, 'industry': industry}).fetchone()
        if pref:
            summary['purchasing']['preferred_supplier'] = pref[0] or ''
            summary['purchasing']['standard_lead_time_days'] = pref[1] or 0
        else:
            lt = conn.execute(text('''
                SELECT LeadTime FROM t_Product 
                WHERE ProductId = :pid AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            '''), {'pid': component_id, 'industry': industry}).fetchone()
            if lt:
                summary['purchasing']['standard_lead_time_days'] = lt[0] or 0

        active_orders = conn.execute(text('''
            SELECT COUNT(*) FROM t_ProductionOrder
            WHERE ProductId = :pid AND Status NOT IN ('Completed', 'Closed', 'Canceled')
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['production']['active_orders_using_count'] = active_orders

        today_key_int = get_today_key()
        future_key = int((datetime.now() + timedelta(days=30)).strftime('%Y%m%d'))
        req_30 = conn.execute(text('''
            SELECT SUM(OrderQuantity) FROM t_ProductionOrder
            WHERE ProductId = :pid AND Status NOT IN ('Completed', 'Closed', 'Canceled')
              AND StartDateKey BETWEEN :today AND :future
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry, 'today': today_key_int, 'future': future_key}).fetchone()[0] or 0
        summary['production']['required_next_30_days'] = float(req_30)

        open_ncrs = conn.execute(text('''
            SELECT COUNT(*) FROM t_NonConformance 
            WHERE ProductID = :pid AND Status = 'Open'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        closed_ncrs = conn.execute(text('''
            SELECT COUNT(*) FROM t_NonConformance 
            WHERE ProductID = :pid AND Status = 'Closed'
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry}).fetchone()[0]
        summary['non_conformance']['open_ncrs_count'] = open_ncrs
        summary['non_conformance']['closed_ncrs_count'] = closed_ncrs

        doc_count = conn.execute(text('''
            SELECT COUNT(*) FROM t_EngineeringDocument
            WHERE (ProjectID IN (SELECT ProjectID FROM t_ProjectProducts WHERE ProductID = :pid)
               OR DocumentNumber LIKE :pat)
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'pat': f'%{component_id}%', 'industry': industry}).fetchone()[0]
        summary['engineering']['document_count'] = doc_count

        year_start = int(f"{datetime.now().year}0101")
        sold_ytd = conn.execute(text('''
            SELECT SUM(Qty) FROM t_SalesOrder
            WHERE ProductID = :pid AND TRY_CAST(OrderDateKey AS INT) >= :start
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'pid': component_id, 'industry': industry, 'start': year_start}).fetchone()[0] or 0
        summary['sales']['sold_as_spare_ytd'] = float(sold_ytd)

    return jsonify(summary)


# ==================== PRODUCT ENDPOINTS ====================

@api_bp.route('/products')
def get_products():
    industry, project_id = get_industry_and_project(request)
    procurement = request.args.get('procurement_type')

    engine = get_engine()
    query = """
        SELECT ProductId, descEnglish, UOM, StandardCost, ProcurementType
        FROM t_Product
        WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
    """
    params = {'industry': industry}

    if procurement:
        query += " AND ProcurementType = :proc"
        params['proc'] = procurement
    if project_id and project_id != 'All' and project_id != '':
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
    industry, _project_id = get_industry_and_project(request)
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT ProductId, descEnglish, UOM, StandardCost, ProcurementType,
                   LeadTime, MinOrderQty, SafetyStock, IsManufactured, BatchSize, TechnicalDrawing
            FROM t_Product
            WHERE ProductId = :pid AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
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
            'IsManufactured': str(row[8]) == '1' if row[8] is not None else False,
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
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        """), {'industry': industry}).fetchone()[0]

        manufactured = conn.execute(text("""
            SELECT COUNT(*) FROM t_Product
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND ProcurementType = 'Manufactured'
        """), {'industry': industry}).fetchone()[0]

        purchased = conn.execute(text("""
            SELECT COUNT(*) FROM t_Product
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
              AND ProcurementType = 'Purchased'
        """), {'industry': industry}).fetchone()[0]

    return jsonify({
        'totalProducts': total,
        'manufactured': manufactured,
        'purchased': purchased,
        'activeProducts': total
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
                'cost': str(data.get('StandardCost', 0)),
                'proc': data.get('ProcurementType', 'Manufactured'),
                'lead': str(data.get('LeadTime', 0)),
                'minqty': str(data.get('MinOrderQty', 1)),
                'safety': str(data.get('SafetyStock', 0)),
                'isman': '1' if data.get('ProcurementType') == 'Manufactured' else '0',
                'batch': str(data.get('BatchSize', 1)),
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
                WHERE ProductId = :pid AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            '''), {
                'pid': product_id,
                'name': data.get('descEnglish'),
                'uom': data.get('UOM', 'EA'),
                'cost': str(data.get('StandardCost', 0)),
                'proc': data.get('ProcurementType', 'Manufactured'),
                'lead': str(data.get('LeadTime', 0)),
                'minqty': str(data.get('MinOrderQty', 1)),
                'safety': str(data.get('SafetyStock', 0)),
                'isman': '1' if data.get('ProcurementType') == 'Manufactured' else '0',
                'batch': str(data.get('BatchSize', 1)),
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
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    raw_bytes = file.stream.read()

    csv_text = None
    used_encoding = None
    for enc in ('utf-8-sig', 'utf-8', 'cp1256', 'cp1252', 'latin-1'):
        try:
            csv_text = raw_bytes.decode(enc)
            used_encoding = enc
            break
        except UnicodeDecodeError:
            continue

    if csv_text is None:
        return jsonify({
            'success': False,
            'error': 'Could not decode file. Please save it as UTF-8 CSV.'
        }), 400

    try:
        dialect = csv.Sniffer().sniff(csv_text[:2048], delimiters=',\t;')
        delimiter = dialect.delimiter
    except csv.Error:
        first_line = csv_text.split('\n', 1)[0]
        delimiter = '\t' if '\t' in first_line else (',' if ',' in first_line else ';')

    stream = io.StringIO(csv_text, newline=None)
    reader = csv.DictReader(stream, delimiter=delimiter)

    if reader.fieldnames:
        reader.fieldnames = [h.strip().replace('\ufeff', '') for h in reader.fieldnames]

    industry = session.get('demo_industry', 'valve')
    engine = get_engine()

    inserted = 0
    updated = 0
    skipped = 0
    errors = []

    def to_float(v, default=0.0):
        try:
            if v is None or str(v).strip() == '': return default
            return float(str(v).strip())
        except (ValueError, TypeError):
            return default

    def to_int(v, default=0):
        try:
            if v is None or str(v).strip() == '': return default
            return int(float(str(v).strip()))
        except (ValueError, TypeError):
            return default

    with engine.connect() as conn:
        for line_no, row in enumerate(reader, start=2):
            try:
                product_id = (row.get('ProductId') or '').strip()
                if not product_id:
                    skipped += 1
                    continue

                procurement = (row.get('ProcurementType') or 'Manufactured').strip()
                is_man = '1' if procurement == 'Manufactured' else '0'

                params = {
                    'pid': product_id,
                    'name': (row.get('descEnglish') or '').strip(),
                    'uom': (row.get('UOM') or 'EA').strip(),
                    'cost': str(to_float(row.get('StandardCost'), 0.0)),
                    'proc': procurement,
                    'lead': str(to_int(row.get('LeadTime'), 0)),
                    'minqty': str(to_int(row.get('MinOrderQty'), 1)),
                    'safety': str(to_int(row.get('SafetyStock'), 0)),
                    'isman': is_man,
                    'batch': str(to_int(row.get('BatchSize'), 1)),
                    'drawing': (row.get('TechnicalDrawing') or '').strip(),
                    'industry': industry
                }

                exists = conn.execute(
                    text("SELECT COUNT(*) FROM t_Product WHERE ProductId = :pid"),
                    {'pid': product_id}
                ).fetchone()[0]

                if exists:
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
                            TechnicalDrawing = :drawing,
                            DemoIndustryCode = :industry
                        WHERE ProductId = :pid
                    '''), params)
                    updated += 1
                else:
                    conn.execute(text('''
                        INSERT INTO t_Product (
                            ProductId, descEnglish, UOM, StandardCost, ProcurementType,
                            LeadTime, MinOrderQty, SafetyStock, IsManufactured, BatchSize,
                            TechnicalDrawing, DemoIndustryCode
                        ) VALUES (
                            :pid, :name, :uom, :cost, :proc,
                            :lead, :minqty, :safety, :isman, :batch,
                            :drawing, :industry
                        )
                    '''), params)
                    inserted += 1

            except Exception as e:
                errors.append(f"Line {line_no}: {str(e)}")
                print(f"[IMPORT] Row {line_no} failed: {e}")

        conn.commit()

    return jsonify({
        'success': True,
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
        'encoding': used_encoding,
        'delimiter': delimiter,
        'industry': industry
    })


@api_bp.route('/products/export')
def export_products_csv():
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT ProductId, descEnglish, UOM, StandardCost, ProcurementType 
            FROM t_Product 
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'industry': industry}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ProductId', 'descEnglish', 'UOM', 'StandardCost', 'ProcurementType'])
    writer.writerows(rows)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=products.csv'
    return response


# ==================== ENGINEERING DOCUMENTS ENDPOINTS ====================

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
    project = request.args.get('project')
    checkout = request.args.get('checkout_status')
    engine = get_engine()

    def count_by_status(stat, conn):
        q = "SELECT COUNT(*) FROM t_EngineeringDocument WHERE Status = :stat AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"
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
        draft = count_by_status('Draft', conn)
        under_review = count_by_status('Under Review', conn)
        approved = count_by_status('Approved', conn)
        obsolete = count_by_status('Obsolete', conn)
        # D3 fix — CheckedOutBy
        checked_out = conn.execute(text('''
            SELECT COUNT(*) FROM t_EngineeringDocument
            WHERE CheckedOutBy IS NOT NULL
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'industry': industry}).fetchone()[0]

    return jsonify({'draft': draft, 'underReview': under_review, 'approved': approved,
                    'obsolete': obsolete, 'checkedOut': checked_out})


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
        WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
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
        docs = [{
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
        } for r in rows]
    return jsonify(docs)


@api_bp.route('/documents/<string:doc_id>')
def get_document(doc_id):
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT * FROM t_EngineeringDocument 
            WHERE DocumentID = :id AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
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
    _upload_folder = get_upload_folder()
    file_path = os.path.join('static', 'uploads', unique_name)
    full_path = os.path.join(current_app.root_path, file_path)
    file.save(full_path)

    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    with engine.connect() as conn:
        new_id = _new_id()
        conn.execute(text('''
            INSERT INTO t_EngineeringDocument (
                DocumentID, DocumentNumber, Title, Revision, Status, DocumentType, ProjectID,
                FileName, FilePath, LastModified, CreatedDateKey, DemoIndustryCode
            ) VALUES (
                :did, :num, :title, '1', 'Draft', :type, :proj,
                :fname, :fpath, :modified, :datekey, :industry
            )
        '''), {
            'did': new_id,
            'num': data['docNumber'],
            'title': data['title'],
            'type': data.get('docType', 'Drawing'),
            'proj': data.get('projectId'),
            'fname': filename,
            'fpath': file_path,
            'modified': datetime.now().isoformat(),
            'datekey': str(get_today_key()),
            'industry': industry
        })
        conn.commit()
    return jsonify({'success': True, 'message': 'Document created', 'documentId': new_id})


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
            _upload_folder = get_upload_folder()
            file_path = os.path.join('static', 'uploads', unique_name)
            full_path = os.path.join(current_app.root_path, file_path)
            file.save(full_path)

            curr_rev = conn.execute(text('SELECT Revision FROM t_EngineeringDocument WHERE DocumentID = :id'), {'id': doc_id}).fetchone()[0]
            try:
                new_rev = str(int(curr_rev or 0) + 1)
            except (TypeError, ValueError):
                new_rev = '2'

            conn.execute(text('''
                UPDATE t_EngineeringDocument SET
                    Revision = :new_rev, FileName = :fname, FilePath = :fpath,
                    LastModified = :date, CheckedOutBy = NULL, CheckedOutDate = NULL
                WHERE DocumentID = :id
            '''), {'new_rev': new_rev, 'fname': filename, 'fpath': file_path,
                   'date': datetime.now().isoformat(), 'id': doc_id})

            # t_DocumentRevisions PK is RevisionID (nvarchar)
            conn.execute(text('''
                INSERT INTO t_DocumentRevisions
                (RevisionID, DocumentID, RevisionNumber, ChangeReason, FilePath, CreatedDate)
                VALUES (:rid, :did, :rev, :notes, :path, :date)
            '''), {'rid': _new_id(), 'did': doc_id, 'rev': new_rev,
                   'notes': notes, 'path': file_path,
                   'date': datetime.now().isoformat()})
        else:
            conn.execute(text('''
                UPDATE t_EngineeringDocument SET CheckedOutBy = NULL, CheckedOutDate = NULL
                WHERE DocumentID = :id
            '''), {'id': doc_id})
        conn.commit()
    return jsonify({'success': True, 'message': 'Checked in'})


@api_bp.route('/documents/<string:doc_id>/share', methods=['POST'])
def share_document(doc_id):
    email = request.json.get('email')
    return jsonify({'success': True, 'message': f'External share link sent to {email}'})


@api_bp.route('/documents/<string:doc_id>/subscribe', methods=['POST'])
def subscribe_document(doc_id):
    user_id = request.json.get('userId', 1)
    engine = get_engine()
    with engine.connect() as conn:
        # t_NotificationSettings has no DocumentID — subscribe by Module/EventType
        exists = conn.execute(
            text("SELECT SettingID FROM t_NotificationSettings "
                 "WHERE UserID = :uid AND Module = 'Engineering' AND EventType = :evt"),
            {'uid': user_id, 'evt': f'DocumentChange:{doc_id}'}
        ).fetchone()
        if not exists:
            conn.execute(text('''
                INSERT INTO t_NotificationSettings
                (SettingID, UserID, NotificationType, Module, EventType, IsEnabled, CreatedAt)
                VALUES (:sid, :uid, 'InApp', 'Engineering', :evt, '1', :now)
            '''), {'sid': _new_id(), 'uid': user_id,
                   'evt': f'DocumentChange:{doc_id}',
                   'now': datetime.now().isoformat()})
            conn.commit()
    return jsonify({'success': True, 'message': 'Subscribed to document changes'})


@api_bp.route('/documents/<string:doc_id>/unsubscribe', methods=['POST'])
def unsubscribe_document(doc_id):
    user_id = request.json.get('userId', 1)
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM t_NotificationSettings WHERE UserID = :uid AND Module = 'Engineering' AND EventType = :evt"),
            {'uid': user_id, 'evt': f'DocumentChange:{doc_id}'}
        )
        conn.commit()
    return jsonify({'success': True})


@api_bp.route('/documents/import', methods=['POST'])
def import_documents_csv():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    today_key = str(get_today_key())
    with engine.connect() as conn:
        for row in reader:
            num = row.get('DocumentNumber')
            if not num:
                continue
            exists = conn.execute(
                text("SELECT DocumentID FROM t_EngineeringDocument WHERE DocumentNumber = :num"),
                {'num': num}
            ).fetchone()
            if exists:
                continue
            conn.execute(text('''
                INSERT INTO t_EngineeringDocument (
                    DocumentID, DocumentNumber, Title, Revision, Status, DocumentType, ProjectID,
                    CreatedDateKey, DemoIndustryCode
                ) VALUES (
                    :did, :num, :title, '1', 'Draft', :type, :proj,
                    :datekey, :industry
                )
            '''), {
                'did': _new_id(),
                'num': num,
                'title': row.get('Title', ''),
                'type': row.get('DocumentType', 'Drawing'),
                'proj': row.get('ProjectID') or None,
                'datekey': today_key,
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
            WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'industry': industry}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['DocumentNumber', 'Title', 'Revision', 'Status', 'DocumentType', 'ProjectID'])
    writer.writerows(rows)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=engineering_documents.csv'
    return response


# ==================== DOCUMENT CONTROL (WORKFLOW) ENDPOINTS ====================
# D2 fix — t_WorkflowInstances does not exist. Rewritten on t_Workflow + t_WorkflowDefinitions.

@api_bp.route('/document-control/stats')
def doc_control_stats():
    current_user = request.args.get('user', 'Current User')
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    today_key = str(get_today_key())
    with engine.connect() as conn:
        pending = conn.execute(text('''
            SELECT COUNT(*) FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :user
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
        '''), {'user': current_user, 'industry': industry}).fetchone()[0]

        overdue = conn.execute(text('''
            SELECT COUNT(*) FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            WHERE w.EntityType = 'Document'
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND w.DueDateKey IS NOT NULL AND w.DueDateKey < :today
              AND (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
        '''), {'today': today_key, 'industry': industry}).fetchone()[0]
    return jsonify({'pendingMyApproval': pending, 'overdueReviews': overdue})


@api_bp.route('/document-control/tasks')
def get_document_tasks():
    industry = session.get('demo_industry', 'valve')
    doc_type = request.args.get('doc_type')
    pending_action = request.args.get('pending_action')
    assignee = request.args.get('assignee')

    query = '''
        SELECT w.WorkflowID, w.EntityID, d.DocumentNumber, d.Title, d.Revision,
               w.Status, w.AssignedTo, w.DueDateKey
        FROM t_Workflow w
        JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
        WHERE w.EntityType = 'Document'
          AND (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
    '''
    params = {'industry': industry}
    if doc_type:
        query += ' AND d.DocumentType = :type'
        params['type'] = doc_type
    if pending_action:
        query += ' AND w.Status = :state'
        params['state'] = pending_action
    if assignee:
        query += ' AND w.AssignedTo = :assignee'
        params['assignee'] = assignee
    query += ' ORDER BY w.DueDateKey ASC'

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        tasks = [{
            'instanceId': r[0],
            'docId': r[1],
            'docNumber': r[2],
            'title': r[3],
            'revision': r[4],
            'pendingAction': r[5],
            'assignedTo': r[6],
            'dueDate': r[7]
        } for r in rows]
    return jsonify(tasks)


@api_bp.route('/document-control/approve/<string:instance_id>', methods=['POST'])
def approve_document(instance_id):
    comments = request.json.get('comments', '')
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text('''
            UPDATE t_Workflow
            SET Status = 'Completed', CompletedDateKey = :date, Description = :comments
            WHERE WorkflowID = :id
        '''), {'id': instance_id, 'date': str(get_today_key()), 'comments': comments})
        row = conn.execute(text('SELECT EntityID FROM t_Workflow WHERE WorkflowID = :id'), {'id': instance_id}).fetchone()
        if row:
            conn.execute(text('''
                UPDATE t_EngineeringDocument SET Status = 'Approved'
                WHERE DocumentID = :did
            '''), {'did': row[0]})
        conn.commit()
    return jsonify({'success': True, 'message': 'Document approved'})


# ==================== EXTERNAL DOCUMENT SHARING ====================

@api_bp.route('/external/documents')
def external_documents():
    user_id = request.args.get('user_id')
    engine = get_engine()
    today_key = str(get_today_key())
    if not user_id:
        query = '''
            SELECT DocumentID, DocumentNumber, Title, Revision, ExternalExpiryDate
            FROM t_EngineeringDocument
            WHERE IsExternallyShared = '1'
              AND (ExternalExpiryDate IS NULL OR TRY_CAST(ExternalExpiryDate AS INT) >= :today)
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
                WHERE IsExternallyShared = '1'
                  AND (ExternalExpiryDate IS NULL OR TRY_CAST(ExternalExpiryDate AS INT) >= :today)
                  AND (ProjectID = :proj OR ProjectID IN (
                      SELECT ProjectID FROM t_Project WHERE CompanyID = :comp
                  ))
            '''
            params = {'today': today_key, 'proj': project_id, 'comp': company_id}

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        docs = [{
            'docId': r[0],
            'docNumber': r[1],
            'title': r[2],
            'revision': r[3],
            'expiryDate': r[4]
        } for r in rows]
    return jsonify(docs)


@api_bp.route('/external/documents/<string:doc_id>/download')
def external_download(doc_id):
    engine = get_engine()
    today_key = str(get_today_key())
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT FilePath FROM t_EngineeringDocument
            WHERE DocumentID = :id AND IsExternallyShared = '1'
              AND (ExternalExpiryDate IS NULL OR TRY_CAST(ExternalExpiryDate AS INT) >= :today)
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
    return jsonify({'success': True, 'message': 'Request sent. You will be notified when access is granted.'})


# ==================== DOCUMENT EXCHANGE & APPROVAL ====================

@api_bp.route('/documents/approval/tasks')
def approval_tasks():
    user_id = request.args.get('user_id') or '1'
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    today_key = str(get_today_key())
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT w.WorkflowID, w.EntityID, d.DocumentNumber, d.Title, d.Revision,
                   w.CurrentStep, w.Status, w.DueDateKey, w.AssignedTo,
                   wf.Steps
            FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :uid
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
            ORDER BY w.DueDateKey ASC
        '''), {'uid': user_id, 'industry': industry}).fetchall()
        tasks = []
        for r in rows:
            try:
                steps = json.loads(r[8]) if r[8] else []
            except Exception:
                steps = []
            step_name = ''
            try:
                cs = int(r[5]) if r[5] is not None else None
            except (TypeError, ValueError):
                cs = None
            for s in steps:
                if s.get('step') == cs:
                    step_name = s.get('name', '')
                    break
            tasks.append({
                'workflowId': r[0],
                'docId': r[1],
                'docNumber': r[2],
                'title': r[3],
                'revision': r[4],
                'currentStep': cs,
                'stepName': step_name,
                'status': r[6],
                'dueDateKey': r[7],
                'assignedTo': r[8],
                'overdue': r[7] is not None and str(r[7]) < today_key
            })
    return jsonify(tasks)


@api_bp.route('/documents/approval/stats')
def approval_stats():
    user_id = request.args.get('user_id') or '1'
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    today_key = str(get_today_key())
    with engine.connect() as conn:
        pending = conn.execute(text('''
            SELECT COUNT(*) FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :uid
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
        '''), {'uid': user_id, 'industry': industry}).fetchone()[0]
        overdue = conn.execute(text('''
            SELECT COUNT(*) FROM t_Workflow w
            JOIN t_EngineeringDocument d ON w.EntityID = d.DocumentID
            WHERE w.EntityType = 'Document'
              AND w.AssignedTo = :uid
              AND w.Status NOT IN ('Completed', 'Rejected')
              AND w.DueDateKey IS NOT NULL AND w.DueDateKey < :today
              AND (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
        '''), {'uid': user_id, 'today': today_key, 'industry': industry}).fetchone()[0]
    return jsonify({'pendingMyApproval': pending, 'overdueTasks': overdue})


@api_bp.route('/documents/approval/list')
def approval_document_list():
    industry, project_id = get_industry_and_project(request)
    doc_type = request.args.get('doc_type')
    status = request.args.get('status')
    assigned_to = request.args.get('assigned_to')

    query = '''
        SELECT d.DocumentID, d.DocumentNumber, d.Title, d.Revision, d.DocumentType,
               d.Status as DocStatus, w.Status as WorkflowStatus, w.CurrentStep,
               d.PreparedBy, d.CheckedBy, d.ApprovedBy, w.WorkflowID,
               wf.Steps
        FROM t_EngineeringDocument d
        LEFT JOIN t_Workflow w ON d.DocumentID = w.EntityID AND w.EntityType = 'Document'
        LEFT JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
        WHERE (d.DemoIndustryCode = :industry OR d.DemoIndustryCode IS NULL)
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
            steps_json = r[12] if len(r) > 12 else '[]'
            try:
                steps = json.loads(steps_json) if steps_json else []
            except Exception:
                steps = []
            step_name = ''
            try:
                cs = int(r[7]) if r[7] is not None else None
            except (TypeError, ValueError):
                cs = None
            if cs is not None:
                for s in steps:
                    if s.get('step') == cs:
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
                'currentStep': cs,
                'stepName': step_name,
                'preparedBy': r[8],
                'checkedBy': r[9],
                'approvedBy': r[10],
                'workflowId': r[11] if len(r) > 11 else None
            })
    return jsonify(docs)


@api_bp.route('/documents/approval/start-workflow/<string:doc_id>', methods=['POST'])
def start_document_workflow(doc_id):
    engine = get_engine()
    with engine.connect() as conn:
        def_row = conn.execute(text('''
            SELECT TOP 1 WorkflowID, Steps FROM t_WorkflowDefinitions
            WHERE EntityType = 'Document' AND IsActive = '1'
        ''')).fetchone()
        if not def_row:
            return jsonify({'error': 'No active workflow definition for Document'}), 400
        template_id = def_row[0]
        steps_json = def_row[1]
        try:
            steps = json.loads(steps_json) if steps_json else []
        except Exception:
            steps = []
        first_step = steps[0]['step'] if steps else 1
        current_user = request.json.get('user_id', '1') if request.json else '1'
        today_key = str(get_today_key())
        new_wf_id = _new_id()
        conn.execute(text('''
            INSERT INTO t_Workflow (
                WorkflowID, WorkflowTemplateID, EntityType, EntityID, CurrentStep, Status,
                AssignedTo, AssignedToType, CreatedBy, CreatedDateKey, LastUpdatedDateKey, IsActive
            ) VALUES (
                :wid, :tid, 'Document', :eid, :step, 'InProgress',
                :assigned, 'User', :creator, :datekey, :datekey, '1'
            )
        '''), {
            'wid': new_wf_id,
            'tid': template_id,
            'eid': doc_id,
            'step': str(first_step),
            'assigned': current_user,
            'creator': current_user,
            'datekey': today_key
        })
        conn.execute(text('UPDATE t_EngineeringDocument SET WorkflowID = :wid WHERE DocumentID = :did'),
                     {'wid': new_wf_id, 'did': doc_id})
        conn.commit()
    return jsonify({'success': True, 'workflowId': new_wf_id})


@api_bp.route('/documents/approval/action/<string:workflow_id>', methods=['POST'])
def approval_action(workflow_id):
    data = request.json
    action = data.get('action')
    comments = data.get('comments', '')
    user_id = data.get('user_id', '1')

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
        try:
            current_step = int(wf[2] or 0)
        except (TypeError, ValueError):
            current_step = 0
        try:
            steps = json.loads(wf[4]) if wf[4] else []
        except Exception:
            steps = []
        step_index = None
        for i, s in enumerate(steps):
            if s.get('step') == current_step:
                step_index = i
                break

        if action == 'reject':
            if step_index is None or step_index == 0:
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
            '''), {'stat': new_status, 'step': str(new_step), 'ass': new_assigned,
                   'datekey': str(get_today_key()), 'wid': workflow_id})
            # SQL Server: use CONCAT instead of ||
            conn.execute(text("UPDATE t_EngineeringDocument SET Notes = CONCAT(COALESCE(Notes, ''), :note) WHERE DocumentID = :did"),
                         {'note': f'\nRejected at step {current_step} by user {user_id}: {comments}', 'did': doc_id})
            conn.commit()
            return jsonify({'success': True, 'message': f'Document rejected at step {current_step}'})
        else:
            if step_index is None or step_index + 1 >= len(steps):
                new_status = 'Completed'
                new_step = current_step
                new_assigned = None
                conn.execute(text("UPDATE t_EngineeringDocument SET Status = 'Approved' WHERE DocumentID = :did"),
                             {'did': doc_id})
            else:
                new_step = steps[step_index + 1]['step']
                new_status = 'InProgress'
                new_assigned = None
            conn.execute(text('''
                UPDATE t_Workflow SET Status = :stat, CurrentStep = :step, AssignedTo = :ass, LastUpdatedDateKey = :datekey
                WHERE WorkflowID = :wid
            '''), {'stat': new_status, 'step': str(new_step), 'ass': new_assigned,
                   'datekey': str(get_today_key()), 'wid': workflow_id})
            if current_step == 2:
                conn.execute(text('UPDATE t_EngineeringDocument SET CheckedBy = :uid WHERE DocumentID = :did'), {'uid': user_id, 'did': doc_id})
            elif current_step == 3:
                conn.execute(text('UPDATE t_EngineeringDocument SET ApprovedBy = :uid WHERE DocumentID = :did'), {'uid': user_id, 'did': doc_id})
            conn.commit()
            return jsonify({'success': True, 'message': f'Step {current_step} approved'})


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
        threads = [{'threadId': r[0], 'subject': r[1], 'createdAt': r[2],
                    'customerProject': r[3], 'internalProject': r[4]} for r in rows]
    return jsonify(threads)


@api_bp.route('/documents/exchange/thread', methods=['POST'])
def create_exchange_thread():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        new_id = _new_id()
        conn.execute(text('''
            INSERT INTO t_DocumentExchangeThread
            (ThreadID, CustomerProjectID, InternalProjectID, Subject, CreatedAt)
            VALUES (:tid, :cust, :int, :subj, :now)
        '''), {'tid': new_id, 'cust': data.get('customerProjectId'),
               'int': data.get('internalProjectId'), 'subj': data.get('subject'),
               'now': datetime.now().isoformat()})
        conn.commit()
    return jsonify({'success': True, 'threadId': new_id})


# ==================== ENGINEERING HANDOVER ====================

@api_bp.route('/handover/items')
def handover_items():
    industry, project_id = get_industry_and_project(request)
    status = request.args.get('status')

    query = '''
        SELECT p.ProductId, p.descEnglish, p.UOM, p.QuickNotes,
               p.IsEngineeringItem,
               CASE WHEN EXISTS (SELECT 1 FROM t_BOM WHERE ParentProductID = p.ProductId AND ComponentProductID != ParentProductID) THEN 1 ELSE 0 END as HasBOM
        FROM t_Product p
        WHERE p.IsEngineeringItem = '1'
          AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
    '''
    params = {'industry': industry}
    if project_id and project_id != 'All' and project_id != '':
        query += ' AND EXISTS (SELECT 1 FROM t_ProjectProducts WHERE ProductID = p.ProductId AND ProjectID = :proj)'
        params['proj'] = project_id
    if status == 'pending':
        query += " AND p.IsEngineeringItem = '1'"
    elif status == 'product_created':
        query += " AND p.IsEngineeringItem = '0'"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        items = []
        for r in rows:
            quick_notes = r[3] or '{}'
            try:
                eng_data = json.loads(quick_notes) if isinstance(quick_notes, str) else {}
            except Exception:
                eng_data = {}
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
            WHERE IsEngineeringItem = '1' AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'industry': industry}).fetchone()[0]
        products_created = conn.execute(text('''
            SELECT COUNT(*) FROM t_Product 
            WHERE IsEngineeringItem = '0' 
              AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
        '''), {'industry': industry}).fetchone()[0]
        bom_pending = conn.execute(text('''
            SELECT COUNT(*) FROM t_Product p
            WHERE p.IsEngineeringItem = '1' 
              AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
              AND NOT EXISTS (SELECT 1 FROM t_BOM WHERE ParentProductID = p.ProductId AND ComponentProductID != p.ProductId)
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
                IsEngineeringItem = '0',
                ProcurementType = :proc,
                StandardCost = :cost,
                LeadTime = :lead,
                UOM = :uom
            WHERE ProductId = :pid
        '''), {
            'pid': item_id,
            'proc': data.get('ProcurementType', 'Manufactured'),
            'cost': str(data.get('StandardCost', 0)),
            'lead': str(data.get('LeadTime', 0)),
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
        current_notes = conn.execute(text('SELECT QuickNotes FROM t_Product WHERE ProductId = :pid'), {'pid': item_id}).fetchone()
        notes = json.loads(current_notes[0]) if current_notes and current_notes[0] else {}
        notes['linkedToProduct'] = target_product_id
        conn.execute(text('UPDATE t_Product SET QuickNotes = :notes WHERE ProductId = :pid'),
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


@api_bp.route('/documents/<string:doc_id>/revisions')
def document_revisions(doc_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT RevisionNumber, FilePath, ChangeReason, CreatedDate, CheckedOutBy
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
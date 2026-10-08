from flask import session, request, jsonify
from flask_login import login_required



@api_bp.route('/set-current-project', methods=['POST'])
@login_required
def set_current_project():
    data = request.get_json(silent=True) or request.form
    project_id = data.get('project_id')
    
    if not project_id:
        return jsonify({'status': 'error', 'message': 'Missing project_id'}), 400
    
    # Handle array (from legacy multiple-select)
    if isinstance(project_id, list):
        project_id = project_id[0] if project_id else 'All'
    
    session['current_project_id'] = project_id
    session.modified = True      # ← CRITICAL
    session.permanent = True     # ← makes it survive
    
    print(f"🔵 Set current_project_id = {project_id}")  # ← debug
    
    return jsonify({'status': 'ok', 'project_id': project_id})

@api_bp.route('/current-project', methods=['GET'])
def get_current_project():
    from flask import session
    return jsonify({
        'project_id': session.get('current_project_id', 'All')
    })
 
@api_bp.route('/user/projects', methods=['GET'])
@login_required
def get_user_projects():
    project_ids = session.get('selected_projects', ['All'])
    return jsonify(project_ids)

@api_bp.route('/user/projects', methods=['POST'])
@login_required
def set_user_projects():
    data = request.get_json()
    project_ids = data.get('projects', [])
    session['selected_projects'] = project_ids
    # Also set first project as current for compatibility
    if project_ids and len(project_ids) > 0:
        session['current_project_id'] = project_ids[0]
    else:
        session['current_project_id'] = 'All'
    return jsonify({'status': 'ok'})
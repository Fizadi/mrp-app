window.crmOpportunities = {
    currentView: 'list',
    currentOpportunityId: null,
    
    init: function() {
        this.loadUsers();
        this.loadCustomers();
        this.loadStages();
        this.bindEvents();
        this.refreshAll();
    },
    
    bindEvents: function() {
        $('#listViewBtn').on('click', () => this.switchView('list'));
        $('#kanbanViewBtn').on('click', () => this.switchView('kanban'));
        $('#createOpportunityBtn').on('click', () => this.showCreateModal());
        $('#refreshBtn').on('click', () => this.refreshAll());
        $('#exportCsvBtn').on('click', () => this.exportCSV());
        $('#importCsvBtn').on('click', () => this.importCSV());
        $('#saveOpportunityBtn').on('click', () => this.saveOpportunity());
        $('#saveActivityBtn').on('click', () => this.saveActivity());
        $('#convertToProjectBtn').on('click', () => this.convertToProject());
        $('#convertToQuoteBtn').on('click', () => this.convertToQuote());
        
        // Filters
        $('#filterStage, #filterAssignedTo, #filterStatus, #filterDateFrom, #filterDateTo, #filterSearch').on('change', () => this.refreshAll());
        
        // Inline editing delegation
        $(document).on('click', '.inline-edit-probability', (e) => this.inlineEditProbability(e));
        $(document).on('click', '.inline-edit-stage', (e) => this.inlineEditStage(e));
        $(document).on('click', '.log-activity-btn', (e) => this.showActivityModal(e));
        $(document).on('click', '.convert-opportunity-btn', (e) => this.showConvertModal(e));
        $(document).on('click', '.view-opportunity-btn', (e) => this.viewOpportunity(e));
    },
    
    loadUsers: function() {
        $.get('/api/crm/users', (data) => {
            let options = '<option value="">{{ get_translation("auto.crm_all", "All") }}</option>';
            data.forEach(user => {
                options += `<option value="${user.user_id}">${user.full_name}</option>`;
            });
            $('#filterAssignedTo').html(options);
            
            let assignOptions = '<option value="">{{ get_translation("auto.crm_unassigned", "Unassigned") }}</option>';
            data.forEach(user => {
                assignOptions += `<option value="${user.user_id}">${user.full_name}</option>`;
            });
            $('#oppAssignedTo').html(assignOptions);
        });
    },
    
    loadCustomers: function() {
        $.get('/api/crm/customers', (data) => {
            let options = '<option value="">{{ get_translation("auto.crm_select_customer", "Select Customer") }}</option>';
            data.forEach(customer => {
                options += `<option value="${customer.customer_id}">${customer.name}</option>`;
            });
            $('#oppCustomerId').html(options);
        });
    },
    
    loadStages: function() {
        $.get('/api/crm/stages', (data) => {
            let options = '';
            data.forEach(stage => {
                options += `<option value="${stage}">${stage}</option>`;
            });
            $('#oppStage').html(options);
        });
    },
    
    refreshAll: function() {
        this.refreshKPIs();
        if (this.currentView === 'list') {
            this.refreshTable();
        } else {
            this.refreshKanban();
        }
    },
    
    refreshKPIs: function() {
        const filters = this.getFilterParams();
        $.get('/api/crm/opportunities/stats', filters, (data) => {
            $('#kpiTotalValue').text('$' + data.total_value.toLocaleString());
            $('#kpiWeighted').text('$' + data.weighted_value.toLocaleString());
            $('#kpiWinRate').text(data.win_rate + '%');
            $('#kpiOpenCount').text(data.opportunity_count);
            $('#opportunitiesCount').text(data.opportunity_count);
        });
    },
    
    refreshTable: function() {
        const filters = this.getFilterParams();
        $('#opportunitiesTableBody').html('<tr><td class="text-center py-4" colspan="9">{{ get_translation("auto.crm_loading", "Loading...") }}</td></tr>');
        
        $.get('/api/crm/opportunities', filters, (data) => {
            if (!data || data.length === 0) {
                $('#opportunitiesTableBody').html('<tr><td class="text-center py-4" colspan="9">{{ get_translation("auto.crm_no_data", "No opportunities found") }}</td></tr>');
                return;
            }
            
            let html = '';
            data.forEach(opp => {
                const probClass = opp.probability >= 70 ? 'probability-high' : (opp.probability >= 30 ? 'probability-medium' : 'probability-low');
                const closeDate = opp.expected_close_date_key ? 
                    `${opp.expected_close_date_key.toString().slice(0,4)}-${opp.expected_close_date_key.toString().slice(4,6)}-${opp.expected_close_date_key.toString().slice(6,8)}` : 'N/A';
                
                html += `<tr>
                    <td><a href="#" class="view-opportunity-btn" data-id="${opp.opportunity_id}">${this.escapeHtml(opp.name)}</a></td>
                    <td>${this.escapeHtml(opp.customer_name || '')}</td>
                    <td>$${opp.value.toLocaleString()}</td>
                    <td class="inline-edit-probability ${probClass}" data-id="${opp.opportunity_id}" data-value="${opp.probability}" style="cursor:pointer">${opp.probability}%</td>
                    <td class="inline-edit-stage" data-id="${opp.opportunity_id}" data-stage="${opp.stage}" style="cursor:pointer">${this.escapeHtml(opp.stage)}</td>
                    <td>${closeDate}</td>
                    <td>${this.escapeHtml(opp.assigned_user_name || 'Unassigned')}</td>
                    <td><span class="badge ${opp.status === 'Closed Won' ? 'bg-success' : (opp.status === 'Closed Lost' ? 'bg-danger' : 'bg-primary')}">${opp.status}</span></td>
                    <td>
                        <button class="btn btn-sm btn-info log-activity-btn" data-id="${opp.opportunity_id}" title="Log Activity"><i class="fas fa-clock"></i></button>
                        <button class="btn btn-sm btn-warning convert-opportunity-btn" data-id="${opp.opportunity_id}" title="Convert"><i class="fas fa-exchange-alt"></i></button>
                    </td>
                </tr>`;
            });
            $('#opportunitiesTableBody').html(html);
        });
    },
    
    refreshKanban: function() {
        const filters = this.getFilterParams();
        $.get('/api/crm/opportunities', filters, (data) => {
            const stages = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation', 'Closed Won', 'Closed Lost'];
            let html = '';
            
            stages.forEach(stage => {
                const opportunities = data.filter(opp => opp.stage === stage);
                html += `<div class="col-md-6 col-lg-4 mb-3">
                    <div class="kanban-column">
                        <div class="stage-column-header">${stage} <span class="badge bg-secondary">${opportunities.length}</span></div>
                        <div class="kanban-cards-container" data-stage="${stage}">`;
                
                opportunities.forEach(opp => {
                    const probClass = opp.probability >= 70 ? 'probability-high' : (opp.probability >= 30 ? 'probability-medium' : 'probability-low');
                    html += `<div class="kanban-card" data-id="${opp.opportunity_id}">
                        <div class="opportunity-name">${this.escapeHtml(opp.name)}</div>
                        <div class="opportunity-customer text-muted small">${this.escapeHtml(opp.customer_name || 'No Customer')}</div>
                        <div class="opportunity-value">$${opp.value.toLocaleString()}</div>
                        <div class="probability ${probClass}">${opp.probability}%</div>
                        <div class="mt-2">
                            <button class="btn btn-sm btn-outline-primary move-stage-up" data-id="${opp.opportunity_id}" data-direction="up" ${stage === stages[0] ? 'disabled' : ''}><i class="fas fa-arrow-up"></i></button>
                            <button class="btn btn-sm btn-outline-primary move-stage-down" data-id="${opp.opportunity_id}" data-direction="down" ${stage === stages[stages.length-1] ? 'disabled' : ''}><i class="fas fa-arrow-down"></i></button>
                        </div>
                    </div>`;
                });
                
                html += `</div></div></div>`;
            });
            
            $('#kanbanContainer').html(html);
            $('.move-stage-up').on('click', (e) => this.moveStage(e, 'up'));
            $('.move-stage-down').on('click', (e) => this.moveStage(e, 'down'));
        });
    },
    
    moveStage: function(event, direction) {
        const card = $(event.currentTarget).closest('.kanban-card');
        const opportunityId = card.data('id');
        const currentStage = card.closest('.kanban-column').find('.stage-column-header').text().split(' ')[0];
        
        const stages = ['Prospecting', 'Qualification', 'Proposal', 'Negotiation', 'Closed Won', 'Closed Lost'];
        let currentIndex = stages.indexOf(currentStage);
        let newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1;
        
        if (newIndex >= 0 && newIndex < stages.length) {
            const newStage = stages[newIndex];
            $.ajax({
                url: `/api/crm/opportunities/${opportunityId}/inline`,
                method: 'PATCH',
                contentType: 'application/json',
                data: JSON.stringify({ stage: newStage }),
                success: () => this.refreshKanban()
            });
        }
    },
    
    switchView: function(view) {
        this.currentView = view;
        if (view === 'list') {
            $('#listView').show();
            $('#kanbanView').hide();
            $('#listViewBtn').addClass('btn-primary').removeClass('btn-outline-primary');
            $('#kanbanViewBtn').addClass('btn-outline-primary').removeClass('btn-primary');
            this.refreshTable();
        } else {
            $('#listView').hide();
            $('#kanbanView').show();
            $('#kanbanViewBtn').addClass('btn-primary').removeClass('btn-outline-primary');
            $('#listViewBtn').addClass('btn-outline-primary').removeClass('btn-primary');
            this.refreshKanban();
        }
    },
    
    getFilterParams: function() {
        return {
            stage: $('#filterStage').val(),
            assigned_to: $('#filterAssignedTo').val(),
            status: $('#filterStatus').val(),
            date_from: $('#filterDateFrom').val(),
            date_to: $('#filterDateTo').val(),
            search: $('#filterSearch').val()
        };
    },
    
    showCreateModal: function() {
        $('#opportunityModalTitle').text('{{ get_translation("auto.crm_create_opportunity", "Create Opportunity") }}');
        $('#opportunityForm')[0].reset();
        $('#opportunityId').val('');
        $('#oppExpectedClose').val(new Date().toISOString().split('T')[0]);
        $('#opportunityModal').modal('show');
    },
    
    saveOpportunity: function() {
        const data = {
            name: $('#oppName').val(),
            customer_id: $('#oppCustomerId').val(),
            assigned_to_user_id: $('#oppAssignedTo').val(),
            value: parseFloat($('#oppValue').val()) || 0,
            probability: parseInt($('#oppProbability').val()) || 0,
            stage: $('#oppStage').val(),
            expected_close_date: $('#oppExpectedClose').val(),
            project_id: $('#oppProjectId').val(),
            contact_person: $('#oppContactPerson').val(),
            contact_email: $('#oppContactEmail').val(),
            contact_phone: $('#oppContactPhone').val(),
            notes: $('#oppNotes').val()
        };
        
        const opportunityId = $('#opportunityId').val();
        const method = opportunityId ? 'PUT' : 'POST';
        const url = opportunityId ? `/api/crm/opportunities/${opportunityId}` : '/api/crm/opportunities';
        
        $.ajax({
            url: url,
            method: method,
            contentType: 'application/json',
            data: JSON.stringify(data),
            success: () => {
                $('#opportunityModal').modal('hide');
                this.refreshAll();
            },
            error: (xhr) => alert('Error: ' + xhr.responseJSON?.error || 'Unknown error')
        });
    },
    
    showActivityModal: function(e) {
        const id = $(e.currentTarget).data('id');
        $('#activityOpportunityId').val(id);
        $('#activityDate').val(new Date().toISOString().split('T')[0]);
        $('#activityForm')[0].reset();
        $('#activityModal').modal('show');
    },
    
    saveActivity: function() {
        const data = {
            activity_type: $('#activityType').val(),
            subject: $('#activitySubject').val(),
            activity_date: $('#activityDate').val(),
            notes: $('#activityNotes').val(),
            reminder_date: $('#activityReminder').val()
        };
        
        const opportunityId = $('#activityOpportunityId').val();
        $.ajax({
            url: `/api/crm/opportunities/${opportunityId}/activity`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(data),
            success: () => {
                $('#activityModal').modal('hide');
                alert('Activity logged successfully!');
            },
            error: (xhr) => alert('Error: ' + xhr.responseJSON?.error || 'Unknown error')
        });
    },
    
    showConvertModal: function(e) {
        const id = $(e.currentTarget).data('id');
        this.currentOpportunityId = id;
        $('#convertModal').modal('show');
    },
    
    convertToProject: function() {
        $.ajax({
            url: `/api/crm/opportunities/${this.currentOpportunityId}/convert`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ convert_type: 'project' }),
            success: (response) => {
                $('#convertModal').modal('hide');
                if (response.redirect_url) {
                    window.location.href = response.redirect_url;
                } else {
                    this.refreshAll();
                }
            },
            error: (xhr) => alert('Error: ' + xhr.responseJSON?.error || 'Unknown error')
        });
    },
    
    convertToQuote: function() {
        $.ajax({
            url: `/api/crm/opportunities/${this.currentOpportunityId}/convert`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ convert_type: 'quote' }),
            success: (response) => {
                $('#convertModal').modal('hide');
                alert(response.message);
                this.refreshAll();
            },
            error: (xhr) => alert('Error: ' + xhr.responseJSON?.error || 'Unknown error')
        });
    },
    
    inlineEditProbability: function(e) {
        const cell = $(e.currentTarget);
        const id = cell.data('id');
        const currentValue = cell.data('value');
        const newValue = prompt('Enter probability (0-100):', currentValue);
        if (newValue !== null && !isNaN(newValue) && newValue >= 0 && newValue <= 100) {
            $.ajax({
                url: `/api/crm/opportunities/${id}/inline`,
                method: 'PATCH',
                contentType: 'application/json',
                data: JSON.stringify({ probability: parseInt(newValue) }),
                success: () => this.refreshTable()
            });
        }
    },
    
    inlineEditStage: function(e) {
        const cell = $(e.currentTarget);
        const id = cell.data('id');
        const currentStage = cell.data('stage');
        const newStage = prompt('Enter new stage (Prospecting, Qualification, Proposal, Negotiation, Closed Won, Closed Lost):', currentStage);
        if (newStage && ['Prospecting', 'Qualification', 'Proposal', 'Negotiation', 'Closed Won', 'Closed Lost'].includes(newStage)) {
            $.ajax({
                url: `/api/crm/opportunities/${id}/inline`,
                method: 'PATCH',
                contentType: 'application/json',
                data: JSON.stringify({ stage: newStage }),
                success: () => this.refreshAll()
            });
        }
    },
    
    viewOpportunity: function(e) {
        e.preventDefault();
        const id = $(e.currentTarget).data('id');
        window.location.href = `/crm/opportunity/${id}`;
    },
    
    exportCSV: function() {
        const filters = this.getFilterParams();
        const queryString = $.param(filters);
        window.location.href = `/api/crm/opportunities/export?${queryString}`;
    },
    
    importCSV: function() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.csv';
        input.onchange = (e) => {
            const file = e.target.files[0];
            const formData = new FormData();
            formData.append('file', file);
            $.ajax({
                url: '/api/crm/opportunities/import',
                method: 'POST',
                data: formData,
                processData: false,
                contentType: false,
                success: (response) => {
                    alert(response.message);
                    this.refreshAll();
                },
                error: (xhr) => alert('Error: ' + xhr.responseJSON?.error || 'Unknown error')
            });
        };
        input.click();
    },
    
    escapeHtml: function(text) {
        if (!text) return '';
        return text.replace(/[&<>]/g, function(m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            return m;
        });
    }
};
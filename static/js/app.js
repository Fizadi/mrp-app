// static/js/app.js
window.operationLibrary = {
    pendingArchiveId: null,
    dataTable: null,

    init: async function() {
    // Wait for i18n to be ready
    if (window.i18n) {
        if (!window.i18n.initialized) {
            await window.i18n.init();
        }
    } else {
        console.warn('i18n not loaded, translations will fallback to English');
    }
    this.bindEvents();
    this.loadOperations();
    this.initSelect2();
    this.loadEquipmentOptions();
    this.loadStats();
},

loadStats: function() {
    $.get('/api/quality/operations/stats')
        .done((data) => {
            if (data.total !== undefined) {
                $('#kpiTotal').text(data.total);
                $('#kpiActive').text(data.active);
                $('#kpiUsed').text(data.used_in_templates);
            } else if (data.error) {
                console.error("Stats API error:", data.error);
                $('#kpiTotal').text('Error');
            }
        })
        .fail((xhr) => {
            console.error("Stats API failed:", xhr.statusText);
            $('#kpiTotal').text('N/A');
        });
},

    bindEvents: function() {
        // Unbind all to prevent duplicates
        $(document).off('click', '.equipment-link');
        $(document).off('click', '.templates-link');
        $(document).off('click', '.edit-operation');
        $(document).off('click', '.copy-operation');
        $(document).off('click', '.view-products');
        $(document).off('click', '.view-history');
        $(document).off('click', '.archive-operation');
        $('#confirmArchiveBtn').off('click');
        $('#createOperationBtn').off('click');
        $('#refreshBtn').off('click');
        $('#saveOperationBtn').off('click');
        $('#filterCategory, #filterSkill, #filterActiveOnly, #filterSearch').off('change keyup');
        $('#importCsvBtn, #exportCsvBtn').off('click');
        $('#archiveModal').off('hidden.bs.modal');

        // Bind fresh
        $('#createOperationBtn').on('click', () => this.openCreateModal());
        $('#refreshBtn').on('click', () => this.loadOperations());
        $('#saveOperationBtn').on('click', () => this.saveOperation());
        $('#filterCategory, #filterSkill, #filterActiveOnly, #filterSearch').on('change keyup', () => this.loadOperations());
        $('#importCsvBtn').on('click', () => this.triggerImport());
        $('#exportCsvBtn').on('click', () => this.exportCsv());
        $(document).on('click', '.equipment-link', (e) => this.showEquipmentModal($(e.currentTarget).data('equipment')));
        $(document).on('click', '.templates-link', (e) => this.showTemplatesModal($(e.currentTarget).data('opid')));
        $(document).on('click', '.edit-operation', (e) => this.openEditModal($(e.currentTarget).data('id')));
        $(document).on('click', '.copy-operation', (e) => this.copyOperation($(e.currentTarget).data('id')));
        $(document).on('click', '.view-products', (e) => this.viewProducts($(e.currentTarget).data('id')));
        $(document).on('click', '.view-history', (e) => this.showVersionHistory($(e.currentTarget).data('id')));
        $(document).on('click', '.archive-operation', (e) => this.confirmArchive($(e.currentTarget).data('id')));
        $('#confirmArchiveBtn').on('click', () => this.archiveOperation());
        $('#archiveModal').on('hidden.bs.modal', () => {
            this.pendingArchiveId = null;
            $('#confirmArchiveBtn').prop('disabled', false).text('Archive');
        });
    },

    initSelect2: function() {
        $('#filterCategory, #filterSkill').select2({ theme: 'bootstrap-5', width: '100%' });
    },

    loadEquipmentOptions: function() {
        $.get('/api/quality/equipment-list')
            .done((data) => {
                var select = $('#opEquipment');
                select.empty().append('<option value="">-- Select equipment --</option>');
                data.forEach(eq => {
                    select.append(`<option value="${eq.name}">${eq.name}</option>`);
                });
            })
            .fail(() => console.log('Failed to load equipment list'));
    },

    loadOperations: function() {
        var params = {
            category: $('#filterCategory').val(),
            skill_level: $('#filterSkill').val(),
            active_only: $('#filterActiveOnly').is(':checked'),
            search: $('#filterSearch').val()
        };
        $.get('/api/quality/operations', params, (data) => {
            this.renderTable(data.operations);
            $('#operationsCount').text(data.total);
        }).fail(() => showToast('Failed to load operations', 'error'));
    },

    renderTable: function(ops) {
    var tbody = $('#operationsTableBody');
    if (!ops.length) {
        tbody.html('<tr><td colspan="13" class="text-center">No operations found</td></tr>');
        return;
    }
    var html = '';
    ops.forEach(op => {
        var statusBadge = op.is_active ? '<span class="badge bg-success">Active</span>' : '<span class="badge bg-secondary">Archived</span>';
        var calibrationIcon = op.calibration_required ? '<i class="fas fa-check-circle text-success"></i>' : '<i class="fas fa-times-circle text-danger"></i>';
        var minTargetMax = `${op.min_value || ''} / ${op.target_value || ''} / ${op.max_value || ''}`;
        var equipmentLink = op.equipment_required ? `<a href="#" class="equipment-link" data-equipment="${op.equipment_required}">${op.equipment_required}</a>` : '';
        var templatesLink = op.used_in_templates ? `<a href="#" class="templates-link" data-opid="${op.id}">${op.used_in_templates}</a>` : '0';
		
		
			console.log('i18n object:', window.i18n);
			console.log('i18n initialized:', window.i18n ? window.i18n.initialized : false);
			console.log('test translation edit:', window.i18n ? window.i18n.t('edit', 'Edit') : 'no i18n');
        
		
		var _t = (key, def) => (window.i18n && window.i18n.t) ? window.i18n.t(key, def) : def;

        html += `<tr${!op.is_active ? ' class="table-secondary"' : ''}>
            <td><a href="/quality/operation/${op.id}" target="_blank">${op.code || ''}</a></td>
            <td>${op.name}</td>
            <td>${op.category || ''}</td>
            <td>${op.unit || ''}</td>
            <td>${minTargetMax}</td>
            <td>${equipmentLink}</td>
            <td>${calibrationIcon}</td>
            <td>${op.estimated_time || ''}</td>
            <td>${op.skill_level || ''}</td>
            <td>${statusBadge}</td>
            <td>${templatesLink}</td>
            <td>${op.last_modified || ''}</td>
            <td>
                <div class="d-flex flex-wrap gap-1">
                    <button class="btn btn-warning btn-sm edit-operation" data-id="${op.id}" title="${_t('edit', 'Edit')}">
                        <i class="fas fa-edit fa-xs"></i> ${_t('edit', 'Edit')}
                    </button>
                    <button class="btn btn-secondary btn-sm copy-operation" data-id="${op.id}" title="${_t('copy', 'Copy')}">
                        <i class="fas fa-copy fa-xs"></i> ${_t('copy', 'Copy')}
                    </button>
                    <button class="btn btn-info btn-sm view-products" data-id="${op.id}" title="${_t('view_products', 'View Products')}">
                        <i class="fas fa-boxes fa-xs"></i> ${_t('view_products', 'View Products')}
                    </button>
                    <button class="btn btn-secondary btn-sm view-history" data-id="${op.id}" title="${_t('history', 'History')}">
                        <i class="fas fa-history fa-xs"></i> ${_t('history', 'History')}
                    </button>
                    <button class="btn btn-danger btn-sm archive-operation" data-id="${op.id}" title="${_t('archive', 'Archive')}">
                        <i class="fas fa-archive fa-xs"></i> ${_t('archive', 'Archive')}
                    </button>
                </div>
            </td>
        </tr>`;
    });
    tbody.html(html);
},

    openCreateModal: function() {
        $('#operationForm')[0].reset();
        $('#operationId').val('');
        $('#opProductCode').val('General');
        $('#opDescription').val('');
        $('#operationModalTitle').text('Create Operation');
        $('#opIsActive').prop('checked', true);
        $('#opEquipment').val('');
        $('#operationModal').modal('show');
    },

    openEditModal: function(id) {
        if (!id) {
            showToast('Invalid operation ID', 'error');
            return;
        }
        console.log("Fetching operation ID:", id);
        $.ajax({
            url: `/api/quality/operations/${id}`,
            method: 'GET',
            dataType: 'json',
            success: (data) => {
                console.log("GET response:", data);
                if (!data.success) {
                    showToast(data.message || 'Error loading operation', 'error');
                    return;
                }
                var op = data.operation;
                $('#operationId').val(op.id);
                $('#opCode').val(op.code);
                $('#opName').val(op.name);
                $('#opProductCode').val(op.product_code || 'General');
                $('#opDescription').val(op.description || '');
                $('#opCategory').val(op.category);
                $('#opUnit').val(op.unit);
                $('#opMin').val(op.min_value);
                $('#opTarget').val(op.target_value);
                $('#opMax').val(op.max_value);
                $('#opEquipment').val(op.equipment_required || '');
                $('#opCalibrationReq').prop('checked', op.calibration_required);
                $('#opEstTime').val(op.cycle_time || op.estimated_time);
                $('#opSkill').val(op.skill_level);
                $('#opIsActive').prop('checked', op.is_active);
                $('#operationModalTitle').text('Edit Operation');
                $('#operationModal').modal('show');
            },
            error: (xhr) => {
                console.error("GET error:", xhr.status, xhr.responseText);
                showToast(`Error loading operation: ${xhr.status} ${xhr.statusText}`, 'error');
            }
        });
    },

    saveOperation: function() {
        // Numeric validation
        var numFields = [
            {id: '#opMin', name: 'Min Value'},
            {id: '#opTarget', name: 'Target Value'},
            {id: '#opMax', name: 'Max Value'},
            {id: '#opEstTime', name: 'Est. Time (min)'}
        ];
        for (var i = 0; i < numFields.length; i++) {
            var val = $(numFields[i].id).val();
            if (val && isNaN(parseFloat(val))) {
                showToast(numFields[i].name + ' must be a number', 'error');
                return;
            }
        }

        var id = $('#operationId').val();
        var payload = {
            code: $('#opCode').val(),
            name: $('#opName').val(),
            product_code: $('#opProductCode').val(),
            category: $('#opCategory').val(),
            unit: $('#opUnit').val(),
            min_value: $('#opMin').val(),
            target_value: $('#opTarget').val(),
            max_value: $('#opMax').val(),
            equipment_required: $('#opEquipment').val(),
            calibration_required: $('#opCalibrationReq').is(':checked'),
            cycle_time: $('#opEstTime').val(),
            skill_level: $('#opSkill').val(),
            is_active: $('#opIsActive').is(':checked')
        };
        var method = id ? 'PUT' : 'POST';
        var url = id ? `/api/quality/operations/${id}` : '/api/quality/operations';

        $.ajax({
            url: url,
            method: method,
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: (data) => {
                if (data.success) {
                    $('#operationModal').modal('hide');
                    this.loadOperations();
                    showToast('Operation saved', 'success');
                } else {
                    showToast(data.message || 'Error saving', 'error');
                }
            },
            error: (xhr) => {
                showToast(xhr.responseJSON?.message || 'Error saving', 'error');
            }
        });
    },

    copyOperation: function(id) {
        if (!id) return;
        if (confirm('Copy this operation?')) {
            $.post(`/api/quality/operations/${id}/copy`)
                .done((data) => {
                    if (data.success) {
                        this.loadOperations();
                        showToast('Operation copied', 'success');
                    } else {
                        showToast(data.message || 'Copy failed', 'error');
                    }
                })
                .fail(() => showToast('Copy failed', 'error'));
        }
    },

    confirmArchive: function(id) {
        if (!id) return;
        this.pendingArchiveId = id;
        $('#archiveModal').modal('show');
    },

    archiveOperation: function() {
        var self = this;
        var idToDelete = this.pendingArchiveId;
        if (!idToDelete) {
            showToast('No operation selected for archive', 'error');
            $('#archiveModal').modal('hide');
            return;
        }
        $('#confirmArchiveBtn').prop('disabled', true).text('Archiving...');
        
        $.ajax({
            url: `/api/quality/operations/${idToDelete}`,
            method: 'DELETE',
            success: (data) => {
                if (data && data.success) {
                    $('#archiveModal').modal('hide');
                    self.loadOperations();
                    showToast('Operation archived', 'success');
                } else {
                    showToast(data?.message || 'Archive failed', 'error');
                }
            },
            error: (xhr) => {
                showToast('Archive failed: ' + (xhr.responseJSON?.message || xhr.statusText), 'error');
            },
            complete: () => {
                $('#confirmArchiveBtn').prop('disabled', false).text('Archive');
                self.pendingArchiveId = null;
            }
        });
    },

    showEquipmentModal: function(equipmentName) {
        if (!equipmentName) return;
        $.get('/api/quality/equipment', { name: equipmentName })
            .done((data) => {
                var html = data.length ? `<ul class="list-group">${data.map(e => `<li class="list-group-item"><strong>${e.EquipmentName}</strong><br>Last Cal: ${e.LastMaintenanceDate || 'N/A'}<br>Next Due: ${e.NextMaintenanceDate || 'N/A'}<br>Status: ${e.Status}</li>`).join('')}</ul>` : '<p>No equipment details found.</p>';
                $('#equipmentModalBody').html(html);
                $('#equipmentModal').modal('show');
            })
            .fail(() => showToast('Error loading equipment details', 'error'));
    },

    showTemplatesModal: function(opId) {
        if (!opId) return;
        $.get(`/api/quality/operations/${opId}/templates`)
            .done((data) => {
                var html = data.length ? `<ul class="list-group">${data.map(t => `<li class="list-group-item"><a href="/quality/template/${t.id}">${t.name}</a></li>`).join('')}</ul>` : '<p>Not used in any template.</p>';
                $('#templatesModalBody').html(html);
                $('#templatesModal').modal('show');
            })
            .fail(() => showToast('Error loading templates', 'error'));
    },

    showVersionHistory: function(opId) {
        $.get(`/api/quality/operations/${opId}/versions`)
            .done((data) => {
                $('#versionModalBody').html(data.length ? '<pre>' + JSON.stringify(data, null, 2) + '</pre>' : '<p class="text-muted">No version history yet.</p>');
                $('#versionModal').modal('show');
            })
            .fail(() => {
                $('#versionModalBody').html('<p class="text-muted">Version history not available.</p>');
                $('#versionModal').modal('show');
            });
    },

    viewProducts: function(opId) {
        if (!opId) {
            showToast('Invalid operation ID', 'error');
            return;
        }
        showToast(`Products page for operation ${opId} – will be available soon.`, 'info');
    },

    triggerImport: function() {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = '.csv';
        input.onchange = (e) => {
            var file = e.target.files[0];
            var formData = new FormData();
            formData.append('file', file);
            $.ajax({
                url: '/api/quality/operations/import',
                method: 'POST',
                data: formData,
                processData: false,
                contentType: false,
                success: (res) => {
                    showToast(res.message, 'success');
                    this.loadOperations();
                },
                error: () => showToast('Import failed', 'error')
            });
        };
        input.click();
    },

    exportCsv: function() {
        window.location.href = '/api/quality/operations/export';
    }
};

// Page detection and initialization
$(document).ready(function() {
    if ($('#operationsTable').length) window.operationLibrary.init();
    if ($('#productsTable').length) window.productManagement.init();
});
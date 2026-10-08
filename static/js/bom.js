window.bomManagement = {
    currentProductId: null,
    currentNodeData: null,

    init: function(config) {
        console.log("BOM Management init", config);
        this.products = config.products;
        if (config.initialProductId && config.initialProductId !== 0) {
            this.currentProductId = config.initialProductId;
        } else if (this.products && this.products.length > 0) {
            this.currentProductId = this.products[0].ProductID;
        } else {
            console.error("No products available");
            return;
        }
        // Populate product selector
        const $selector = $('#productSelector');
        $selector.empty();
        this.products.forEach(p => {
            $selector.append(`<option value="${p.ProductID}">${p.ProductName}</option>`);
        });
        $selector.val(this.currentProductId);

        this.bindEvents();
        this.loadTree();
        this.loadStats();
    },

    bindEvents: function() {
        $('#productSelector').on('change', () => {
            this.currentProductId = $('#productSelector').val();
            this.loadTree();
        });
        $('#effectivityDate, #showLockedOnly').on('change', () => this.loadTree());
        $('#importBtn').click(() => this.importCSV());
        $('#exportBtn').click(() => this.exportCSV());
        $('#addComponentBtn').click(() => this.showAddModal());
        $('#saveComponentBtn').click(() => this.saveComponent());

        $(document).on('click', '.tree-node-link', (e) => {
            e.preventDefault();
            const node = $(e.currentTarget).data('node');
            if (node) this.showSidePanel(node);
        });
        $(document).on('click', '.editComponentBtn', () => this.editComponent());
        $(document).on('click', '.deleteComponentBtn', () => this.deleteComponent());

        $('#actionRaiseNCR').click(() => this.raiseNCR());
        $('#actionCreatePO').click(() => this.createPO());
        $('#actionViewQualityPlan').click(() => this.viewQualityPlan());
        $('#actionRequestECO').click(() => this.requestECO());
        $('#actionViewDocuments').click(() => this.viewDocuments());
    },

    loadTree: function() {
        const effDate = $('#effectivityDate').val();
        const lockedOnly = $('#showLockedOnly').is(':checked');
        const url = `/api/bom/tree/${encodeURIComponent(this.currentProductId)}?effectivity_date=${effDate}&show_locked_only=${lockedOnly}`;
        console.log("Loading tree from", url);
        $.get(url)
            .done(data => {
                console.log("Tree data received", data);
                this.renderTree(data);
            })
            .fail(err => console.error("Tree load error", err));
    },

    renderTree: function(nodes, $parent) {
        const $container = $parent || $('#bomTree');
        $container.empty();
        if (!nodes || nodes.length === 0) {
            $container.html('<div class="text-muted">No components found</div>');
            return;
        }
        const $ul = $('<ul class="list-unstyled ms-3"></ul>');
        nodes.forEach(node => {
            const $li = $('<li class="mt-2"></li>');
            const hasChildren = node.children && node.children.length > 0;
            const icon = hasChildren ? '<i class="fas fa-folder-open toggle-icon me-1"></i>' : '<i class="fas fa-file-alt me-1"></i>';
            const lockIcon = node.locked ? '<i class="fas fa-lock text-danger ms-2"></i>' : '';
            const $link = $(`<a href="#" class="tree-node-link text-decoration-none">${icon} ${node.name} (${node.quantity})${lockIcon}</a>`);
            $link.data('node', node);
            $li.append($link);
            if (hasChildren) {
                const $childUl = $('<ul class="list-unstyled ms-4" style="display: block;"></ul>');
                $li.append($childUl);
                this.renderTree(node.children, $childUl);
                $link.find('.toggle-icon').on('click', (e) => {
                    e.stopPropagation();
                    $childUl.toggle();
                    $(e.currentTarget).toggleClass('fa-folder-open fa-folder');
                });
            }
            $ul.append($li);
        });
        $container.append($ul);
    },

    showSidePanel: function(node) {
        console.log("showSidePanel called with node:", node);
        if (!node) { console.error("node is undefined"); return; }
        this.currentNodeData = node;

        // Basic details for the Details tab
        $('#detailName').text(node.name);
        $('#detailQty').text(node.quantity);
        $('#detailScrap').text(node.scrap_factor);
        $('#detailEffectivity').text(node.effectivity || 'N/A');

        // Load summary using the same code that worked manually
        $('#componentSummary').html('<p class="text-muted">Loading summary...</p>');
        const pid = node.component_id;
        console.log("Fetching summary for", pid);
        $.get(`/api/bom/component/${encodeURIComponent(pid)}/summary`)
            .done((data) => {
                console.log("Summary data received:", data);
                let html = '';
                // Inventory
                html += `<div class="mb-3"><h6>📦 Inventory</h6>
                         <p>On Hand: ${data.inventory.on_hand} | Reserved: ${data.inventory.reserved} | Available: ${data.inventory.available}</p>
                         <button class="btn btn-sm btn-outline-primary view-stock" data-pid="${data.component_id}">View Stock</button>
                         </div>`;
                // Quality
                html += `<div class="mb-3"><h6>✅ Quality</h6>
                         <p>Assigned QC Operations: ${data.quality.assigned_operations_count}<br>
                         Last Inspection: ${data.quality.last_inspection_result} (${data.quality.last_inspection_date || 'N/A'})</p>
                         <button class="btn btn-sm btn-outline-primary view-quality" data-pid="${data.component_id}">View Quality Plan</button>
                         </div>`;
                // Purchasing
                html += `<div class="mb-3"><h6>🛒 Purchasing</h6>
                         <p>Open POs: ${data.purchasing.open_pos_count} | Overdue: ${data.purchasing.overdue_pos_count}<br>
                         Preferred Supplier: ${data.purchasing.preferred_supplier || 'None'} (Lead Time: ${data.purchasing.standard_lead_time_days} days)</p>
                         <button class="btn btn-sm btn-outline-primary create-po" data-pid="${data.component_id}">Create PO</button>
                         </div>`;
                // Production
                html += `<div class="mb-3"><h6>⚙️ Production</h6>
                         <p>Used in Active Orders: ${data.production.active_orders_using_count}<br>
                         Required Next 30 Days: ${data.production.required_next_30_days} units</p>
                         <button class="btn btn-sm btn-outline-primary view-production" data-pid="${data.component_id}">View Production Orders</button>
                         </div>`;
                // Non-Conformance
                html += `<div class="mb-3"><h6>⚠️ Non-Conformance</h6>
                         <p>Open NCRs: ${data.non_conformance.open_ncrs_count} | Closed: ${data.non_conformance.closed_ncrs_count}</p>
                         <button class="btn btn-sm btn-outline-danger raise-ncr" data-pid="${data.component_id}">Raise NCR</button>
                         </div>`;
                // Engineering
                html += `<div class="mb-3"><h6>🔄 Engineering</h6>
                         <p>Pending ECOs: ${data.engineering.pending_ecos_count}<br>
                         Documents: ${data.engineering.document_count}</p>
                         <button class="btn btn-sm btn-outline-primary view-ecos" data-pid="${data.component_id}">View ECOs</button>
                         <button class="btn btn-sm btn-outline-primary view-docs" data-pid="${data.component_id}">View Documents</button>
                         </div>`;
                // Sales & Service
                html += `<div class="mb-3"><h6>📊 Sales & Service</h6>
                         <p>Sold as Spare (YTD): ${data.sales.sold_as_spare_ytd}<br>
                         Service Orders: ${data.sales.service_orders_count}</p>
                         <button class="btn btn-sm btn-outline-primary view-sales" data-pid="${data.component_id}">View Sales Orders</button>
                         </div>`;
                $('#componentSummary').html(html);
            })
            .fail((err) => {
                console.error("Summary error", err);
                $('#componentSummary').html('<p class="text-danger">Failed to load summary</p>');
            });

        // Load where-used
        $.get(`/api/bom/where-used/${encodeURIComponent(pid)}`)
            .done((data) => {
                const $list = $('#whereUsedList').empty();
                data.forEach(p => {
                    $list.append(`<li class="list-group-item">${p.ProductName} (Qty: ${p.Quantity})</li>`);
                });
            })
            .fail((err) => console.error("Where-used error", err));

        // Placeholders for ECO history and doc status
        $('#ecoHistoryList').html('<em>ECO history not implemented</em>');
        $('#docStatusContent').html('<em>Document checkout status not implemented</em>');

        // Show offcanvas
        const offcanvas = new bootstrap.Offcanvas($('#bomSidePanel'));
        offcanvas.show();
    },

    loadStats: function() {
        $.get('/api/bom/stats')
            .done(stats => {
                $('#kpiTotalComponents').text(stats.totalComponents);
                $('#kpiWhereUsed').text(stats.whereUsedRefs);
                $('#kpiPendingECOs').text(stats.pendingECOs);
                $('#kpiLockedDocs').text(stats.lockedDocuments);
            })
            .fail(err => console.error("Stats error", err));
    },

    showAddModal: function() {
        $('#compParentId').val(this.currentProductId);
        $('#addComponentModal').modal('show');
    },

    saveComponent: function() {
        const data = {
            parent_id: $('#compParentId').val(),
            component_id: $('#compComponentId').val(),
            quantity: $('#compQuantity').val(),
            scrap_factor: $('#compScrap').val(),
            effectivity_date: $('#compEffectivity').val()
        };
        $.post('/api/bom/component', JSON.stringify(data))
            .done(() => {
                $('#addComponentModal').modal('hide');
                this.loadTree();
                this.loadStats();
                this.showToast('Component added', 'success');
            })
            .fail(() => this.showToast('Error adding component', 'danger'));
    },

    editComponent: function() {
        const newQty = prompt('New quantity', this.currentNodeData.quantity);
        if (newQty) {
            $.ajax({
                url: `/api/bom/component/${this.currentNodeData.id}`,
                method: 'PUT',
                data: JSON.stringify({ quantity: newQty, scrap_factor: this.currentNodeData.scrap_factor }),
                success: () => {
                    this.loadTree();
                    this.showToast('Updated', 'success');
                }
            });
        }
    },

    deleteComponent: function() {
        if (confirm('Delete this component?')) {
            $.ajax({
                url: `/api/bom/component/${this.currentNodeData.id}`,
                method: 'DELETE',
                success: () => {
                    this.loadTree();
                    this.loadStats();
                    this.showToast('Deleted', 'success');
                }
            });
        }
    },

    importCSV: function() {
        const input = $('<input type="file" accept=".csv">').on('change', e => {
            const fd = new FormData();
            fd.append('file', e.target.files[0]);
            $.ajax({
                url: '/api/bom/import',
                method: 'POST',
                data: fd,
                processData: false,
                contentType: false,
                success: () => {
                    this.loadTree();
                    this.showToast('Import done', 'success');
                }
            });
        });
        input.click();
    },

    exportCSV: function() {
        window.location.href = `/api/bom/export/${encodeURIComponent(this.currentProductId)}`;
    },

    raiseNCR: function() { alert('Raise NCR for component ' + this.currentNodeData.name); },
    createPO: function() { alert('Create PO for component ' + this.currentNodeData.name); },
    viewQualityPlan: function() { alert('View Quality Plan'); },
    requestECO: function() { alert('Request ECO'); },
    viewDocuments: function() { window.location.href = '/engineering/documents'; },

    showToast: function(msg, type) {
        if (typeof toast === 'function') toast(msg, type);
        else alert(msg);
    }
};
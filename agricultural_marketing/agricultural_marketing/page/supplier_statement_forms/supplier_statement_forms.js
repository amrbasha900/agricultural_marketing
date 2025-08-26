// Complete Supplier Statement Forms JavaScript File with Party Name Column and Search
frappe.pages['supplier-statement-forms'].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Supplier Statement Forms'),
        single_column: true
    });

    // Add CSS styles (same as statement forms)
    $('<style>').prop('type', 'text/css').html(`
        .party-name-cell {
            min-width: 200px;
            max-width: 250px;
        }
        .party-name-cell strong {
            font-size: 14px;
            color: #333;
            display: block;
            margin-bottom: 2px;
        }
        .party-name-cell .text-muted {
            font-size: 11px;
            color: #888;
            font-style: italic;
        }
        #party-search, #history-search, #party-details-search {
            border-radius: 4px;
        }
        #party-search:focus, #history-search:focus, #party-details-search:focus {
            border-color: #007bff;
            box-shadow: 0 0 5px rgba(0, 123, 255, 0.3);
        }
        #search-results-info, #history-search-results-info, #party-details-search-info {
            font-size: 12px;
            color: #666;
            font-style: italic;
        }
        #pdf-status-table tbody tr:hover,
        #history-table tbody tr:hover,
        #party-details-table tbody tr:hover {
            background-color: #f8f9fa;
        }
        .search-highlight {
            background-color: #fff3cd;
            padding: 1px 3px;
            border-radius: 2px;
        }
        @media (max-width: 768px) {
            .party-name-cell {
                min-width: 150px;
                max-width: 180px;
            }
            .party-name-cell strong {
                font-size: 13px;
            }
            .party-name-cell .text-muted {
                font-size: 10px;
            }
        }
        .history-browser {
            padding: 10px 0;
        }
        .badge {
            font-size: 11px;
            padding: 4px 8px;
        }
        .btn-sm + .btn-sm {
            margin-left: 5px;
        }
        .input-group-append .btn {
            border-left: 0;
        }
        .input-group .form-control:focus + .input-group-append .btn {
            border-color: #007bff;
        }
    `).appendTo('head');

    // Generate unique session ID for this page instance
    const sessionId = 'ssf_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);

    // Global variables
    let currentHistoryId = null;
    let refreshInterval = null;

    // SECTION 2: Field definitions with saved filters
    function getSavedFilters() {
        try {
            const saved = localStorage.getItem('supplier_statement_forms_filters');
            return saved ? JSON.parse(saved) : {};
        } catch (e) {
            return {};
        }
    }

    function saveFilters() {
        const filters = {};
        for (let key in page.fields_dict) {
            if (page.fields_dict[key] && page.fields_dict[key].get_value) {
                filters[key] = page.fields_dict[key].get_value();
            }
        }
        localStorage.setItem('supplier_statement_forms_filters', JSON.stringify(filters));
    }

    const savedFilters = getSavedFilters();

    // Create fields
    let openingApproach = page.add_field({
        label: 'Calculate Opening Balance with Totals',
        fieldtype: 'Check',
        fieldname: 'calculate_opening_balance_with_totals',
        default: savedFilters.calculate_opening_balance_with_totals || 0,
        change: saveFilters
    });
    openingApproach.$wrapper.addClass('col-md-3');

    let considerDraft = page.add_field({
        label: __('Consider Drafts'),
        fieldtype: 'Check',
        fieldname: 'consider_draft',
        default: savedFilters.consider_draft || 0,
        change: saveFilters
    });
    considerDraft.$wrapper.addClass('col-md-2');

    let considerDraftPayments = page.add_field({
        label: __('Consider Draft Payments'),
        fieldtype: 'Check',
        fieldname: 'consider_draft_payments',
        default: savedFilters.consider_draft_payments || 0,
        change: saveFilters
    });
    considerDraftPayments.$wrapper.addClass('col-md-2');

    let neglectItems = page.add_field({
        label: __('Neglect Items'),
        fieldtype: 'Check',
        fieldname: 'neglect_items',
        default: savedFilters.neglect_items || 0,
        change: saveFilters
    });
    neglectItems.$wrapper.addClass('col-md-2');

    let company = page.add_field({
        label: 'Company',
        fieldtype: 'Link',
        fieldname: 'company',
        options: 'Company',
        reqd: 1,
        default: savedFilters.company || frappe.defaults.get_default('company'),
        change: saveFilters
    });
    company.$wrapper.removeClass('col-md-2').addClass('col-md-3');

    let fromDate = page.add_field({
        label: 'From Date',
        fieldtype: 'Date',
        fieldname: 'from_date',
        reqd: 1,
        default: savedFilters.from_date || frappe.datetime.get_today(),
        change: saveFilters
    });
    fromDate.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    let toDate = page.add_field({
        label: 'To Date',
        fieldtype: 'Date',
        fieldname: 'to_date',
        default: savedFilters.to_date || frappe.datetime.get_today(),
        change: saveFilters
    });
    toDate.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    // Fixed Party Type field for Supplier
    let partyTypeField = page.add_field({
        label: 'Party Type',
        fieldtype: 'Link',
        fieldname: 'party_type',
        options: 'Party Type',
        default: 'Supplier',
        reqd: 1,
        read_only: 1
    });
    partyTypeField.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    // Supplier Group field
    let supplierGroupField = page.add_field({
        label: 'Supplier Group',
        fieldtype: 'Link',
        fieldname: 'party_group',
        options: 'Supplier Group',
        change: saveFilters
    });
    supplierGroupField.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    // Supplier field
    let supplierField = page.add_field({
        label: 'Supplier',
        fieldtype: 'Link',
        fieldname: 'party',
        options: 'Supplier',
        get_query: function() {
            var filters = {};
            if (supplierGroupField.get_value()) {
                filters['supplier_group'] = supplierGroupField.get_value();
            }
            return {
                filters: filters
            };
        },
        change: saveFilters
    });
    supplierField.$wrapper.removeClass('col-md-2').addClass('col-md-3');

    // Initialize saved values
    setTimeout(() => {
        if (savedFilters.party_group) {
            supplierGroupField.set_value(savedFilters.party_group);
        }
        if (savedFilters.party) {
            supplierField.set_value(savedFilters.party);
        }
    }, 500);

    // Update supplier field when supplier group changes
    supplierGroupField.$input.on('change', function() {
        if (supplierField) {
            supplierField.set_value('');
            supplierField.refresh();
        }
        saveFilters();
    });

    // SECTION 3: History Browse Section
    let $history_section = $(`
        <div class="history-section" style="margin-top: 20px; margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; background-color: #f9f9f9;">
            <div class="row">
                <div class="col-md-6">
                    <h5>${__('Browse Generation History')}</h5>
                    <p class="text-muted">${__('View previous supplier statement generations and their status')}</p>
                </div>
                <div class="col-md-6 text-right">
                    <button class="btn btn-info btn-sm" id="browse-history">${__('Browse History')}</button>
                    <button class="btn btn-secondary btn-sm" id="filter-history" style="margin-left: 5px;">${__('Filter History')}</button>
                </div>
            </div>
        </div>
    `);
    $(page.body).append($history_section);

    // SECTION 4: Results container and core functions
    let $results_container = $('<div class="results-container" style="margin-top: 20px;"></div>');
    $(page.body).append($results_container);

    // Core function to generate PDFs using statement forms backend
    function generatePDFs(filters) {
        var final_filters = {};
        for (let key in filters) {
            if (filters[key] && filters[key].get_value) {
                final_filters[key] = filters[key].get_value();
            }
        }

        final_filters.session_id = sessionId;
        validateMandatoryFilters(final_filters);

        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.supplier_statement_forms.supplier_statement_forms.queue_pdf_generation',
            args: {
                filters: final_filters
            },
            callback: function (r) {
                if (r.message.success) {
                    frappe.msgprint(__('PDF generation jobs queued successfully'));
                    saveFilters();

                    // Store current history ID for tracking
                    currentHistoryId = r.message.history_id;

                    // Load status based on history ID
                    loadPDFStatusByHistory(currentHistoryId);
                    startAutoRefresh();
                } else if (r.message.error) {
                    frappe.throw({
                        title: __("Error"),
                        indicator: "red",
                        message: __(r.message.error)
                    });
                }
            },
        });
    }

    // Validation function
    function validateMandatoryFilters(filters) {
        let error = [];
        if (!filters['company']) {
            error.push(__('Company'));
        }
        if (!filters['from_date']) {
            error.push(__('From Date'));
        }
        if (!filters['party_type']) {
            error.push(__('Party Type'));
        }
        if (error.length) {
            frappe.throw({
                title: __('Missing Filters'),
                message: __('Missing Filters') + '<br><ul><li>' + error.join('</li><li>') + '</ul>'
            });
        }
    }

    // Load PDF status by history ID (reuse from statement forms)
    function loadPDFStatusByHistory(historyId) {
        if (!historyId) return;

        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status',
            args: {
                history_id: historyId
            },
            callback: function (r) {
                if (r.message) {
                    displayPDFStatus(r.message, historyId);
                }
            },
        });
    }

    // Load PDF status (legacy support)
    function loadPDFStatus(filters) {
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status',
            args: {
                filters: filters
            },
            callback: function (r) {
                if (r.message) {
                    displayPDFStatus(r.message);
                }
            },
        });
    }

    // SECTION 5: Status display functions (reuse from statement forms)
    function displayPDFStatus(logs, historyId = null) {
        if (logs.length === 0) {
            $results_container.html('<div class="alert alert-info">No PDF generation logs found.</div>');
            return;
        }

        const totalJobs = logs.length;
        const completedJobs = logs.filter(log => log.status === 'Completed').length;
        const failedJobs = logs.filter(log => log.status === 'Failed').length;
        const processingJobs = logs.filter(log => log.status === 'Processing').length;
        const queuedJobs = logs.filter(log => log.status === 'Queued').length;
        
        // WhatsApp metrics
        const sentStatuses = ['Sent', 'Delivered', 'Read'];
        const whatsappNotCreated = logs.filter(log => (!log.whatsapp_status || log.whatsapp_status === 'Not Created')).length;
        const whatsappFailed = logs.filter(log => log.whatsapp_status === 'Failed').length;
        const whatsappSent = logs.filter(log => sentStatuses.includes(log.whatsapp_status)).length;
        const whatsappCreated = logs.filter(log => (log.whatsapp_message_id || (log.whatsapp_status && log.whatsapp_status !== 'Not Created'))).length;
        
        // Check if current user is Administrator
        const isAdmin = frappe.session && frappe.session.user === 'Administrator';

        // Find generation_time for 5-minute logic
        let showRetryQueuedFailedBtn = false;
        let generationTime = null;
        if (logs.length > 0 && logs[0].generation_time) {
            generationTime = logs[0].generation_time;
        } else if (logs.length > 0 && logs[0].history_generation_time) {
            generationTime = logs[0].history_generation_time;
        }
        if (generationTime && historyId) {
            const genTime = moment(generationTime);
            const nowTime = moment();
            if (nowTime.diff(genTime, 'minutes') >= 5) {
                showRetryQueuedFailedBtn = true;
            }
        }

        if (failedJobs > 0) {
            showRetryQueuedFailedBtn = true;
        }

        let html = `
            <div class="pdf-status-container">
                <div class="row">
                    <div class="col-md-6">
                        <div class="row" style="margin-bottom: 5px;">
                            <div class="col-md-12">
                                <h5 style="margin-bottom: 8px;">${__('PDF Generation Status')}</h5>
                                <div class="sf-summary-group">
                                    <span class="sf-pill sf-pill-default">${__('Total')}: ${totalJobs}</span>
                                    <span class="sf-pill sf-pill-success">${__('Completed')}: ${completedJobs}</span>
                                    <span class="sf-pill sf-pill-danger">${__('Failed')}: <span id="failed-count">${failedJobs}</span></span>
                                    <span class="sf-pill sf-pill-warning">${__('Processing')}: ${processingJobs}</span>
                                    <span class="sf-pill sf-pill-info">${__('Queued')}: ${queuedJobs}</span>
                                </div>
                            </div>
                            <div class="col-md-12">
                                <h5 style="margin-bottom: 8px;margin-top: 16px;">${__('WhatsApp Message Status')}</h5>
                                <div class="sf-summary-group">
                                    <span class="sf-pill sf-pill-default">${__('Not Created')}: ${whatsappNotCreated}</span>
                                    <span class="sf-pill sf-pill-warning">${__('Created')}: ${whatsappCreated}</span>
                                    <span class="sf-pill sf-pill-success">${__('Sent')}: ${whatsappSent}</span>
                                    <span class="sf-pill sf-pill-danger">${__('Failed')}: ${whatsappFailed}</span>
                                </div>
                                ${historyId ? `<br><small class="text-info">History ID: ${historyId}</small>` : ''}
                            </div>
                        </div>
                    </div>
                    <div class="col-md-6 text-right">
                        <button class="btn btn-sm sf-action-btn btn-info" id="show-report-info" style="margin:5px !important">${__('Report Info')}</button>
                        ${isAdmin ? `<button class="btn btn-sm sf-action-btn btn-secondary" id="cleanup-jobs" style="margin:5px !important">${__('Cleanup Stuck')}</button>` : ''}
                        ${isAdmin ? `<button class="btn btn-sm sf-action-btn btn-danger" id="cleanup-no-data" style="margin:5px !important">${__('Clean No Data')}</button>` : ''}
                        <button class="btn btn-sm sf-action-btn btn-primary" id="refresh-status" style="margin: 5px;">${__('Refresh')}</button>
                        <button class="btn btn-sm sf-action-btn btn-success" id="download-all" style="margin: 5px;">${__('Download All (ZIP)')}</button>
                        <button class="btn btn-sm sf-action-btn btn-warning" id="send-all-whatsapp" style="margin: 5px;">${__('Send All WhatsApp')}</button>
                        <button class="btn btn-sm sf-action-btn btn-outline-warning" id="retry-all-whatsapp" style="margin: 5px;">${__('Retry All WhatsApp')}</button>
                        ${(failedJobs > 0 && historyId) ? `<button class=\"btn btn-sm sf-action-btn btn-outline-danger\" id=\"retry-all-failed\" style=\"margin: 5px;\">${__('Retry All Failed')}</button>` : ''}
                        ${(showRetryQueuedFailedBtn && historyId) ? `<button class=\"btn btn-sm sf-action-btn btn-outline-danger\" id=\"retry-all-queued-failed\" style=\"margin: 5px;\">${__('Retry All Queued/Failed')}</button>` : ''}
                        ${historyId ? `<button class="btn btn-sm sf-action-btn btn-outline-info" id="view-history-details" style="margin: 5px;">${__('View History')}</button>` : ''}
                    </div>
                </div>

                <!-- Search Bar -->
                <div class="row" style="margin-bottom: 15px;">
                    <div class="col-md-4">
                        <div class="input-group">
                            <input type="text" class="form-control" id="party-search" placeholder="${__('Search by supplier name...')}" />
                            <div class="input-group-append">
                                <button class="btn btn-outline-secondary" type="button" id="clear-search">
                                    <i class="fa fa-times"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <select class="form-control" id="whatsapp-status-filter">
                            <option value="">${__('All WhatsApp Statuses')}</option>
                            <option value="Not Created">${__('Not Created')}</option>
                            <option value="Queued">${__('Queued')}</option>
                            <option value="Sent">${__('Sent')}</option>
                            <option value="Delivered">${__('Delivered')}</option>
                            <option value="Failed">${__('Failed')}</option>
                        </select>
                    </div>
                    <div class="col-md-5">
                        <small class="text-muted" id="search-results-info"></small>
                    </div>
                </div>
                
                <div class="table-responsive">
                    <table class="table table-bordered table-striped" id="pdf-status-table">
                        <thead>
                            <tr>
                                <th><input type="checkbox" id="select-all"></th>
                                <th>${__('Supplier Name')}</th>
                                <th>${__('Supplier ID')}</th>
                                <th>${__('Status')}</th>
                                <th>${__('WhatsApp')}</th>
                                <th>${__('Creation Time')}</th>
                                <th>${__('Actions')}</th>
                            </tr>
                        </thead>
                        <tbody id="pdf-status-tbody">
        `;

        logs.forEach(log => {
            let statusBadge = getStatusBadge(log.status);
            let whatsappIcon = getWhatsAppIcon(log.whatsapp_sent, log.whatsapp_status);
            let actions = getActionButtons(log);
            let creationTime = log.creation_time ? frappe.datetime.str_to_user(log.creation_time) : '';

            const partyNameSafe = (log.party_display_name || log.party_name || '').toString();
            const partyTypeSafe = (log.party_type || '').toString();
            const whatsappStatusSafe = (log.whatsapp_status || 'Not Created').toString();

            html += `
                <tr data-log-id="${log.name}" data-party-name="${partyNameSafe.toLowerCase()}" data-party-id="${(log.party_name || '').toString().toLowerCase()}" data-wa-status="${whatsappStatusSafe}">
                    <td><input type="checkbox" class="row-checkbox" value="${log.name}" ${log.status === 'Completed' ? '' : 'disabled'}></td>
                    <td class="party-name-cell">
                        <strong>${partyNameSafe || __('Unknown')}</strong>
                        <br><small class="text-muted">${partyTypeSafe}</small>
                    </td>
                    <td><small>${log.party_name || ''}</small></td>
                    <td>${statusBadge}</td>
                    <td>${whatsappIcon}</td>
                    <td><small>${creationTime}</small></td>
                    <td>${actions}</td>
                </tr>
            `;
        });

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;

        // Preserve selection across refresh
        const previouslySelected = new Set($('.row-checkbox:checked').map(function () { return this.value; }).get());
        const previousPartySearch = $('#party-search').val() || '';
        const previousWAFilter = $('#whatsapp-status-filter').val() || '';

        $results_container.html(html);

        // Restore selections
        $('#pdf-status-tbody .row-checkbox').each(function () {
            if (previouslySelected.has(this.value)) {
                $(this).prop('checked', true);
            }
        });
        updateSelectAllCheckbox();

        // Initialize search functionality
        initializePartySearch(logs);

        // Bind WhatsApp status filter
        $('#whatsapp-status-filter').on('change', function () {
            const currentSearch = $('#party-search').val().toLowerCase().trim();
            filterTableByPartyNameAndWhatsAppStatus(currentSearch, logs);
        });

        bindStatusEvents(historyId);

        // Reapply filters
        if (previousWAFilter) {
            $('#whatsapp-status-filter').val(previousWAFilter).trigger('change');
        }
        if (previousPartySearch) {
            $('#party-search').val(previousPartySearch);
            const searchTerm = previousPartySearch.toLowerCase().trim();
            filterTableByPartyNameAndWhatsAppStatus(searchTerm, logs);
        }
    }

    // Helper functions (same as statement forms)
    function updateSelectAllCheckbox() {
        const $tbody = $('#pdf-status-tbody');
        const $visibleCheckboxes = $tbody.find('tr:visible .row-checkbox:not(:disabled)');
        const $checkedBoxes = $tbody.find('tr:visible .row-checkbox:checked');
        const $selectAll = $('#select-all');
        if ($visibleCheckboxes.length === 0) {
            $selectAll.prop('indeterminate', false).prop('checked', false);
        } else if ($checkedBoxes.length === $visibleCheckboxes.length) {
            $selectAll.prop('indeterminate', false).prop('checked', true);
        } else if ($checkedBoxes.length > 0) {
            $selectAll.prop('indeterminate', true);
        } else {
            $selectAll.prop('indeterminate', false).prop('checked', false);
        }
    }

    function filterTableByPartyNameAndWhatsAppStatus(searchTerm, logs) {
        const $tbody = $('#pdf-status-tbody');
        const $rows = $tbody.find('tr');
        const $resultsInfo = $('#search-results-info');
        const waFilter = $('#whatsapp-status-filter').val();
        let visibleCount = 0;
        const totalCount = Array.isArray(logs) ? logs.length : $rows.length;

        $rows.each(function () {
            const $row = $(this);
            const partyName = ($row.data('party-name') || '').toString();
            const partyId = ($row.data('party-id') || '').toString();
            const waStatus = ($row.data('wa-status') || 'Not Created').toString();

            const matchesNameOrId = !searchTerm || partyName.includes(searchTerm) || partyId.includes(searchTerm);
            const matchesWA = !waFilter || waStatus === waFilter;

            if (matchesNameOrId && matchesWA) {
                $row.show();
                visibleCount++;
            } else {
                $row.hide();
                $row.find('.row-checkbox').prop('checked', false);
            }
        });

        if (!searchTerm && !waFilter) {
            $resultsInfo.text('');
        } else {
            $resultsInfo.text(`${__('Showing')} ${visibleCount} ${__('of')} ${totalCount} ${__('suppliers')}`);
        }

        updateSelectAllCheckbox();
    }

    function initializePartySearch(allLogs) {
        const $searchInput = $('#party-search');
        const $clearButton = $('#clear-search');
        const $tbody = $('#pdf-status-tbody');
        const $resultsInfo = $('#search-results-info');

        let searchTimeout;

        $searchInput.on('input', function () {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                const searchTerm = $(this).val().toLowerCase().trim();
                filterTableByPartyNameAndWhatsAppStatus(searchTerm, allLogs);
            }, 300);
        });

        $clearButton.on('click', function () {
            $searchInput.val('');
            filterTableByPartyNameAndWhatsAppStatus('', allLogs);
            $searchInput.focus();
        });

        $searchInput.on('keypress', function (e) {
            if (e.which === 13) {
                const searchTerm = $(this).val().toLowerCase().trim();
                filterTableByPartyNameAndWhatsAppStatus(searchTerm, allLogs);
            }
        });

        $('#select-all').off('change').on('change', function () {
            const isChecked = $(this).prop('checked');
            $tbody.find('tr:visible .row-checkbox:not(:disabled)').prop('checked', isChecked);
        });

        $tbody.on('change', '.row-checkbox', function () {
            updateSelectAllCheckbox();
        });
    }

    function getStatusBadge(status) {
        const s = (status || '').toLowerCase();
        let cls = 'sf-pill-default';
        if (s === 'completed' || s === 'finished') cls = 'sf-pill-success';
        else if (s === 'failed') cls = 'sf-pill-danger';
        else if (s === 'processing') cls = 'sf-pill-warning';
        else if (s === 'queued') cls = 'sf-pill-info';
        return `<span class="sf-pill ${cls}">${__(status)}</span>`;
    }

    function getWhatsAppIcon(sent, whatsapp_status) {
        if (!sent || whatsapp_status === 'Not Created') {
            return '<i class="fa fa-times-circle text-muted" title="WhatsApp Not Created"></i>';
        }

        switch (whatsapp_status) {
            case 'Queued':
                return '<i class="fa fa-clock-o text-warning" title="WhatsApp Queued"></i>';
            case 'Sent':
                return '<i class="fa fa-check text-primary" title="WhatsApp Sent"></i>';
            case 'Delivered':
                return '<i class="fa fa-check-circle text-success" title="WhatsApp Delivered"></i>';
            case 'Failed':
                return '<i class="fa fa-times-circle text-danger" title="WhatsApp Failed"></i>';
            default:
                return '<i class="fa fa-question-circle text-muted" title="WhatsApp Status Unknown"></i>';
        }
    }

    function getActionButtons(log) {
        let buttons = '';

        if (log.status === 'Completed' && log.pdf_file) {
            buttons += `<button class="btn btn-sm sf-action-btn btn-info download-pdf" data-url="${log.pdf_file}">${__('Download')}</button> `;
            const label = (!log.whatsapp_sent || log.whatsapp_status === 'Not Created') ? __('Send WhatsApp') : __('Re-send WhatsApp');
            buttons += `<button class="btn btn-sm btn-primary send-whatsapp" data-log-id="${log.name}">${label}</button>`;
        } else if (log.status === 'Failed') {
            buttons += `<button class="btn btn-sm btn-warning retry-pdf" data-log-id="${log.name}">${__('Retry')}</button> `;
            buttons += `<small class="text-danger">${log.error_message || 'Generation failed'}</small>`;
        } else if (log.status === 'Processing') {
            buttons += `<span class="text-info">${__('Processing...')}</span>`;
        } else {
            buttons += `<span class="text-muted">${__('Queued...')}</span>`;
        }

        return buttons;
    }

    // SECTION 6: Event handling and history functions
    function bindStatusEvents(historyId = null) {
        $('#show-report-info').on('click', function () {
            showReportInfo();
        });

        $('#refresh-status').on('click', function () {
            if (currentHistoryId) {
                loadPDFStatusByHistory(currentHistoryId);
            } else {
                var final_filters = {};
                for (let key in page.fields_dict) {
                    if (page.fields_dict[key] && page.fields_dict[key].get_value) {
                        final_filters[key] = page.fields_dict[key].get_value();
                    }
                }
                loadPDFStatus(final_filters);
            }
        });

        $('#download-all').on('click', function () {
            let selectedIds = $('.row-checkbox:checked').map(function () {
                return this.value;
            }).get();

            if (selectedIds.length === 0) {
                frappe.msgprint(__('Please select at least one completed PDF'));
                return;
            }
            downloadBulkPDFs(selectedIds);
        });

        $('#send-all-whatsapp').on('click', function () {
            if (historyId) {
                frappe.confirm(
                    __('Are you sure you want to send WhatsApp messages to all completed suppliers in this generation?'),
                    () => sendAllWhatsAppByHistory(historyId),
                    () => frappe.msgprint(__('Cancelled'))
                );
            } else {
                let selectedIds = $('.row-checkbox:checked').map(function () {
                    return this.value;
                }).get();

                if (selectedIds.length === 0) {
                    frappe.msgprint(__('Please select at least one completed PDF'));
                    return;
                }

                frappe.confirm(
                    __(`Are you sure you want to send WhatsApp messages to ${selectedIds.length} selected suppliers?`),
                    () => sendBulkWhatsApp(selectedIds),
                    () => frappe.msgprint(__('Cancelled'))
                );
            }
        });

        $('#view-history-details').on('click', function () {
            if (historyId) {
                viewHistoryDetails(historyId);
            }
        });

        $('#cleanup-jobs').on('click', function () {
            frappe.confirm(
                __('Are you sure you want to cleanup stuck jobs? This will mark jobs processing for more than 10 minutes as failed.'),
                () => {
                    frappe.call({
                        method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.cleanup_failed_logs',
                        callback: function (r) {
                            if (r.message.success) {
                                frappe.msgprint(r.message.success);
                                if (currentHistoryId) {
                                    loadPDFStatusByHistory(currentHistoryId);
                                }
                            } else {
                                frappe.msgprint(__('Cleanup failed: ') + r.message.error);
                            }
                        }
                    });
                }
            );
        });

        $('#cleanup-no-data').on('click', function () {
            frappe.confirm(
                __('Are you sure you want to cleanup jobs with no data? This will mark jobs as failed if no data is found for the supplier.'),
                () => {
                    frappe.call({
                        method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.cleanup_logs_with_no_data',
                        callback: function (r) {
                            if (r.message.success) {
                                frappe.msgprint(r.message.message);
                                if (currentHistoryId) {
                                    loadPDFStatusByHistory(currentHistoryId);
                                }
                            } else {
                                frappe.msgprint(__('Cleanup failed: ') + r.message.error);
                            }
                        }
                    });
                }
            );
        });

        $('.download-pdf').on('click', function () {
            let url = $(this).data('url');
            window.open(url, '_blank');
        });

        $('.send-whatsapp').on('click', function () {
            let logId = $(this).data('log-id');
            queueSingleWhatsApp(logId, historyId || currentHistoryId);
        });

        $('.retry-pdf').on('click', function () {
            let logId = $(this).data('log-id');
            frappe.confirm(
                __('Are you sure you want to retry PDF generation for this supplier?'),
                () => {
                    frappe.call({
                        method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.retry_failed_pdf',
                        args: { log_id: logId },
                        callback: function (r) {
                            if (r.message.success) {
                                frappe.msgprint(r.message.success);
                                if (currentHistoryId) {
                                    loadPDFStatusByHistory(currentHistoryId);
                                    startAutoRefresh();
                                }
                            } else {
                                frappe.msgprint(__('Retry failed: ') + r.message.error);
                            }
                        }
                    });
                }
            );
        });

        // Bind retry buttons using statement forms methods
        if (historyId) {
            $('#retry-all-failed').on('click', function () {
                frappe.confirm(
                    __('Are you sure you want to retry all failed jobs?'),
                    () => {
                        frappe.call({
                            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.retry_all_failed_pdfs',
                            args: { history_id: historyId },
                            callback: function (r) {
                                if (r.message && r.message.success) {
                                    frappe.show_alert({ message: r.message.success, indicator: 'green' });
                                    loadPDFStatusByHistory(historyId);
                                    startAutoRefresh();
                                } else {
                                    frappe.msgprint(__('Retry all failed error: ') + (r.message && r.message.error ? r.message.error : 'Unknown error'));
                                }
                            }
                        });
                    },
                    () => frappe.msgprint(__('Cancelled'))
                );
            });

            $('#retry-all-queued-failed').on('click', function () {
                frappe.confirm(
                    __('Are you sure you want to retry all queued and failed jobs?'),
                    () => {
                        frappe.call({
                            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.retry_all_queued_and_failed_pdf_jobs_for_history',
                            args: { history_id: historyId },
                            callback: function (r) {
                                if (r.message && r.message.success) {
                                    frappe.show_alert({ message: r.message.success, indicator: 'green' });
                                    loadPDFStatusByHistory(historyId);
                                    startAutoRefresh();
                                } else {
                                    frappe.msgprint(__('Retry all queued/failed error: ') + (r.message && r.message.error ? r.message.error : 'Unknown error'));
                                }
                            }
                        });
                    },
                    () => frappe.msgprint(__('Cancelled'))
                );
            });
        }

        $('#retry-all-whatsapp').on('click', function () {
            if (!historyId && !currentHistoryId) {
                frappe.msgprint(__('No history loaded'));
                return;
            }
            const hid = historyId || currentHistoryId;
            frappe.confirm(
                __('Queue WhatsApp for all completed items with Not Created/Failed status?'),
                () => {
                    frappe.call({
                        method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.queue_all_whatsapp',
                        args: { history_id: hid, retry_failed: 1 },
                        callback: function (r) {
                            if (r.message && r.message.success) {
                                frappe.show_alert({ message: r.message.success, indicator: 'green' });
                                loadPDFStatusByHistory(hid);
                                startAutoRefresh();
                            } else {
                                frappe.msgprint(__('Failed to queue WhatsApp: ') + (r.message && r.message.error ? r.message.error : 'Unknown error'));
                            }
                        }
                    });
                },
                () => { }
            );
        });
    }

    // History browsing functions (reuse from statement forms)
    $('#browse-history').on('click', function () {
        browseHistory();
    });

    $('#filter-history').on('click', function () {
        filterHistory();
    });

    function browseHistory() {
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_statement_generation_history',
            args: { party_type: 'Supplier' }, // Filter for supplier only
            callback: function (r) {
                if (r.message) {
                    showHistoryDialog(r.message);
                }
            },
        });
    }

    function filterHistory() {
        let dialog = new frappe.ui.Dialog({
            title: __('Filter Supplier Generation History'),
            fields: [
                {
                    label: 'From Date',
                    fieldname: 'from_date',
                    fieldtype: 'Date'
                },
                {
                    label: 'To Date',
                    fieldname: 'to_date',
                    fieldtype: 'Date'
                },
                {
                    label: 'Supplier Name',
                    fieldname: 'party_name',
                    fieldtype: 'Data',
                    description: 'Search for specific supplier name'
                },
                {
                    label: 'Company',
                    fieldname: 'company',
                    fieldtype: 'Link',
                    options: 'Company'
                }
            ],
            primary_action_label: __('Search'),
            primary_action(values) {
                values.party_type = 'Supplier'; // Force supplier filter
                frappe.call({
                    method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_statement_generation_history',
                    args: values,
                    callback: function (r) {
                        if (r.message) {
                            showHistoryDialog(r.message);
                        }
                    },
                });
                dialog.hide();
            }
        });
        dialog.show();
    }

    function showHistoryDialog(histories) {
        let html = `
            <div class="history-browser">
                <div class="row" style="margin-bottom: 15px;">
                    <div class="col-md-6">
                        <div class="input-group">
                            <input type="text" class="form-control" id="history-search" placeholder="${__('Search histories by company, supplier, or user...')}" />
                            <div class="input-group-append">
                                <button class="btn btn-outline-secondary" type="button" id="clear-history-search">
                                    <i class="fa fa-times"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-6">
                        <small class="text-muted" id="history-search-results-info"></small>
                    </div>
                </div>
                
                <div class="table-responsive">
                    <table class="table table-bordered table-striped" id="history-table">
                        <thead>
                            <tr>
                                <th>${__('Generation Time')}</th>
                                <th>${__('Date Range')}</th>
                                <th>${__('Total Suppliers')}</th>
                                <th>${__('Completed')}</th>
                                <th>${__('Failed')}</th>
                                <th>${__('WhatsApp Sent')}</th>
                                <th>${__('Created')}</th>
                                <th>${__('Created By')}</th>
                                <th>${__('Actions')}</th>
                            </tr>
                        </thead>
                        <tbody id="history-tbody">
        `;

        histories.forEach(history => {
            let generationTime = frappe.datetime.str_to_user(history.generation_time);
            let dateRange = `${history.from_date} to ${history.to_date || 'N/A'}`;
            let searchData = `${history.company} ${history.party_type} ${history.created_by_user}`.toLowerCase();

            html += `
                <tr data-search-text="${searchData}">
                    <td><small>${generationTime}</small></td>
                    <td><small>${dateRange}</small></td>
                    <td>${history.total_parties}</td>
                    <td><span class="badge badge-success">${history.completed_count}</span></td>
                    <td><span class="badge badge-danger">${history.failed_count}</span></td>
                    <td><span class="badge badge-info">${history.whatsapp_sent_count}</span></td>
                    <td><span class="badge badge-primary">${history.whatsapp_created_count}</span></td>
                    <td><small>${history.created_by_user}</small></td>
                    <td>
                        <button class="btn btn-sm btn-primary view-history" data-history-id="${history.name}">${__('View')}</button>
                        <button class="btn btn-sm btn-success load-history" data-history-id="${history.name}">${__('Load')}</button>
                    </td>
                </tr>
            `;
        });

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;

        let dialog = new frappe.ui.Dialog({
            title: __('Supplier Statement Generation History'),
            size: 'extra-large',
            fields: [
                {
                    fieldname: 'history_table',
                    fieldtype: 'HTML',
                    options: html
                }
            ]
        });

        dialog.show();

        // Initialize history search (same pattern as statement forms)
        const $historySearch = dialog.$wrapper.find('#history-search');
        const $clearHistorySearch = dialog.$wrapper.find('#clear-history-search');
        const $historyTbody = dialog.$wrapper.find('#history-tbody');
        const $historyResultsInfo = dialog.$wrapper.find('#history-search-results-info');

        let historySearchTimeout;

        $historySearch.on('input', function () {
            clearTimeout(historySearchTimeout);
            historySearchTimeout = setTimeout(() => {
                const searchTerm = $(this).val().toLowerCase().trim();
                filterHistoryTable(searchTerm);
            }, 300);
        });

        $clearHistorySearch.on('click', function () {
            $historySearch.val('');
            filterHistoryTable('');
            $historySearch.focus();
        });

        function filterHistoryTable(searchTerm) {
            const $rows = $historyTbody.find('tr');
            let visibleCount = 0;

            if (!searchTerm) {
                $rows.show();
                visibleCount = histories.length;
                $historyResultsInfo.text('');
            } else {
                $rows.each(function () {
                    const $row = $(this);
                    const searchText = $row.data('search-text') || '';

                    if (searchText.includes(searchTerm)) {
                        $row.show();
                        visibleCount++;
                    } else {
                        $row.hide();
                    }
                });

                $historyResultsInfo.text(`${__('Showing')} ${visibleCount} ${__('of')} ${histories.length} ${__('records')}`);
            }
        }

        // Bind events for history dialog
        dialog.$wrapper.find('.view-history').on('click', function () {
            let historyId = $(this).data('history-id');
            viewHistoryDetails(historyId);
        });

        dialog.$wrapper.find('.load-history').on('click', function () {
            let historyId = $(this).data('history-id');
            currentHistoryId = historyId;
            loadPDFStatusByHistory(historyId);
            dialog.hide();
            frappe.msgprint(__('Loaded supplier history generation. You can now view status and send WhatsApp messages.'));
        });
    }

    function viewHistoryDetails(historyId) {
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_history_details',
            args: { history_id: historyId },
            callback: function (r) {
                if (r.message && r.message.history) {
                    showHistoryDetailsDialog(r.message.history, r.message.logs);
                }
            },
        });
    }

    function showHistoryDetailsDialog(history, logs) {
        let html = `
            <div class="history-details">
                <div class="row">
                    <div class="col-md-6">
                        <h5>${__('Generation Details')}</h5>
                        <table class="table table-bordered">
                            <tr><td><strong>Company:</strong></td><td>${history.company}</td></tr>
                            <tr><td><strong>Party Type:</strong></td><td>${history.party_type}</td></tr>
                            <tr><td><strong>Date Range:</strong></td><td>${history.from_date} to ${history.to_date || 'N/A'}</td></tr>
                            <tr><td><strong>Created By:</strong></td><td>${history.created_by_user}</td></tr>
                            <tr><td><strong>Generation Time:</strong></td><td>${frappe.datetime.str_to_user(history.generation_time)}</td></tr>
                        </table>
                    </div>
                    <div class="col-md-6">
                        <h5>${__('Summary')}</h5>
                        <table class="table table-bordered">
                            <tr><td><strong>Total Suppliers:</strong></td><td>${history.total_parties}</td></tr>
                            <tr><td><strong>Completed:</strong></td><td><span class="badge badge-success">${history.completed_count}</span></td></tr>
                            <tr><td><strong>Failed:</strong></td><td><span class="badge badge-danger">${history.failed_count}</span></td></tr>
                            <tr><td><strong>WhatsApp Sent:</strong></td><td><span class="badge badge-info">${history.whatsapp_sent_count}</span></td></tr>
                            <tr><td><strong>WhatsApp Created:</strong></td><td><span class="badge badge-primary">${history.whatsapp_created_count}</span></td></tr>
                        </table>
                    </div>
                </div>
                
                <h5 style="margin-top: 20px;">${__('Supplier Details')}</h5>
                
                <div class="row" style="margin-bottom: 15px;">
                    <div class="col-md-4">
                        <div class="input-group">
                            <input type="text" class="form-control" id="party-details-search" placeholder="${__('Search suppliers...')}" />
                            <div class="input-group-append">
                                <button class="btn btn-outline-secondary" type="button" id="clear-party-details-search">
                                    <i class="fa fa-times"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-8">
                        <small class="text-muted" id="party-details-search-info"></small>
                    </div>
                </div>
                
                <div class="table-responsive">
                    <table class="table table-bordered table-striped" id="party-details-table">
                        <thead>
                            <tr>
                                <th>${__('Supplier Name')}</th>
                                <th>${__('PDF Status')}</th>
                                <th>${__('WhatsApp Status')}</th>
                                <th>${__('Error Message')}</th>
                                <th>${__('Actions')}</th>
                            </tr>
                        </thead>
                        <tbody id="party-details-tbody">
        `;

        logs.forEach(log => {
            let statusBadge = getStatusBadge(log.status);
            let whatsappStatus = log.whatsapp_status || 'Not Created';
            let errorMessage = log.error_message ? log.error_message.substring(0, 100) + '...' : '';

            html += `
                <tr data-party-search="${log.party_name.toLowerCase()}">
                    <td class="party-name-cell">
                        <strong>${log.party_name}</strong>
                        <br><small class="text-muted">${history.party_type}</small>
                    </td>
                    <td>${statusBadge}</td>
                    <td><small>${whatsappStatus}</small></td>
                    <td><small class="text-muted">${errorMessage}</small></td>
                    <td>
                        ${log.pdf_file ? `<button class="btn btn-sm btn-info" onclick="window.open('${log.pdf_file}', '_blank')">${__('Download')}</button>` : ''}
                        ${log.status === 'Completed' && log.whatsapp_status === 'Not Created' ? `<button class="btn btn-sm btn-primary send-whatsapp-detail" data-log-id="${log.pdf_generator_log}">${__('Send WhatsApp')}</button>` : ''}
                    </td>
                </tr>
            `;
        });

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;

        let dialog = new frappe.ui.Dialog({
            title: __('History Details - ') + history.name,
            size: 'extra-large',
            fields: [
                {
                    fieldname: 'details_html',
                    fieldtype: 'HTML',
                    options: html
                }
            ]
        });

        dialog.show();

        // Initialize party details search
        const $partyDetailsSearch = dialog.$wrapper.find('#party-details-search');
        const $clearPartyDetailsSearch = dialog.$wrapper.find('#clear-party-details-search');
        const $partyDetailsTbody = dialog.$wrapper.find('#party-details-tbody');
        const $partyDetailsSearchInfo = dialog.$wrapper.find('#party-details-search-info');

        let partyDetailsSearchTimeout;

        $partyDetailsSearch.on('input', function () {
            clearTimeout(partyDetailsSearchTimeout);
            partyDetailsSearchTimeout = setTimeout(() => {
                const searchTerm = $(this).val().toLowerCase().trim();
                filterPartyDetailsTable(searchTerm);
            }, 300);
        });

        $clearPartyDetailsSearch.on('click', function () {
            $partyDetailsSearch.val('');
            filterPartyDetailsTable('');
            $partyDetailsSearch.focus();
        });

        function filterPartyDetailsTable(searchTerm) {
            const $rows = $partyDetailsTbody.find('tr');
            let visibleCount = 0;

            if (!searchTerm) {
                $rows.show();
                visibleCount = logs.length;
                $partyDetailsSearchInfo.text('');
            } else {
                $rows.each(function () {
                    const $row = $(this);
                    const partySearch = $row.data('party-search') || '';

                    if (partySearch.includes(searchTerm)) {
                        $row.show();
                        visibleCount++;
                    } else {
                        $row.hide();
                    }
                });

                $partyDetailsSearchInfo.text(`${__('Showing')} ${visibleCount} ${__('of')} ${logs.length} ${__('suppliers')}`);
            }
        }

        // Bind WhatsApp send events
        dialog.$wrapper.find('.send-whatsapp-detail').on('click', function () {
            let logId = $(this).data('log-id');
            queueSingleWhatsApp(logId, history.name);
        });
    }

    // SECTION 7: Action functions (reuse from statement forms)
    function downloadBulkPDFs(logIds) {
        frappe.dom.freeze(__('Creating ZIP file...'));

        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.download_bulk_pdfs',
            args: {
                log_ids: logIds
            },
            callback: function (r) {
                frappe.dom.unfreeze();
                if (r.message && r.message.file_url) {
                    window.open(r.message.file_url, '_blank');
                } else {
                    frappe.msgprint(__('Failed to create ZIP file'));
                }
            },
        });
    }

    function queueSingleWhatsApp(logId, historyId) {
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.queue_whatsapp_for_party',
            args: { log_id: logId },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: __('Queued WhatsApp sending'), indicator: 'green' });
                    if (historyId) {
                        loadPDFStatusByHistory(historyId);
                        startAutoRefresh();
                    }
                } else {
                    frappe.msgprint(__('Failed to queue WhatsApp: ') + (r.message && r.message.error ? r.message.error : 'Unknown error'));
                }
            }
        });
    }

    function sendAllWhatsAppByHistory(historyId) {
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.queue_all_whatsapp',
            args: { history_id: historyId },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: r.message.success, indicator: 'green' });
                    loadPDFStatusByHistory(historyId);
                    startAutoRefresh();
                } else {
                    frappe.msgprint(__('Failed to queue WhatsApp messages: ') + (r.message && r.message.error ? r.message.error : 'Unknown error'));
                }
            }
        });
    }

    function sendBulkWhatsApp(logIds) {
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.queue_all_whatsapp',
            args: { log_ids: logIds },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: r.message.success, indicator: 'green' });
                    if (currentHistoryId) {
                        loadPDFStatusByHistory(currentHistoryId);
                        startAutoRefresh();
                    }
                } else {
                    frappe.msgprint(__('Failed to queue WhatsApp messages: ') + (r.message && r.message.error ? r.message.error : 'Unknown error'));
                }
            }
        });
    }

    function startAutoRefresh() {
        if (refreshInterval) {
            clearInterval(refreshInterval);
        }

        let refreshCount = 0;
        const maxRefreshes = 60;

        refreshInterval = setInterval(() => {
            refreshCount++;

            if (currentHistoryId) {
                loadPDFStatusByHistory(currentHistoryId);
            }

            if (refreshCount >= maxRefreshes) {
                clearInterval(refreshInterval);
                refreshInterval = null;
                frappe.show_alert({
                    message: __('Auto-refresh stopped. Click Refresh to update manually.'),
                    indicator: 'blue'
                });
            }
        }, 5000);

        frappe.show_alert({
            message: __('Auto-refresh enabled. Updates every 5 seconds.'),
            indicator: 'green'
        });
    }

    function showReportInfo() {
        const currentFilters = {};
        for (let key in page.fields_dict) {
            if (page.fields_dict[key] && page.fields_dict[key].get_value && page.fields_dict[key].get_value()) {
                currentFilters[key] = page.fields_dict[key].get_value();
            }
        }

        let info = `
            <div class="report-info">
                <h5>${__('Current Supplier Report Parameters')}</h5>
                <table class="table table-bordered">
                    <tr><td><strong>Company:</strong></td><td>${currentFilters.company || 'Not Set'}</td></tr>
                    <tr><td><strong>Party Type:</strong></td><td>Supplier (Fixed)</td></tr>
                    <tr><td><strong>Supplier Group:</strong></td><td>${currentFilters.party_group || 'All Groups'}</td></tr>
                    <tr><td><strong>Supplier:</strong></td><td>${currentFilters.party || 'All Suppliers'}</td></tr>
                    <tr><td><strong>From Date:</strong></td><td>${currentFilters.from_date || 'Not Set'}</td></tr>
                    <tr><td><strong>To Date:</strong></td><td>${currentFilters.to_date || 'Not Set'}</td></tr>
                    <tr><td><strong>Consider Drafts:</strong></td><td>${currentFilters.consider_draft ? 'Yes' : 'No'}</td></tr>
                    <tr><td><strong>Consider Draft Payments:</strong></td><td>${currentFilters.consider_draft_payments ? 'Yes' : 'No'}</td></tr>
                    <tr><td><strong>Neglect Items:</strong></td><td>${currentFilters.neglect_items ? 'Yes' : 'No'}</td></tr>
                    <tr><td><strong>Calculate Opening Balance with Totals:</strong></td><td>${currentFilters.calculate_opening_balance_with_totals ? 'Yes' : 'No'}</td></tr>
                </table>
                ${currentHistoryId ? `<p><strong>Current History ID:</strong> ${currentHistoryId}</p>` : ''}
                <small class="text-muted">
                    <strong>Note:</strong> These parameters are automatically saved and will be restored when you return to this page.
                </small>
            </div>
        `;

        frappe.msgprint({
            title: __('Supplier Report Information'),
            message: info,
            wide: true
        });
    }

    // SECTION 8: Set page buttons and initialization
    let $generateBtn = page.set_primary_action(__('Generate PDFs'), () => {
        generatePDFs(page.fields_dict);
    });

    let $refreshBtn = page.set_secondary_action(__('Refresh Status'), () => {
        if (currentHistoryId) {
            loadPDFStatusByHistory(currentHistoryId);
        } else {
            var final_filters = {};
            for (let key in page.fields_dict) {
                if (page.fields_dict[key] && page.fields_dict[key].get_value && page.fields_dict[key].get_value()) {
                    final_filters[key] = page.fields_dict[key].get_value();
                }
            }
            if (final_filters.company && final_filters.party_type) {
                loadPDFStatus(final_filters);
            }
        }
    });

    // Load existing status on page load
    setTimeout(() => {
        var final_filters = {};
        for (let key in page.fields_dict) {
            if (page.fields_dict[key] && page.fields_dict[key].get_value && page.fields_dict[key].get_value()) {
                final_filters[key] = page.fields_dict[key].get_value();
            }
        }
        if (final_filters.company && final_filters.party_type) {
            loadPDFStatus(final_filters);
            // Check if there are any active jobs and start auto-refresh
            frappe.call({
                method: 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status',
                args: { filters: final_filters },
                callback: function (r) {
                    if (r.message && r.message.some(log => log.status === 'Queued' || log.status === 'Processing')) {
                        startAutoRefresh();
                    }
                }
            });
        }
    }, 1500);

    // Cleanup on page unload
    $(window).on('beforeunload', function () {
        if (refreshInterval) {
            clearInterval(refreshInterval);
        }
    });

    // Add CSS for status-summary-badges and custom button styles (same as statement forms)
    if (!$('style#ssf-styles').length) {
        $('<style id="ssf-styles">').prop('type', 'text/css').html(`
            .status-summary-badges .badge {
                margin-right: 6px;
                font-size: 13px;
                padding: 6px 10px;
                border-radius: 4px;
                font-weight: 600;
            }
            .badge-secondary { background-color: #d1d8dd; color: #1f272e; }
            .badge-success { background-color: #21ba45; color: #fff; }
            .badge-danger { background-color: #ff5858; color: #fff; }
            .badge-warning { background-color: #ffa00a; color: #1f272e; }
            .badge-info { background-color: #5e64ff; color: #fff; }
            .badge-primary { background-color: #2490ef; color: #fff; }
            .sf-action-btn {
                background-color: #ffffff !important;
                color: #36414c !important;
                border: 1px solid #d1d8dd !important;
                box-shadow: none !important;
            }
            .sf-action-btn:hover {
                background-color: #f7fafc !important;
                border-color: #b8c2cc !important;
                color: #1f272e !important;
            }
        `).appendTo('head');
    }

    // Inject pill styles (same as statement forms)
    if (!$('style#ssf-pill-styles').length) {
        $('<style id="ssf-pill-styles">').prop('type', 'text/css').html(`
            .sf-pill {
                display: inline-block;
                padding: 3px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 600;
                line-height: 1.4;
                border: 1px solid transparent;
            }
            .sf-pill-success { color: #2e7d32; background-color: #e7f6e7; border-color: #cfeccc; }
            .sf-pill-danger { color: #c62828; background-color: #ffe5e5; border-color: #ffcccc; }
            .sf-pill-warning { color: #a15c00; background-color: #fff3e0; border-color: #ffe0b2; }
            .sf-pill-info { color: #3f51b5; background-color: #e8ebff; border-color: #d2d7ff; }
            .sf-pill-default { color: #5e6b76; background-color: #f5f7fa; border-color: #e4e7eb; }
        `).appendTo('head');
    }

}; // End of frappe.pages['supplier-statement-forms'].on_page_load
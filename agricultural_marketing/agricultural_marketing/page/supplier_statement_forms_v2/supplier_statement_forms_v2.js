// Supplier Statement Forms V2 -- preview-first UI over the unified-ledger layout.
// The old page (supplier-statement-forms) is untouched; this page has its own
// backend module and its own generation history.

frappe.pages['supplier-statement-forms-v2'].on_page_load = function (wrapper) {
    const METHOD = 'agricultural_marketing.agricultural_marketing.page.supplier_statement_forms_v2.supplier_statement_forms_v2.';
    const SF = 'agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.';
    const PARTY_QUERY = 'agricultural_marketing.queries.party_search';

    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Supplier Statement Forms V2'),
        single_column: true
    });

    injectStyles();

    const sessionId = 'ssfv2_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);

    let currentHistoryId = null;
    let refreshInterval = null;
    let lastWhatsAppJobId = null;
    let lastWhatsAppJobName = null;
    let previewParties = [];
    let previewIndex = 0;
    let previewZoom = 1;
    let lastPreviewHtml = '';

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------

    function getSavedFilters() {
        try {
            return JSON.parse(localStorage.getItem('supplier_statement_forms_v2_filters') || '{}');
        } catch (e) {
            return {};
        }
    }

    function saveFilters() {
        const stored = {};
        for (let key in page.fields_dict) {
            if (key === 'from_date' || key === 'to_date') continue;
            if (page.fields_dict[key] && page.fields_dict[key].get_value) {
                stored[key] = page.fields_dict[key].get_value();
            }
        }
        localStorage.setItem('supplier_statement_forms_v2_filters', JSON.stringify(stored));
    }

    const saved = getSavedFilters();

    function addField(opts, cls) {
        const field = page.add_field(Object.assign({ change: saveFilters }, opts));
        field.$wrapper.removeClass('col-md-2').addClass(cls);
        return field;
    }

    const company = addField({
        label: __('Company'), fieldtype: 'Link', fieldname: 'company', options: 'Company', reqd: 1,
        default: saved.company || frappe.defaults.get_default('company')
    }, 'col-md-3');

    const fromDate = addField({
        label: __('From Date'), fieldtype: 'Date', fieldname: 'from_date', reqd: 1,
        default: frappe.datetime.get_today()
    }, 'col-md-2');

    const toDate = addField({
        label: __('To Date'), fieldtype: 'Date', fieldname: 'to_date',
        default: frappe.datetime.get_today()
    }, 'col-md-2');

    const partyTypeField = page.add_field({
        label: __('Party Type'), fieldtype: 'Link', fieldname: 'party_type', options: 'Party Type',
        default: 'Supplier', reqd: 1, read_only: 1
    });
    partyTypeField.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    const supplierGroupField = addField({
        label: __('Supplier Group'), fieldtype: 'Link', fieldname: 'party_group', options: 'Supplier Group'
    }, 'col-md-2');

    const supplierField = addField({
        label: __('Supplier'), fieldtype: 'Link', fieldname: 'party', options: 'Supplier',
        get_query: function () {
            const f = {};
            if (supplierGroupField.get_value()) f['supplier_group'] = supplierGroupField.get_value();
            // Shared query: matches the code or any words of the name, and
            // ranks code hits first so 0001 wins when you type 1.
            return { query: PARTY_QUERY, filters: f };
        }
    }, 'col-md-3');

    addField({
        label: __('Calculate Opening Balance with Totals'), fieldtype: 'Check',
        fieldname: 'calculate_opening_balance_with_totals',
        default: saved.calculate_opening_balance_with_totals || 0
    }, 'col-md-3');

    addField({
        label: __('Consider Drafts'), fieldtype: 'Check', fieldname: 'consider_draft',
        default: saved.consider_draft || 0
    }, 'col-md-2');

    addField({
        label: __('Consider Draft Payments'), fieldtype: 'Check', fieldname: 'consider_draft_payments',
        default: saved.consider_draft_payments || 0
    }, 'col-md-2');

    addField({
        label: __('Neglect Items'), fieldtype: 'Check', fieldname: 'neglect_items',
        default: saved.neglect_items || 0
    }, 'col-md-2');

    // One number now: the totals block lives inside the ledger table and pays
    // for itself out of the same budget. 0 = fill the sheet; anything larger
    // than the sheet holds is clamped server-side so rows are never clipped.
    addField({
        label: __('Rows / Page'), fieldtype: 'Int', fieldname: 'rows_per_page',
        description: __('0 = auto fit'),
        default: saved.rows_per_page !== undefined ? saved.rows_per_page : 0
    }, 'col-md-2');

    setTimeout(() => {
        if (saved.party_group) supplierGroupField.set_value(saved.party_group);
        if (saved.party) supplierField.set_value(saved.party);
    }, 500);

    supplierGroupField.$input.on('change', function () {
        if (supplierField) {
            supplierField.set_value('');
            supplierField.refresh();
        }
        saveFilters();
    });

    function collectFilters() {
        const out = { session_id: sessionId };
        for (let key in page.fields_dict) {
            if (page.fields_dict[key] && page.fields_dict[key].get_value) {
                out[key] = page.fields_dict[key].get_value();
            }
        }
        return out;
    }

    function validateFilters(filters) {
        const missing = [];
        if (!filters.company) missing.push(__('Company'));
        if (!filters.from_date) missing.push(__('From Date'));
        if (missing.length) {
            frappe.throw({
                title: __('Missing Filters'),
                message: __('Missing Filters') + '<br><ul><li>' + missing.join('</li><li>') + '</ul>'
            });
        }
    }

    // ------------------------------------------------------------------
    // Layout: tabs
    // ------------------------------------------------------------------

    const $layout = $(`
        <div class="ssv2-layout">
            <ul class="nav nav-tabs ssv2-tabs">
                <li class="nav-item"><a class="nav-link active" data-tab="preview" href="#">${__('Preview')}</a></li>
                <li class="nav-item"><a class="nav-link" data-tab="bulk" href="#">${__('Bulk Generation')}</a></li>
                <li class="nav-item"><a class="nav-link" data-tab="history" href="#">${__('History')}</a></li>
            </ul>

            <div class="ssv2-pane" data-pane="preview">
                <div class="ssv2-toolbar">
                    <div class="ssv2-toolbar-group">
                        <button class="btn btn-primary btn-sm" id="ssv2-load-preview">${__('Load Preview')}</button>
                        <button class="btn btn-default btn-sm" id="ssv2-prev" title="${__('Previous supplier')}">&#8250;</button>
                        <div class="ssv2-party-select" id="ssv2-party-select"></div>
                        <button class="btn btn-default btn-sm" id="ssv2-next" title="${__('Next supplier')}">&#8249;</button>
                    </div>
                    <div class="ssv2-toolbar-group">
                        <button class="btn btn-default btn-sm" id="ssv2-zoom-out">&minus;</button>
                        <span class="ssv2-zoom-label" id="ssv2-zoom-label">100%</span>
                        <button class="btn btn-default btn-sm" id="ssv2-zoom-in">+</button>
                        <button class="btn btn-default btn-sm" id="ssv2-print">${__('Print')}</button>
                        <button class="btn btn-success btn-sm" id="ssv2-download">${__('Download PDF')}</button>
                    </div>
                </div>
                <div class="ssv2-meta" id="ssv2-meta"></div>
                <div class="ssv2-stage" id="ssv2-stage">
                    <div class="ssv2-empty">${__('Choose your filters and press Load Preview.')}</div>
                </div>
            </div>

            <div class="ssv2-pane" data-pane="bulk" style="display:none;">
                <div class="ssv2-results"></div>
            </div>

            <div class="ssv2-pane" data-pane="history" style="display:none;">
                <div class="ssv2-toolbar">
                    <div class="ssv2-toolbar-group">
                        <button class="btn btn-info btn-sm" id="ssv2-browse-history">${__('Browse History')}</button>
                        <button class="btn btn-default btn-sm" id="ssv2-filter-history">${__('Filter History')}</button>
                    </div>
                </div>
                <div class="ssv2-history-body">
                    <p class="text-muted">${__('Only generations created from this V2 page are listed here.')}</p>
                </div>
            </div>
        </div>
    `);
    $(page.body).append($layout);

    const $results_container = $layout.find('.ssv2-results');

    // ------------------------------------------------------------------
    // Supplier picker -- type to search instead of scrolling a long <select>
    // ------------------------------------------------------------------

    // Frappe's Autocomplete filters on label+value with no ranking; the filter
    // and sort are replaced below.
    const partySelect = frappe.ui.form.make_control({
        parent: $layout.find('#ssv2-party-select'),
        df: {
            fieldtype: 'Autocomplete',
            fieldname: 'ssv2_party',
            placeholder: __('Search by supplier code or name...'),
            max_items: 500,
            options: [],
            change: function () {
                const value = partySelect.get_value();
                if (!value) return;
                const index = previewParties.findIndex((p) => p.code === value);
                // Ignore half-typed text; only react to a real pick.
                if (index === -1 || index === previewIndex) return;
                previewIndex = index;
                renderPartyPreview();
            }
        },
        render_input: true,
        only_input: true
    });
    partySelect.$input.addClass('input-sm');

    // ---- relevance ranking -------------------------------------------------
    //
    // Mirrors agricultural_marketing/queries.py so the picker and the Link
    // fields behave identically.
    //
    // Code hits outrank name hits, and a code whose number *equals* the query
    // wins outright -- 0001 is supplier number 1, so typing 1 must surface it
    // rather than 1001. Names are matched token by token, in any order, so
    // "سالم الموسى" finds "سالم صالح محمد الموسى".

    const digitsOnly = (text) => String(text || '').replace(/\D/g, '');

    function partyRank(item, query) {
        const q = String(query || '').trim().toLowerCase();
        if (!q) return 0;

        const code = String(item.value || '').toLowerCase();
        // The label is "name (code)"; strip the trailing code so a digit cannot
        // match the same code twice.
        const name = String(item.label || '').toLowerCase().replace(/\s*\([^)]*\)\s*$/, '');
        const codeDigits = digitsOnly(code);
        const queryDigits = digitsOnly(q);
        const numericQuery = /^[0-9]+$/.test(q);

        if (code === q) return 1;
        // 0001 is supplier number 1 -- leading zeros must not hide it.
        if (numericQuery && codeDigits && parseInt(codeDigits, 10) === parseInt(queryDigits, 10)) return 2;
        if (code.startsWith(q)) return 3;
        if (numericQuery && codeDigits.startsWith(queryDigits)) return 4;
        if (code.includes(q)) return 5;
        if (name.startsWith(q)) return 6;
        if (name.includes(q)) return 7;

        // Every word present somewhere, in any order.
        const tokens = q.split(/\s+/).filter(Boolean);
        if (tokens.length > 1) {
            const hay = code + ' ' + name;
            if (tokens.every((t) => hay.includes(t))) return 8;
        }
        return 0;   // 0 = no match
    }

    // Awesomplete calls filter() for every item and then sort() on the result,
    // and sort() is not handed the query -- so remember it here.
    let partyQuery = '';

    partySelect.awesomplete.filter = function (item, input) {
        partyQuery = input;
        return partyRank(item, input) > 0;
    };

    partySelect.awesomplete.sort = function (a, b) {
        const ra = partyRank(a, partyQuery);
        const rb = partyRank(b, partyQuery);
        if (ra !== rb) return ra - rb;

        // Same tier: the code containing the query more often comes first,
        // then the smaller number, then alphabetically.
        const count = (text) => {
            const q = String(partyQuery || '').trim().toLowerCase();
            if (!q) return 0;
            return String(text || '').toLowerCase().split(q).length - 1;
        };
        const ca = count(a.value), cb = count(b.value);
        if (ca !== cb) return cb - ca;

        const na = parseInt(digitsOnly(a.value), 10);
        const nb = parseInt(digitsOnly(b.value), 10);
        if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;

        return String(a.label).localeCompare(String(b.label));
    };

    function setPartyOptions(parties) {
        partySelect.set_data(
            parties.map((p) => ({ label: `${p.name} (${p.code})`, value: p.code }))
        );
        if (!parties.length) partySelect.set_value('');
    }

    function syncPartySelect() {
        const party = previewParties[previewIndex];
        if (!party) return;
        // set_value would re-fire change(); we only need the text to match.
        partySelect.$input.val(`${party.name} (${party.code})`);
        partySelect.value = party.code;
    }


    $layout.find('.ssv2-tabs .nav-link').on('click', function (e) {
        e.preventDefault();
        const tab = $(this).data('tab');
        $layout.find('.ssv2-tabs .nav-link').removeClass('active');
        $(this).addClass('active');
        $layout.find('.ssv2-pane').hide();
        $layout.find(`.ssv2-pane[data-pane="${tab}"]`).show();
    });

    // ------------------------------------------------------------------
    // Preview
    // ------------------------------------------------------------------

    function loadPreview(resetParties) {
        const filters = collectFilters();
        validateFilters(filters);
        saveFilters();

        if (resetParties) {
            frappe.dom.freeze(__('Collecting suppliers...'));
            frappe.call({
                method: METHOD + 'get_parties_with_data',
                args: { filters: filters },
                callback: function (r) {
                    frappe.dom.unfreeze();
                    const data = r.message || {};
                    previewParties = data.parties || [];
                    previewIndex = 0;

                    if (!previewParties.length) {
                        renderPreviewHtml('');
                        $('#ssv2-meta').html(`<span class="text-danger">${__('No data matches the chosen criteria')}</span>`);
                        setPartyOptions([]);
                        return;
                    }

                    setPartyOptions(previewParties);

                    if (data.engine && !data.engine.searchable_arabic) {
                        frappe.show_alert({ message: data.engine.message, indicator: 'orange' }, 8);
                    }

                    renderPartyPreview();
                },
                error: function () { frappe.dom.unfreeze(); }
            });
        } else {
            renderPartyPreview();
        }
    }

    function renderPartyPreview() {
        if (!previewParties.length) return;

        const party = previewParties[previewIndex];
        const filters = collectFilters();

        frappe.dom.freeze(__('Rendering statement...'));
        frappe.call({
            method: METHOD + 'get_statement_preview',
            args: { filters: filters, party: party.code },
            callback: function (r) {
                frappe.dom.unfreeze();
                const data = r.message || {};
                if (data.error) {
                    $('#ssv2-meta').html(`<span class="text-danger">${data.error}</span>`);
                    renderPreviewHtml('');
                    return;
                }
                renderPreviewHtml(data.html);
                $('#ssv2-meta').html(`
                    <span class="ssv2-chip">${__('Supplier')}: <b>${frappe.utils.escape_html(data.party_name)}</b></span>
                    <span class="ssv2-chip">${__('Rows')}: <b>${data.row_count}</b></span>
                    <span class="ssv2-chip">${__('Pages')}: <b>${data.total_pages}</b></span>
                    <span class="ssv2-chip">${__('Closing')}: <b>${data.closing} (${data.closing_side})</b></span>
                    <span class="ssv2-chip">${previewIndex + 1} / ${previewParties.length}</span>
                `);
                syncPartySelect();
            },
            error: function () { frappe.dom.unfreeze(); }
        });
    }

    function renderPreviewHtml(html) {
        lastPreviewHtml = html || '';
        const $stage = $('#ssv2-stage').empty();

        if (!lastPreviewHtml) {
            $stage.append(`<div class="ssv2-empty">${__('Nothing to preview.')}</div>`);
            return;
        }

        const $frame = $('<iframe class="ssv2-frame" id="ssv2-frame"></iframe>');
        $stage.append($frame);

        const doc = $frame[0].contentWindow.document;
        doc.open();
        doc.write('<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">' + lastPreviewHtml + '</body></html>');
        doc.close();

        applyZoom();
        // Size the iframe to its content so the page scrolls, not the frame.
        setTimeout(() => {
            try {
                const h = doc.body.scrollHeight;
                $frame.css('height', (h * previewZoom + 40) + 'px');
            } catch (e) { /* cross-origin never happens here */ }
        }, 250);
    }

    function applyZoom() {
        const $frame = $('#ssv2-frame');
        if (!$frame.length) return;
        try {
            const body = $frame[0].contentWindow.document.body;
            body.style.transformOrigin = 'top center';
            body.style.transform = 'scale(' + previewZoom + ')';
            $frame.css('height', (body.scrollHeight * previewZoom + 40) + 'px');
        } catch (e) { /* ignore */ }
        $('#ssv2-zoom-label').text(Math.round(previewZoom * 100) + '%');
    }

    $layout.on('click', '#ssv2-load-preview', () => loadPreview(true));


    $layout.on('click', '#ssv2-prev', function () {
        if (!previewParties.length) return;
        previewIndex = (previewIndex - 1 + previewParties.length) % previewParties.length;
        renderPartyPreview();
    });

    $layout.on('click', '#ssv2-next', function () {
        if (!previewParties.length) return;
        previewIndex = (previewIndex + 1) % previewParties.length;
        renderPartyPreview();
    });

    $layout.on('click', '#ssv2-zoom-in', function () {
        previewZoom = Math.min(previewZoom + 0.1, 2);
        applyZoom();
    });

    $layout.on('click', '#ssv2-zoom-out', function () {
        previewZoom = Math.max(previewZoom - 0.1, 0.4);
        applyZoom();
    });

    $layout.on('click', '#ssv2-print', function () {
        const frame = document.getElementById('ssv2-frame');
        if (!frame) {
            frappe.msgprint(__('Load a preview first.'));
            return;
        }
        const body = frame.contentWindow.document.body;
        const previous = body.style.transform;
        body.style.transform = 'none';
        frame.contentWindow.focus();
        frame.contentWindow.print();
        body.style.transform = previous;
    });

    $layout.on('click', '#ssv2-download', function () {
        if (!previewParties.length) {
            frappe.msgprint(__('Load a preview first.'));
            return;
        }
        const party = previewParties[previewIndex];
        frappe.dom.freeze(__('Generating PDF...'));
        frappe.call({
            method: METHOD + 'download_statement_pdf',
            args: { filters: collectFilters(), party: party.code },
            callback: function (r) {
                frappe.dom.unfreeze();
                if (r.message && r.message.file_url) {
                    window.open(r.message.file_url, '_blank');
                } else {
                    frappe.msgprint(__('Failed to generate PDF'));
                }
            },
            error: function () { frappe.dom.unfreeze(); }
        });
    });

    // ------------------------------------------------------------------
    // Bulk generation
    // ------------------------------------------------------------------

    function generatePDFs() {
        const filters = collectFilters();
        validateFilters(filters);

        frappe.call({
            method: METHOD + 'queue_pdf_generation',
            args: { filters: filters },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.msgprint(__('PDF generation jobs queued successfully'));
                    saveFilters();
                    currentHistoryId = r.message.history_id;
                    $layout.find('.ssv2-tabs .nav-link[data-tab="bulk"]').click();
                    loadPDFStatusByHistory(currentHistoryId);
                    startAutoRefresh();
                } else if (r.message && r.message.error) {
                    frappe.throw({ title: __('Error'), indicator: 'red', message: __(r.message.error) });
                }
            }
        });
    }

    function loadPDFStatusByHistory(historyId) {
        if (!historyId) return;
        frappe.call({
            method: METHOD + 'get_pdf_generation_status',
            args: { history_id: historyId },
            callback: function (r) {
                if (r.message) {
                    displayPDFStatus(r.message, historyId);
                    updateWhatsAppQueueControls();
                }
            }
        });
        loadHistoryWhatsAppJob(historyId);
    }

    function loadPDFStatus(filters) {
        frappe.call({
            method: METHOD + 'get_pdf_generation_status',
            args: { filters: filters },
            callback: function (r) {
                if (r.message) {
                    displayPDFStatus(r.message);
                    updateWhatsAppQueueControls();
                }
            }
        });
    }

    function loadHistoryWhatsAppJob(historyId) {
        frappe.call({
            method: SF + 'get_history_whatsapp_job',
            args: { history_id: historyId },
            callback: function (r) {
                if (r.message) {
                    lastWhatsAppJobId = r.message.job_id || null;
                    lastWhatsAppJobName = r.message.job_name || null;
                    updateWhatsAppQueueControls();
                }
            }
        });
    }

    function displayPDFStatus(logs, historyId) {
        logs = (Array.isArray(logs) ? logs : []).filter(
            l => (l.party_type || '').toString().toLowerCase() === 'supplier'
        );

        if (!logs.length) {
            $results_container.html(`<div class="alert alert-info">${__('No PDF generation logs found.')}</div>`);
            return;
        }

        const total = logs.length;
        const completed = logs.filter(l => l.status === 'Completed').length;
        const failed = logs.filter(l => l.status === 'Failed').length;
        const processing = logs.filter(l => l.status === 'Processing').length;
        const queued = logs.filter(l => l.status === 'Queued').length;

        const sentStatuses = ['Sent', 'Delivered', 'Read'];
        const waNotCreated = logs.filter(l => !l.whatsapp_status || l.whatsapp_status === 'Not Created').length;
        const waFailed = logs.filter(l => l.whatsapp_status === 'Failed').length;
        const waSent = logs.filter(l => sentStatuses.includes(l.whatsapp_status)).length;
        const waCreated = logs.filter(l => l.whatsapp_message_id || (l.whatsapp_status && l.whatsapp_status !== 'Not Created')).length;

        const isAdmin = frappe.session && frappe.session.user === 'Administrator';

        let html = `
            <div class="pdf-status-container">
                <div class="row">
                    <div class="col-md-6">
                        <h5 style="margin-bottom:8px">${__('PDF Generation Status')}</h5>
                        <div class="sf-summary-group">
                            <span class="sf-pill sf-pill-default">${__('Total')}: ${total}</span>
                            <span class="sf-pill sf-pill-success">${__('Completed')}: ${completed}</span>
                            <span class="sf-pill sf-pill-danger">${__('Failed')}: ${failed}</span>
                            <span class="sf-pill sf-pill-warning">${__('Processing')}: ${processing}</span>
                            <span class="sf-pill sf-pill-info">${__('Queued')}: ${queued}</span>
                        </div>
                        <h5 style="margin:16px 0 8px">${__('WhatsApp Message Status')}</h5>
                        <div class="sf-summary-group">
                            <span class="sf-pill sf-pill-default">${__('Not Created')}: ${waNotCreated}</span>
                            <span class="sf-pill sf-pill-warning">${__('Created')}: ${waCreated}</span>
                            <span class="sf-pill sf-pill-success">${__('Sent')}: ${waSent}</span>
                            <span class="sf-pill sf-pill-danger">${__('Failed')}: ${waFailed}</span>
                        </div>
                        ${historyId ? `<br><small class="text-info">History ID: ${historyId}</small>` : ''}
                    </div>
                    <div class="col-md-6 text-right">
                        <button class="btn btn-sm sf-action-btn" id="refresh-status">${__('Refresh')}</button>
                        <button class="btn btn-sm sf-action-btn" id="download-all">${__('Download All (ZIP)')}</button>
                        <button class="btn btn-sm sf-action-btn" id="send-all-whatsapp">${__('Send All WhatsApp')}</button>
                        <button class="btn btn-sm sf-action-btn" id="send-selected-whatsapp">${__('Send Selected to WhatsApp')}</button>
                        <button class="btn btn-sm sf-action-btn" id="retry-all-whatsapp">${__('Retry All WhatsApp')}</button>
                        <button class="btn btn-sm sf-action-btn" id="cancel-whatsapp-queue">${__('Cancel WhatsApp Queue')}</button>
                        ${failed && historyId ? `<button class="btn btn-sm sf-action-btn" id="retry-all-failed">${__('Retry All Failed')}</button>` : ''}
                        ${historyId ? `<button class="btn btn-sm sf-action-btn" id="retry-all-queued-failed">${__('Retry All Queued/Failed')}</button>` : ''}
                        ${isAdmin ? `<button class="btn btn-sm sf-action-btn" id="cleanup-jobs">${__('Cleanup Stuck')}</button>` : ''}
                    </div>
                </div>

                <div class="row" style="margin:15px 0">
                    <div class="col-md-4">
                        <div class="input-group">
                            <input type="text" class="form-control" id="party-search" placeholder="${__('Search by supplier name...')}" />
                            <div class="input-group-append">
                                <button class="btn btn-outline-secondary" type="button" id="clear-search"><i class="fa fa-times"></i></button>
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
                    <div class="col-md-5"><small class="text-muted" id="search-results-info"></small></div>
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
            const name = (log.party_display_name || log.party_name || '').toString();
            const waStatus = (log.whatsapp_status || 'Not Created').toString();
            html += `
                <tr data-log-id="${log.name}"
                    data-party-name="${frappe.utils.escape_html(name.toLowerCase())}"
                    data-party-id="${frappe.utils.escape_html((log.party_name || '').toString().toLowerCase())}"
                    data-wa-status="${waStatus}">
                    <td><input type="checkbox" class="row-checkbox" value="${log.name}" ${log.status === 'Completed' ? '' : 'disabled'}></td>
                    <td class="party-name-cell"><strong>${frappe.utils.escape_html(name) || __('Unknown')}</strong></td>
                    <td><small>${frappe.utils.escape_html(log.party_name || '')}</small></td>
                    <td>${statusBadge(log.status)}</td>
                    <td>${whatsappIcon(waStatus)}</td>
                    <td><small>${log.creation_time ? frappe.datetime.str_to_user(log.creation_time) : ''}</small></td>
                    <td>${actionButtons(log)}</td>
                </tr>
            `;
        });

        html += '</tbody></table></div></div>';

        const previouslySelected = new Set($('.row-checkbox:checked').map(function () { return this.value; }).get());
        const previousSearch = $('#party-search').val() || '';
        const previousWA = $('#whatsapp-status-filter').val() || '';

        $results_container.html(html);

        $('#pdf-status-tbody .row-checkbox').each(function () {
            if (previouslySelected.has(this.value)) $(this).prop('checked', true);
        });

        initSearch(logs);
        bindStatusEvents(historyId);

        if (previousWA) $('#whatsapp-status-filter').val(previousWA).trigger('change');
        if (previousSearch) {
            $('#party-search').val(previousSearch);
            filterTable(previousSearch.toLowerCase().trim(), logs);
        }
    }

    function statusBadge(status) {
        const s = (status || '').toLowerCase();
        let cls = 'sf-pill-default';
        if (s === 'completed') cls = 'sf-pill-success';
        else if (s === 'failed') cls = 'sf-pill-danger';
        else if (s === 'processing') cls = 'sf-pill-warning';
        else if (s === 'queued') cls = 'sf-pill-info';
        return `<span class="sf-pill ${cls}">${__(status)}</span>`;
    }

    function whatsappIcon(status) {
        switch (status) {
            case 'Sent': return '<i class="fa fa-check text-primary" title="Sent"></i>';
            case 'Delivered':
            case 'Read': return '<i class="fa fa-check-circle text-success" title="Delivered"></i>';
            case 'Queued': return '<i class="fa fa-clock-o text-warning" title="Queued"></i>';
            case 'Failed': return '<i class="fa fa-times-circle text-danger" title="Failed"></i>';
            default: return '<i class="fa fa-times-circle text-muted" title="Not Created"></i>';
        }
    }

    function actionButtons(log) {
        let buttons = '';
        const sentStatuses = ['Sent', 'Delivered', 'Read'];

        if (log.status === 'Completed' && log.pdf_file) {
            buttons += `<button class="btn btn-sm sf-action-btn download-pdf" data-url="${log.pdf_file}">${__('Download')}</button> `;
            if (sentStatuses.includes(log.whatsapp_status)) {
                buttons += `<span class="text-success">${__('WhatsApp Sent')}</span>`;
            } else if (log.whatsapp_status === 'Failed') {
                buttons += `<button class="btn btn-sm btn-warning send-whatsapp" data-log-id="${log.name}">${__('Retry WhatsApp')}</button>`;
            } else {
                buttons += `<button class="btn btn-sm btn-primary send-whatsapp" data-log-id="${log.name}">${__('Send WhatsApp')}</button>`;
            }
        } else if (log.status === 'Failed') {
            buttons += `<button class="btn btn-sm btn-warning retry-pdf" data-log-id="${log.name}">${__('Retry')}</button> `;
            buttons += `<small class="text-danger">${frappe.utils.escape_html(log.error_message || 'Generation failed')}</small>`;
        } else if (log.status === 'Processing') {
            buttons += `<span class="text-info">${__('Processing...')}</span>`;
        } else {
            buttons += `<span class="text-muted">${__('Queued...')}</span>`;
        }
        return buttons;
    }

    function filterTable(searchTerm, logs) {
        const waFilter = $('#whatsapp-status-filter').val();
        let visible = 0;

        $('#pdf-status-tbody tr').each(function () {
            const $row = $(this);
            const name = ($row.data('party-name') || '').toString();
            const id = ($row.data('party-id') || '').toString();
            const wa = ($row.data('wa-status') || 'Not Created').toString();

            const matches = (!searchTerm || name.includes(searchTerm) || id.includes(searchTerm))
                && (!waFilter || wa === waFilter);

            if (matches) { $row.show(); visible++; }
            else { $row.hide(); $row.find('.row-checkbox').prop('checked', false); }
        });

        $('#search-results-info').text(
            (!searchTerm && !waFilter) ? '' : `${__('Showing')} ${visible} ${__('of')} ${logs.length}`
        );
        updateSelectAll();
    }

    function updateSelectAll() {
        const $visible = $('#pdf-status-tbody tr:visible .row-checkbox:not(:disabled)');
        const $checked = $('#pdf-status-tbody tr:visible .row-checkbox:checked');
        const $all = $('#select-all');
        if (!$visible.length) $all.prop('indeterminate', false).prop('checked', false);
        else if ($checked.length === $visible.length) $all.prop('indeterminate', false).prop('checked', true);
        else if ($checked.length) $all.prop('indeterminate', true);
        else $all.prop('indeterminate', false).prop('checked', false);
    }

    function initSearch(logs) {
        let timer;
        $('#party-search').on('input', function () {
            clearTimeout(timer);
            const value = $(this).val().toLowerCase().trim();
            timer = setTimeout(() => filterTable(value, logs), 300);
        });
        $('#clear-search').on('click', function () {
            $('#party-search').val('');
            filterTable('', logs);
        });
        $('#whatsapp-status-filter').on('change', function () {
            filterTable(($('#party-search').val() || '').toLowerCase().trim(), logs);
        });
        $('#select-all').on('change', function () {
            $('#pdf-status-tbody tr:visible .row-checkbox:not(:disabled)').prop('checked', $(this).prop('checked'));
        });
        $('#pdf-status-tbody').on('change', '.row-checkbox', updateSelectAll);
    }

    function selectedLogIds() {
        return $('.row-checkbox:checked').map(function () { return this.value; }).get();
    }

    function bindStatusEvents(historyId) {
        updateWhatsAppQueueControls();

        $('#refresh-status').on('click', function () {
            if (currentHistoryId) loadPDFStatusByHistory(currentHistoryId);
            else loadPDFStatus(collectFilters());
        });

        $('#download-all').on('click', function () {
            const ids = selectedLogIds();
            if (!ids.length) return frappe.msgprint(__('Please select at least one completed PDF'));
            frappe.dom.freeze(__('Creating ZIP file...'));
            frappe.call({
                method: SF + 'download_bulk_pdfs',
                args: { log_ids: ids },
                callback: function (r) {
                    frappe.dom.unfreeze();
                    if (r.message && r.message.file_url) window.open(r.message.file_url, '_blank');
                    else frappe.msgprint(__('Failed to create ZIP file'));
                },
                error: function () { frappe.dom.unfreeze(); }
            });
        });

        $('#send-all-whatsapp').on('click', function () {
            if ($(this).prop('disabled')) return frappe.msgprint(__('WhatsApp queue is running. Please wait until it finishes.'));
            const hid = historyId || currentHistoryId;
            ensureWhatsAppSession(() => {
                if (hid) {
                    frappe.confirm(
                        __('Are you sure you want to send WhatsApp messages to all completed suppliers in this generation?'),
                        () => queueWhatsApp({ history_id: hid }, hid)
                    );
                } else {
                    const ids = selectedLogIds();
                    if (!ids.length) return frappe.msgprint(__('Please select at least one completed PDF'));
                    frappe.confirm(
                        __('Are you sure you want to send WhatsApp messages to {0} selected suppliers?', [ids.length]),
                        () => queueWhatsApp({ log_ids: ids }, hid)
                    );
                }
            });
        });

        $('#send-selected-whatsapp').on('click', function () {
            if ($(this).prop('disabled')) return frappe.msgprint(__('WhatsApp queue is running. Please wait until it finishes.'));
            const ids = selectedLogIds();
            if (!ids.length) return frappe.msgprint(__('Please select at least one completed PDF to send via WhatsApp.'));
            ensureWhatsAppSession(() => {
                frappe.confirm(
                    __('Send WhatsApp to {0} selected supplier(s) only?', [ids.length]),
                    () => queueWhatsApp({ log_ids: ids }, historyId || currentHistoryId)
                );
            });
        });

        $('#retry-all-whatsapp').on('click', function () {
            if ($(this).prop('disabled')) return frappe.msgprint(__('WhatsApp queue is running. Please wait until it finishes.'));
            const hid = historyId || currentHistoryId;
            if (!hid) return frappe.msgprint(__('No history loaded'));
            ensureWhatsAppSession(() => {
                frappe.confirm(
                    __('Queue WhatsApp for all completed items with Not Created/Failed status?'),
                    () => queueWhatsApp({ history_id: hid, retry_failed: 1 }, hid)
                );
            });
        });

        $('#cancel-whatsapp-queue').on('click', function () {
            if (!lastWhatsAppJobId && !lastWhatsAppJobName) return frappe.msgprint(__('No WhatsApp queue job to cancel.'));
            frappe.confirm(__('Are you sure you want to cancel the WhatsApp queue job?'), cancelWhatsAppQueue);
        });

        $('#retry-all-failed').on('click', function () {
            frappe.confirm(__('Are you sure you want to retry all failed jobs?'), () => {
                frappe.call({
                    method: METHOD + 'retry_all_failed_pdfs',
                    args: { history_id: historyId },
                    callback: function (r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({ message: r.message.success, indicator: 'green' });
                            loadPDFStatusByHistory(historyId);
                            startAutoRefresh();
                        } else {
                            frappe.msgprint(__('Retry all failed error: ') + ((r.message || {}).error || 'Unknown error'));
                        }
                    }
                });
            });
        });

        $('#retry-all-queued-failed').on('click', function () {
            frappe.confirm(__('Are you sure you want to retry all queued and failed jobs?'), () => {
                frappe.call({
                    method: METHOD + 'retry_all_queued_and_failed_pdf_jobs_for_history',
                    args: { history_id: historyId },
                    callback: function (r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({ message: r.message.success, indicator: 'green' });
                            loadPDFStatusByHistory(historyId);
                            startAutoRefresh();
                        }
                    }
                });
            });
        });

        $('#cleanup-jobs').on('click', function () {
            frappe.confirm(
                __('Are you sure you want to cleanup stuck jobs? This will mark jobs processing for more than 10 minutes as failed.'),
                () => {
                    frappe.call({
                        method: SF + 'cleanup_failed_logs',
                        callback: function (r) {
                            if (r.message && r.message.success) {
                                frappe.msgprint(r.message.success);
                                if (currentHistoryId) loadPDFStatusByHistory(currentHistoryId);
                            }
                        }
                    });
                }
            );
        });

        $('.download-pdf').on('click', function () { window.open($(this).data('url'), '_blank'); });

        $('.send-whatsapp').on('click', function () {
            queueSingleWhatsApp($(this).data('log-id'), historyId || currentHistoryId);
        });

        $('.retry-pdf').on('click', function () {
            const logId = $(this).data('log-id');
            frappe.confirm(__('Are you sure you want to retry PDF generation for this supplier?'), () => {
                frappe.call({
                    method: METHOD + 'retry_failed_pdf',
                    args: { log_id: logId },
                    callback: function (r) {
                        if (r.message && r.message.success) {
                            frappe.msgprint(r.message.success);
                            if (currentHistoryId) {
                                loadPDFStatusByHistory(currentHistoryId);
                                startAutoRefresh();
                            }
                        } else {
                            frappe.msgprint(__('Retry failed: ') + ((r.message || {}).error || ''));
                        }
                    }
                });
            });
        });
    }

    // ------------------------------------------------------------------
    // WhatsApp helpers (shared statement_forms backend)
    // ------------------------------------------------------------------

    function ensureWhatsAppSession(callback) {
        frappe.call({
            method: SF + 'get_whatsapp_session_status',
            callback: function (r) {
                const data = r.message || {};
                if (!data.connected) return frappe.msgprint(__(data.message || 'WhatsApp session is not connected.'));
                callback();
            }
        });
    }

    function queueWhatsApp(args, historyId) {
        frappe.call({
            method: SF + 'queue_all_whatsapp',
            args: args,
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: r.message.success, indicator: 'green' });
                    lastWhatsAppJobId = r.message.job_id || null;
                    lastWhatsAppJobName = r.message.job_name || null;
                    updateWhatsAppQueueControls();
                    if (historyId) {
                        loadPDFStatusByHistory(historyId);
                        startAutoRefresh();
                    }
                } else {
                    frappe.msgprint(__('Failed to queue WhatsApp messages: ') + ((r.message || {}).error || 'Unknown error'));
                }
            }
        });
    }

    function queueSingleWhatsApp(logId, historyId) {
        frappe.call({
            method: SF + 'queue_whatsapp_for_party',
            args: { log_id: logId },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: __('Queued WhatsApp sending'), indicator: 'green' });
                    if (historyId) {
                        loadPDFStatusByHistory(historyId);
                        startAutoRefresh();
                    }
                } else {
                    frappe.msgprint(__('Failed to queue WhatsApp: ') + ((r.message || {}).error || 'Unknown error'));
                }
            }
        });
    }

    function updateWhatsAppQueueControls() {
        const hasJob = Boolean(lastWhatsAppJobId || lastWhatsAppJobName);
        $('#cancel-whatsapp-queue').prop('disabled', !hasJob);
        if (!hasJob) {
            setWhatsAppButtonsDisabled(false);
            return;
        }
        frappe.call({
            method: SF + 'get_whatsapp_job_status',
            args: { job_id: lastWhatsAppJobId, job_name: lastWhatsAppJobName },
            callback: function (r) {
                const status = (r.message || {}).status;
                const active = ['queued', 'started', 'running'].includes(status);
                setWhatsAppButtonsDisabled(active);
                if (!active) {
                    lastWhatsAppJobId = null;
                    lastWhatsAppJobName = null;
                    $('#cancel-whatsapp-queue').prop('disabled', true);
                }
            }
        });
    }

    function setWhatsAppButtonsDisabled(disabled) {
        $('#send-all-whatsapp, #send-selected-whatsapp, #retry-all-whatsapp').prop('disabled', disabled);
    }

    function cancelWhatsAppQueue() {
        frappe.call({
            method: METHOD + 'cancel_whatsapp_job',
            args: { job_id: lastWhatsAppJobId, job_name: lastWhatsAppJobName },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: r.message.success, indicator: 'green' });
                    lastWhatsAppJobId = null;
                    lastWhatsAppJobName = null;
                    updateWhatsAppQueueControls();
                }
            }
        });
    }

    function startAutoRefresh() {
        if (refreshInterval) clearInterval(refreshInterval);
        let count = 0;
        refreshInterval = setInterval(() => {
            count++;
            if (currentHistoryId) loadPDFStatusByHistory(currentHistoryId);
            if (count >= 60) {
                clearInterval(refreshInterval);
                refreshInterval = null;
                frappe.show_alert({ message: __('Auto-refresh stopped. Click Refresh to update manually.'), indicator: 'blue' });
            }
        }, 5000);
        frappe.show_alert({ message: __('Auto-refresh enabled. Updates every 5 seconds.'), indicator: 'green' });
    }

    // ------------------------------------------------------------------
    // History
    // ------------------------------------------------------------------

    $layout.on('click', '#ssv2-browse-history', function () {
        frappe.call({
            method: METHOD + 'get_statement_generation_history',
            callback: function (r) { showHistoryDialog(r.message || []); }
        });
    });

    $layout.on('click', '#ssv2-filter-history', function () {
        const dialog = new frappe.ui.Dialog({
            title: __('Filter Supplier Generation History (V2)'),
            fields: [
                { label: __('From Date'), fieldname: 'from_date', fieldtype: 'Date' },
                { label: __('To Date'), fieldname: 'to_date', fieldtype: 'Date' },
                { label: __('Supplier Name'), fieldname: 'party_name', fieldtype: 'Data' },
                { label: __('Company'), fieldname: 'company', fieldtype: 'Link', options: 'Company' }
            ],
            primary_action_label: __('Search'),
            primary_action(values) {
                frappe.call({
                    method: METHOD + 'get_statement_generation_history',
                    args: values,
                    callback: function (r) { showHistoryDialog(r.message || []); }
                });
                dialog.hide();
            }
        });
        dialog.show();
    });

    function showHistoryDialog(histories) {
        let html = `
            <div class="table-responsive">
                <table class="table table-bordered table-striped">
                    <thead>
                        <tr>
                            <th>${__('Generation Time')}</th>
                            <th>${__('Date Range')}</th>
                            <th>${__('Total Suppliers')}</th>
                            <th>${__('Completed')}</th>
                            <th>${__('Failed')}</th>
                            <th>${__('WhatsApp Sent')}</th>
                            <th>${__('Created By')}</th>
                            <th>${__('Actions')}</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        if (!histories.length) {
            html += `<tr><td colspan="8" class="text-muted text-center">${__('No V2 generations yet.')}</td></tr>`;
        }

        histories.forEach(h => {
            html += `
                <tr>
                    <td><small>${frappe.datetime.str_to_user(h.generation_time)}</small></td>
                    <td><small>${h.from_date} → ${h.to_date || 'N/A'}</small></td>
                    <td>${h.total_parties}</td>
                    <td><span class="sf-pill sf-pill-success">${h.completed_count}</span></td>
                    <td><span class="sf-pill sf-pill-danger">${h.failed_count}</span></td>
                    <td><span class="sf-pill sf-pill-info">${h.whatsapp_sent_count}</span></td>
                    <td><small>${frappe.utils.escape_html(h.created_by_user || '')}</small></td>
                    <td><button class="btn btn-sm btn-success load-history" data-history-id="${h.name}">${__('Load')}</button></td>
                </tr>
            `;
        });

        html += '</tbody></table></div>';

        const dialog = new frappe.ui.Dialog({
            title: __('Supplier Statement Generation History (V2)'),
            size: 'extra-large',
            fields: [{ fieldname: 'history_table', fieldtype: 'HTML', options: html }]
        });
        dialog.show();

        dialog.$wrapper.find('.load-history').on('click', function () {
            currentHistoryId = $(this).data('history-id');
            loadPDFStatusByHistory(currentHistoryId);
            dialog.hide();
            $layout.find('.ssv2-tabs .nav-link[data-tab="bulk"]').click();
        });
    }

    // ------------------------------------------------------------------
    // Page actions
    // ------------------------------------------------------------------

    page.set_primary_action(__('Load Preview'), () => loadPreview(true));
    page.set_secondary_action(__('Generate All PDFs'), () => generatePDFs());

    $(window).on('beforeunload.ssv2', function () {
        if (refreshInterval) clearInterval(refreshInterval);
    });

    function injectStyles() {
        if ($('#ssv2-styles').length) return;
        $('<style id="ssv2-styles">').html(`
            .ssv2-tabs { margin: 15px 0 0; border-bottom: 1px solid var(--border-color, #d1d8dd); }
            .ssv2-tabs .nav-link { cursor: pointer; padding: 8px 16px; color: #6c7680; border: 0; }
            .ssv2-tabs .nav-link.active { color: #171717; font-weight: 600; box-shadow: inset 0 -2px 0 #2490ef; }
            .ssv2-pane { padding-top: 15px; }
            .ssv2-toolbar {
                display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
                gap: 10px; padding: 10px 12px; background: #f7fafc;
                border: 1px solid #e4e7eb; border-radius: 6px;
            }
            .ssv2-toolbar-group { display: flex; align-items: center; gap: 6px; }
            .ssv2-party-select { min-width: 280px; }
            .ssv2-party-select input { height: 30px; }
            .ssv2-party-select .awesomplete { width: 100%; }
            .ssv2-party-select .awesomplete > ul { max-height: 320px; overflow-y: auto; z-index: 1010; }
            .ssv2-zoom-label { font-size: 12px; min-width: 42px; text-align: center; color: #6c7680; }
            .ssv2-meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
            .ssv2-chip {
                font-size: 12px; padding: 3px 10px; border-radius: 999px;
                background: #f5f7fa; border: 1px solid #e4e7eb; color: #5e6b76;
            }
            .ssv2-stage {
                background: #e9edf1; border: 1px solid #e4e7eb; border-radius: 6px;
                padding: 16px; min-height: 300px; overflow-x: auto;
            }
            .ssv2-frame {
                width: 100%; min-height: 400px; border: 0; background: transparent; display: block;
            }
            .ssv2-empty { text-align: center; color: #8d99a6; padding: 60px 0; }
            .sf-summary-group { display: flex; flex-wrap: wrap; gap: 6px; }
            .sf-pill {
                display: inline-block; padding: 3px 10px; border-radius: 999px;
                font-size: 12px; font-weight: 600; line-height: 1.4; border: 1px solid transparent;
            }
            .sf-pill-success { color: #2e7d32; background: #e7f6e7; border-color: #cfeccc; }
            .sf-pill-danger  { color: #c62828; background: #ffe5e5; border-color: #ffcccc; }
            .sf-pill-warning { color: #a15c00; background: #fff3e0; border-color: #ffe0b2; }
            .sf-pill-info    { color: #3f51b5; background: #e8ebff; border-color: #d2d7ff; }
            .sf-pill-default { color: #5e6b76; background: #f5f7fa; border-color: #e4e7eb; }
            .sf-action-btn {
                background: #fff !important; color: #36414c !important;
                border: 1px solid #d1d8dd !important; box-shadow: none !important; margin: 4px 2px;
            }
            .sf-action-btn:hover { background: #f7fafc !important; border-color: #b8c2cc !important; }
            @media (max-width: 768px) {
                .ssv2-party-select { min-width: 150px; }
                .ssv2-toolbar { flex-direction: column; align-items: stretch; }
            }
        `).appendTo('head');
    }
};

frappe.pages['supplier-statement-forms-v2'].on_page_show = function (wrapper) {
    const page = wrapper.page;
    if (page && page.fields_dict) {
        if (page.fields_dict.from_date) page.fields_dict.from_date.set_value(frappe.datetime.get_today());
        if (page.fields_dict.to_date) page.fields_dict.to_date.set_value(frappe.datetime.get_today());
    }
};

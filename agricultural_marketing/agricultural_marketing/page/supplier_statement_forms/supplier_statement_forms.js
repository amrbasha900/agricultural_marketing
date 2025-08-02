frappe.pages['supplier-statement-forms'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Supplier Statement Forms'),
		single_column: true
	});

	let openingApproach = page.add_field({
	    label: 'Calculate Opening Balance with Totals',
	    fieldtype: 'Check',
	    fieldname: 'calculate_opening_balance_with_totals',
	    default: frappe.db.get_single_value("Agriculture Settings", "calculate_opening_balance_with_totals").then(
	    (value) => {
	        openingApproach.set_value(value);
	    })
	});
    openingApproach.$wrapper.addClass('col-md-3');

    let considerDraft = page.add_field({
	    label: __('Consider Drafts'),
	    fieldtype: 'Check',
	    fieldname: 'consider_draft',
	    default: frappe.db.get_single_value("Agriculture Settings", "consider_drafts").then(
            (value) => {
                considerDraft.set_value(value);
            })
	});
    considerDraft.$wrapper.addClass('col-md-2');

    let considerDraftPayments = page.add_field({
	    label: __('Consider Draft Payments'),
	    fieldtype: 'Check',
	    fieldname: 'consider_draft_payments',
	    default: frappe.db.get_single_value("Agriculture Settings", "consider_draft_payments").then(
            (value) => {
                considerDraftPayments.set_value(value);
            })
	});
    considerDraftPayments.$wrapper.addClass('col-md-2');

    let neglectItems = page.add_field({
	    label: __('Neglect Items'),
	    fieldtype: 'Check',
	    fieldname: 'neglect_items',
	    default: 0
	});
    neglectItems.$wrapper.addClass('col-md-2');

    let company = page.add_field({
	    label: 'Company',
	    fieldtype: 'Link',
	    fieldname: 'company',
	    options: 'Company',
	    reqd: 1,
	    default: frappe.defaults.get_default('company'),
	});
    company.$wrapper.removeClass('col-md-2').addClass('col-md-3');

    let fromDate = page.add_field({
	    label: 'From Date',
	    fieldtype: 'Date',
	    fieldname: 'from_date',
	    reqd: 1,
        default: frappe.datetime.get_today()
	});
    fromDate.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    // Bind a manual change event to the input field
    fromDate.$input.on('change', function() {
        // Check if the field is empty
        if (!fromDate.get_value()) {
            fromDate.value = '';
            fromDate.$wrapper.addClass('has-error');
        }
    });

	let toDate = page.add_field({
	    label: 'To Date',
	    fieldtype: 'Date',
	    fieldname: 'to_date',
	    default: frappe.datetime.get_today()
	});
    toDate.$wrapper.removeClass('col-md-2').addClass('col-md-2');

    // Bind a manual change event to the input field
    toDate.$input.on('change', function() {
        // Check if the field is empty
        if (!toDate.get_value()) {
            toDate.value = '';
        }
    });

	// Simplified Party Type field - fixed to Supplier
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
        fieldname: 'supplier_group',
        options: 'Supplier Group'
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
        }
    });
    supplierField.$wrapper.removeClass('col-md-2').addClass('col-md-3');

    // Update supplier field when supplier group changes
    supplierGroupField.$input.on('change', function() {
        if (supplierField) {
            supplierField.set_value('');
            supplierField.refresh();
        }
    });

    function get_reports(filters) {
        frappe.dom.freeze('Processing...');
        var final_filters = {};
        for (let key in filters) {
            final_filters[key] = filters[key].value;
        }
        validateMandatoryFilters(final_filters);
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.supplier_statement_forms.supplier_statement_forms.get_reports',
            args : {
                filters: final_filters
            },
            callback: function (r) {
                if (r.message.file_urls) {
                    downloadFiles(r.message.file_urls);
                    frappe.dom.unfreeze();
                } else if (r.message.error) {
                    frappe.dom.unfreeze();
                    frappe.throw({
                        title : __("No Data"),
                        indicator: "blue",
                        message: __(r.message.error)
                    });
                }
            },
        });
    }

    async function downloadFiles(file_urls) {
        for (const file_url of file_urls) {
            await new Promise((resolve, reject) => {
                open_url_post(frappe.request.url, {
                    cmd: 'frappe.core.doctype.file.file.download_file',
                    file_url: file_url,
                });
                setTimeout(resolve, 2000);  // Wait for 2 second before downloading the next file
            });
        }
    }

    function validateMandatoryFilters(filters) {
        error = [];
        if (!filters['company']) {
            frappe.dom.unfreeze();
            error.push(__('Company'))
        }
        if (!filters['from_date']) {
            frappe.dom.unfreeze();
            error.push(__('From Date'))
        }
        if (!filters['party_type']) {
            frappe.dom.unfreeze();
            error.push(__('Party Type'))
        }
        if (error.length) {
            frappe.throw({
                title: __('Missing Filters'),
                message: __('Missing Filters') + '<br><ul><li>' + error.join('</li><li>') + '</ul>'
            })
        }
    }
    
    function sendWhatsAppMsg(filters){
        frappe.dom.freeze(`<img src="/assets/agricultural_marketing/img/whatsapp.gif" >`);
        var final_filters = {};
        for (let key in filters) {
            final_filters[key] = filters[key].value;
        }
        
        validateMandatoryFilters(final_filters);
        if (!filters.party_type || !filters.party) {
            frappe.throw(__('Please select a Party Type and Party before sending a message.'));
            return;
        }
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.supplier_statement_forms.supplier_statement_forms.task_msg_creation',
            args: {
                filters: final_filters
            },
            callback: function (res) {
                if (res.message.success) {
                    frappe.msgprint(__('Finish Send WhatsApp Messages Successfully'));
                    frappe.dom.unfreeze();
                } else {
                    frappe.dom.unfreeze();
                    frappe.msgprint(__('Failed to log WhatsApp message: ') + res.message.error);
                }
            }
        });
    }
    
    let $btn = page.set_primary_action( __('Download Reports'), () => { get_reports(page.fields_dict) });
    let sendWhatsappBtn = page.set_secondary_action(__('Send WhatsApp Message'), () => {
        frappe.confirm(
            __('Are you sure you want to send WhatsApp messages and Lock Invoices From Updates?'),
            () => {
                // Yes - proceed
                sendWhatsAppMsg(page.fields_dict);
            },
            () => {
                // No - do nothing
                frappe.msgprint(__('Cancelled'));
            }
        );
    });
}
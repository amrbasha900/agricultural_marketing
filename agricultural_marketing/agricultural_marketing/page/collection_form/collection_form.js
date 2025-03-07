frappe.pages['collection-form'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Collection Form'),
		single_column: true
	});

	let newLayout = page.add_field({
	    label: __('New Layout'),
	    fieldtype: 'Check',
	    fieldname: 'new_layout',
	    default: frappe.db.get_single_value("Agriculture Settings", "new_report_layout").then(
	    (value) => {
	        newLayout.set_value(value);
	    })
	});
    newLayout.$wrapper.addClass('col-md-3');

	let considerDraft = page.add_field({
	    label: __('Consider Drafts'),
	    fieldtype: 'Check',
	    fieldname: 'consider_draft',
	    default: 0
	});
    considerDraft.$wrapper.addClass('col-md-3');

    let ignoreZeroTransactions = page.add_field({
	    label: __('Ignore Zero Transactions'),
	    fieldtype: 'Check',
	    fieldname: 'ignore_zero_transactions',
	    default: frappe.db.get_single_value("Agriculture Settings", "ignore_zero_transactions").then(
	    (value) => {
	        ignoreZeroTransactions.set_value(value);
	    })
	});
    ignoreZeroTransactions.$wrapper.addClass('col-md-3');

    let hideDecimal = page.add_field({
	    label: __('Hide Decimal'),
	    fieldtype: 'Check',
	    fieldname: 'hide_decimal',
	    default: frappe.db.get_single_value("Agriculture Settings", "hide_decimal").then(
	    (value) => {
	        hideDecimal.set_value(value);
	    })
	});
    hideDecimal.$wrapper.addClass('col-md-3');

	let company = page.add_field({
	    label: 'Company',
	    fieldtype: 'Link',
	    fieldname: 'company',
	    options: 'Company',
	    reqd: 1,
	    default: frappe.defaults.get_default('company'),
	});
    company.$wrapper.removeClass('col-md-2').addClass('col-md-4');

    let fromDate = page.add_field({
	    label: 'From Date',
	    fieldtype: 'Date',
	    fieldname: 'from_date',
	    reqd: 1,
        default: frappe.datetime.get_today()
	});
    fromDate.$wrapper.removeClass('col-md-2').addClass('col-md-4');

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
    toDate.$wrapper.removeClass('col-md-2').addClass('col-md-4');

    // Bind a manual change event to the input field
    toDate.$input.on('change', function() {
        // Check if the field is empty
        if (!toDate.get_value()) {
            toDate.value = '';
        }
    });

	let partyTypeField = page.add_field({
	    label: 'Party Type',
	    fieldtype: 'Link',
	    fieldname: 'party_type',
	    options: 'Party Type',
	    reqd: 1,
	    get_query: function() {
	        return {
	            filters: {
	                name: ['in', Object.keys(frappe.boot.party_account_types)],
	            }
	        }
	    },
	    change() {
	        let partyField;
	        let partyGroupField;
            if (!partyTypeField.get_value()) {
                partyField = page.fields_dict['party']
                partyGroupField = page.fields_dict['party_group']
                if (partyGroupField) {
                    partyGroupField.set_value('');
                    partyGroupField.$wrapper.hide();
                }
                if (partyField) {
                    partyField.set_value('');
                    partyField.$wrapper.hide();
                }
            } else {
                partyField = page.fields_dict['party']
                partyGroupField = page.fields_dict['party_group']
                if (!partyGroupField) {
                    partyGroupField = page.add_field({
                        label: 'Party Group',
                        fieldtype: 'Link',
                        fieldname: 'party_group'
                    });
                    partyGroupField.$wrapper.removeClass('col-md-2').addClass('col-md-4');
                }
                if (!partyField) {
                    partyField = page.add_field({
                        label: 'Party',
                        fieldtype: 'Link',
                        fieldname: 'party'
                    });
                }
                    partyField.$wrapper.removeClass('col-md-2').addClass('col-md-4');
                if (partyGroupField) {
                    partyGroupField.set_value('');
                    partyGroupField.$wrapper.show();
                    partyGroupField.df.options = partyTypeField.get_value() + ' Group';
                }
                if (partyField) {
                    partyField.set_value('');
                    partyField.$wrapper.show();
                    partyField.df.options = partyTypeField.get_value();
                    partyField.df.get_query = () => {
                        var field = (partyGroupField.df.options == 'Customer Group') ? 'customer_group' :
                        'supplier_group'
                        if (partyTypeField.get_value() == 'Customer') {
                            var filters = {is_farmer:0}
                            if (partyGroupField.get_value()) {
                                filters[field] = partyGroupField.get_value()
                            }
                            return {
                                filters: filters
                            }
                        } else {
                            var filters = {}
                            if (partyGroupField.get_value()) {
                                filters[field] = partyGroupField.get_value()
                            }
                            return {
                                filters: filters
                            }
                        }
                    }
                }
            }
	    }
	});
    partyTypeField.$wrapper.removeClass('col-md-2').addClass('col-md-4');

    function get_data(filters) {
        frappe.dom.freeze('Processing...');
        var final_filters = {};
        for (let key in filters) {
            final_filters[key] = filters[key].value;
        }
        validateMandatoryFilters(final_filters);
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.collection_form.collection_form.execute',
            args : {
                filters: final_filters
            },
            callback: function (r) {
                if (r.message.file_url) {
                    downloadFiles(r.message.file_url);
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


    function open_pdf(filters) {
        frappe.dom.freeze('Processing...');
        var final_filters = {};
        for (let key in filters) {
            final_filters[key] = filters[key].value;
        }
        final_filters["open_pdf"] = true;
        validateMandatoryFilters(final_filters);
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.collection_form.collection_form.execute',
            args : {
                filters: final_filters
            },
            callback: function (r) {
                if (r.message.html) {
                    frappe.dom.unfreeze();
                    var newWindow = window.open();
                    newWindow.document.write(`${r.message.html}`)
                    newWindow.document.close();
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

    async function downloadFiles(file_url) {
        await new Promise((resolve, reject) => {
            open_url_post(frappe.request.url, {
                cmd: 'frappe.core.doctype.file.file.download_file',
                file_url: file_url,
            });
            setTimeout(resolve, 2000);  // Wait for 2 second before downloading the next file
        });
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
    let $btn = page.set_primary_action( __('Generate Collection Form'), () => { get_data(page.fields_dict) });
    let $btnPDF = page.set_secondary_action( __('Open PDF'), () => { open_pdf(page.fields_dict) });

}
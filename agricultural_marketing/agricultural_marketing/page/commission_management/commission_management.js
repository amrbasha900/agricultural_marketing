frappe.pages['commission-management'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Commission Management',
		single_column: true
	});
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

    let postingDate = page.add_field({
	    label: 'Posting Date',
	    fieldtype: 'Date',
	    fieldname: 'posting_date',
	    default: frappe.datetime.get_today()
	});
    postingDate.$wrapper.removeClass('col-md-2').addClass('col-md-4');

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
            if (!partyTypeField.get_value()) {
                partyField = page.fields_dict['party']
                if (partyField) {
                    partyField.set_value('');
                    partyField.$wrapper.hide();
                }
            } else {
                partyField = page.fields_dict['party']
                if (!partyField) {
                    partyField = page.add_field({
                        label: 'Party',
                        fieldtype: 'Link',
                        fieldname: 'party'
                    });
                }
                partyField.$wrapper.removeClass('col-md-2').addClass('col-md-4');
                if (partyField) {
                    partyField.set_value('');
                    partyField.$wrapper.show();
                    partyField.df.options = partyTypeField.get_value();
                    partyField.df.get_query = () => {
                        if (partyTypeField.get_value() == 'Customer') {
                            var filters = {is_farmer:0}
                            return {
                                filters: filters
                            }
                        } else {
                            var filters = {}
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

    function get_invoices(filters){
        var final_filters = {};
        for (let key in filters) {
            final_filters[key] = filters[key].value;
        }
        validateMandatoryFilters(final_filters);
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.page.commission_management.commission_management.get_invoices',
            args : {
                filters: final_filters
            },
            callback: function (r) {
                console.log(r.message);
                if (r.message["success"] == true && Object.keys(r.message["data"]).length) {
                    let $btn = page.set_primary_action( __('Create Invoices'), () => {
                        frappe.dom.freeze('Processing...');
                        frappe.call({
                            method: 'agricultural_marketing.agricultural_marketing.page.commission_management.commission_management.generate_commission_invoices',
                            args : {
                                data: r.message["data"],
                                filters: final_filters
                            },
                            callback: function (r) {
                                frappe.dom.unfreeze();
                                if (r.message["data"].length) {
                                    $(frappe.render_template("commission_management", {data: r.message["data"]})).appendTo(page.body);
                                    frappe.throw(r.message["msg"]);
                                    let $btn = page.set_primary_action( __('Get Party Invoices'), () => {
                                        get_invoices(page.fields_dict);
                                    });
                                } else {
                                    frappe.msgprint(r.message["msg"]);
                                    setTimeout(() => {
                            			window.location.reload();
                                    }, 5000);
                                }
                            },
                        });
                    });
                } else if (r.message["success"] == true && !Object.keys(r.message["data"]).length) {
                    frappe.msgprint("There is no data to show.");
                } else if (r.message["success"] == false) {
                    frappe.throw(r.message["msg"])
                }
            },
        });
    };

    function validateMandatoryFilters(filters) {
        error = [];
        if (!filters['to_date']) {
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

    let $btn = page.set_primary_action( __('Get Party Invoices'), () => {
        get_invoices(page.fields_dict);
    });
}
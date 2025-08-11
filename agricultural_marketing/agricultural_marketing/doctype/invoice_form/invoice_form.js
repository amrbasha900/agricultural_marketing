// Copyright (c) 2024, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.ui.form.on("Invoice Form", {
    onload: function(frm) {
        set_fields_readonly_based_on_bulk_reference(frm);
    },
 	refresh(frm) {
        set_fields_readonly_based_on_bulk_reference(frm);
     	filter_basic_info_fields(frm);
     	filter_child_tables_fields(frm);
     	frm.add_custom_button("Print", () => {
     	    let dialog = new frappe.ui.Dialog({
     	        title: "Print options",
     	        fields: [
     	            {
                        label: 'Party Type',
                        fieldname: 'party_type',
                        fieldtype: 'Link',
                        options: "DocType",
                        reqd: 1,
                        get_query: function () {
                            return {
                                filters: {
                                    name: ["in", ["Supplier", "Customer"]]
                                },
                            };
					    },
					    onchange: function () {
    					    let partyTypeField = dialog.fields_dict["party_type"];
    					    if (partyTypeField.value) {
                                dialog.set_df_property("party", "options", partyTypeField.value);
                                if (partyTypeField.value == "Supplier") {
    					            dialog.set_df_property("party", "hidden", 0);
                                    dialog.set_df_property("customer_type", "hidden", 1);
                                    dialog.set_df_property("customer_type", "reqd", 0);
                                    dialog.set_df_property("party", "get_query", get_query(frm, partyTypeField.value, ""));
                                } else {
    					            dialog.set_df_property("party", "hidden", 1);
                                    dialog.set_df_property("customer_type", "hidden", 0);
                                    dialog.set_df_property("customer_type", "reqd", 1);
                                }
    					    } else {
    					        dialog.fields_dict["party"].value = "";
    					        dialog.set_df_property("party", "hidden", 1);
    					        dialog.fields_dict["customer_type"].value = "";
                                dialog.set_df_property("customer_type", "hidden", 1);
    					    }
						},
                    },
                    {
                        label: 'Customer Type',
                        fieldname: 'customer_type',
                        fieldtype: 'Select',
                        options: ["", "Customer", "Pamper"],
                        hidden:1,
                        reqd: 1,
                        onchange: function() {
    					    let partyTypeField = dialog.fields_dict["party_type"];
    					    let customerTypeField = dialog.fields_dict["customer_type"];
    					    if (customerTypeField.value) {
                                dialog.set_df_property("party", "hidden", 0);
                                dialog.set_df_property("party", "get_query", get_query(frm, partyTypeField.value,
                                customerTypeField.value));
    					    } else {
    					        dialog.fields_dict["party"].value = "";
                                dialog.set_df_property("party", "hidden", 1);
    					    }
                        },
                    },
                    {
                        label: 'Party',
                        fieldname: 'party',
                        fieldtype: 'Link',
                        hidden: 1,
                        reqd: 1,
                    },
     	        ],
     	        size: "small",
     	        primary_action_label: 'Print',
                primary_action(values) {
                    values['customer_type'] = (values['customer_type']) ? values['customer_type'] : "";
                    values['reference_doctype'] = frm.doc.doctype
                    values['reference_name'] = frm.doc.name
                    dialog.hide();
        			window.open(`/api/method/agricultural_marketing.pdf.get_pdf?filters={"reference_doctype": "${values.reference_doctype}", "reference_name": "${values.reference_name}", "party_type": "${values.party_type}", "party": "${values.party}", "customer_type": "${values.customer_type}"}&template=invoice_form&doctype=invoice_form`);
                }
     	    });
     	    dialog.show();
        })
        frm.add_custom_button("Send via WhatsApp", () => {
            show_whatsapp_send_options(frm);
        });


        if (frm.doc.company) {
            setTimeout(function() {
                check_multiple_customers_credit_limits(frm);
            }, 500);
        }
        frm._credit_validation_done = false;
 	},
 	customer: function (frm, cdt, cdn) {
 	    frm.doc.items.forEach((row)=> {
 	        row.customer = frm.doc.customer;
 	        frm.refresh_field("items");
 	    });
 	},
    pamper: function (frm, cdt, cdn) {
        frm.doc.items.forEach((row)=> {
 	        row.pamper = frm.doc.pamper;
 	        frm.refresh_field("items");
 	    });
 	},
     validate: function(frm) {
        // Final validation before save
        //return validate_all_customers_credit_limits(frm);
    }
});

frappe.ui.form.on("Invoice Form Item", {
    items_add: function (frm, cdt, cdn) {
        let fields = ["item_code", "item_name"];

        let row = frm.selected_doc;
    	row.customer = frm.doc.customer;
	row.pamper = frm.doc.pamper;
        var last_row_index = frm.doc.items.length - 2;
        let prev_row = frm.doc.items[last_row_index]
        for (const [key, value] of Object.entries(prev_row)) {
            if (fields.includes(key))
                row[key] = value;
        }
        frm.refresh_field('items');
        calculate_totals_and_check_multiple_credits(frm);
        
    },
    qty: function (frm, cdt, cdn) {
        calculate_total_line(frm);
        calculate_item_total(frm, cdt, cdn);
        calculate_totals_and_check_multiple_credits(frm);
    },
    price: function (frm, cdt, cdn) {
        calculate_total_line(frm);
        calculate_item_total(frm, cdt, cdn);
        calculate_totals_and_check_multiple_credits(frm);
    },
    items_remove: function(frm, cdt, cdn) {
        calculate_totals_and_check_multiple_credits(frm);
    },
    
    customer: function(frm, cdt, cdn) {
        calculate_totals_and_check_multiple_credits(frm);
    },total: function(frm, cdt, cdn) {
        calculate_totals_and_check_multiple_credits(frm);
    }
});

frappe.ui.form.on("Invoice Form Commission", {
    commission: function (frm, cdt, cdn) {
        calculate_total_commission_line(frm);
    },
    taxes: function (frm, cdt, cdn) {
        calculate_total_commission_line(frm);
    }
});


frappe.ui.form.on("Invoice Form Pamper Commission", {
    price: function (frm, cdt, cdn) {
        calculate_commission(frm);
    },
    percentage: function (frm, cdt, cdn) {
        calculate_commission(frm);
    }
});


function filter_basic_info_fields(frm) {
    frm.set_query("supplier", function () {
        return {
            filters: {
                is_farmer: 1,
            },
        };
    });

    frm.set_query("customer", function () {
        return {
            filters: {
                is_customer: 1,
                is_frozen: 0
            },
        };
    });

    frm.set_query("pamper", function () {
        return {
            filters: {
                is_pamper: 1,
                is_frozen: 0
            },
        };
    });
}

function filter_child_tables_fields(frm) {
    frm.fields_dict['items'].grid.get_field("customer").get_query = function() {
            return {
                filters: {"is_customer": 1, "is_frozen": 0}
            }
    };
    frm.fields_dict['items'].grid.get_field("pamper").get_query = function() {
        return {
            filters: {"is_pamper": 1, "is_frozen": 0}
        }
    };
    frm.fields_dict['items'].grid.get_field("item_code").get_query = function() {
        return {
            filters: {
                commission_item: 0,
                is_agriculture_item: 1
            }
        }
    };
    frm.fields_dict['commissions'].grid.get_field("item").get_query = function() {
        return {
            filters: {
                commission_item: 1
            }
        }
    };
    frm.fields_dict['pamper_commissions'].grid.get_field("pamper").get_query = function() {
        return {
            filters: {
                name: ["in", [frm.doc.pamper]]
            }
        }
    }
}

function calculate_total_line(frm) {
    let row = frm.selected_doc;
    row.qty = (row.qty) ? row.qty: null;
    row.price = (row.price) ? row.price: null;
    row.total = row.qty * row.price;
    frm.refresh_field('items');
}

function calculate_total_commission_line(frm) {
    let row = frm.selected_doc;
    row.taxes = (row.taxes) ? row.taxes: 0;
    row.commission = (row.commission) ? row.commission: 0;
    row.commission_total = row.commission + row.taxes;
    frm.refresh_field('commissions');
}

function calculate_commission(frm) {
    let row = frm.selected_doc;
    row.price = (row.price) ? row.price: 0;
    row.percentage = (row.percentage) ? row.percentage: 0;
    row.commission = (row.price * row.percentage) / 100;
    frm.refresh_field('pamper_commissions');
}

let get_query = function (frm, partyType, customerType) {
    result = []
    if (partyType == "Supplier") {
        result.push(frm.doc.supplier);
    } else {
        let rows = frm.doc.items;
        if (customerType == "Customer") {
            for (var i=0; i<rows.length; i++) {
                if (!result.includes(rows[i].customer)) {
                    result.push(rows[i].customer)
                }
            }
        } else {
            for (var i=0; i<rows.length; i++) {
                if (!result.includes(rows[i].pamper)) {
                    result.push(rows[i].pamper)
                }
            }
        }
    }
    return {
        filters: {
            name: ["in", result]
        },
    };
}


function show_whatsapp_send_options(frm) {
    let dialog = new frappe.ui.Dialog({
        title: "Send via WhatsApp",
        fields: [
            {
                label: 'Send To',
                fieldname: 'send_to',
                fieldtype: 'Select',
                options: ["", "Customers", "Supplier", "Both"],
                reqd: 1
            }
        ],
        size: "small",
        primary_action_label: 'Send',
        primary_action(values) {
            dialog.hide();
            if (values.send_to === "Customers") {
                send_invoice_via_whatsapp_customers_manual(frm);
            } else if (values.send_to === "Supplier") {
                send_invoice_via_whatsapp_supplier_manual(frm);
            } else if (values.send_to === "Both") {
                send_invoice_via_whatsapp_both_manual(frm);
            }
        }
    });
    dialog.show();
}

function send_invoice_via_whatsapp_customers_manual(frm) {
    let customers = [...new Set(frm.doc.items.map(item => item.customer))];
    
    if (customers.length === 0) {
        frappe.msgprint(__("No customers found in items table"));
        return;
    }
    
    // Check for WhatsApp numbers only (not the enable flag)
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_parties_with_whatsapp_numbers",
        args: {
            customers: customers
        },
        callback: function(r) {
            if (r.message && r.message.customers_with_whatsapp.length > 0) {
                show_whatsapp_confirmation_dialog_manual(frm, r.message.customers_with_whatsapp, r.message.customers_without_whatsapp, "customers");
            } else {
                frappe.msgprint(__("No customers have WhatsApp numbers configured"));
            }
        }
    });
}

function send_invoice_via_whatsapp_supplier_manual(frm) {
    if (!frm.doc.supplier) {
        frappe.msgprint(__("No supplier found in this invoice"));
        return;
    }
    
    // Check for WhatsApp number only (not the enable flag)
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_parties_with_whatsapp_numbers",
        args: {
            customers: [],
            supplier: frm.doc.supplier
        },
        callback: function(r) {
            if (r.message && r.message.supplier_has_whatsapp) {
                show_whatsapp_confirmation_dialog_manual(frm, [frm.doc.supplier], [], "supplier");
            } else {
                frappe.msgprint(__("Supplier does not have WhatsApp number configured"));
            }
        }
    });
}

function send_invoice_via_whatsapp_both_manual(frm) {
    let customers = [...new Set(frm.doc.items.map(item => item.customer))];
    
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_parties_with_whatsapp_numbers",
        args: {
            customers: customers,
            supplier: frm.doc.supplier
        },
        callback: function(r) {
            if (r.message) {
                let total_with_whatsapp = r.message.customers_with_whatsapp.length + (r.message.supplier_has_whatsapp ? 1 : 0);
                if (total_with_whatsapp > 0) {
                    show_whatsapp_confirmation_dialog_both_manual(frm, r.message);
                } else {
                    frappe.msgprint(__("No parties have WhatsApp numbers configured"));
                }
            }
        }
    });
}

function show_whatsapp_confirmation_dialog_manual(frm, parties_with_whatsapp, parties_without_whatsapp, type) {
    let message = `<p><strong>Ready to send via WhatsApp:</strong></p>`;
    message += `<ul>`;
    parties_with_whatsapp.forEach(party => {
        message += `<li>${party}</li>`;
    });
    message += `</ul>`;
    
    if (parties_without_whatsapp.length > 0) {
        message += `<p><strong>No WhatsApp number configured for:</strong></p>`;
        message += `<ul>`;
        parties_without_whatsapp.forEach(party => {
            message += `<li>${party}</li>`;
        });
        message += `</ul>`;
    }
    
    frappe.confirm(
        message + "<br>Do you want to proceed with sending?",
        function() {
            if (type === "customers") {
                send_to_customers_manual(frm, parties_with_whatsapp);
            } else if (type === "supplier") {
                send_to_supplier_manual(frm, parties_with_whatsapp[0]);
            }
        }
    );
}

function show_whatsapp_confirmation_dialog_both_manual(frm, data) {
    let message = `<p><strong>Ready to send via WhatsApp:</strong></p><ul>`;
    
    if (data.supplier_has_whatsapp) {
        message += `<li>Supplier: ${frm.doc.supplier}</li>`;
    }
    
    data.customers_with_whatsapp.forEach(customer => {
        message += `<li>Customer: ${customer}</li>`;
    });
    message += `</ul>`;
    
    let without_whatsapp_count = data.customers_without_whatsapp.length + (data.supplier_has_whatsapp ? 0 : 1);
    if (without_whatsapp_count > 0) {
        message += `<p><strong>No WhatsApp number configured for:</strong></p><ul>`;
        if (!data.supplier_has_whatsapp) {
            message += `<li>Supplier: ${frm.doc.supplier}</li>`;
        }
        data.customers_without_whatsapp.forEach(customer => {
            message += `<li>Customer: ${customer}</li>`;
        });
        message += `</ul>`;
    }
    
    frappe.confirm(
        message + "<br>Do you want to proceed with sending?",
        function() {
            send_to_both_manual(frm, data);
        }
    );
}

function send_to_customers_manual(frm, customers) {
    frappe.show_alert({
        message: __("Preparing WhatsApp messages..."),
        indicator: "blue"
    });
    
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.send_invoice_whatsapp_bulk_manual",
        args: {
            invoice_name: frm.doc.name,
            customers: customers
        },
        callback: function(r) {
            if (r.message && r.message.success) {
                frappe.show_alert({
                    message: r.message.message,
                    indicator: "green"
                });
            } else {
                frappe.show_alert({
                    message: r.message ? r.message.error : "Error sending WhatsApp messages",
                    indicator: "red"
                });
            }
        }
    });
}

function send_to_supplier_manual(frm, supplier) {
    frappe.show_alert({
        message: __("Preparing WhatsApp message for supplier..."),
        indicator: "blue"
    });
    
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.send_invoice_whatsapp_supplier_manual",
        args: {
            invoice_name: frm.doc.name,
            supplier: supplier
        },
        callback: function(r) {
            if (r.message && r.message.success) {
                frappe.show_alert({
                    message: r.message.message,
                    indicator: "green"
                });
            } else {
                frappe.show_alert({
                    message: r.message ? r.message.error : "Error sending WhatsApp message",
                    indicator: "red"
                });
            }
        }
    });
}

function send_to_both_manual(frm, data) {
    frappe.show_alert({
        message: __("Preparing WhatsApp messages..."),
        indicator: "blue"
    });
    
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.send_invoice_whatsapp_all_manual",
        args: {
            invoice_name: frm.doc.name,
            customers: data.customers_with_whatsapp,
            supplier: data.supplier_has_whatsapp ? frm.doc.supplier : null
        },
        callback: function(r) {
            if (r.message && r.message.success) {
                frappe.show_alert({
                    message: r.message.message,
                    indicator: "green"
                });
            } else {
                frappe.show_alert({
                    message: r.message ? r.message.error : "Error sending WhatsApp messages",
                    indicator: "red"
                });
            }
        }
    });
}


function set_fields_readonly_based_on_bulk_reference(frm) {
    // Check if bulk_invoice_reference has a value
    if (frm.doc.bulk_invoice_reference) {
        // Get all fields from DocType metadata
        let all_fields = get_all_doctype_fields(frm);
        
        // Loop through all fields and make them readonly
        all_fields.forEach(function(fieldname) {
            // Skip making bulk_invoice_reference itself readonly if you want users to be able to clear it
            // Remove this condition if you want ALL fields including bulk_invoice_reference to be readonly
            if (fieldname !== 'bulk_invoice_reference') {
                frm.set_df_property(fieldname, 'read_only', 1);
            }
        });
        
        
    } else {
        // If bulk_invoice_reference is empty, make fields editable again
        let all_fields = get_all_doctype_fields(frm);
        
        // all_fields.forEach(function(fieldname) {
        //     // Make fields editable (you might want to add conditions here for fields that should always be readonly)
        //     // Skip system fields that should remain readonly
        //     if (!is_system_field(fieldname)) {
        //         frm.set_df_property(fieldname, 'read_only', 0);
        //     }
        // });
        
        // // Clear any previous messages
        // frm.dashboard.clear_comment();
    }
}

function get_all_doctype_fields(frm) {
    let all_fields = [];
    
    // Method 1: Get from DocType meta (most comprehensive)
    if (frm.meta && frm.meta.fields) {
        frm.meta.fields.forEach(function(field) {
            if (field.fieldname && field.fieldtype !== 'Section Break' && 
                field.fieldtype !== 'Column Break' && field.fieldtype !== 'HTML') {
                all_fields.push(field.fieldname);
            }
        });
    }
    
    // Method 2: Also include fields from form fields_dict (in case some are missed)
    if (frm.fields_dict) {
        Object.keys(frm.fields_dict).forEach(function(fieldname) {
            if (!all_fields.includes(fieldname)) {
                all_fields.push(fieldname);
            }
        });
    }
    
    // Method 3: Get from document object (includes all data fields)
    if (frm.doc) {
        Object.keys(frm.doc).forEach(function(fieldname) {
            if (!all_fields.includes(fieldname) && !is_system_field(fieldname)) {
                all_fields.push(fieldname);
            }
        });
    }
    
    console.log('All fields found:', all_fields); // For debugging
    return all_fields;
}

function is_system_field(fieldname) {
    // List of system fields that should not be made readonly
    let system_fields = [
        'name', 'owner', 'creation', 'modified', 'modified_by', 
        'docstatus', 'doctype', 'idx', '_user_tags', '_comments', 
        '_assign', '_liked_by', '_seen'
    ];
    
    return system_fields.includes(fieldname) || fieldname.startsWith('_');
}

// Client Script for Invoice Form - Credit Limit Validation (Multi-Customer)

// Utility functions first
function calculate_item_total(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    let total = flt(row.qty) * flt(row.price);
    frappe.model.set_value(cdt, cdn, 'total', total);
}

function calculate_grand_total(frm) {
    let grand_total = 0;
    
    // Calculate total from items
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item) {
            grand_total += flt(item.total);
        });
    }
    
    // Add commissions total
    if (frm.doc.commissions) {
        frm.doc.commissions.forEach(function(commission) {
            grand_total += flt(commission.commission_total);
        });
    }
    
    // Add other totals if needed
    grand_total += flt(frm.doc.total_commissions_and_taxes);
    
    frm.set_value('grand_total', grand_total);
}

function get_customers_from_items(frm) {
    let customers = new Set();
    
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item) {
            if (item.customer) {
                customers.add(item.customer);
            }
        });
    }
    
    return Array.from(customers);
}

function calculate_customer_total(frm, customer) {
    let customer_total = 0;
    
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item) {
            if (item.customer === customer) {
                customer_total += flt(item.total);
            }
        });
    }
    
    return customer_total;
}

function clear_credit_limit_indicators(frm) {
    // Remove credit limit related indicators and comments
    if (frm.dashboard) {
        // Remove indicators
        if (frm.dashboard.indicators) {
            frm.dashboard.indicators = frm.dashboard.indicators.filter(function(indicator) {
                return !indicator.label.includes('Credit') && 
                       !indicator.label.includes('Exceeded') && 
                       !indicator.label.includes('Customers');
            });
        }
        
        // Remove comments/warnings
        if (frm.dashboard.stats_area_row) {
            frm.dashboard.stats_area_row.find('.form-comment-box').remove();
        }
        
        frm.dashboard.refresh();
    }
}

function format_currency(amount, currency) {
    return frappe.format(amount, {fieldtype: 'Currency', currency: currency || frappe.defaults.get_default('currency')});
}

function get_customer_display_name(customer) {
    return customer;
}

function validate_all_customers_credit_limits(frm) {
    // Use frappe.validated to control save process
    frappe.validated = false; // Prevent save initially
    
    if (!frm.doc.company) {
        frappe.validated = true;
        return;
    }
    
    let customers = get_customers_from_items(frm);
    if (customers.length === 0) {
        frappe.validated = true;
        return;
    }
    
    // Check if already validated in this save cycle
    if (frm._credit_validation_done) {
        frappe.validated = true;
        return;
    }
    
    // Validate each customer
    validate_customers_for_save_sync(frm, customers, 0, []);
}

function validate_customers_for_save_sync(frm, customers, index, violations) {
    if (index >= customers.length) {
        // All customers checked
        if (violations.length > 0) {
            // Show error and prevent save
            show_credit_limit_save_error(frm, violations);
            frappe.validated = false;
        } else {
            // All OK, allow save
            frm._credit_validation_done = true;
            frappe.validated = true;
        }
        return;
    }
    
    let customer = customers[index];
    let customer_total = calculate_customer_total(frm, customer);
    
    if (customer_total <= 0) {
        // Skip customers with no items
        validate_customers_for_save_sync(frm, customers, index + 1, violations);
        return;
    }
    
    // Make synchronous call to check credit limit
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.check_customer_credit_limit_detailed',
        args: {
            customer: customer,
            company: frm.doc.company,
            current_invoice_amount: customer_total,
            exclude_invoice: frm.doc.name
        },
        async: false, // Make synchronous
        callback: function(r) {
            if (r.message && r.message.is_over_limit) {
                let customer_name = get_customer_display_name(customer);
                violations.push({
                    customer: customer,
                    customer_name: customer_name,
                    customer_total: customer_total,
                    credit_data: r.message
                });
            }
            
            // Check next customer
            validate_customers_for_save_sync(frm, customers, index + 1, violations);
        }
    });
}

function show_credit_limit_save_error(frm, violations) {
    let error_content = '<div style="font-family: Arial, sans-serif;">';
    error_content += '<h4 style="color: #d73527; margin-bottom: 15px;">Cannot Save - Credit Limits Exceeded</h4>';
    error_content += '<p style="margin-bottom: 15px;">The following customers exceed their credit limits:</p>';
    
    violations.forEach(function(violation, index) {
        let credit_data = violation.credit_data;
        error_content += '<div style="margin-bottom: 15px; padding: 10px; border: 1px solid #d73527; background-color: #fff5f5; border-radius: 5px;">';
        error_content += '<h5 style="color: #d73527; margin: 0 0 8px 0;">' + (index + 1) + '. ' + violation.customer_name + '</h5>';
        error_content += '<table style="width: 100%; font-size: 13px;">';
        error_content += '<tr><td style="padding: 2px; width: 40%;">Credit Limit:</td><td style="padding: 2px; font-weight: bold;">' + format_currency(credit_data.credit_limit, frm.doc.currency) + '</td></tr>';
        error_content += '<tr><td style="padding: 2px;">Current Balance:</td><td style="padding: 2px;">' + format_currency(credit_data.total_current_balance, frm.doc.currency) + '</td></tr>';
        error_content += '<tr><td style="padding: 2px;">Items in Invoice:</td><td style="padding: 2px;">' + format_currency(violation.customer_total, frm.doc.currency) + '</td></tr>';
        error_content += '<tr><td style="padding: 2px;"><strong>Total Exposure:</strong></td><td style="padding: 2px; font-weight: bold;">' + format_currency(credit_data.total_exposure, frm.doc.currency) + '</td></tr>';
        error_content += '<tr style="color: #d73527;"><td style="padding: 2px;"><strong>Excess Amount:</strong></td><td style="padding: 2px; font-weight: bold;">' + format_currency(credit_data.excess_amount, frm.doc.currency) + '</td></tr>';
        error_content += '</table>';
        error_content += '</div>';
    });
    
    error_content += '<p style="margin-top: 15px; color: #666; font-style: italic;">Please reduce the invoice amounts or contact the customers to make payments before saving.</p>';
    error_content += '</div>';
    
    frappe.msgprint({
        title: __('Credit Limit Exceeded - Cannot Save'),
        indicator: 'red',
        message: error_content
    });
}

// Credit limit checking functions
function check_customers_sequentially(frm, customers, index, results) {
    if (index >= customers.length) {
        // All customers checked, display results
        display_multiple_customers_status(frm, results);
        return;
    }
    
    let customer = customers[index];
    let customer_total = calculate_customer_total(frm, customer);
    
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.check_customer_credit_limit_detailed',
        args: {
            customer: customer,
            company: frm.doc.company,
            current_invoice_amount: customer_total,
            exclude_invoice: frm.doc.name
        },
        callback: function(r) {
            results.push({
                customer: customer,
                customer_total: customer_total,
                credit_data: r.message
            });
            
            // Check next customer
            check_customers_sequentially(frm, customers, index + 1, results);
        }
    });
}

function display_multiple_customers_status(frm, customer_results) {
    // Clear existing indicators
    clear_credit_limit_indicators(frm);
    
    let over_limit_customers = [];
    
    customer_results.forEach(function(result) {
        let credit_data = result.credit_data;
        
        if (!credit_data || !credit_data.has_credit_limit) {
            return;
        }
        
        let total_exposure = credit_data.total_current_balance + result.customer_total;
        
        if (total_exposure > credit_data.credit_limit) {
            let excess = total_exposure - credit_data.credit_limit;
            over_limit_customers.push({
                customer: result.customer,
                customer_name: get_customer_display_name(result.customer),
                excess: excess,
                credit_data: credit_data
            });
        }
    });
    
    // Only show warning if there are customers over limit
    if (over_limit_customers.length > 0) {
        // Create detailed warning message
        let warning_lines = [];
        over_limit_customers.forEach(function(c) {
            warning_lines.push(c.customer_name + ' (Excess: ' + format_currency(c.excess, frm.doc.currency) + ')');
        });
        
        frm.dashboard.add_comment(
            __('Warning: Credit limits exceeded for: {0}', [warning_lines.join(', ')]), 
            'red', true
        );
    }
}

function check_multiple_customers_credit_limits(frm) {
    if (!frm.doc.company) return;
    
    let customers = get_customers_from_items(frm);
    
    if (customers.length === 0) {
        clear_credit_limit_indicators(frm);
        return;
    }
    
    // Check each customer using sequential calls
    check_customers_sequentially(frm, customers, 0, []);
}

function calculate_totals_and_check_multiple_credits(frm) {
    calculate_grand_total(frm);
    
    // Check credit limits after a short delay
    setTimeout(function() {
        if (frm.doc.company) {
            check_multiple_customers_credit_limits(frm);
        }
    }, 100);
}

// Dialog functions
function check_customers_for_dialog(frm, customers, index, results) {
    if (index >= customers.length) {
        // All customers checked, show dialog
        let dialog_content = generate_customers_credit_dialog_content(frm, results);
        
        let d = new frappe.ui.Dialog({
            title: __('Credit Limits Summary - {0} Customers', [customers.length]),
            fields: [
                {
                    fieldtype: 'HTML',
                    fieldname: 'credit_details',
                    options: dialog_content
                }
            ],
            size: 'extra-large'
        });
        
        d.show();
        return;
    }
    
    let customer = customers[index];
    let customer_total = calculate_customer_total(frm, customer);
    
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.check_customer_credit_limit_detailed',
        args: {
            customer: customer,
            company: frm.doc.company,
            current_invoice_amount: customer_total,
            exclude_invoice: frm.doc.name
        },
        callback: function(r) {
            results.push({
                customer: customer,
                customer_name: get_customer_display_name(customer),
                customer_total: customer_total,
                credit_data: r.message
            });
            
            // Check next customer
            check_customers_for_dialog(frm, customers, index + 1, results);
        }
    });
}

function generate_customers_credit_dialog_content(frm, customer_results) {
    let content = '<div style="font-family: Arial, sans-serif;">';
    content += '<div style="margin-bottom: 15px; padding: 10px; background-color: #f8f9fa; border-radius: 5px;">';
    content += '<strong>Invoice:</strong> ' + (frm.doc.name || 'New') + ' | ';
    content += '<strong>Company:</strong> ' + frm.doc.company + ' | ';
    content += '<strong>Total Amount:</strong> ' + format_currency(frm.doc.grand_total, frm.doc.currency);
    content += '</div>';
    
    customer_results.forEach(function(result, index) {
        let credit_data = result.credit_data;
        let status_color = '#2e7d32'; // green
        let status_text = 'Within Limit';
        
        if (!credit_data.has_credit_limit) {
            status_color = '#1976d2'; // blue
            status_text = 'No Credit Limit';
        } else if (credit_data.is_over_limit) {
            status_color = '#d73527'; // red
            status_text = 'EXCEEDED';
        } else if (credit_data.available_credit < (credit_data.credit_limit * 0.1)) {
            status_color = '#f57c00'; // orange
            status_text = 'Low Credit';
        }
        
        content += '<div style="margin-bottom: 20px; border: 1px solid ' + status_color + '; padding: 15px; border-radius: 5px;">';
        content += '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">';
        content += '<h5 style="margin: 0; color: ' + status_color + ';">' + (index + 1) + '. ' + result.customer_name + '</h5>';
        content += '<span style="background-color: ' + status_color + '; color: white; padding: 4px 8px; border-radius: 3px; font-size: 12px;">';
        content += status_text + '</span>';
        content += '</div>';
        
        if (credit_data.has_credit_limit) {
            content += '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">';
            content += '<tr><td style="padding: 4px; width: 50%; border-bottom: 1px solid #eee;">Credit Limit:</td>';
            content += '<td style="padding: 4px; font-weight: bold; border-bottom: 1px solid #eee;">' + format_currency(credit_data.credit_limit, frm.doc.currency) + '</td></tr>';
            content += '<tr><td style="padding: 4px; border-bottom: 1px solid #eee;">Current Balance:</td>';
            content += '<td style="padding: 4px; border-bottom: 1px solid #eee;">' + format_currency(credit_data.total_current_balance, frm.doc.currency) + '</td></tr>';
            content += '<tr><td style="padding: 4px; border-bottom: 1px solid #eee;">Customer\'s Items in Invoice:</td>';
            content += '<td style="padding: 4px; border-bottom: 1px solid #eee;">' + format_currency(result.customer_total, frm.doc.currency) + '</td></tr>';
            
            let bg_color = credit_data.is_over_limit ? '#ffebe9' : '#e8f5e8';
            content += '<tr style="background-color: ' + bg_color + ';"><td style="padding: 4px; font-weight: bold;">Total Exposure:</td>';
            content += '<td style="padding: 4px; font-weight: bold;">' + format_currency(credit_data.total_exposure, frm.doc.currency) + '</td></tr>';
            
            if (credit_data.is_over_limit) {
                content += '<tr style="background-color: #ffebe9; color: #d73527;"><td style="padding: 4px; font-weight: bold;">Excess Amount:</td>';
                content += '<td style="padding: 4px; font-weight: bold;">' + format_currency(credit_data.excess_amount, frm.doc.currency) + '</td></tr>';
            } else {
                content += '<tr style="background-color: #e8f5e8; color: #2e7d32;"><td style="padding: 4px; font-weight: bold;">Available Credit:</td>';
                content += '<td style="padding: 4px; font-weight: bold;">' + format_currency(credit_data.available_credit, frm.doc.currency) + '</td></tr>';
            }
            
            content += '<tr><td style="padding: 4px;">Credit Utilization:</td>';
            content += '<td style="padding: 4px;">' + credit_data.credit_utilization_percent.toFixed(1) + '%</td></tr>';
            content += '</table>';
        } else {
            content += '<p style="margin: 0; color: #666; font-style: italic;">';
            content += 'No credit limit configured for this customer and company.</p>';
        }
        
        content += '</div>';
    });
    
    content += '</div>';
    return content;
}

function show_multiple_customers_credit_dialog(frm, customers) {
    // Check customers and show dialog
    check_customers_for_dialog(frm, customers, 0, []);
}

function check_all_customers_credit_limits(frm) {
    if (!frm.doc.company) {
        frappe.msgprint(__('Please select company first'));
        return;
    }
    
    let customers = get_customers_from_items(frm);
    
    if (customers.length === 0) {
        frappe.msgprint(__('No customers found in invoice items'));
        return;
    }
    
    // Show detailed dialog for all customers
    show_multiple_customers_credit_dialog(frm, customers);
}

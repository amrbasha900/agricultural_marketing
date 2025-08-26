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

         if (frm.doc.docstatus === 1 && !frm.doc.is_return ) {
            frm.add_custom_button(__("Create Return Invoice"), function() {
                create_return_invoice_from_original(frm);
            }, __("Actions"));
            
            // Add button to view return status
            frm.add_custom_button(__("View Return Status"), function() {
                show_return_status_dialog(frm);
            }, __("Actions"));
        }
        if (frm.doc.docstatus === 1) {
            show_return_info_indicator(frm);
            
            // Handle return invoice display
            if (frm.doc.is_return) {
                handle_return_invoice_form(frm);
            }
        }
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
     return_against: function(frm) {
        if (frm.doc.is_return && frm.doc.return_against) {
            validate_return_against_invoice(frm);
        }
    },is_return: function(frm) {
        if (frm.doc.is_return) {
            // Clear items when converting to return
            if (frm.doc.items && frm.doc.items.length > 0) {
                frappe.confirm(
                    __('Converting to return will clear all current items. Continue?'),
                    function() {
                        frm.clear_table('items');
                        frm.refresh_field('items');
                    },
                    function() {
                        frm.set_value('is_return', 0);
                    }
                );
            }
        } else {
            // Clear return_against when unchecking is_return
            frm.set_value('return_against', '');
        }
    },before_save: function(frm) {
        return validate_return_invoice_client_side(frm);
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
        
        // Initialize return tracking fields for new items
        if (!frm.doc.is_return) {
            row.returned_qty = 0;
            row.available_qty = row.qty || 0;
        }
        
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
        
        // Update available quantity for non-return invoices
        if (!frm.doc.is_return) {
            let row = locals[cdt][cdn];
            row.available_qty = row.qty - (row.returned_qty || 0);
            frm.refresh_field('items');
        }
        
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
function create_return_invoice_from_original(frm) {
    // First validate if return can be created
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_return_validation_status',
        args: {
            invoice_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.can_create) {
                // Can create return, proceed
                proceed_with_return_creation(frm);
            } else {
                // Cannot create return, show error
                let reason = r.message ? r.message.reason : 'Unknown error';
                let details = r.message ? r.message.details : {};
                
                show_return_validation_error(frm, reason, details);
            }
        }
    });
}

function proceed_with_return_creation(frm) {
    frappe.confirm(
        __('This will create a return invoice with negative quantities and amounts. Continue?'),
        function() {
            frappe.show_alert({
                message: __('Creating return invoice...'),
                indicator: 'blue'
            });
            
            frappe.call({
                method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.create_return_invoice',
                args: {
                    original_invoice_name: frm.doc.name
                },
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({
                            message: __('Return invoice created successfully'),
                            indicator: 'green'
                        });
                        
                        // Open the new return invoice
                        frappe.set_route('Form', 'Invoice Form', r.message.return_invoice_name);
                    } else {
                        let error = r.message ? r.message.error : 'Failed to create return invoice';
                        frappe.msgprint({
                            title: __('Error'),
                            indicator: 'red',
                            message: error
                        });
                    }
                }
            });
        }
    );
}

function show_return_validation_error(frm, reason, details) {
    let message = '<div style="font-family: Arial, sans-serif;">';
    message += '<h4 style="color: #d73527; margin-bottom: 15px;">Cannot Create Return Invoice</h4>';
    message += '<p style="margin-bottom: 15px;"><strong>Reason:</strong> ' + reason + '</p>';
    
    // Show additional details if available
    if (details.existing_returns && details.existing_returns.length > 0) {
        message += '<p style="margin-bottom: 10px;"><strong>Existing Returns:</strong></p>';
        message += '<table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">';
        message += '<thead><tr style="background-color: #f8f9fa;">';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Return Invoice</th>';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Status</th>';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Date</th>';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Amount</th>';
        message += '</tr></thead><tbody>';
        
        details.existing_returns.forEach(function(ret) {
            let status = ret.docstatus === 1 ? 'Submitted' : ret.docstatus === 0 ? 'Draft' : 'Cancelled';
            let status_color = ret.docstatus === 1 ? '#28a745' : ret.docstatus === 0 ? '#ffc107' : '#dc3545';
            
            message += '<tr>';
            message += '<td style="padding: 8px; border: 1px solid #ddd;">' + ret.name + '</td>';
            message += '<td style="padding: 8px; border: 1px solid #ddd; color: ' + status_color + ';">' + status + '</td>';
            message += '<td style="padding: 8px; border: 1px solid #ddd;">' + frappe.datetime.str_to_user(ret.posting_date) + '</td>';
            message += '<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">' + format_currency(ret.grand_total) + '</td>';
            message += '</tr>';
        });
        
        message += '</tbody></table>';
        message += '<p style="color: #666; font-style: italic;">Please cancel or delete existing returns before creating a new one.</p>';
    }
    
    message += '</div>';
    
    frappe.msgprint({
        title: __('Cannot Create Return'),
        indicator: 'red',
        message: message
    });
}

function show_return_info_indicator(frm) {
    // Check if this invoice has any returns
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_invoice_return_info',
        args: {
            invoice_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.exists && r.message.returns.length > 0) {
                let returns = r.message.returns;
                let submitted_returns = returns.filter(ret => ret.docstatus === 1);
                let draft_returns = returns.filter(ret => ret.docstatus === 0);
                
                let indicator_text = '';
                if (submitted_returns.length > 0) {
                    indicator_text += submitted_returns.length + ' submitted return(s)';
                }
                if (draft_returns.length > 0) {
                    if (indicator_text) indicator_text += ', ';
                    indicator_text += draft_returns.length + ' draft return(s)';
                }
                
                if (indicator_text) {
                    frm.dashboard.add_indicator(__('Returns: {0}', [indicator_text]), 'orange');
                }
            }
        }
    });
}

function handle_return_invoice_form(frm) {
    if (frm.doc.is_return) {
        // Add visual indicators
        frm.dashboard.add_indicator(__('Return Invoice'), 'red');
        
        if (frm.doc.return_against) {
            frm.dashboard.add_indicator(__('Return Against: {0}', [frm.doc.return_against]), 'orange');
            frm.dashboard.add_comment(
                __('This is a return invoice against {0}', [frm.doc.return_against]),
                'blue'
            );
        }
        
        // Ensure negative values are maintained
        ensure_negative_values_for_return(frm);
    }
}

function ensure_negative_values_for_return(frm) {
    if (!frm.doc.is_return) return;
    
    let needs_refresh = false;
    
    // Ensure grand total is negative
    if (frm.doc.grand_total > 0) {
        frm.set_value('grand_total', -Math.abs(frm.doc.grand_total));
        needs_refresh = true;
    }
    
    // Ensure item totals are negative
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item, idx) {
            if (item.total > 0) {
                frappe.model.set_value('Invoice Form Item', item.name, 'total', -Math.abs(item.total));
                needs_refresh = true;
            }
            if (item.qty > 0) {
                frappe.model.set_value('Invoice Form Item', item.name, 'qty', -Math.abs(item.qty));
                needs_refresh = true;
            }
        });
    }
    
    if (needs_refresh) {
        frm.refresh_fields();
    }
}
function create_return_invoice_from_original(frm) {
    // First validate if return can be created
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_return_validation_status',
        args: {
            invoice_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.can_create) {
                // Show return items selection dialog
                show_return_items_dialog(frm);
            } else {
                // Cannot create return, show error
                let reason = r.message ? r.message.reason : 'Unknown error';
                let details = r.message ? r.message.details : {};
                
                show_return_validation_error(frm, reason, details);
            }
        }
    });
}

function show_return_items_dialog(frm) {
    // Get available items for return
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_returnable_items',
        args: {
            invoice_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.success) {
                create_return_dialog(frm, r.message.items);
            } else {
                frappe.msgprint({
                    title: __('Error'),
                    indicator: 'red',
                    message: r.message ? r.message.error : 'Failed to get returnable items'
                });
            }
        }
    });
}

function create_return_dialog(frm, returnable_items) {
    // Get unique items and customers for filter options
    let unique_items = [...new Set(returnable_items.map(item => item.item_code))].sort();
    let unique_customers = [...new Set(returnable_items.map(item => item.customer).filter(c => c))].sort();
    let unique_pampers = [...new Set(returnable_items.map(item => item.pamper).filter(p => p))].sort();
    
    let dialog_fields = [
        {
            fieldtype: 'Section Break',
            label: 'Filters',
            css_class: 'filter-section'
        },
        {
            fieldtype: 'Column Break'
        },
        {
            fieldtype: 'Link',
            fieldname: 'filter_item',
            label: 'Filter by Item',
            options: 'Item',
            placeholder: 'All Items',
            onchange: function() {
                apply_return_filters(returnable_items);
            }
        },
        {
            fieldtype: 'Column Break'
        },
        {
            fieldtype: 'Link',
            fieldname: 'filter_customer',
            label: 'Filter by Customer',
            options: 'Customer',
            placeholder: 'All Customers',
            onchange: function() {
                apply_return_filters(returnable_items);
            }
        },
        {
            fieldtype: 'Column Break'
        },
        {
            fieldtype: 'Link',
            fieldname: 'filter_pamper',
            label: 'Filter by Pamper',
            options: 'Customer',
            placeholder: 'All Pampers',
            onchange: function() {
                apply_return_filters(returnable_items);
            }
        },
        {
            fieldtype: 'Section Break'
        },
        {
            fieldtype: 'HTML',
            fieldname: 'return_items_html',
            options: generate_return_items_html_with_filters(returnable_items)
        },
        {
            fieldtype: 'Section Break'
        },
        {
            fieldtype: 'HTML',
            fieldname: 'totals_html',
            options: '<div id="return-totals" style="font-weight: bold; padding: 10px; background-color: #f8f9fa; border-radius: 5px;"></div>'
        }
    ];

    let return_dialog = new frappe.ui.Dialog({
        title: __('Create Return Invoice - Select Items'),
        fields: dialog_fields,
        size: 'extra-large',
        primary_action_label: __('Create Return'),
        primary_action: function() {
            let return_items = get_selected_return_items();
            if (return_items.length === 0) {
                frappe.msgprint(__('Please select at least one item to return'));
                return;
            }
            
            // Validate return quantities
            let validation_error = validate_return_quantities(return_items, returnable_items);
            if (validation_error) {
                frappe.msgprint(validation_error);
                return;
            }
            
            // Create return invoice with selected items
            create_partial_return_invoice(frm, return_items);
            return_dialog.hide();
        },
        secondary_action_label: __('Select All Visible'),
        secondary_action: function() {
            select_all_visible_items_for_return(returnable_items);
        }
    });

    // Store dialog reference for filter functions
    window.current_return_dialog = return_dialog;
    
    return_dialog.show();
    
    // Fix z-index issues for filter fields
    setTimeout(function() {
        fix_filter_field_zindex();
    }, 200);
    
    // Initialize return quantities and bind events
    initialize_return_dialog_events(returnable_items);
    update_return_totals(returnable_items);
}
function select_all_visible_items_for_return(items) {
    $('.return-item-row:visible').each(function() {
        let $row = $(this);
        let index = $row.data('item-idx');
        let item = items[index];
        
        if (item) {
            let available_qty = item.original_qty - item.returned_qty;
            if (available_qty > 0) {
                $row.find('.return-item-checkbox').prop('checked', true);
                $row.find('.return-qty-input').val(available_qty).prop('disabled', false);
            }
        }
    });
    
    update_return_totals(items);
    update_select_all_state();
}
function generate_return_items_html_with_filters(items) {
    let html = `
        <div id="return-items-container" style="position: relative; z-index: 1;">
            <div style="max-height: 400px; overflow-y: auto; position: relative; z-index: 1;">
                <table class="table table-bordered" style="margin-bottom: 0; position: relative; z-index: 1;">
                    <thead style="background-color: #f8f9fa; position: sticky; top: 0; z-index: 10;">
                        <tr>
                            <th style="width: 60px;">
                                <input type="checkbox" id="select-all-return-items" style="margin: 0;">
                            </th>
                            <th>Item / Line</th>
                            <th style="width: 120px;">Original Qty</th>
                            <th style="width: 120px;">Returned Qty</th>
                            <th style="width: 120px;">Available</th>
                            <th style="width: 150px;">Return Qty</th>
                            <th style="width: 100px;">Price</th>
                        </tr>
                    </thead>
                    <tbody id="return-items-tbody">
    `;
    
    items.forEach((item, index) => {
        html += generate_return_item_row(item, index);
    });
    
    html += `
                    </tbody>
                </table>
            </div>
            <div id="filter-summary" style="margin-top: 10px; padding: 8px; background-color: #e3f2fd; border-radius: 4px; font-size: 13px; position: relative; z-index: 1;">
                <strong>Showing:</strong> <span id="visible-items-count">${items.length}</span> of ${items.length} items
            </div>
        </div>
    `;
    
    return html;
}

// Generate individual item row
function generate_return_item_row(item, index) {
    let available_qty = item.original_qty - item.returned_qty;
    let initial_return_qty = available_qty > 0 ? available_qty : 0;
    
    // Use line_description if available, otherwise create one
    let display_name = item.line_description || `Line ${item.idx}: ${item.item_name || item.item_code}`;
    
    // Create filter attributes for easy filtering
    let filter_attrs = `
        data-item-code="${item.item_code || ''}"
        data-customer="${item.customer || ''}"
        data-pamper="${item.pamper || ''}"
    `;
    
    return `
        <tr class="return-item-row" data-item-idx="${index}" ${filter_attrs}>
            <td style="text-align: center;">
                <input type="checkbox" class="return-item-checkbox" 
                       data-idx="${index}" 
                       data-actual-idx="${item.idx}"
                       data-array-index="${item.array_index || index}"
                       ${available_qty > 0 ? 'checked' : 'disabled'}>
            </td>
            <td>
                <strong>${display_name}</strong><br>
                <small class="text-muted">Code: ${item.item_code}</small>
                ${item.customer ? `<br><small class="text-info">Customer: ${item.customer}</small>` : ''}
                ${item.pamper ? `<br><small class="text-warning">Pamper: ${item.pamper}</small>` : ''}
                <br><small class="text-muted">Row Index: ${item.idx}</small>
            </td>
            <td style="text-align: right;">${format_number(item.original_qty)}</td>
            <td style="text-align: right;">${format_number(item.returned_qty)}</td>
            <td style="text-align: right;">
                <span class="${available_qty > 0 ? 'text-success' : 'text-danger'}">
                    ${format_number(available_qty)}
                </span>
            </td>
            <td>
                <input type="number" 
                       class="form-control return-qty-input" 
                       data-idx="${index}"
                       data-array-index="${item.array_index || index}"
                       min="0" 
                       max="${available_qty}"
                       step="0.001"
                       value="${initial_return_qty}"
                       ${available_qty <= 0 ? 'disabled' : ''}
                       style="text-align: right;">
            </td>
            <td style="text-align: right;">${format_currency(item.price)}</td>
        </tr>
    `;
}

// Apply filters function
function apply_return_filters(returnable_items) {
    if (!window.current_return_dialog) return;
    
    let filter_item = window.current_return_dialog.get_value('filter_item') || '';
    let filter_customer = window.current_return_dialog.get_value('filter_customer') || '';
    let filter_pamper = window.current_return_dialog.get_value('filter_pamper') || '';
    
    let visible_count = 0;
    
    $('.return-item-row').each(function() {
        let $row = $(this);
        let item_code = $row.data('item-code') || '';
        let customer = $row.data('customer') || '';
        let pamper = $row.data('pamper') || '';
        
        let show_item = (!filter_item || item_code === filter_item);
        let show_customer = (!filter_customer || customer === filter_customer);
        let show_pamper = (!filter_pamper || pamper === filter_pamper);
        
        if (show_item && show_customer && show_pamper) {
            $row.show();
            visible_count++;
        } else {
            $row.hide();
            // Uncheck hidden items
            $row.find('.return-item-checkbox').prop('checked', false);
            $row.find('.return-qty-input').val(0);
        }
    });
    
    // Update filter summary
    $('#visible-items-count').text(visible_count);
    $('#filter-summary').html(`
        <strong>Showing:</strong> <span style="color: #1976d2;">${visible_count}</span> of ${returnable_items.length} items
        ${filter_item ? `<span class="label label-info" style="margin-left: 5px;">Item: ${filter_item}</span>` : ''}
        ${filter_customer ? `<span class="label label-info" style="margin-left: 5px;">Customer: ${filter_customer}</span>` : ''}
        ${filter_pamper ? `<span class="label label-info" style="margin-left: 5px;">Pamper: ${filter_pamper}</span>` : ''}
    `);
    
    // Update totals and select all state
    update_return_totals(returnable_items);
    update_select_all_state();
}


function generate_return_items_html(items) {
    let html = `
        <div style="max-height: 400px; overflow-y: auto;">
            <table class="table table-bordered" style="margin-bottom: 0;">
                <thead style="background-color: #f8f9fa;">
                    <tr>
                        <th style="width: 60px;">
                            <input type="checkbox" id="select-all-return-items" style="margin: 0;">
                        </th>
                        <th>Item / Line</th>
                        <th style="width: 120px;">Original Qty</th>
                        <th style="width: 120px;">Returned Qty</th>
                        <th style="width: 120px;">Available</th>
                        <th style="width: 150px;">Return Qty</th>
                        <th style="width: 100px;">Price</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    items.forEach((item, index) => {
        let available_qty = item.original_qty - item.returned_qty;
        let initial_return_qty = available_qty > 0 ? available_qty : 0;
        
        // Use line_description if available, otherwise create one
        let display_name = item.line_description || `Line ${item.idx}: ${item.item_name || item.item_code}`;
        
        html += `
            <tr data-item-idx="${index}">
                <td style="text-align: center;">
                    <input type="checkbox" class="return-item-checkbox" 
                           data-idx="${index}" 
                           data-actual-idx="${item.idx}"
                           data-array-index="${item.array_index || index}"
                           ${available_qty > 0 ? 'checked' : 'disabled'}>
                </td>
                <td>
                    <strong>${display_name}</strong><br>
                    <small class="text-muted">Code: ${item.item_code}</small>
                    ${item.customer ? `<br><small class="text-info">Customer: ${item.customer}</small>` : ''}
                    ${item.pamper ? `<br><small class="text-warning">Pamper: ${item.pamper}</small>` : ''}
                    <br><small class="text-muted">Row Index: ${item.idx}</small>
                </td>
                <td style="text-align: right;">${format_number(item.original_qty)}</td>
                <td style="text-align: right;">${format_number(item.returned_qty)}</td>
                <td style="text-align: right;">
                    <span class="${available_qty > 0 ? 'text-success' : 'text-danger'}">
                        ${format_number(available_qty)}
                    </span>
                </td>
                <td>
                    <input type="number" 
                           class="form-control return-qty-input" 
                           data-idx="${index}"
                           data-array-index="${item.array_index || index}"
                           min="0" 
                           max="${available_qty}"
                           step="0.001"
                           value="${initial_return_qty}"
                           ${available_qty <= 0 ? 'disabled' : ''}
                           style="text-align: right;">
                </td>
                <td style="text-align: right;">${format_currency(item.price)}</td>
            </tr>
        `;
    });
    
    html += `
                </tbody>
            </table>
        </div>
    `;
    
    return html;
}


function initialize_return_dialog_events(items) {
    // Select all checkbox - only affects visible items
    $(document).off('change', '#select-all-return-items');
    $(document).on('change', '#select-all-return-items', function() {
        let checked = $(this).is(':checked');
        
        $('.return-item-row:visible .return-item-checkbox:not(:disabled)').each(function() {
            $(this).prop('checked', checked);
            
            let idx = $(this).data('idx');
            let available_qty = items[idx].original_qty - items[idx].returned_qty;
            
            if (checked && available_qty > 0) {
                $(`.return-qty-input[data-idx="${idx}"]`).val(available_qty).prop('disabled', false);
            } else {
                $(`.return-qty-input[data-idx="${idx}"]`).val(0).prop('disabled', true);
            }
        });
        
        update_return_totals(items);
    });
    
    // Individual item checkboxes
    $(document).off('change', '.return-item-checkbox');
    $(document).on('change', '.return-item-checkbox', function() {
        let idx = $(this).data('idx');
        let checked = $(this).is(':checked');
        
        // Enable/disable quantity input
        $(`.return-qty-input[data-idx="${idx}"]`).prop('disabled', !checked);
        
        if (!checked) {
            $(`.return-qty-input[data-idx="${idx}"]`).val(0);
        } else {
            let available_qty = items[idx].original_qty - items[idx].returned_qty;
            $(`.return-qty-input[data-idx="${idx}"]`).val(Math.min(available_qty, available_qty));
        }
        
        update_return_totals(items);
        update_select_all_state();
    });
    
    // Quantity input changes
    $(document).off('input change', '.return-qty-input');
    $(document).on('input change', '.return-qty-input', function() {
        let idx = $(this).data('idx');
        let qty = parseFloat($(this).val()) || 0;
        let available_qty = items[idx].original_qty - items[idx].returned_qty;
        
        // Strict validation and correction
        if (qty < 0) {
            qty = 0;
            $(this).val(0);
        } else if (qty > available_qty) {
            qty = available_qty;
            $(this).val(available_qty);
            frappe.show_alert({
                message: __('Return quantity cannot exceed available quantity ({0})', [available_qty]),
                indicator: 'orange'
            });
        }
        
        // Update checkbox based on quantity
        $(`.return-item-checkbox[data-idx="${idx}"]`).prop('checked', qty > 0);
        
        update_return_totals(items);
        update_select_all_state();
    });
    
    // Validation on input blur
    $(document).off('blur', '.return-qty-input');
    $(document).on('blur', '.return-qty-input', function() {
        let idx = $(this).data('idx');
        let qty = parseFloat($(this).val()) || 0;
        let available_qty = items[idx].original_qty - items[idx].returned_qty;
        
        if (qty > available_qty) {
            $(this).val(available_qty);
            $(this).trigger('change');
        }
    });
}

function update_select_all_state() {
    let total_visible_checkboxes = $('.return-item-row:visible .return-item-checkbox:not(:disabled)').length;
    let checked_visible_checkboxes = $('.return-item-row:visible .return-item-checkbox:not(:disabled):checked').length;
    
    let select_all = $('#select-all-return-items');
    if (checked_visible_checkboxes === 0) {
        select_all.prop('indeterminate', false);
        select_all.prop('checked', false);
    } else if (checked_visible_checkboxes === total_visible_checkboxes) {
        select_all.prop('indeterminate', false);
        select_all.prop('checked', true);
    } else {
        select_all.prop('indeterminate', true);
    }
}


function update_return_totals(items) {
    let total_items = 0;
    let total_qty = 0;
    let total_amount = 0;
    let visible_items = 0;
    
    $('.return-item-row:visible .return-item-checkbox:checked').each(function() {
        let idx = $(this).data('idx');
        let qty = parseFloat($(`.return-qty-input[data-idx="${idx}"]`).val()) || 0;
        
        if (qty > 0) {
            total_items++;
            total_qty += qty;
            total_amount += qty * items[idx].price;
        }
    });
    
    // Count visible items
    visible_items = $('.return-item-row:visible').length;
    
    let totals_html = `
        <div class="row">
            <div class="col-md-2">
                <strong>Visible Items:</strong> ${visible_items}
            </div>
            <div class="col-md-2">
                <strong>Selected Items:</strong> ${total_items}
            </div>
            <div class="col-md-3">
                <strong>Total Quantity:</strong> ${format_number(total_qty)}
            </div>
            <div class="col-md-2">
                <strong>Total Amount:</strong> ${format_currency(total_amount)}
            </div>
            <div class="col-md-3">
                <strong>Return Amount:</strong> <span class="text-danger">${format_currency(-total_amount)}</span>
            </div>
        </div>
    `;
    
    $('#return-totals').html(totals_html);
}

function clear_return_filters() {
    if (!window.current_return_dialog) return;
    
    window.current_return_dialog.set_value('filter_item', '');
    window.current_return_dialog.set_value('filter_customer', '');
    window.current_return_dialog.set_value('filter_pamper', '');
    
    // This will trigger the onchange events and reset the view
}
function get_selected_return_items() {
    let selected_items = [];
    
    $('.return-item-checkbox:checked').each(function() {
        let idx = $(this).data('idx');
        let array_index = $(this).data('array-index');
        let qty = parseFloat($(`.return-qty-input[data-idx="${idx}"]`).val()) || 0;
        
        if (qty > 0) {
            selected_items.push({
                array_index: array_index,
                return_qty: qty,
                idx: $(this).data('actual-idx')
            });
        }
    });
    
    return selected_items;
}

function validate_return_quantities(return_items, returnable_items) {
    for (let return_item of return_items) {
        let array_index = return_item.array_index;
        
        if (array_index >= returnable_items.length) {
            return `Invalid item index: ${array_index}`;
        }
        
        let item = returnable_items[array_index];
        let available_qty = item.original_qty - item.returned_qty;
        
        if (return_item.return_qty <= 0) {
            return `Return quantity must be greater than 0 for item: ${item.item_name || item.item_code}`;
        }
        
        if (return_item.return_qty > available_qty) {
            return `Return quantity (${return_item.return_qty}) cannot exceed available quantity (${available_qty}) for item: ${item.item_name || item.item_code}. Please adjust the quantity to ${available_qty} or less.`;
        }
        
        // FIX: Add precision check to avoid floating point issues
        if (return_item.return_qty.toFixed(3) > available_qty.toFixed(3)) {
            return `Return quantity (${return_item.return_qty.toFixed(3)}) cannot exceed available quantity (${available_qty.toFixed(3)}) for item: ${item.item_name || item.item_code}`;
        }
    }
    
    return null;
}

function select_all_items_for_return(items) {
    items.forEach((item, index) => {
        let available_qty = item.original_qty - item.returned_qty;
        if (available_qty > 0) {
            $(`.return-item-checkbox[data-idx="${index}"]`).prop('checked', true);
            $(`.return-qty-input[data-idx="${index}"]`).val(available_qty).prop('disabled', false);
            $(`.return-total-display[data-idx="${index}"]`).text(format_currency(available_qty * item.price));
        }
    });
    
    update_return_totals(items);
    update_select_all_state();
}


function create_partial_return_invoice(frm, return_items) {
    frappe.show_alert({
        message: __("Creating return invoice..."),
        indicator: "blue"
    });
    
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.create_partial_return_invoice',
        args: {
            original_invoice_name: frm.doc.name,
            return_items: return_items
        },
        callback: function(r) {
            if (r.message && r.message.success) {
                frappe.show_alert({
                    message: __('Return invoice created successfully'),
                    indicator: 'green'
                });
                
                // Refresh the form to update return quantities
                frm.reload_doc();
                
                // Open the new return invoice
                frappe.set_route('Form', 'Invoice Form', r.message.return_invoice_name);
            } else {
                let error = r.message ? r.message.error : 'Failed to create return invoice';
                frappe.msgprint({
                    title: __('Error'),
                    indicator: 'red',
                    message: error
                });
            }
        }
    });
}

function show_return_status_dialog(frm) {
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_invoice_items_with_return_info',
        args: {
            invoice_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.success) {
                create_return_status_dialog(frm, r.message.items);
            } else {
                frappe.msgprint(__('Error loading return status'));
            }
        }
    });
}

function create_return_status_dialog(frm, items) {
    let html = generate_return_status_html(items);
    
    let dialog = new frappe.ui.Dialog({
        title: __('Item Return Status - {0}', [frm.doc.name]),
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'return_status_html',
                options: html
            }
        ],
        size: 'large'
    });
    
    dialog.show();
}

function generate_return_status_html(items) {
    let html = `
        <div class="table-responsive">
            <table class="table table-bordered">
                <thead style="background-color: #f8f9fa;">
                    <tr>
                        <th>Line / Item</th>
                        <th style="text-align: right;">Original Qty</th>
                        <th style="text-align: right;">Returned Qty</th>
                        <th style="text-align: right;">Available</th>
                        <th style="text-align: center;">Return %</th>
                        <th style="text-align: center;">Status</th>
                        <th style="text-align: right;">Original Total</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    items.forEach(item => {
        let status_badge = `<span class="label label-${item.status_color}">${item.return_status}</span>`;
        let return_percentage = item.return_percentage ? item.return_percentage.toFixed(1) + '%' : '0%';
        
        // Use line_description if available
        let display_name = item.line_description || `Line ${item.idx}: ${item.item_name || item.item_code}`;
        
        html += `
            <tr>
                <td>
                    <strong>${display_name}</strong><br>
                    <small class="text-muted">Code: ${item.item_code}</small>
                    ${item.customer ? `<br><small class="text-info">Customer: ${item.customer}</small>` : ''}
                    ${item.pamper ? `<br><small class="text-warning">Pamper: ${item.pamper}</small>` : ''}
                </td>
                <td style="text-align: right;">${format_number(item.original_qty)}</td>
                <td style="text-align: right;">${format_number(item.returned_qty)}</td>
                <td style="text-align: right;">
                    <span class="${item.available_qty > 0 ? 'text-success' : 'text-muted'}">
                        ${format_number(item.available_qty)}
                    </span>
                </td>
                <td style="text-align: center;">${return_percentage}</td>
                <td style="text-align: center;">${status_badge}</td>
                <td style="text-align: right;">${format_currency(item.total)}</td>
            </tr>
        `;
    });
    
    html += `
                </tbody>
            </table>
        </div>
    `;
    
    return html;
}
function validate_return_against_invoice(frm) {
    if (!frm.doc.return_against) {
        return;
    }
    
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_return_validation_status',
        args: {
            invoice_name: frm.doc.return_against
        },
        callback: function(r) {
            if (r.message && !r.message.can_create) {
                frappe.msgprint({
                    title: __('Cannot Create Return'),
                    indicator: 'red',
                    message: r.message.reason
                });
                frm.set_value('return_against', '');
            }
        }
    });
}

function validate_return_invoice_client_side(frm) {
    if (!frm.doc.is_return) {
        return true;
    }
    
    // Validate return against is set
    if (!frm.doc.return_against) {
        frappe.msgprint(__('Return Against is mandatory for return invoices'));
        return false;
    }
    
    // Validate items have negative quantities
    let has_positive_qty = false;
    frm.doc.items.forEach(item => {
        if (item.qty > 0) {
            has_positive_qty = true;
        }
    });
    
    if (has_positive_qty) {
        frappe.msgprint(__('Return invoice items must have negative quantities'));
        return false;
    }
    
    // Validate grand total is negative
    if (frm.doc.grand_total > 0) {
        frappe.msgprint(__('Return invoice grand total must be negative'));
        return false;
    }
    
    return true;
}

function show_return_validation_error(frm, reason, details) {
    let message = '<div style="font-family: Arial, sans-serif;">';
    message += '<h4 style="color: #d73527; margin-bottom: 15px;">Cannot Create Return Invoice</h4>';
    message += '<p style="margin-bottom: 15px;"><strong>Reason:</strong> ' + reason + '</p>';
    
    // Show additional details if available
    if (details.existing_returns && details.existing_returns.length > 0) {
        message += '<p style="margin-bottom: 10px;"><strong>Existing Returns:</strong></p>';
        message += '<table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">';
        message += '<thead><tr style="background-color: #f8f9fa;">';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Return Invoice</th>';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Status</th>';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Date</th>';
        message += '<th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Amount</th>';
        message += '</tr></thead><tbody>';
        
        details.existing_returns.forEach(function(ret) {
            let status = ret.docstatus === 1 ? 'Submitted' : ret.docstatus === 0 ? 'Draft' : 'Cancelled';
            let status_color = ret.docstatus === 1 ? '#28a745' : ret.docstatus === 0 ? '#ffc107' : '#dc3545';
            
            message += '<tr>';
            message += '<td style="padding: 8px; border: 1px solid #ddd;">' + ret.name + '</td>';
            message += '<td style="padding: 8px; border: 1px solid #ddd; color: ' + status_color + ';">' + status + '</td>';
            message += '<td style="padding: 8px; border: 1px solid #ddd;">' + frappe.datetime.str_to_user(ret.posting_date) + '</td>';
            message += '<td style="padding: 8px; border: 1px solid #ddd; text-align: right;">' + format_currency(ret.grand_total) + '</td>';
            message += '</tr>';
        });
        
        message += '</tbody></table>';
        message += '<p style="color: #666; font-style: italic;">Please cancel or delete existing returns before creating a new one.</p>';
    }
    
    message += '</div>';
    
    frappe.msgprint({
        title: __('Cannot Create Return'),
        indicator: 'red',
        message: message
    });
}

// Update the show_return_info_indicator function - REPLACE your existing one
function show_return_info_indicator(frm) {
    // Check if this invoice has any returns
    frappe.call({
        method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_invoice_return_info',
        args: {
            invoice_name: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.exists && r.message.returns.length > 0) {
                let returns = r.message.returns;
                let submitted_returns = returns.filter(ret => ret.docstatus === 1);
                let draft_returns = returns.filter(ret => ret.docstatus === 0);
                
                let indicator_text = '';
                let total_return_amount = 0;
                
                if (submitted_returns.length > 0) {
                    indicator_text += submitted_returns.length + ' submitted return(s)';
                    total_return_amount = submitted_returns.reduce((sum, ret) => sum + Math.abs(ret.grand_total), 0);
                }
                if (draft_returns.length > 0) {
                    if (indicator_text) indicator_text += ', ';
                    indicator_text += draft_returns.length + ' draft return(s)';
                }
                
                if (indicator_text) {
                    frm.dashboard.add_indicator(__('Returns: {0}', [indicator_text]), 'orange');
                    
                    if (total_return_amount > 0) {
                        frm.dashboard.add_indicator(
                            __('Return Amount: {0}', [format_currency(total_return_amount)]), 
                            'red'
                        );
                    }
                    
                    // Add button to view returns
                    frm.add_custom_button(__("View Returns"), function() {
                        show_returns_list_dialog(frm, returns);
                    }, __("Returns"));
                }
                
                // Refresh return information for items
                refresh_return_information(frm);
            }
        }
    });
}

function show_returns_list_dialog(frm, returns) {
    let returns_html = `
        <div class="table-responsive">
            <table class="table table-bordered">
                <thead style="background-color: #f8f9fa;">
                    <tr>
                        <th>Return Invoice</th>
                        <th>Date</th>
                        <th>Status</th>
                        <th style="text-align: right;">Amount</th>
                        <th style="text-align: center;">Action</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    let total_return_amount = 0;
    
    returns.forEach(ret => {
        let status = ret.docstatus === 1 ? 'Submitted' : ret.docstatus === 0 ? 'Draft' : 'Cancelled';
        let status_color = ret.docstatus === 1 ? 'success' : ret.docstatus === 0 ? 'warning' : 'danger';
        
        if (ret.docstatus === 1) {
            total_return_amount += Math.abs(ret.grand_total);
        }
        
        returns_html += `
            <tr>
                <td><strong>${ret.name}</strong></td>
                <td>${frappe.datetime.str_to_user(ret.posting_date)}</td>
                <td><span class="label label-${status_color}">${status}</span></td>
                <td style="text-align: right;">${format_currency(ret.grand_total)}</td>
                <td style="text-align: center;">
                    <button class="btn btn-xs btn-primary" onclick="frappe.set_route('Form', 'Invoice Form', '${ret.name}')">
                        View
                    </button>
                </td>
            </tr>
        `;
    });
    
    returns_html += `
                </tbody>
                <tfoot style="background-color: #f8f9fa;">
                    <tr>
                        <th colspan="3">Total Submitted Returns:</th>
                        <th style="text-align: right;">${format_currency(-total_return_amount)}</th>
                        <th></th>
                    </tr>
                </tfoot>
            </table>
        </div>
        
        <div class="row" style="margin-top: 15px;">
            <div class="col-md-12">
                <p class="text-muted">
                    <i class="fa fa-info-circle"></i> 
                    Return amounts are shown as negative values. 
                    Only submitted returns affect the original invoice balance.
                </p>
            </div>
        </div>
    `;
    
    let dialog = new frappe.ui.Dialog({
        title: __('Return Invoices for {0}', [frm.doc.name]),
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'returns_list',
                options: returns_html
            }
        ],
        size: 'large',
        primary_action_label: __('Create New Return'),
        primary_action: function() {
            dialog.hide();
            create_return_invoice_from_original(frm);
        }
    });
    
    dialog.show();
}

function refresh_return_information(frm) {
    if (frm.doc.docstatus === 1 && !frm.doc.is_return) {
        // Update returned quantities from server
        frappe.call({
            method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.get_invoice_items_with_return_info',
            args: {
                invoice_name: frm.doc.name
            },
            callback: function(r) {
                if (r.message && r.message.success) {
                    // Update the form with latest return information
                    r.message.items.forEach(server_item => {
                        frm.doc.items.forEach(form_item => {
                            if (form_item.idx === server_item.idx) {
                                form_item.returned_qty = server_item.returned_qty;
                                form_item.available_qty = server_item.available_qty;
                            }
                        });
                    });
                    frm.refresh_field('items');
                }
            }
        });
    }
}

function handle_return_invoice_form(frm) {
    if (frm.doc.is_return) {
        // Add visual indicators
        frm.dashboard.add_indicator(__('Return Invoice'), 'red');
        
        if (frm.doc.return_against) {
            frm.dashboard.add_indicator(__('Return Against: {0}', [frm.doc.return_against]), 'orange');
            frm.dashboard.add_comment(
                __('This is a return invoice against {0}', [frm.doc.return_against]),
                'blue'
            );
            
            // Add button to view original invoice
            frm.add_custom_button(__("View Original Invoice"), function() {
                frappe.set_route('Form', 'Invoice Form', frm.doc.return_against);
            }, __("Actions"));
        }
        
        // Ensure negative values are maintained
        ensure_negative_values_for_return(frm);
    }
}

function ensure_negative_values_for_return(frm) {
    if (!frm.doc.is_return) return;
    
    let needs_refresh = false;
    
    // Ensure grand total is negative
    if (frm.doc.grand_total > 0) {
        frm.set_value('grand_total', -Math.abs(frm.doc.grand_total));
        needs_refresh = true;
    }
    
    // Ensure item totals are negative
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item, idx) {
            if (item.total > 0) {
                frappe.model.set_value('Invoice Form Item', item.name, 'total', -Math.abs(item.total));
                needs_refresh = true;
            }
            if (item.qty > 0) {
                frappe.model.set_value('Invoice Form Item', item.name, 'qty', -Math.abs(item.qty));
                needs_refresh = true;
            }
        });
    }
    
    if (needs_refresh) {
        frm.refresh_fields();
    }
}

// Helper functions for formatting
function format_currency(amount) {
    return frappe.format(amount, {fieldtype: 'Currency'});
}

function format_number(number) {
    return frappe.format(number, {fieldtype: 'Float', precision: 3});
}

// ===========================================
// KEYBOARD SHORTCUTS AND LIST VIEW SETTINGS
// ===========================================

// Add keyboard shortcuts for return operations
$(document).ready(function() {
    $(document).on('keydown', function(e) {
        // Ctrl+Shift+R for create return
        if (e.ctrlKey && e.shiftKey && e.keyCode === 82) {
            let frm = cur_frm;
            if (frm && frm.doctype === 'Invoice Form' && frm.doc.docstatus === 1 && !frm.doc.is_return) {
                e.preventDefault();
                create_return_invoice_from_original(frm);
            }
        }
        
        // Ctrl+Shift+S for return status
        if (e.ctrlKey && e.shiftKey && e.keyCode === 83) {
            let frm = cur_frm;
            if (frm && frm.doctype === 'Invoice Form' && frm.doc.docstatus === 1 && !frm.doc.is_return) {
                e.preventDefault();
                show_return_status_dialog(frm);
            }
        }
    });
});

// Add custom field formatter for return status in list view

// Fix z-index issues for filter fields in return dialog
function fix_filter_field_zindex() {
    if (!window.current_return_dialog) return;
    
    // Get the dialog element
    let dialog_element = window.current_return_dialog.$wrapper;
    
    // Add comprehensive CSS fixes
    if (!dialog_element.find('#filter-zindex-fix').length) {
        dialog_element.append(`
            <style id="filter-zindex-fix">
                /* Ensure filter section has proper z-index */
                .filter-section {
                    position: relative !important;
                    z-index: 9999 !important;
                }
                
                /* Ensure all form groups in filter section have proper z-index */
                .filter-section .form-group {
                    position: relative !important;
                    z-index: 9999 !important;
                }
                
                
                /* Ensure the HTML content section has lower z-index */
                [data-fieldname="return_items_html"] {
                    position: relative !important;
                    z-index: 1 !important;
                }
                
                /* Ensure the return items container has lower z-index */
                #return-items-container {
                    position: relative !important;
                    z-index: 1 !important;
                }
                
                /* Ensure the table has lower z-index */
                #return-items-container table {
                    position: relative !important;
                    z-index: 1 !important;
                }
                
                /* Ensure modal dialog has proper z-index */
                .modal-dialog {
                    z-index: 9999 !important;
                }
                
                /* Ensure modal backdrop has proper z-index */
                .modal-backdrop {
                    z-index: 0 !important;
                }
                
              
                
               
            </style>
        `);
    }
    
    // Apply z-index fixes to specific elements
    dialog_element.find('.filter-section .form-group').each(function() {
        let $form_group = $(this);
        let $input = $form_group.find('input[data-fieldname], select[data-fieldname]');
        
        if ($input.length) {
            // Ensure the form group has proper z-index
            $form_group.css({
                'position': 'relative',
                'z-index': '9999'
            });
            
            // Ensure the input field has proper z-index
            $input.css({
                'position': 'relative',
                'z-index': '10000'
            });
            
            // Find and fix any autocomplete dropdowns
            let $awesomplete = $form_group.find('.awesomplete');
            if ($awesomplete.length) {
                $awesomplete.css({
                    'position': 'relative',
                    'z-index': '10001'
                });
                
                $awesomplete.find('ul').css({
                    'position': 'absolute',
                    'z-index': '10001'
                });
            }
        }
    });
    
    // Ensure the HTML content section has lower z-index
    dialog_element.find('[data-fieldname="return_items_html"]').css({
        'position': 'relative',
        'z-index': '1'
    });
    
    // Ensure the return items container has lower z-index
    dialog_element.find('#return-items-container').css({
        'position': 'relative',
        'z-index': '1'
    });
    
    // Force refresh of any existing autocomplete instances
    if (window.awesomplete) {
        dialog_element.find('.awesomplete').each(function() {
            let $this = $(this);
            if ($this[0]._awesomplete) {
                $this[0]._awesomplete.evaluate();
            }
        });
    }
}

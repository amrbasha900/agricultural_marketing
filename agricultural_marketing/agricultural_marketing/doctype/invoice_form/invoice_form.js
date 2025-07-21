// Copyright (c) 2024, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.ui.form.on("Invoice Form", {
 	refresh(frm) {
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
    },
    qty: function (frm, cdt, cdn) {
        calculate_total_line(frm);
    },
    price: function (frm, cdt, cdn) {
        calculate_total_line(frm);
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
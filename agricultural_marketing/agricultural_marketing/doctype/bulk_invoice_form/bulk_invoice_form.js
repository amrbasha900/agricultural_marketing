// Copyright (c) 2024, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bulk Invoice Form", {
    refresh(frm) {
        // Initialize original suppliers storage
        initialize_original_suppliers(frm);
        
        // Set up filters after a short delay to ensure grid is initialized
        setTimeout(() => {
            setup_filters(frm);
        }, 100);
        
        // Add custom buttons at the top
        add_custom_buttons(frm);
        
        // Add visual indicators for items with references
        add_reference_indicators(frm);

        // Add save button beside add row button
        add_save_button_to_grid(frm);
    },
    
    onload(frm) {
        // Set up filters on load
        setup_filters(frm);
        if (frm.is_new()) {
            // Remove first empty row if exists
            if (frm.doc.items && frm.doc.items.length > 0) {
                frm.doc.items.splice(0, 1);
                frm.refresh_field('items');
            }
        }
    },
    
    default_supplier: function(frm) {
        // Set default supplier for new rows
        if (frm.doc.default_supplier) {
            frm.fields_dict.items.grid.docfields.forEach(function(field) {
                if (field.fieldname === "supplier") {
                    field.default = frm.doc.default_supplier;
                }
            });
        }
    },
    
    default_customer: function(frm) {
        // Set default customer for new rows
        if (frm.doc.default_customer) {
            frm.fields_dict.items.grid.docfields.forEach(function(field) {
                if (field.fieldname === "customer") {
                    field.default = frm.doc.default_customer;
                }
            });
        }
    },
    
    default_pamper: function(frm) {
        // Set default pamper for new rows
        if (frm.doc.default_pamper) {
            frm.fields_dict.items.grid.docfields.forEach(function(field) {
                if (field.fieldname === "pamper") {
                    field.default = frm.doc.default_pamper;
                }
            });
        }
    },
    
    create_invoices_btn: function(frm) {
        create_invoice_forms(frm);
    }
});

function add_save_button_to_grid(frm) {
    if (frm.fields_dict.items && frm.fields_dict.items.grid) {
        setTimeout(() => {
            let grid = frm.fields_dict.items.grid;
            let grid_buttons = grid.wrapper.find('.grid-add-row');
            
            // Add save button next to add row button
            if (grid_buttons.length && !grid.wrapper.find('.grid-save-btn').length) {
                grid_buttons.after(`
                    <button class="btn btn-xs btn-default grid-save-btn" style="margin-left: 5px;">
                        <i class="fa fa-save"></i> Save Document
                    </button>
                `);
                
                // Add click handler for save button
                grid.wrapper.find('.grid-save-btn').click(function() {
                    frm.save().then(() => {
                        frappe.show_alert({
                            message: __("Document saved successfully"),
                            indicator: 'green'
                        });
                    }).catch((error) => {
                        frappe.show_alert({
                            message: __("Error saving document"),
                            indicator: 'red'
                        });
                    });
                });
            }
        }, 500);
    }
}

frappe.ui.form.on("Bulk Invoice Form Item", {
    items_add: function(frm, cdt, cdn) {
        // Set up child table filters when item is added
        setup_child_table_filters(frm);
        
        let row = locals[cdt][cdn];
        
        // Set default values from parent if not already set
        if (frm.doc.default_supplier && !row.supplier) {
            row.supplier = frm.doc.default_supplier;
        }
        if (frm.doc.default_customer && !row.customer) {
            row.customer = frm.doc.default_customer;
        }
        if (frm.doc.default_pamper && !row.pamper) {
            row.pamper = frm.doc.default_pamper;
        }
        
        // Copy item details from previous row if exists
        if (frm.doc.items && frm.doc.items.length > 1) {
            let previous_row = frm.doc.items[frm.doc.items.length - 2]; // Get previous row
            if (previous_row && previous_row.item_code) {
                row.item_code = previous_row.item_code;
                row.item_name = previous_row.item_name;
                // Override with previous row's values if they exist
                if (previous_row.supplier) row.supplier = previous_row.supplier;
                if (previous_row.customer) row.customer = previous_row.customer;
                if (previous_row.pamper) row.pamper = previous_row.pamper;
            }
        }
        
        frm.refresh_field("items");
    },
    
    // Store original supplier value when row is loaded or focused
    before_items_remove: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (!frm.original_suppliers) {
            frm.original_suppliers = {};
        }
        frm.original_suppliers[row.name] = row.supplier;
    },
    
    form_render: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (!frm.original_suppliers) {
            frm.original_suppliers = {};
        }
        // Store the original supplier value
        frm.original_suppliers[row.name] = row.supplier;
    },
    
    qty: function(frm, cdt, cdn) {
        calculate_total(frm, cdt, cdn);
        
        // Auto-update if there's a reference invoice form
        let row = locals[cdt][cdn];
        if (row.reference_invoice_form) {
            // Debounce the update to avoid too many calls
            clearTimeout(frm.update_timeout);
            frm.update_timeout = setTimeout(() => {
                update_item(frm, row, cdn, true); // Silent update for qty/price changes
            }, 1000);
        }
    },
    
    price: function(frm, cdt, cdn) {
        calculate_total(frm, cdt, cdn);
        
        // Auto-update if there's a reference invoice form
        let row = locals[cdt][cdn];
        if (row.reference_invoice_form) {
            // Debounce the update to avoid too many calls
            clearTimeout(frm.update_timeout);
            frm.update_timeout = setTimeout(() => {
                update_item(frm, row, cdn, true); // Silent update for qty/price changes
            }, 1000);
        }
    },
    
    supplier: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        
        // If item has reference, prevent supplier change
        if (row.reference_invoice_form) {
            // Show warning message
            frappe.msgprint({
                title: __('Cannot Change Supplier'),
                message: __('This item is already linked to Invoice Form: <strong>{0}</strong><br><br>To change the supplier, please:<br>1. Delete this row<br>2. Add a new row with the desired supplier', [row.reference_invoice_form]),
                indicator: 'orange'
            });
            
            // Revert the supplier field to its original value
            setTimeout(() => {
                // Get original value from stored data or fallback
                let original_supplier = frm.original_suppliers && frm.original_suppliers[row.name] 
                    ? frm.original_suppliers[row.name] 
                    : row.__original_supplier;
                
                if (original_supplier) {
                    frappe.model.set_value(cdt, cdn, 'supplier', original_supplier);
                } else {
                    // If we can't find the original, reload the document
                    frappe.show_alert({
                        message: __('Reloading document to restore original supplier'),
                        indicator: 'blue'
                    });
                    setTimeout(() => {
                        frm.reload_doc();
                    }, 1000);
                }
            }, 100);
            
            return false;
        } else {
            // For rows without reference, store the supplier value
            if (!frm.original_suppliers) {
                frm.original_suppliers = {};
            }
            // Only store if it's not already stored (to preserve original)
            if (!frm.original_suppliers[row.name]) {
                frm.original_suppliers[row.name] = row.supplier;
            }
            row.__original_supplier = row.supplier;
        }
    },
    
    // Handle the action button click
    action: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        show_action_popup(frm, row, cdt, cdn);
    }
});

function initialize_original_suppliers(frm) {
    // Initialize storage for original supplier values
    if (!frm.original_suppliers) {
        frm.original_suppliers = {};
    }
    
    // Store original supplier values for all existing rows
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item) {
            if (item.supplier && !frm.original_suppliers[item.name]) {
                frm.original_suppliers[item.name] = item.supplier;
            }
        });
    }
}

function add_custom_buttons(frm) {
    // Clear any existing custom buttons first
    frm.custom_buttons = {};
    
    // Add View All Invoice Forms button (always visible)
    frm.add_custom_button(__("View All Invoice Forms"), function() {
        view_all_invoice_forms(frm);
    }, __("Actions"));
    
    // Add Create Invoice Forms button
    if (frm.doc.items && frm.doc.items.length > 0) {
        frm.add_custom_button(__("Create Invoice Forms"), function() {
            create_invoice_forms(frm);
        }, __("Actions"));
        
        frm.add_custom_button(__("Sync with Invoice Forms"), function() {
            sync_with_invoice_forms(frm);
        }, __("Actions"));
    }
    
    // Move Actions menu to be more prominent
    if (frm.custom_buttons[__("Actions")]) {
        frm.custom_buttons[__("Actions")].addClass('btn-primary');
    }
}

function sync_with_invoice_forms(frm) {
    if (!frm.doc.items || frm.doc.items.length === 0) {
        frappe.msgprint(__("No items to sync"));
        return;
    }
    
    // Check if there are any items with invoice form references
    let items_with_references = frm.doc.items.filter(item => item.reference_invoice_form);
    
    if (items_with_references.length === 0) {
        frappe.msgprint(__("No items have Invoice Form references to sync"));
        return;
    }
    
    frappe.confirm(
        __("This will sync all changes with related Invoice Forms. Are you sure?"),
        function() {
            frappe.call({
                method: "sync_with_invoice_forms",
                doc: frm.doc,
                callback: function(r) {
                    if (r.message) {
                        let result = r.message;
                        if (result.synced && result.synced.length > 0) {
                            frappe.show_alert({
                                message: __("Successfully synced with {0} Invoice Forms", [result.synced.length]),
                                indicator: "green"
                            });
                        }
                        if (result.failed && result.failed.length > 0) {
                            frappe.show_alert({
                                message: __("Failed to sync with {0} Invoice Forms", [result.failed.length]),
                                indicator: "red"
                            });
                        }
                    }
                }
            });
        }
    );
}

function view_all_invoice_forms(frm) {
    if (!frm.doc.name) {
        frappe.msgprint(__("Please save the document first"));
        return;
    }
    
    // Get all unique invoice form names from the items
    let invoice_form_names = [];
    if (frm.doc.items) {
        frm.doc.items.forEach(function(item) {
            if (item.reference_invoice_form && !invoice_form_names.includes(item.reference_invoice_form)) {
                invoice_form_names.push(item.reference_invoice_form);
            }
        });
    }
    
    if (invoice_form_names.length === 0) {
        frappe.msgprint(__("No Invoice Forms found for this Bulk Invoice"));
        return;
    }
    
    // Navigate to Invoice Form list with filter
    frappe.route_options = {
        "name": ["in", invoice_form_names]
    };
    
    frappe.set_route("List", "Invoice Form");
    
    // Show a message about the filter
    setTimeout(() => {
        frappe.show_alert({
            message: __("Showing {0} Invoice Forms related to this Bulk Invoice", [invoice_form_names.length]),
            indicator: "blue"
        });
    }, 1000);
}

function setup_filters(frm) {
    // Check if form and fields exist before setting up filters
    if (!frm || !frm.fields_dict) {
        return;
    }
    
    // Filter for default supplier
    frm.set_query("default_supplier", function() {
        return {
            filters: {
                is_farmer: 1
            }
        };
    });
    
    // Filter for default customer
    frm.set_query("default_customer", function() {
        return {
            filters: {
                is_customer: 1,
                is_frozen: 0
            }
        };
    });
    
    // Filter for default pamper
    frm.set_query("default_pamper", function() {
        return {
            filters: {
                is_pamper: 1,
                is_frozen: 0
            }
        };
    });
    
    // Filters for child table - check if items field exists and is initialized
    if (frm.fields_dict['items'] && frm.fields_dict['items'].grid) {
        // Set up child table filters
        setup_child_table_filters(frm);
    }
}

function setup_child_table_filters(frm) {
    // Supplier filter
    if (frm.fields_dict['items'].grid.get_field("supplier")) {
        frm.fields_dict['items'].grid.get_field("supplier").get_query = function() {
            return {
                filters: {
                    is_farmer: 1
                }
            };
        };
    }
    
    // Customer filter
    if (frm.fields_dict['items'].grid.get_field("customer")) {
        frm.fields_dict['items'].grid.get_field("customer").get_query = function() {
            return {
                filters: {
                    is_customer: 1,
                    is_frozen: 0
                }
            };
        };
    }
    
    // Pamper filter
    if (frm.fields_dict['items'].grid.get_field("pamper")) {
        frm.fields_dict['items'].grid.get_field("pamper").get_query = function() {
            return {
                filters: {
                    is_pamper: 1,
                    is_frozen: 0
                }
            };
        };
    }
    
    // Item code filter
    if (frm.fields_dict['items'].grid.get_field("item_code")) {
        frm.fields_dict['items'].grid.get_field("item_code").get_query = function() {
            return {
                filters: {
                    commission_item: 0,
                    is_agriculture_item: 1
                }
            };
        };
    }
}

function calculate_total(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    row.total = (row.qty || 0) * (row.price || 0);
    frm.refresh_field("items");
}

function create_invoice_forms(frm) {
    if (!frm.doc.items || frm.doc.items.length === 0) {
        frappe.msgprint(__("Please add items before creating invoice forms"));
        return;
    }
    
    // Check if there are any items without references
    let items_without_references = frm.doc.items.filter(item => !item.reference_invoice_form);
    
    if (items_without_references.length === 0) {
        frappe.msgprint(__("All items already have Invoice Form references"));
        return;
    }
    
    let message = __("This will create/update Invoice Forms grouped by supplier.");
    
    if (items_without_references.length < frm.doc.items.length) {
        message += __("<br><br>Items without references: {0}<br>Items with existing references: {1}", 
            [items_without_references.length, frm.doc.items.length - items_without_references.length]);
    }
    
    message += __("<br><br>Are you sure?");
    
    frappe.confirm(
        message,
        function() {
            frappe.call({
                method: "create_invoice_forms",
                doc: frm.doc,
                callback: function(r) {
                    if (r.message) {
                        frm.reload_doc();
                        frappe.show_alert({
                            message: __("Invoice Forms processed successfully"),
                            indicator: "green"
                        });
                    }
                }
            });
        }
    );
}

function show_action_popup(frm, row, cdt, cdn) {
    console.log("Showing action popup for row", row);
    
    // Get row index for display
    let row_index = frm.doc.items.findIndex(item => item.name === row.name);
    
    // Create a dialog with action buttons
    let dialog = new frappe.ui.Dialog({
        title: __("Item Actions - Row {0}", [row_index + 1]),
        size: 'small',
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "item_info",
                options: `
                    <div style="margin-bottom: 20px; padding: 15px; background-color: #f8f9fa; border-radius: 6px; border-left: 4px solid #007bff;">
                        <h6 style="margin-bottom: 10px; color: #495057;"><i class="fa fa-info-circle"></i> Item Details</h6>
                        <div style="line-height: 1.6;">
                            <strong>Item:</strong> ${row.item_name || row.item_code || 'N/A'}<br>
                            <strong>Supplier:</strong> ${row.supplier || 'N/A'}<br>
                            <strong>Customer:</strong> ${row.customer || 'N/A'}<br>
                            <strong>Qty:</strong> ${row.qty || 0} | <strong>Price:</strong> ${row.price || 0} | <strong>Total:</strong> ${row.total || 0}<br>
                            ${row.reference_invoice_form ? 
                                `<strong style="color: green;"><i class="fa fa-link"></i> Linked to:</strong> ${row.reference_invoice_form}` : 
                                '<strong style="color: orange;"><i class="fa fa-unlink"></i> Status:</strong> Not linked to any Invoice Form'
                            }
                        </div>
                    </div>
                    <div style="text-align: center; margin-top: 20px;">
                        <h6 style="margin-bottom: 15px; color: #495057;">Choose an action:</h6>
                        <div class="btn-group-vertical" style="width: 100%;">
                            ${row.reference_invoice_form ? `
                                <button class="btn btn-primary btn-action-update" style="margin-bottom: 8px; padding: 10px;">
                                    <i class="fa fa-edit"></i> Update Item in Invoice Form
                                </button>
                                <button class="btn btn-info btn-action-view" style="margin-bottom: 8px; padding: 10px;">
                                    <i class="fa fa-eye"></i> View Invoice Form
                                </button>
                            ` : ''}
                            <button class="btn btn-danger btn-action-delete" style="padding: 10px;">
                                <i class="fa fa-trash"></i> Delete Item
                            </button>
                        </div>
                    </div>
                `
            }
        ]
    });
    
    // Add event listeners for action buttons
    dialog.$wrapper.on('click', '.btn-action-update', function() {
        console.log("Update action triggered");
        dialog.hide();
        update_item(frm, row, cdn);
    });
    
    dialog.$wrapper.on('click', '.btn-action-view', function() {
        console.log("View action triggered");
        dialog.hide();
        view_invoice_form(row.reference_invoice_form);
    });
    
    dialog.$wrapper.on('click', '.btn-action-delete', function() {
        console.log("Delete action triggered");
        dialog.hide();
        delete_item(frm, row, cdn);
    });
    
    dialog.show();
}

function view_invoice_form(invoice_form_name) {
    if (invoice_form_name) {
        frappe.set_route("Form", "Invoice Form", invoice_form_name);
    }
}

function update_item(frm, row, cdn, silent = false) {
    if (!row.reference_invoice_form) {
        if (!silent) {
            frappe.msgprint(__("No reference invoice form found for this item"));
        }
        return;
    }
    
    // Get the row index from the items array
    let row_index = -1;
    for (let i = 0; i < frm.doc.items.length; i++) {
        if (frm.doc.items[i].name === row.name) {
            row_index = i;
            break;
        }
    }
    
    if (row_index === -1) {
        if (!silent) {
            frappe.msgprint(__("Could not find item in the list"));
        }
        return;
    }
    
    // Function to perform the update
    function perform_update() {
        frappe.call({
            method: "agricultural_marketing.agricultural_marketing.doctype.bulk_invoice_form.bulk_invoice_form.handle_item_update",
            args: {
                bulk_invoice_name: frm.doc.name,
                item_idx: row_index,
                updated_data: {
                    item_code: row.item_code,
                    item_name: row.item_name,
                    qty: row.qty,
                    price: row.price,
                    total: row.total,
                    supplier: row.supplier,
                    customer: row.customer,
                    pamper: row.pamper
                }
            },
            callback: function(r) {
                if (r.message && r.message.success) {
                    // If supplier changed, reload the document to show updated references
                    if (r.message.supplier_changed) {
                        frm.reload_doc();
                    }
                    
                    if (!silent) {
                        frappe.show_alert({
                            message: r.message.message,
                            indicator: "green"
                        });
                    }
                } else {
                    if (!silent) {
                        frappe.msgprint(__("Error: ") + (r.message ? r.message.message : "Unknown error"));
                    }
                }
            }
        });
    }
    
    // Show confirmation dialog only if not silent
    if (!silent) {
        frappe.confirm(
            __("This will update the item in the linked Invoice Form. Continue?"),
            perform_update
        );
    } else {
        perform_update();
    }
}

function delete_item(frm, row, cdn) {
    let message = __("Are you sure you want to delete this item?");
    
    if (row.reference_invoice_form) {
        message += __(" This will also remove it from Invoice Form {0}.", [row.reference_invoice_form]);
    }
    
    frappe.confirm(message, function() {
        // If there's a reference, handle the server-side deletion first
        if (row.reference_invoice_form) {
            // Get the row index from the items array
            let row_index = -1;
            for (let i = 0; i < frm.doc.items.length; i++) {
                if (frm.doc.items[i].name === row.name) {
                    row_index = i;
                    break;
                }
            }
            
            if (row_index === -1) {
                frappe.msgprint(__("Could not find item in the list"));
                return;
            }
            
            frappe.call({
                method: "agricultural_marketing.agricultural_marketing.doctype.bulk_invoice_form.bulk_invoice_form.handle_item_action",
                args: {
                    bulk_invoice_name: frm.doc.name,
                    item_idx: row_index,
                    action_type: "delete"
                },
                callback: function(r) {
                    if (r.message && r.message.success) {
                        // Remove the row from the grid after successful server operation
                        frappe.model.clear_doc(row.doctype, row.name);
                        frm.refresh_field("items");
                        
                        // Update reference indicators
                        add_reference_indicators(frm);
                        
                        frappe.show_alert({
                            message: r.message.message,
                            indicator: "green"
                        });
                    } else {
                        frappe.msgprint(__("Error: ") + (r.message ? r.message.message : "Unknown error"));
                    }
                }
            });
        } else {
            // If no reference, just remove the row from the grid
            frappe.model.clear_doc(row.doctype, row.name);
            frm.refresh_field("items");
            
            // Update reference indicators
            add_reference_indicators(frm);
        }
    });
}

function add_reference_indicators(frm) {
    // Add visual indicators to show which items have Invoice Form references
    if (frm.fields_dict.items && frm.fields_dict.items.grid) {
        setTimeout(() => {
            frm.doc.items.forEach((item, index) => {
                let row = frm.fields_dict.items.grid.grid_rows[index];
                if (row && row.wrapper) {
                    let indicator = row.wrapper.find('.reference-indicator');
                    if (indicator.length) {
                        indicator.remove();
                    }
                    
                    if (item.reference_invoice_form) {
                        row.wrapper.find('.grid-row-index').after(
                            `<span class="reference-indicator" style="color: green; margin-left: 5px;" title="Linked to Invoice Form ${item.reference_invoice_form}">●</span>`
                        );
                    }
                }
            });
        }, 500);
    }
}
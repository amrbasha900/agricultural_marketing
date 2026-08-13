// Copyright (c) 2024, Muhammad Salama and contributors
// For license information, please see license.txt

// Party link search: matches the code or any words of the name, ranked so a
// code whose number equals the query comes first. Shared with the other forms.
const AGM_PARTY_QUERY = "agricultural_marketing.queries.party_search";

// A voucher built from Invoice Form supplier charges is owned by its invoices.
// Every field is shown read-only and the rows are frozen, so the only way to change
// it is through the invoices themselves. Submitting and cancelling stay available:
// the accountant still posts the batch. The server enforces this independently in
// `supplier_charges.lock_charge_voucher` - this is only so the form shows it rather
// than failing on save.
function lock_supplier_charge_voucher(frm) {
    if (!frm.doc.is_supplier_charge) return;

    frm.meta.fields.forEach(df => {
        if (!["Section Break", "Column Break", "Tab Break"].includes(df.fieldtype)) {
            frm.set_df_property(df.fieldname, "read_only", 1);
        }
    });

    const grid = frm.fields_dict.references && frm.fields_dict.references.grid;
    if (grid) {
        grid.cannot_add_rows = true;
        grid.df.cannot_add_rows = true;
        grid.df.cannot_delete_rows = true;
        grid.docfields.forEach(df => (df.read_only = 1));
        grid.refresh();
    }

    frm.set_intro(
        __("Built from Invoice Form supplier charges. Change the invoices to change this voucher."),
        "blue"
    );
}

frappe.ui.form.on("Payments and Receipts", {
    refresh: function(frm) {
        lock_supplier_charge_voucher(frm);

        frm.add_custom_button(__("Print References"), function() {
            // Get references table data
            const references = frm.doc.references || [];
            
            if (!references.length) {
                frappe.msgprint(__('No references found to print'));
                return;
            }
            
            // Prepare data for the table
            const tableData = frm.doc.references.map(function(r, i) {
                return {
                    idx: r.idx || (i + 1),
                    party: r.party || '',
                    amount: r.amount || 0,
                    mode_of_payment: r.mode_of_payment || "",
                    reference_idx: i
                };
            });
            
            // Create dialog for selecting references
            let dialog = new frappe.ui.Dialog({
                title: __("Select References to Print"),
                fields: [
                    {
                        label: __('Print Option'),
                        fieldname: 'print_option',
                        fieldtype: 'Select',
                        options: [
                            'Individual PDFs (One per reference)',
                            'Combined PDF (All in one)'
                        ],
                        default: 'Individual PDFs (One per reference)',
                        reqd: 1
                    },
                    {
                        fieldname: 'letterhead_section',
                        fieldtype: 'Column Break'
                    },
                    {
                        label: __('Letter Head'),
                        fieldname: 'letter_head',
                        fieldtype: 'Link',
                        options: 'Letter Head'
                    },
                    {
                        label: __('No Letter Head'),
                        fieldname: 'no_letterhead',
                        fieldtype: 'Check',
                        default: 0
                    },
                    {
                        fieldname: 'filter_section',
                        fieldtype: 'Section Break',
                        label: __('Filter Options')
                    },
                    {
                        label: __('Filter by Party'),
                        fieldname: 'party_filter',
                        fieldtype: 'Data',
                        placeholder: __('Enter party name to filter...'),
                        onchange: function() {
                            setTimeout(applyFilter, 300);
                        }
                    },
                    {
                        fieldname: 'amount_section',
                        fieldtype: 'Column Break'
                    },
                    {
                        label: __('Min Amount'),
                        fieldname: 'min_amount',
                        fieldtype: 'Currency',
                        placeholder: __('Minimum amount'),
                        onchange: function() {
                            setTimeout(applyFilter, 300);
                        }
                    },
                    {
                        fieldname: 'max_section',
                        fieldtype: 'Column Break'
                    },
                    {
                        label: __('Max Amount'),
                        fieldname: 'max_amount',
                        fieldtype: 'Currency',
                        placeholder: __('Maximum amount'),
                        onchange: function() {
                            setTimeout(applyFilter, 300);
                        }
                    },
                    {
                        fieldname: 'clear_section',
                        fieldtype: 'Column Break'
                    },
                    {
                        label: __('Clear Filter'),
                        fieldname: 'clear_filter',
                        fieldtype: 'Button',
                        click: function() {
                            dialog.set_value('party_filter', '');
                            dialog.set_value('min_amount', '');
                            dialog.set_value('max_amount', '');
                            resetTable();
                        }
                    },
                    {
                        fieldname: 'table_break',
                        fieldtype: 'Section Break'
                    },
                    {
                        label: __('References'),
                        fieldname: 'references',
                        fieldtype: 'Table',
                        cannot_add_rows: true,
                        cannot_delete_rows: true,
                        fields: [
                            {
                                fieldname: 'idx',
                                fieldtype: 'Data',
                                in_list_view: 1,
                                label: __('No.'),
                                read_only: 1,
                                columns: 1
                            },
                            {
                                fieldname: 'party',
                                fieldtype: 'Data',
                                in_list_view: 1,
                                label: __('Party'),
                                read_only: 1,
                                columns: 3
                            },
                            {
                                fieldname: 'amount',
                                fieldtype: 'Currency',
                                in_list_view: 1,
                                label: __('Amount'),
                                read_only: 1,
                                columns: 2
                            },
                            {
                                fieldname: 'mode_of_payment',
                                fieldtype: 'Data',
                                in_list_view: 1,
                                label: __('Mode of Payment'),
                                read_only: 1,
                                columns: 3
                            },
                            {
                                fieldname: 'reference_idx',
                                fieldtype: 'Int',
                                hidden: 1
                            }
                        ],
                        data: tableData
                    }
                ],
                size: "large",
                primary_action_label: __('Print Selected'),
                primary_action: function(values) {
                    const grid = dialog.fields_dict.references.grid;
                    const selected_rows = grid.get_selected_children();
                    
                    if (!selected_rows.length) {
                        frappe.msgprint(__('Please select at least one reference to print'));
                        return;
                    }
                    
                    const selected_indexes = selected_rows.map(function(row) {
                        console.log(row.reference_idx);
                        return row.reference_idx;
                    });
                    
                    dialog.hide();
                    
                    if (values.print_option === 'Combined PDF (All in one)') {
                        // Print all selected references in one PDF
                        const url = "/api/method/payment_management.payment_management.doctype.payments_and_receipts.payments_and_receipts.print_multiple_references";
                        const params = new URLSearchParams({
                            doctype: frm.doctype,
                            name: frm.docname,
                            reference_indexes: JSON.stringify(selected_indexes),
                            letter_head: values.letter_head || '',
                            no_letterhead: values.no_letterhead ? 1 : 0
                        });
                        
                        window.open(url + "?" + params.toString(), '_blank');
                    } else {
                        // Print individual PDFs for each selected reference
                        selected_indexes.forEach(function(idx, i) {
                            setTimeout(function() {
                                const url = "/api/method/payment_management.payment_management.doctype.payments_and_receipts.payments_and_receipts.print_single_reference";
                                const params = new URLSearchParams({
                                    doctype: frm.doctype,
                                    name: frm.docname,
                                    reference_index: idx,
                                    letter_head: values.letter_head || '',
                                    no_letterhead: values.no_letterhead ? 1 : 0
                                });
                                
                                window.open(url + "?" + params.toString(), '_blank');
                            }, i * 500);
                        });
                    }
                }
            });
            
            // Function to apply filter
            function applyFilter() {
                const partyFilter = (dialog.get_value('party_filter') || '').toLowerCase();
                const minAmount = parseFloat(dialog.get_value('min_amount')) || 0;
                const maxAmount = parseFloat(dialog.get_value('max_amount')) || Number.MAX_VALUE;
                
                const filteredData = tableData.filter(function(row) {
                    let matchParty = true;
                    let matchAmount = true;
                    
                    // Check party filter
                    if (partyFilter && row.party) {
                        matchParty = row.party.toLowerCase().includes(partyFilter);
                    }
                    
                    // Check amount filter
                    const amount = parseFloat(row.amount) || 0;
                    matchAmount = amount >= minAmount && amount <= maxAmount;
                    
                    return matchParty && matchAmount;
                });
                
                // Update table data properly
                const referencesField = dialog.get_field('references');
                if (referencesField) {
                    // Clear existing data
                    referencesField.df.data = [];
                    
                    // Set new filtered data
                    referencesField.df.data = filteredData;
                    
                    // Refresh the field
                    referencesField.refresh();
                    
                    // Also update grid if it exists
                    if (referencesField.grid) {
                        referencesField.grid.data = filteredData;
                        referencesField.grid.refresh();
                    }
                }
            }
            
            // Function to reset table
            function resetTable() {
                const referencesField = dialog.get_field('references');
                if (referencesField) {
                    // Clear existing data
                    referencesField.df.data = [];
                    
                    // Reset to original data
                    referencesField.df.data = tableData;
                    
                    // Refresh the field
                    referencesField.refresh();
                    
                    // Also update grid if it exists
                    if (referencesField.grid) {
                        referencesField.grid.data = tableData;
                        referencesField.grid.refresh();
                    }
                }
            }
            
            dialog.show();
        });
    },
 	setup: function (frm) {
        //eval:in_list(["Receive", "Pay"], doc.payment_type)
        // On a new doc the table is empty -- reading references[0].party directly
        // throws and aborts the rest of the form's setup/refresh chain.
        const first_reference = (frm.doc.references || [])[0];
        if (first_reference && first_reference.party) {
            frm.set_df_property('party_type', 'read_only', 1);
        } else {
            frm.set_df_property('party_type', 'read_only', 0);
        }
        toggle_payment_type_read_only(frm);
        frm.set_query("party_type", function () {
			return {
				filters: {
					name: ["in", Object.keys(frappe.boot.party_account_types)],
				},
			};
		});
 	},
 	mode_of_payment: function (frm) {
        erpnext.accounts.pos.get_payment_mode_account(frm, frm.doc.mode_of_payment, function (account) {
            frm.doc.references.forEach((row)=> {
                frappe.model.set_value(row.doctype, row.name, "mode_of_payment", frm.doc.mode_of_payment);
                frm.refresh_fields();
 	        });
        });
    },
 	party_type: function(frm) {
 	    frm.set_query("party", function () {
 	        if (frm.doc.party_type == "Customer") {
                return {
				    filters: {is_customer : 1},
			    };
 	        }
		});
		frm.doc.references.forEach((row)=> {
		    frm.doc.references.splice(row);
            frm.refresh_fields();
 	    });
 	},
    
});

frappe.ui.form.on("Payments Receipts Reference", {
    references_add: function (frm, cdt, cdn) {
        frm.fields_dict['references'].grid.get_field("party_type").get_query = function() {
                return {
                    filters: {
                        name: ["in", Object.keys(frappe.boot.party_account_types)],
                    }
                }
        };
        let row = frm.selected_doc;
        frappe.model.set_value(row.doctype, row.name, "party_type", frm.doc.party_type);
        // Both branches keep AGM_PARTY_QUERY: without it the party list falls back
        // to Frappe's relevance ranking (typing "1" puts 1001 before 0001) and to
        // whole-substring name matching, so a multi-word name needs typing in full.
        if (row.party_type == "Customer") {
            frm.fields_dict['references'].grid.get_field("party").get_query = function() {
                return {
                    query: AGM_PARTY_QUERY,
                    filters: {
                        is_customer: 1,
                        couple_customer: 0
                    }
                }
            };
        } else {
            frm.fields_dict['references'].grid.get_field("party").get_query = function() {
                return {
                    query: AGM_PARTY_QUERY
                }
            };
        }
        frappe.model.set_value(row.doctype, row.name, "mode_of_payment", frm.doc.mode_of_payment);
        if (row.mode_of_payment) {
            frappe.db.get_value("Mode of Payment", {"name": row.mode_of_payment}, "type", (r) => {
            if (r.type == "Bank") {
                frm.get_field('references').grid.get_row(cdn).toggle_reqd("reference_no", true);
                frm.get_field('references').grid.get_row(cdn).toggle_reqd("reference_date", true);
            }
        })
        }
        frm.refresh_field();

        frm.set_df_property('party_type', 'read_only', 1);
        toggle_payment_type_read_only(frm);

    },
    mode_of_payment: function (frm, cdt, cdn) {
        let row = frm.selected_doc;
        erpnext.accounts.pos.get_payment_mode_account(frm, row.mode_of_payment, function (account) {
            if (!account) {
                frappe.throw(_("error"));
            }
        });
        frappe.db.get_value("Mode of Payment", {"name": row.mode_of_payment}, "type", (r) => {
            if (r.type == "Bank") {
                frm.get_field('references').grid.get_row(cdn).toggle_reqd("reference_no", true);
                frm.get_field('references').grid.get_row(cdn).toggle_reqd("reference_date", true);
            }
        })
    },
    party: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        // Picking (or clearing) a party is what locks / unlocks payment_type.
        toggle_payment_type_read_only(frm);
        if (row.party) {
            frappe.call({
                method: "payment_management.payment_management.doctype.payments_and_receipts.payments_and_receipts.get_party_account_and_bank_details",
                args: {
                    party_type: row.party_type,
                    party: row.party,
                    date: frm.doc.posting_date,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message) {
                        frappe.model.set_value(cdt, cdn, "party_balance",r.message.party_balance);
                        frappe.model.set_value(cdt, cdn, "party_bank",r.message.bank);
                        frappe.model.set_value(cdt, cdn, "party_account",r.message.bank_account);
                        frappe.model.set_value(cdt, cdn, "beneficiary",row.party);
                        frappe.model.set_value(cdt, cdn, "drafts_balance",r.message.drafts_balance);
                        frappe.model.set_value(cdt, cdn, "balance_without_drafts",r.message.balance_without_drafts);
                   }
                }
            });
        }
    },
    references_remove: function(frm, cdt, cdn) {
        if(!frm.doc.references || frm.doc.references.length === 0) {
            frm.set_df_property('party_type', 'read_only', 0);
        }
        toggle_payment_type_read_only(frm);
    },
    
});
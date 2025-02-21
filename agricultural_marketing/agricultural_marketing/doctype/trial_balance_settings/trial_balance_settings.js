// Copyright (c) 2025, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.ui.form.on("Trial Balance Settings", {
    refresh: function(frm) {
       filtering_account_field_in_sections(frm);
       set_options(frm, frm.doc.cash_section, frm.fields_dict.cash_section);
       set_options(frm, frm.doc.customers_section, frm.fields_dict.customers_section);
       set_options(frm, frm.doc.suppliers_section, frm.fields_dict.suppliers_section);
       set_options(frm, frm.doc.share_capital_section, frm.fields_dict.share_capital_section);
       set_options(frm, frm.doc.taxes_section, frm.fields_dict.taxes_section);
       set_options(frm, frm.doc.income_section, frm.fields_dict.income_section);
       set_options(frm, frm.doc.expense_section, frm.fields_dict.expense_section);
       frm.fields_dict['income_section'].grid.get_field("commission_item").get_query = function() {
        return {
            filters: {
                commission_item: 1
            }
        }
    };
 	},
});

frappe.ui.form.on("Trial Balance Cash Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.cash_section, frm.fields_dict.cash_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.cash_section, frm.fields_dict.cash_section);
 	},
});

frappe.ui.form.on("Trial Balance Customer Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.customers_section, frm.fields_dict.customers_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.customers_section, frm.fields_dict.customers_section);
 	},
});

frappe.ui.form.on("Trial Balance Supplier Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.suppliers_section, frm.fields_dict.suppliers_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.suppliers_section, frm.fields_dict.suppliers_section);
 	},
});

frappe.ui.form.on("Trial Balance Share Capital Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.share_capital_section, frm.fields_dict.share_capital_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.share_capital_section, frm.fields_dict.share_capital_section);
 	},
});

frappe.ui.form.on("Trial Balance Tax Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.taxes_section, frm.fields_dict.taxes_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.taxes_section, frm.fields_dict.taxes_section);
 	},
});

frappe.ui.form.on("Trial Balance Income Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.income_section, frm.fields_dict.income_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.income_section, frm.fields_dict.income_section);
 	},
});

frappe.ui.form.on("Trial Balance Expense Section", {
    is_parent: function(frm) {
       set_options(frm, frm.doc.expense_section, frm.fields_dict.expense_section);
    },

    title: function(frm) {
       set_options(frm, frm.doc.expense_section, frm.fields_dict.expense_section);
 	},
});

function filtering_account_field_in_sections (frm) {
    frm.fields_dict['cash_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"account_type": ["in", ["Bank", "Cash"]]}
            }
       };

       frm.fields_dict['customers_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"root_type": "Asset", "account_type": ["not in", ["Bank", "Cash"]]}
            }
       };

       frm.fields_dict['suppliers_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"root_type": "Liability"}
            }
       };

       frm.fields_dict['share_capital_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"root_type": "Equity"}
            }
       };

       frm.fields_dict['taxes_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"account_type": "Tax"}
            }
       };

       frm.fields_dict['income_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"root_type": "Income"}
            }
       };

       frm.fields_dict['expense_section'].grid.get_field("account").get_query = function() {
            return {
                filters: {"root_type": "Expense"}
            }
       };
};

function set_options(frm, section, section_grid){
    let options = []
    section.forEach((row)=> {
        if (row.is_parent)
            options.push(row.title);
    });
    section_grid.grid.update_docfield_property(
        "parent1",
        "options",
        [""].concat(options)
    );
}
// Copyright (c) 2023, Your Company and contributors
// For license information, please see license.txt

frappe.query_reports["Account Statement"] = {
    "filters": [
        {
            "fieldname": "company",
            "label": __("Company"),
            "fieldtype": "Link",
            "options": "Company",
            "default": frappe.defaults.get_user_default("Company"),
            "reqd": 1
        },
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "party_type",
            "label": __("Party Type"),
            "fieldtype": "Select",
            "options": ["Customer", "Supplier"],
            "default": "Customer",
            "reqd": 1
        },
        {
            "fieldname": "party",
            "label": __("Customer/Supplier"),
            "fieldtype": "Link",
            "options": "Customer",
            "get_query": function() {
                let party_type = frappe.query_report.get_filter_value('party_type');
                if (!party_type) party_type = "Customer"; // Default if not set
                return {
                    filters: { "disabled": 0 }
                };
            }
        },
        {
            "fieldname": "party_group",
            "label": __("Customer/Supplier Group"),
            "fieldtype": "Link",
            "options": "Customer Group",
            "default": ""
        },
        {
            "fieldname": "include_pampers",
            "label": __("Include Pampers"),
            "fieldtype": "Check",
            "default": 1,
            "depends_on": "eval:doc.party_type=='Customer'"
        },
        {
            "fieldname": "consider_drafts",
            "label": __("Consider Drafts"),
            "fieldtype": "Check",
            "default": 0
        },
        {
            "fieldname": "consider_draft_payments",
            "label": __("Consider Draft Payments"),
            "fieldtype": "Check",
            "default": 0
        },
        {
            "fieldname": "ignore_zero_transactions",
            "label": __("Ignore Zero Balance Accounts"),
            "fieldtype": "Check",
            "default": 0
        },
        {
            "fieldname": "make_balance_in_opening_total",
            "label": __("Make Balance in Opening/Total"),
            "fieldtype": "Check",
            "default": 0
        }
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        let formatted = default_formatter(value, row, column, data);

        if (data && (column.fieldname === "total_debit" || column.fieldname === "total_credit")) {
            // Highlight the total columns
            formatted = `<span style="font-weight: bold;">${formatted}</span>`;
        }

        return formatted;
    },
    
    "initial_depth": 0,
    "tree": true,
    "parent_field": null,
    "name_field": "party",
    
    "onload": function(report) {
        // Simple initialization without complex filter manipulation
        if (!frappe.query_report.get_filter_value('party_type')) {
            frappe.query_report.set_filter_value('party_type', 'Customer');
        }
        
        // Set up a listener for party_type changes using jQuery event delegation
        $(document).off('change', '[data-fieldname="party_type"]');
        $(document).on('change', '[data-fieldname="party_type"]', function() {
            setTimeout(function() {
                let party_type = frappe.query_report.get_filter_value('party_type');
                
                // Clear related filters
                frappe.query_report.set_filter_value('party', '');
                frappe.query_report.set_filter_value('party_group', '');
                
                // Update party filter options
                let party_filter = frappe.query_report.get_filter('party');
                if (party_filter && party_filter.df) {
                    party_filter.df.options = party_type;
                }
                
                // Update party group filter
                let party_group_filter = frappe.query_report.get_filter('party_group');
                if (party_group_filter && party_group_filter.df) {
                    if (party_type === "Customer") {
                        party_group_filter.df.options = "Customer Group";
                        party_group_filter.df.label = __("Customer Group");
                    } else {
                        party_group_filter.df.options = "Supplier Group";
                        party_group_filter.df.label = __("Supplier Group");
                    }
                }
            }, 200);
        });
    }
};

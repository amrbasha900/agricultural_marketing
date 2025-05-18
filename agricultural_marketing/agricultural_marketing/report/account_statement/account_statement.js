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
            "reqd": 1,
            "on_change": function() {
                let party_type = frappe.query_report.get_filter_value('party_type');
                frappe.query_report.set_filter_value('party', "");
                frappe.query_report.set_filter_value('party_group', "");
                
                let party_group_field = party_type === "Customer" ? "customer_group" : "supplier_group";
                let party_group_options = party_type === "Customer" ? "Customer Group" : "Supplier Group";
                
                frappe.query_report.get_filter('party').df.options = party_type;
                frappe.query_report.get_filter('party_group').df.fieldname = party_group_field;
                frappe.query_report.get_filter('party_group').df.options = party_group_options;
                
                // Show or hide the "Include Pampers" filter based on party type
                let include_pampers_filter = frappe.query_report.get_filter('include_pampers');
                if (party_type === "Customer") {
                    include_pampers_filter.df.hidden = 0;
                } else {
                    include_pampers_filter.df.hidden = 1;
                    frappe.query_report.set_filter_value('include_pampers', 1); // Default to true for Suppliers
                }
                
                frappe.query_report.refresh_filters();
            }
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
            "hidden": 0, // Will be shown or hidden based on party_type
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
        }
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        if (data && (column.fieldname === "total_debit" || column.fieldname === "total_credit")) {
            // Highlight the total columns
            value = `<span style="font-weight: bold;">${value}</span>`;
        }
        
        return value;
    },
    
    "initial_depth": 0,
    "tree": true,
    "parent_field": null,
    "name_field": "party",
    
    "onload": function(report) {
        // Ensure party_type is set to a default value
        if (!frappe.query_report.get_filter_value('party_type')) {
            frappe.query_report.set_filter_value('party_type', 'Customer');
        }
        
       
        
        
        
        // Set initial visibility of the Include Pampers filter
        let party_type = frappe.query_report.get_filter_value('party_type');
        if (!party_type) {
            frappe.query_report.set_filter_value('party_type', 'Customer');
            party_type = 'Customer';
        }
        
        let include_pampers_filter = frappe.query_report.get_filter('include_pampers');
        if (party_type === "Customer") {
            include_pampers_filter.df.hidden = 0;
        } else {
            include_pampers_filter.df.hidden = 1;
        }
        
        
    }
};

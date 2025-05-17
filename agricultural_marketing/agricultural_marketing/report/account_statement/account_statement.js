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
        // Set appropriate label when party type changes
        report.page.add_inner_button(__('Print'), function() {
            let filters = report.get_values();
            frappe.set_route('print', 'Customer Supplier Account Summary', 'Customer Supplier Account Summary', 
                             JSON.stringify(filters));
        });
        
        // If used in agri marketing module, add custom buttons to export data
        if (frappe.boot.active_modules.includes("agricultural_marketing")) {
            report.page.add_inner_button(__('Export PDF'), function() {
                let filters = report.get_values();
                export_report_as_pdf(filters);
            });
        }
    }
};

// Function to export report as PDF using the execute function in the Python file
function export_report_as_pdf(filters) {
    frappe.call({
        method: "agricultural_marketing.agricultural_marketing.page.collection_form.collection_form.execute",
        args: {
            filters: filters
        },
        callback: function(r) {
            if (r.message && r.message.file_url) {
                window.open(r.message.file_url);
            }
        }
    });
}
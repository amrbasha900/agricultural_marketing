// Copyright (c) 2025, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.query_reports["Pamper Sales Statistics"] = {
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
            "fieldname": "pamper",
            "label": __("Pamper"),
            "fieldtype": "Link",
			"options": "Customer",
			get_query: () => {
				return {
					filters: {
						is_pamper: 1,
					},
				};
			}
        },
        {
            "fieldname": "is_draft",
            "label": __("Consider Draft"),
            "fieldtype": "Check",
            "default": 0
        },
        
        {
            "fieldname": "show_commission",
            "label": __("Show Commission"),
            "fieldtype": "Check",
            "default": 0
        },
        {
            "fieldname": "show_tax",
            "label": __("Show Tax"),
            "fieldtype": "Check",
            "default": 0
        },
		{
            "fieldname": "total_commissions_and_taxes",
            "label": __("Show Total Tax and Commission"),
            "fieldtype": "Check",
            "default": 0
        }
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        // Add any custom formatting logic here if needed
        // For example, you can highlight rows based on certain conditions
        
        return value;
    },
    
    "onload": function(report) {
        // Add any initialization logic here
        
        // Update view when checkboxes for commission or tax are toggled
        report.page.add_inner_button(__("Refresh Columns"), function() {
            // This will refresh the report with updated filters
            frappe.query_report.refresh();
        });
    }
};
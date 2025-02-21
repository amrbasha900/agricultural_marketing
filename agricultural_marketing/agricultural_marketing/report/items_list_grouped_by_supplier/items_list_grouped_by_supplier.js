// Copyright (c) 2024, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.query_reports["Items list grouped by supplier"] = {
	"filters": [
	    {
	        "fieldname": "company",
            "label": __("Company"),
            "fieldtype": "Link",
            "options": "Company",
            default: frappe.defaults.get_user_default('company'),
            reqd:1,
            "wildcard_filter": 0
        },
	    {
           "fieldname": "supplier",
           "fieldtype": "Link",
           "label": __("Supplier"),
           "options": "Supplier",
           "wildcard_filter": 0
        },
        {
           "fieldname": "item_code",
           "fieldtype": "Data",
           "label": __("Item"),
           "wildcard_filter": 0
        },
        {
           "fieldname": "from_date",
           "fieldtype": "Date",
           "label": __("From Date"),
           "wildcard_filter": 0
        },
        {
           "fieldname": "to_date",
           "fieldtype": "Date",
           "label": __("To Date"),
           "wildcard_filter": 0
        },
        {
           "fieldname": "draft",
           "fieldtype": "Check",
           "label": __("Consider Drafts"),
           "wildcard_filter": 0
        }
	]
};

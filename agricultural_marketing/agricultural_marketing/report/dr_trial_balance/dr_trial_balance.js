// Copyright (c) 2025, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.query_reports["DR Trial Balance"] = {
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
           "fieldname": "from_date",
           "fieldtype": "Date",
           "label": __("From Date"),
           "wildcard_filter": 0,
           default: frappe.datetime.get_today()
        },
        {
           "fieldname": "to_date",
           "fieldtype": "Date",
           "label": __("To Date"),
           "wildcard_filter": 0,
           default: frappe.datetime.get_today()
        },
	    {
           "fieldname": "consider_drafts",
           "fieldtype": "Check",
           "label": __("Consider Drafts"),
           "wildcard_filter": 0
        }
	]
};

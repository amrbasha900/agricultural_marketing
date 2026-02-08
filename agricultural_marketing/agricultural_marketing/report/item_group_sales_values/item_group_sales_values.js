// Copyright (c) 2026, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.query_reports["Item Group Sales Values"] = {
	"filters": [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "include_drafts",
			label: __("Include Drafts"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "invoice_form",
			label: __("Invoice Form"),
			fieldtype: "Link",
			options: "Invoice Form",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
	],
};

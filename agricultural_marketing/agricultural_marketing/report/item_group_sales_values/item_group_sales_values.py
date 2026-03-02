# Copyright (c) 2026, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("المجموعة"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 150,
		},
		{
			"label": _("مجموع المبيعات"),
			"fieldname": "total_sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 180,
		},
		{
			"label": _("نسبة السعي"),
			"fieldname": "commission_rate",
			"fieldtype": "Float",
			"precision": 2,
			"width": 120,
		},
		{
			"label": _("مجموع السعي"),
			"fieldname": "total_commission",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 180,
		},
		{
			"label": _("عدد البيعات"),
			"fieldname": "num_sales",
			"fieldtype": "Int",
			"width": 120,
		},
		{
			"label": _("عدد الوحدات"),
			"fieldname": "num_units",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("ملاحظات"),
			"fieldname": "notes",
			"fieldtype": "Data",
			"width": 150,
		},
	]


def get_data(filters):
	conditions = get_conditions(filters)

	data = frappe.db.sql(
		"""
		SELECT
			item.item_group AS item_group,
			SUM(ifi.total) AS total_sales,
			ROUND(
				CASE
					WHEN SUM(ifi.total) > 0
					THEN SUM( ifi.commission) / SUM(ifi.total) * 100
					ELSE 0
				END,
			2) AS commission_rate,
			SUM(ifi.commission) AS total_commission,
			COUNT(ifi.name) AS num_sales,
			SUM(ifi.qty) AS num_units
		FROM
			`tabInvoice Form Item` ifi
		JOIN
			`tabInvoice Form` inv ON ifi.parent = inv.name
		JOIN
			`tabItem` item ON ifi.item_code = item.name
		WHERE
			inv.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{conditions}
		GROUP BY
			item.item_group
		ORDER BY
			item.item_group
		""".format(
			conditions=conditions
		),
		filters,
		as_dict=1,
	)

	return data


def get_conditions(filters):
	conditions = ""

	if filters.get("include_drafts"):
		conditions += " AND inv.docstatus IN (0, 1)"
	else:
		conditions += " AND inv.docstatus = 1"

	if filters.get("item_group"):
		conditions += " AND item.item_group = %(item_group)s"

	if filters.get("invoice_form"):
		conditions += " AND inv.name = %(invoice_form)s"

	if filters.get("customer"):
		conditions += " AND ifi.customer = %(customer)s"

	if filters.get("supplier"):
		conditions += " AND inv.supplier = %(supplier)s"

	return conditions

# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_company_address


def execute(filters=None):
    columns = get_columns()
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    invoices_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))

    if filters.get("customer"):
        invoices_query = invoices_query.where(invformitem.customer == filters.get('customer'))

    if filters.get("invoice_id"):
        invoices_query = invoices_query.where(invform.name.like(f"%{filters.get('invoice_id')}%"))

    if filters.get("item_code"):
        invoices_query = invoices_query.where(
            (invformitem.item_code.like(f"%{filters.get('item_code')}%")) |
            (invformitem.item_name.like(f"%{filters.get('item_code')}%"))
        )

    if filters.get("from_date") and filters.get("to_date") and (filters.get("to_date") < filters.get("from_date")):
        frappe.throw(_("To date must be after from date"))

    if filters.get("from_date"):
        invoices_query = invoices_query.where(invform.posting_date.gte(filters.get("from_date")))

    if filters.get("to_date"):
        invoices_query = invoices_query.where(invform.posting_date.lte(filters.get("to_date")))

    docstatuses = ["0", "1"] if filters.get("draft") else ["1"]
    invoices_query = invoices_query.where(invform.docstatus.isin(docstatuses))

    data = invoices_query.select(invform.name.as_("invoice_id"), invform.posting_date.as_("date"), invformitem.qty,
                                 invformitem.price, invformitem.total, invformitem.item_name).run(as_dict=True)

    if data:
        total_amount = sum([row['total'] for row in data]) or 0

        total_row = {}
        total_row.update({
            "qty": None,
            "price": None,
            "invoice_id": _("Grand Total"),
            "total": total_amount
        })
        data.append(total_row)

    company_defaults = frappe.get_doc("Company", filters.get('company')).as_dict()
    company_defaults["address"] = get_company_address(company_defaults['name']).get("company_address_display")
    data.append(company_defaults)
    return columns, data


def get_columns():
    return [
        {
            "fieldname": "invoice_id",
            "label": _("Invoice No"),
            "fieldtype": "Link",
            "options": "Invoice Form",
            "width": 150,
        },
        {
            "fieldname": "date",
            "label": _("Date"),
            "fieldtype": "Date",
            "width": 150,
        },
        {
            "fieldname": "item_name",
            "label": _("Item"),
            "fieldtype": "Link",
            "options": "Item",
            "width": 200,
        },
        {
            "fieldname": "qty",
            "label": _("Quantity"),
            "fieldtype": "Float",
            "width": 100
        },
        {
            "fieldname": "price",
            "label": _("Price"),
            "fieldtype": "Float",
            "width": 100
        },
        {
            "fieldname": "total",
            "label": _("Total"),
            "fieldtype": "Float",
            "width": 100
        },
        {
            "fieldname": "company_defaults",
            "label": _("Company Defaults"),
            "hidden": 1,
            "width": 100
        }
    ]

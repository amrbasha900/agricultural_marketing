# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_company_address


def execute(filters=None):
    columns = get_columns()

    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    invformcomm = frappe.qb.DocType("Invoice Form Commission")

    invoices_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))

    commission_and_taxes_query = frappe.qb.from_(invform).left_join(invformcomm).on(
        invformcomm.parent == invform.name).where(invform.company == filters.get('company'))

    if filters.get("supplier"):
        invoices_query = invoices_query.where(invform.supplier == filters.get('supplier'))
        commission_and_taxes_query = commission_and_taxes_query.where(invform.supplier == filters.get('supplier'))

    if filters.get("invoice_id"):
        invoices_query = invoices_query.where(invform.name.like(f"%{filters.get('invoice_id')}%"))
        commission_and_taxes_query = commission_and_taxes_query.where(
            invform.name.like(f"%{filters.get('invoice_id')}%"))

    if filters.get("item_code"):
        invoices_query = invoices_query.where(
            (invformitem.item_code.like(f"%{filters.get('item_code')}%")) |
            (invformitem.item_name.like(f"%{filters.get('item_code')}%"))
        )
        commission_and_taxes_query = commission_and_taxes_query.where(
            (invformitem.item_code.like(f"%{filters.get('item_code')}%")) |
            (invformitem.item_name.like(f"%{filters.get('item_code')}%"))
        )

    if filters.get("from_date") and filters.get("to_date") and (filters.get("to_date") < filters.get("from_date")):
        frappe.throw(_("To date must be after from date"))

    if filters.get("from_date"):
        invoices_query = invoices_query.where(invform.posting_date.gte(filters.get("from_date")))
        commission_and_taxes_query = commission_and_taxes_query.where(
            invform.posting_date.gte(filters.get("from_date")))

    if filters.get("to_date"):
        invoices_query = invoices_query.where(invform.posting_date.lte(filters.get("to_date")))
        commission_and_taxes_query = commission_and_taxes_query.where(invform.posting_date.lte(filters.get("to_date")))

    docstatuses = ["0", "1"] if filters.get("draft") else ["1"]
    invoices_query = invoices_query.where(invform.docstatus.isin(docstatuses))
    commission_and_taxes_query = commission_and_taxes_query.where(invform.docstatus.isin(docstatuses))

    data = invoices_query.select(invform.name.as_("invoice_id"), invform.posting_date.as_("date"), invformitem.qty,
                                 invformitem.price, invformitem.total, invformitem.item_name).run(as_dict=True)

    commission = (invformcomm.price * invformcomm.commission) / 100
    taxes = (commission * invformcomm.taxes) / 100
    commissions_and_taxes = commission_and_taxes_query.select(invform.name,
                                                              commission.as_("commission"), taxes.as_("taxes")
                                                              ).groupby(invform.name).run(as_dict=True)

    if data:
        calculate_totals(data, commissions_and_taxes)

    company_defaults = frappe.get_doc("Company", filters.get("company")).as_dict()
    company_defaults["address"] = get_company_address(company_defaults['name']).get("company_address_display")
    data.append(company_defaults)
    return columns, data


def calculate_totals(data, commissions_and_taxes):
    # FIXME: Refactor this method
    total_amount = total_commission = total_taxes = net_total = 0
    # Calculate total amounts
    total_amount = sum([row['total'] for row in data])

    total_row = {}
    total_row.update({
        "qty": None,
        "price": None,
        "invoice_id": _("Grand Total"),
        "total": total_amount
    })
    data.append(total_row)
    if commissions_and_taxes:
        total_commission = sum([row['commission'] for row in commissions_and_taxes])
        total_taxes = sum([row['taxes'] for row in commissions_and_taxes])
        total_commission_row = {
            "qty": None,
            "price": None,
            "invoice_id": _("Total Commission"),
            "total": total_commission
        }
        total_taxes_row = {
            "qty": None,
            "price": None,
            "invoice_id": _("Taxes"),
            "total": total_taxes
        }
        data.append(total_commission_row)
        data.append(total_taxes_row)

    net_total = total_amount - (total_commission + total_taxes)
    net_total_row = {
        "qty": None,
        "price": None,
        "invoice_id": _("Net Total"),
        "total": net_total
    }
    data.append(net_total_row)


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

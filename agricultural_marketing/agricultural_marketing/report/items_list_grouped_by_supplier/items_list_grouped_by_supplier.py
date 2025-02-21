# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Sum, Count, Avg
from frappe.contacts.doctype.address.address import get_company_address


def execute(filters=None):
    columns = get_columns()
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")

    items_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name
    ).where(invform.company == filters.get('company'))

    if filters.get("supplier"):
        items_query = items_query.where(invform.supplier == filters.get('supplier'))

    if filters.get("item_code"):
        items_query = items_query.where(
            (invformitem.item_code.like(f"%{filters.get('item_code')}%")) |
            (invformitem.item_name.like(f"%{filters.get('item_code')}%"))
        )

    if filters.get("from_date") and filters.get("to_date") and (filters.get("to_date") < filters.get("from_date")):
        frappe.throw(_("To date must be after from date"))

    if filters.get("from_date"):
        items_query = items_query.where(invform.posting_date.gte(filters.get("from_date")))

    if filters.get("to_date"):
        items_query = items_query.where(invform.posting_date.lte(filters.get("to_date")))

    docstatuses = ["0", "1"] if filters.get("draft") else ["1"]
    items_query = items_query.where(invform.docstatus.isin(docstatuses))

    data = items_query.select(Count(invform.name).as_("total_selling").distinct(),
                              invformitem.item_name.as_("item_name"),
                              Sum(invformitem.commission).as_("total_commission"),
                              Sum(invformitem.qty).as_("total_qty"),
                              (Sum(invformitem.price * invformitem.qty) / Sum(invformitem.qty)).as_("price"),
                              ((Sum(invformitem.price * invformitem.qty) / Sum(invformitem.qty)) * Sum(
                                  invformitem.qty)).as_("total")
                              ).groupby(invformitem.item_name).run(as_dict=True)

    if data:
        calculate_totals(data)

    company_defaults = frappe.get_doc("Company", filters.get("company")).as_dict()
    company_defaults["address"] = get_company_address(company_defaults['name']).get("company_address_display")
    data.append(company_defaults)
    return columns, data


def get_columns():
    return [
        {
            "fieldname": "total_selling",
            "label": _("Total Selling"),
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "fieldname": "total_commission",
            "label": _("Total Commission"),
            "fieldtype": "Float",
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
            "fieldname": "total_qty",
            "label": _("Total Quantity"),
            "fieldtype": "Int",
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


def calculate_totals(data):
    # FIXME: Refactor this method
    total_amount = total_commission = total_taxes = net_total = 0
    # Calculate total amounts
    total_amount = sum([row['total'] for row in data])

    total_row = {}
    total_row.update({
        "total_qty": None,
        "price": None,
        "total_commission": None,
        "total_selling": _("Grand Total"),
        "total": total_amount
    })
    data.append(total_row)
    total_commission = sum([row['total_commission'] for row in data if row['total_commission']])
    default_tax_template = frappe.db.get_single_value("Agriculture Settings", "default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    tax_rate = frappe.db.get_value("Sales Taxes and Charges",
                                   {"parent": default_tax_template}, "rate") or 0

    total_taxes = (total_commission * tax_rate) / 100
    total_commission_row = {
        "total_qty": None,
        "price": None,
        "total_commission": None,
        "total_selling": _("Total Commission"),
        "total": total_commission
    }
    total_taxes_row = {
        "total_qty": None,
        "price": None,
        "total_commission": None,
        "total_selling": _("Taxes"),
        "total": total_taxes
    }
    data.append(total_commission_row)
    data.append(total_taxes_row)

    net_total = total_amount - (total_commission + total_taxes)
    net_total_row = {
        "total_qty": None,
        "price": None,
        "total_commission": None,
        "total_selling": _("Net Total"),
        "total": net_total
    }
    data.append(net_total_row)

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

    if filters.get("customer"):
        items_query = items_query.where(invformitem.customer == filters.get('customer'))

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
                              Sum(invformitem.qty).as_("total_qty"),
                              (Sum(invformitem.price * invformitem.qty) / Sum(invformitem.qty)).as_("price"),
                              ((Sum(invformitem.price * invformitem.qty) / Sum(invformitem.qty)) * Sum(
                                  invformitem.qty)).as_("total")
                              ).groupby(invformitem.item_name).run(as_dict=True)

    if data:
        total_amount = sum([row['total'] for row in data]) or 0

        total_row = {}
        total_row.update({
            "total_qty": None,
            "price": None,
            "total_selling": _("Grand Total"),
            "total": total_amount
        })
        data.append(total_row)

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

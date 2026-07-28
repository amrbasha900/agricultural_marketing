"""Custom fields for the shared supplier charge voucher, plus a backfill.

Vouchers made by the earlier one-voucher-per-invoice code have no ``invoice_form``
on their rows and no ``charge_payment_line`` on their invoices. Filling both in
here means they behave exactly like vouchers built by the batch flow.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Payments Receipts Reference": [
                {
                    "label": _("Invoice Form"),
                    "fieldname": "invoice_form",
                    "fieldtype": "Link",
                    "options": "Invoice Form",
                    "insert_after": "description",
                    "read_only": 1,
                    "no_copy": 1,
                }
            ],
            "Payments and Receipts": [
                {
                    "label": _("Is Supplier Charge"),
                    "fieldname": "is_supplier_charge",
                    "fieldtype": "Check",
                    "insert_after": "company",
                    "read_only": 1,
                    "no_copy": 1,
                    "description": _(
                        "Built from Invoice Form supplier charges. Edit it through the invoices, not here."
                    ),
                }
            ],
        },
        ignore_validate=True,
    )

    invoices = frappe.get_all(
        "Invoice Form",
        filters={"charges_payment": ["is", "set"]},
        fields=["name", "charges_payment"],
    )

    for invoice in invoices:
        if not frappe.db.exists("Payments and Receipts", invoice.charges_payment):
            frappe.db.set_value("Invoice Form", invoice.name, "charges_payment", None, update_modified=False)
            continue

        rows = frappe.get_all(
            "Payments Receipts Reference",
            filters={"parent": invoice.charges_payment, "parenttype": "Payments and Receipts"},
            fields=["name"],
            order_by="idx",
        )
        if len(rows) != 1:
            # Only the old code wrote this link, and it only ever made single-row
            # vouchers. Anything else is not ours to relabel.
            continue

        frappe.db.set_value("Payments Receipts Reference", rows[0].name, "invoice_form", invoice.name, update_modified=False)
        frappe.db.set_value("Invoice Form", invoice.name, "charge_payment_line", rows[0].name, update_modified=False)
        frappe.db.set_value("Payments and Receipts", invoice.charges_payment, "is_supplier_charge", 1, update_modified=False)

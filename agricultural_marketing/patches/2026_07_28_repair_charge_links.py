"""Repair charge rows whose invoice has forgotten them, and index the durable link.

The links were written with ``update_modified=False``, so any invoice loaded before a
batch ran could be saved afterwards and put its empty values straight back over them.
The row stayed on the voucher while the invoice forgot it, and editing the invoice
no longer reached the row. ``repair_charge_links`` restores those from the rows.

The index matters because every invoice save now looks its row up by ``invoice_form``.
"""

import frappe

from agricultural_marketing.agricultural_marketing.doctype.invoice_form.supplier_charges import (
    repair_charge_links,
)


def execute():
    if frappe.db.has_column("Payments Receipts Reference", "invoice_form"):
        frappe.db.add_index("Payments Receipts Reference", ["invoice_form"])

        result = repair_charge_links()
        if result["repaired"]:
            frappe.logger().info(
                f"supplier charges: relinked {len(result['repaired'])} invoices to their rows"
            )

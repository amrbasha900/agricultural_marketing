import frappe


def update_invoice_form(doc, method):
    is_supplier = True
    if doc.is_commission_invoice:
        for it in doc.items:
            invoice_form = it.get("invoice_form")
            if invoice_form:
                invoice_form_doc = frappe.get_doc("Invoice Form", invoice_form)
                for row in invoice_form_doc.items:
                    if row.get("customer") == doc.customer:
                        frappe.db.set_value("Invoice Form Item", row.get("name"), "has_commission_invoice", 0)
                        is_supplier = False

                if is_supplier:
                    frappe.db.set_value("Invoice Form", invoice_form_doc.name, "has_supplier_commission_invoice", 0)

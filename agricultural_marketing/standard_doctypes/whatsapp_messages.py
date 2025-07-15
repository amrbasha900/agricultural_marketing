import frappe

def update_invoice_form(doc, method):
    if doc.status in ['Sent', 'Delivered', 'Read'] and doc.reference_document == "Invoice Form":
        frappe.db.set_value("Invoice Form", doc.document_name, "sent_via_whatsapp", 1)
        frappe.db.commit()
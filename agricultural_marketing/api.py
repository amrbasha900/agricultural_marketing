import frappe

@frappe.whitelist()
def mark_invoices_as_printed(invoice_names):
    """
    Mark multiple invoices as printed, bypassing permissions
    """
    if isinstance(invoice_names, str):
        import json
        invoice_names = json.loads(invoice_names)
    
    updated_count = 0
    
    for name in invoice_names:
        try:
            # Use ignore_permissions=True to bypass permission checks
            doc = frappe.get_doc('Invoice Form', name)
            doc.is_printed = 1
            doc.save(ignore_permissions=True)
            updated_count += 1
        except Exception as e:
            frappe.log_error(f"Error updating invoice {name}: {str(e)}")
    
    return {
        'success': True,
        'updated_count': updated_count,
        'total_count': len(invoice_names)
    }
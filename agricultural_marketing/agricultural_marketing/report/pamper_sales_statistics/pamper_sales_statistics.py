# Copyright (c) 2023, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _

def execute(filters=None):
    # Check for mandatory filters
    if not filters:
        filters = {}
    
    if not filters.get("company"):
        # Set default company if not provided
        filters["company"] = frappe.db.get_default("company")
        
    # Validate mandatory filters
    if not filters.get("company"):
        frappe.throw(_("Company is mandatory"))
    
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are mandatory"))
        
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data

def get_columns(filters):
    columns = [
        {"label": _("Pamper Name"), "fieldname": "pamper_name", "fieldtype": "Link", "options": "Customer", "width": 180},
        {"label": _("Invoice Count"), "fieldname": "invoice_count", "fieldtype": "Int", "width": 150},
        {"label": _("Supplier Count"), "fieldname": "supplier_count", "fieldtype": "Int", "width": 150},
        {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 150},
    ]
    
    # Add conditional columns based on filters
    if filters.get("show_commission"):
        columns.append({"label": _("Commission"), "fieldname": "commission", "fieldtype": "Currency", "width": 120})
    
    if filters.get("show_tax"):
        columns.append({"label": _("Tax"), "fieldname": "tax", "fieldtype": "Currency", "width": 120})
    
    if filters.get("total_commissions_and_taxes"):
        columns.append({"label": _("Total Commissions and Taxes"), "fieldname": "total_commissions_and_taxes", "fieldtype": "Currency", "width": 220})

    return columns

def get_data(filters):
    conditions = get_conditions(filters)
    
    pamper_filter_condition = ""
    if filters.get("pamper"):
        pamper_filter_condition = " AND name = %(pamper)s"
    
    # Get all pamper customers (customers with is_pamper = 1)
    pampers = frappe.db.sql("""
        SELECT 
            name, 
            customer_name
        FROM 
            `tabCustomer` 
        WHERE 
            is_pamper = 1 {pamper_filter_condition}
    """.format(pamper_filter_condition=pamper_filter_condition), filters, as_dict=1)
    
    result = []
    
    # For each pamper, fetch related invoices
    for pamper in pampers:
        pamper_id = pamper.name
        
        # Query to get invoices for this pamper
        filters_copy = filters.copy()
        filters_copy["pamper"] = pamper_id
        
        invoice_conditions = get_conditions(filters_copy)
        
        invoices = frappe.db.sql("""
            SELECT 
                name,
                supplier,
                grand_total,
                total_commissions_and_taxes
            FROM 
                `tabInvoice Form`
            WHERE 1 = 1 AND {conditions}
        """.format(conditions=invoice_conditions), filters_copy, as_dict=1)
        
        if not invoices:
            continue
        
        # Count unique suppliers
        suppliers = set()
        for invoice in invoices:
            if invoice.supplier:
                suppliers.add(invoice.supplier)
        
        # Calculate grand total
        grand_total = sum(invoice.grand_total or 0 for invoice in invoices)
        total_commissions_and_taxes = sum(invoice.total_commissions_and_taxes or 0 for invoice in invoices)
        
        commission_total = 0
        tax_total = 0
        
        # Calculate commission and tax if needed
        if filters.get("show_commission") or filters.get("show_tax"):
            for invoice in invoices:
                # Get commission details from child table
                commission_items = frappe.db.sql("""
                    SELECT 
                        price, 
                        commission,
                        taxes
                    FROM 
                        `tabInvoice Form Commission`
                    WHERE 
                        parent = %(invoice)s
                """, {"invoice": invoice.name}, as_dict=1)
                
                for item in commission_items:
                    price = item.price or 0
                    commission_rate = item.commission or 0
                    tax_rate = item.taxes or 0
                    
                    commission_value = (price * commission_rate / 100)
                    commission_total += commission_value
                    
                    if filters.get("show_tax"):
                        tax_total += ((commission_value) * tax_rate / 100)
        
        # Build the row
        row = {
            "pamper_name": pamper.customer_name or pamper.name,
            "invoice_count": len(invoices),
            "supplier_count": len(suppliers),
            "grand_total": grand_total,
        }
        
        if filters.get("show_commission"):
            row["commission"] = commission_total
            
        if filters.get("show_tax"):
            row["tax"] = tax_total

        if filters.get("total_commissions_and_taxes"):
            row["total_commissions_and_taxes"] = total_commissions_and_taxes
        
        result.append(row)
    
    return result

def get_conditions(filters):
    conditions = []
    
    if filters.get("from_date") and filters.get("to_date"):
        conditions.append("posting_date BETWEEN %(from_date)s AND %(to_date)s")
    
    if filters.get("is_draft"):
        conditions.append("docstatus IN (0, 1)")
    else:
        conditions.append("docstatus = 1")
    
    if filters.get("company"):
        conditions.append("company = %(company)s")
    
    if filters.get("pamper"):
            conditions.append("pamper = %(pamper)s")
        
    
    return " AND ".join(conditions) if conditions else "1=1"
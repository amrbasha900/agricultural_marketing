import json
import frappe
from frappe import _
from frappe.utils import flt
from frappe.query_builder.functions import Sum
from pypika import Case

@frappe.whitelist()
def execute(filters=None):
    if isinstance(filters, str):
        filters = json.loads(filters)
    
    columns = get_columns(filters)
    data = get_report_data(filters)
    
    return columns, data

def get_columns(filters):
    """Define the columns for the report"""
    party_label = _("Customer Name") if filters.get("party_type") == "Customer" else _("Supplier Name")
    
    return [
        {
            "fieldname": "party",
            "label": party_label,
            "fieldtype": "Link",
            "options": filters.get("party_type"),
            "width": 200
        },
        {
            "fieldname": "opening_debit",
            "label": _("Opening Debit"),
            "fieldtype": "Currency",
            "width": 120
        },
        {
            "fieldname": "opening_credit",
            "label": _("Opening Credit"),
            "fieldtype": "Currency",
            "width": 120
        },
        {
            "fieldname": "movement_debit",
            "label": _("Movement Debit"),
            "fieldtype": "Currency",
            "width": 120
        },
        {
            "fieldname": "movement_credit",
            "label": _("Movement Credit"),
            "fieldtype": "Currency",
            "width": 120
        },
        {
            "fieldname": "total_debit",
            "label": _("Total Debit"),
            "fieldtype": "Currency",
            "width": 120
        },
        {
            "fieldname": "total_credit",
            "label": _("Total Credit"),
            "fieldtype": "Currency",
            "width": 120
        }
    ]

def get_report_data(filters):
    """Get report data based on filters"""
    data = []
    parties = get_parties(filters)
    
    for party in parties:
        party_data = {
            "party": party,
            "opening_debit": 0,
            "opening_credit": 0,
            "movement_debit": 0,
            "movement_credit": 0,
            "total_debit": 0,
            "total_credit": 0
        }
        
        # Get opening balances (transactions before from_date)
        opening_balances = get_opening_balances(filters, party)
        
        # For both customers and suppliers, handle opening balance
        opening_balance = opening_balances.get("opening_balance", 0)
        if opening_balance > 0:
            party_data["opening_debit"] = abs(opening_balance)
        else:
            party_data["opening_credit"] = abs(opening_balance)
        
        # Get movement data (transactions between from_date and to_date)
        movements = get_movement_data(filters, party)
        
        # For customers: invoice total -> debit, payments -> credit
        # For suppliers: invoice total -> credit, payments -> debit
        is_customer = filters.get("party_type") == "Customer"
        
        # Handle invoice data
        if is_customer:
            party_data["movement_debit"] += movements.get("invoices_total", 0)
            party_data["movement_credit"] += movements.get("commission_total", 0)
        else:
            party_data["movement_credit"] += movements.get("invoices_total", 0)
            party_data["movement_debit"] += movements.get("commission_total", 0)
        
        # Handle payment data consistently for both party types
        # "Receive" payments always go to credit for customers, debit for suppliers
        # "Pay" payments always go to debit for customers, credit for suppliers
        if is_customer:
            party_data["movement_credit"] += movements.get("receive_payments", 0)
            party_data["movement_debit"] += movements.get("pay_payments", 0)
        else:
            party_data["movement_debit"] += movements.get("receive_payments", 0)
            party_data["movement_credit"] += movements.get("pay_payments", 0)
        
        # Calculate totals
        party_data["total_debit"] = party_data["opening_debit"] + party_data["movement_debit"]
        party_data["total_credit"] = party_data["opening_credit"] + party_data["movement_credit"]
        
        # Only add to report if there are non-zero transactions (if filter set)
        if not filters.get("ignore_zero_transactions") or \
           party_data["total_debit"] != 0 or party_data["total_credit"] != 0:
            data.append(party_data)
    
    return data

def get_parties(filters):
    """Get list of parties based on filters"""
    if filters.get("party"):
        return [filters.get("party")]
    
    _filters = {}
    if filters.get("party_type") == "Customer":
        _filters["is_customer"] = 1
        if filters.get("party_group"):
            _filters["customer_group"] = filters.get("party_group")
    else:
        if filters.get("party_group"):
            _filters["supplier_group"] = filters.get("party_group")
    
    return frappe.db.get_all(filters.get("party_type"), _filters, pluck="name")

def get_opening_balances(filters, party):
    """Get opening balance for a party"""
    result = {"opening_balance": 0}
    
    # Get GL entries before from_date
    gl_filters = {
        "party_type": filters.get("party_type"),
        "party": party,
        "company": filters.get("company"),
        "is_cancelled": 0
    }
    
    # Query for transactions before from_date or marked as opening entries
    if filters.get("from_date"):
        gl_query = """
            SELECT SUM(debit) as total_debit, SUM(credit) as total_credit
            FROM `tabGL Entry`
            WHERE party_type=%(party_type)s
            AND party=%(party)s
            AND company=%(company)s
            AND is_cancelled=%(is_cancelled)s
            AND (posting_date < %(from_date)s OR is_opening = 'Yes')
        """
        gl_filters["from_date"] = filters.get("from_date")
        gl_entries = frappe.db.sql(gl_query, gl_filters, as_dict=True)
    else:
        # If no from_date, just get opening entries
        gl_query = """
            SELECT SUM(debit) as total_debit, SUM(credit) as total_credit
            FROM `tabGL Entry`
            WHERE party_type=%(party_type)s
            AND party=%(party)s
            AND company=%(company)s
            AND is_cancelled=%(is_cancelled)s
            AND is_opening = 'Yes'
        """
        gl_entries = frappe.db.sql(gl_query, gl_filters, as_dict=True)
    
    if gl_entries and gl_entries[0]:
        debit = flt(gl_entries[0].total_debit, 2)
        credit = flt(gl_entries[0].total_credit, 2)
        result["opening_balance"] = debit - credit
    
    # Consider draft entries if requested
    if filters.get("consider_drafts") and filters.get("from_date"):
        # Draft invoices
        draft_invoices = get_draft_invoice_totals(filters, party)
        
        # Draft payments
        if filters.get("consider_draft_payments"):
            draft_payments = get_draft_payment_totals(filters, party)
            
            # For customer: payments received are credits, payments made are debits
            # For supplier: payments made are credits, payments received are debits
            if filters.get("party_type") == "Customer":
                result["opening_balance"] += draft_invoices - draft_payments
            else:
                # For suppliers, invoice totals are credits and commission is debit
                result["opening_balance"] += draft_payments - draft_invoices
    
    return result

def get_movement_data(filters, party):
    """Get movement data between from_date and to_date"""
    result = {
        "invoices_total": 0,
        "commission_total": 0,
        "receive_payments": 0,
        "pay_payments": 0
    }
    
    # Get invoice data
    invoice_data = get_invoice_data(filters, party)
    result["invoices_total"] = invoice_data.get("total", 0)
    result["commission_total"] = invoice_data.get("commission", 0)
    
    # Get payment data
    payment_data = get_payment_data(filters, party)
    result["receive_payments"] = payment_data.get("receive", 0)
    result["pay_payments"] = payment_data.get("pay", 0)
    
    return result

def get_invoice_data(filters, party):
    """Get invoice data for the period"""
    result = {"total": 0, "commission": 0}
    
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    
    query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name
    ).where(
        invform.company == filters.get('company')
    )
    
    # Apply date filters
    if filters.get("from_date"):
        query = query.where(invform.posting_date >= filters.get("from_date"))
    if filters.get("to_date"):
        query = query.where(invform.posting_date <= filters.get("to_date"))
    
    # Apply party filter
    if filters.get("party_type") == "Customer":
        query = query.where(invformitem.customer == party)
        query = query.select(Sum(invformitem.total).as_("total"))
    else:
        query = query.where(invform.supplier == party)
        query = query.select(
            Sum(invformitem.total).as_("total"),
            Sum(invform.total_commissions_and_taxes).as_("commission")
        )
    
    # Apply docstatus filter
    if filters.get("consider_drafts"):
        query = query.where(invform.docstatus.isin([0, 1]))
    else:
        query = query.where(invform.docstatus == 1)
    
    data = query.run(as_dict=True)
    
    if data and data[0]:
        result["total"] = flt(data[0].total, 2) or 0
        result["commission"] = flt(data[0].commission, 2) or 0 if "commission" in data[0] else 0
    
    return result

def get_payment_data(filters, party):
    """Get payment data for the period"""
    result = {"receive": 0, "pay": 0}
    
    entry = frappe.qb.DocType("Payment Entry")
    
    query = frappe.qb.from_(entry).where(
        entry.company == filters.get('company')
    ).where(
        entry.party == party
    )
    
    # Apply date filters
    if filters.get("from_date"):
        query = query.where(entry.posting_date >= filters.get("from_date"))
    if filters.get("to_date"):
        query = query.where(entry.posting_date <= filters.get("to_date"))
    
    # Apply docstatus filter
    if filters.get("consider_drafts"):
        query = query.where(entry.docstatus.isin([0, 1]))
    else:
        query = query.where(entry.docstatus == 1)
    
    # Get totals by payment type
    query = query.select(
        entry.payment_type,
        Sum(entry.paid_amount).as_("total")
    ).groupby(
        entry.payment_type
    )
    
    payment_data = query.run(as_dict=True)
    
    for row in payment_data:
        if row.payment_type == "Receive":
            result["receive"] = flt(row.total, 2)
        elif row.payment_type == "Pay":
            result["pay"] = flt(row.total, 2)
    
    # Include draft payments from receipts if requested
    if filters.get("consider_draft_payments") and filters.get("consider_drafts"):
        draft_payments = get_draft_receipts_data(filters, party)
        result["receive"] += draft_payments.get("receive", 0)
        result["pay"] += draft_payments.get("pay", 0)
    
    return result

def get_draft_invoice_totals(filters, party):
    """Get totals for draft invoices before from_date"""
    total = 0
    
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    
    query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name
    ).where(
        invform.company == filters.get('company')
    ).where(
        invform.docstatus == 0
    ).where(
        invform.posting_date < filters.get("from_date")
    )
    
    if filters.get("party_type") == "Customer":
        query = query.where(invformitem.customer == party)
        query = query.select(Sum(invformitem.total).as_("total"))
    else:
        query = query.where(invform.supplier == party)
        query = query.select(Sum(invformitem.total).as_("total"))
    
    result = query.run(as_dict=True)
    
    if result and result[0] and result[0].total:
        total = flt(result[0].total, 2)
    
    return total

def get_draft_payment_totals(filters, party):
    """Get totals for draft payments before from_date"""
    total = 0
    
    entry = frappe.qb.DocType("Payment Entry")
    
    query = frappe.qb.from_(entry).where(
        entry.company == filters.get('company')
    ).where(
        entry.party == party
    ).where(
        entry.docstatus == 0
    ).where(
        entry.posting_date < filters.get("from_date")
    )
    
    # Conditionally select paid amount based on party type and payment type
    if filters.get("party_type") == "Supplier":
        query = query.select(
            Case().when(entry.payment_type == "Pay", Sum(entry.paid_amount)).
            when(entry.payment_type == "Receive", (Sum(entry.paid_amount * -1))).
            else_(Sum(entry.paid_amount)).as_("paid_amount")
        )
    elif filters.get("party_type") == "Customer":
        query = query.select(
            Case().when(entry.payment_type == "Receive", Sum(entry.paid_amount)).
            when(entry.payment_type == "Pay", (Sum(entry.paid_amount * -1))).
            else_(Sum(entry.paid_amount)).as_("paid_amount")
        )
    
    result = query.run(as_dict=True)
    
    if result and result[0] and result[0].paid_amount:
        total = flt(result[0].paid_amount, 2)
    
    # Include draft payments from receipts if requested
    if filters.get("consider_draft_payments"):
        draft_payments = get_draft_receipts_total(filters, party)
        total += draft_payments
    
    return total

def get_draft_receipts_data(filters, party):
    """Get data for draft receipts within date range"""
    result = {"receive": 0, "pay": 0}
    
    parent = frappe.qb.DocType("Payments and Receipts")
    reference = frappe.qb.DocType("Payments Receipts Reference")
    
    query = (
        frappe.qb.from_(parent)
        .join(reference).on(reference.parent == parent.name)
        .where(parent.company == filters.get("company"))
        .where(parent.docstatus == 0)
        .where(reference.party == party)
    )
    
    # Apply date filters
    if filters.get("from_date"):
        query = query.where(parent.posting_date >= filters.get("from_date"))
    if filters.get("to_date"):
        query = query.where(parent.posting_date <= filters.get("to_date"))
    
    # Get all payments with their types
    query = query.select(
        parent.payment_type,
        reference.amount
    )
    
    payments = query.run(as_dict=True)
    
    # Process each payment according to its type
    for payment in payments:
        if payment.payment_type == "Receive":
            result["receive"] += flt(payment.amount, 2)
        elif payment.payment_type == "Pay":
            result["pay"] += flt(payment.amount, 2)
    
    return result

def get_draft_receipts_total(filters, party):
    """Get total for draft receipts before from_date"""
    total = 0
    
    parent = frappe.qb.DocType("Payments and Receipts")
    reference = frappe.qb.DocType("Payments Receipts Reference")
    
    query = (
        frappe.qb.from_(parent)
        .join(reference).on(reference.parent == parent.name)
        .where(parent.company == filters.get("company"))
        .where(parent.docstatus == 0)
        .where(parent.posting_date < filters.get("from_date"))
        .where(reference.party == party)
    )
    
    # Get all payments with their types
    query = query.select(
        parent.payment_type,
        reference.amount
    )
    
    payments = query.run(as_dict=True)
    
    # Process each payment according to its type and party type
    for payment in payments:
        amount = flt(payment.amount, 2)
        
        if filters.get("party_type") == "Customer":
            if payment.payment_type == "Receive":
                total += amount
            else:  # "Pay"
                total -= amount
        else:  # Supplier
            if payment.payment_type == "Pay":
                total += amount
            else:  # "Receive"
                total -= amount
    
    return total
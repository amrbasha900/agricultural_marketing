import json
import os
import random
import frappe
from frappe import _
from frappe.utils import getdate, flt, now
from frappe.utils.jinja_globals import is_rtl
from frappe.utils.pdf import get_pdf as _get_pdf
from frappe.query_builder.functions import Sum
from pypika import Case
from pypika.terms import Term

import time

# ================================
# PDF GENERATION SYSTEM (Reuses Statement Forms Backend)
# ================================

@frappe.whitelist()
def queue_pdf_generation(filters):
    """Queue PDF generation for suppliers with your original data logic"""
    if isinstance(filters, str):
        filters = json.loads(filters)
    
    # Force party_type to Supplier
    filters['party_type'] = 'Supplier'
    
    # First, get ALL supplier data to see which suppliers actually have data
    frappe.publish_realtime("pdf_generation_status", {"message": "Checking for suppliers with data..."})
    
    temp_data = frappe._dict()
    temp_data = get_data(temp_data, filters)  # Use YOUR data logic
    
    if not temp_data:
        return {"error": "No data matches the chosen criteria"}
    
    suppliers_with_data = list(temp_data.keys())
    
    frappe.publish_realtime("pdf_generation_status", {
        "message": f"Found {len(suppliers_with_data)} suppliers with data",
        "parties": suppliers_with_data[:5]
    })
    
    # Create Statement Generation History record
    history_doc = frappe.get_doc({
        "doctype": "Statement Generation History",
        "company": filters.get("company"),
        "party_type": "Supplier",  # Fixed to Supplier
        "party_group": filters.get("party_group"),
        "party": filters.get("party"),
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date"),
        "created_by_user": frappe.session.user,
        "generation_time": now(),
        "supplier_statement": 1,
        "consider_draft": filters.get("consider_draft", 0),
        "consider_draft_payments": filters.get("consider_draft_payments", 0),
        "neglect_items": filters.get("neglect_items", 0),
        "calculate_opening_balance_with_totals": filters.get("calculate_opening_balance_with_totals", 0),
        "total_parties": len(suppliers_with_data),
        "description": f"Supplier statement generation for {len(suppliers_with_data)} suppliers from {filters.get('from_date')} to {filters.get('to_date')}"
    })
    history_doc.insert(ignore_permissions=True)
    
    # Create PDF Generator Log entries for suppliers with data
    log_entries = []
    
    for supplier in suppliers_with_data:
        # Create new log entry
        log_entry = frappe.get_doc({
            "doctype": "PDF Generator Log",
            "party_type": "Supplier",
            "party_name": supplier,
            "party_group": filters.get("party_group"),
            "company": filters.get("company"),
            "from_date": filters.get("from_date"),
            "to_date": filters.get("to_date"),
            "status": "Queued",
            "created_by": frappe.session.user,
            "filters_json": json.dumps(filters),
            "statement_generation_history": history_doc.name
        })
        log_entry.insert(ignore_permissions=True)
        log_entries.append(log_entry.name)
        
        # Add to history child table
        history_doc.append("pdf_generator_logs", {
            "pdf_generator_log": log_entry.name,
            "party_name": supplier,
            "status": "Queued",
            "whatsapp_status": "Not Created"
        })
        
        # Queue background job for each supplier
        safe_supplier_name = frappe.scrub(supplier).replace("_", "-")[:30]
        frappe.enqueue(
            method=generate_single_supplier_pdf,
            log_id=log_entry.name,
            supplier_name=supplier,
            history_id=history_doc.name,
            job_name=f"SupplierPDF-{safe_supplier_name}",
            timeout=300,
            is_async=True
        )
    
    # Save history with child table entries
    history_doc.save(ignore_permissions=True)
    frappe.db.commit()
    
    result_message = f"Queued {len(log_entries)} supplier PDF generation jobs"
    
    frappe.publish_realtime("pdf_generation_status", {
        "message": "Supplier PDF generation jobs queued successfully",
        "queued": len(log_entries),
        "skipped": 0,
        "history_id": history_doc.name
    })
    
    return {
        "success": result_message, 
        "log_entries": log_entries,
        "parties_with_data": len(suppliers_with_data),
        "skipped_parties": 0,
        "history_id": history_doc.name
    }

@frappe.whitelist()
def get_pdf_generation_status(filters=None, history_id=None):
    """Get PDF generation status using statement forms backend"""
    return frappe.call(
        method='agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status',
        args={'filters': filters, 'history_id': history_id}
    )

# ================================
# LEGACY SINGLE PDF GENERATION (Your Original Logic)
# ================================

@frappe.whitelist()
def get_reports(filters):
    """Legacy function for direct PDF generation - keeps your original supplier logic"""
    data = frappe._dict()
    file_urls = []
    letter_head = None
    if isinstance(filters, str):
        filters = json.loads(filters)

    # Force party_type to Supplier
    filters['party_type'] = 'Supplier'

    default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
    if default_letter_head:
        letter_head = frappe.get_doc("Letter Head", default_letter_head)

    # Get Data using your supplier-specific logic
    data = get_data(data, filters)
    if not data:
        return {
            "error": "No data matches the chosen criteria"
        }
    html_format = get_html_format()

    for key, value in data.items():
        # Get summary table data
        party_summary = get_party_summary(filters=filters, party_type=filters.get("party_type"), party=key,
                                          party_data=value)

        header_details = get_header_data(filters.get("party_group"), key)
        font_size = frappe.db.get_single_value("Agriculture Settings", "font_size") or 14
        
        context = {
            "letter_head": letter_head,
            "header": header_details,
            "summary": party_summary,
            "items": value.get("items"),
            "buying_items": value.get("buying_items"),  # Add buying items for suppliers
            "payments": value.get("payments"),
            "filters": filters,
            "lang": frappe.local.lang,
            "layout_direction": "rtl" if is_rtl() else "ltr",
            "font_size": font_size
        }

        html = frappe.render_template(html_format, context)
        content = _get_pdf(html, {"orientation": "Portrait"})
        file_name = "{0}-{1}.pdf".format(key, str(random.randint(1000, 9999)))
        file_doc = frappe.new_doc("File")
        file_doc.update({
            "file_name": file_name,
            "is_private": 0,
            "content": content
        })
        file_doc.save(ignore_permissions=True)
        file_urls.append(file_doc.file_url)

    return {
        "file_urls": file_urls
    }

def generate_single_pdf(filters, party_name):
    """Generate single PDF for supplier using your specific logic"""
    try:
        data = frappe._dict()
        
        # Force party_type to Supplier
        filters['party_type'] = 'Supplier'
        
        # Get letter head
        default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
        letter_head = None
        if default_letter_head:
            letter_head = frappe.get_doc("Letter Head", default_letter_head)
        
        # Get data for single supplier using your logic
        data = get_data(data, filters)
        if not data or party_name not in data:
            return {"error": f"No data found for supplier: {party_name}"}
        
        html_format = get_html_format()
        value = data[party_name]
        
        # Get summary table data
        party_summary = get_party_summary(
            filters=filters, 
            party_type=filters.get("party_type"), 
            party=party_name,
            party_data=value
        )
        
        header_details = get_header_data(filters.get("party_group"), party_name)
        font_size = frappe.db.get_single_value("Agriculture Settings", "font_size") or 14
        
        context = {
            "letter_head": letter_head,
            "header": header_details,
            "summary": party_summary,
            "items": value.get("items"),
            "buying_items": value.get("buying_items"),  # Include buying items
            "payments": value.get("payments"),
            "filters": filters,
            "lang": frappe.local.lang,
            "layout_direction": "rtl" if is_rtl() else "ltr",
            "font_size": font_size
        }
        
        html = frappe.render_template(html_format, context)
        content = _get_pdf(html, {"orientation": "Portrait"})
        file_name = "{0}-{1}.pdf".format(party_name, str(random.randint(1000, 9999)))
        
        file_doc = frappe.new_doc("File")
        file_doc.update({
            "file_name": file_name,
            "is_private": 0,
            "content": content
        })
        file_doc.save(ignore_permissions=True)
        
        return {"success": True, "file_url": file_doc.file_url}
        
    except Exception as e:
        return {"error": str(e)}

# ================================
# YOUR ORIGINAL SUPPLIER DATA LOGIC (Keep as is)
# ================================

def get_data(data, filters):
    data = get_items_details(data, filters)
    data = get_payments_details(data, filters)
    return data

def get_html_format():
    template_filename = os.path.join("supplier_statement_forms" + '.html')
    folder = os.path.dirname(frappe.get_module("agricultural_marketing" + "." + "agricultural_marketing" +
                                               "." + "page").__file__)
    doctype_path = os.path.join(folder, "supplier_statement_forms")
    paths_temp = os.path.join(doctype_path, template_filename)
    html_format = frappe.utils.get_html_format(paths_temp)
    return html_format

def get_items_details(data, filters):
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    
    # Get selling data (original logic)
    items_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))

    # Determine and apply party filters based on party type
    _filters = {"is_customer": 1} if filters.get("party_type") == "Customer" else {}
    _field = invformitem.customer if filters.get("party_type") == "Customer" else invform.supplier
    parties = get_parties(filters, _filters)
    items_query = items_query.where(_field.isin(parties))

    # validate and apply dates filters
    items_query = validate_and_apply_date_filters(filters, items_query, invform)

    # Filter for submitted (docstatus 1) invoice forms
    if filters.get("consider_draft"):
        items_query = items_query.where(invform.docstatus.isin([0, 1]))
    else:
        items_query = items_query.where(invform.docstatus == 1)

    # Select relative fields based on party type
    items_query = select_fields_for_invoices(filters, items_query, _field, invform, invformitem)

    result = items_query.orderby(invform.posting_date).orderby(invform.name).orderby(
        invformitem.item_name).run(as_dict=True)

    if result:
        # Construct result, appending to final data and appending totals
        process_result_and_totals_for_invoices(result, data, filters)

    # FOR SUPPLIERS: Also get buying data (where supplier acts as customer)
    if filters.get("party_type") == "Supplier":
        get_buying_items_details(data, filters)

    return data

def get_buying_items_details(data, filters):
    """Get buying details where suppliers act as customers (buying from OTHER suppliers)"""
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    
    # Get parties (suppliers from filter)
    parties = get_parties(filters, {})
    
    # Query where filter suppliers are customers BUT invoice supplier is DIFFERENT
    buying_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))
    
    # Filter suppliers appear as customers in invoice items
    buying_query = buying_query.where(invformitem.customer.isin(parties))
    
    # IMPORTANT: Invoice form supplier should NOT be the same as filter supplier
    buying_query = buying_query.where(invform.supplier.notin(parties))
    
    # Apply same date and status filters
    buying_query = validate_and_apply_date_filters(filters, buying_query, invform)
    
    if filters.get("consider_draft"):
        buying_query = buying_query.where(invform.docstatus.isin([0, 1]))
    else:
        buying_query = buying_query.where(invform.docstatus == 1)
    
    # Select fields for buying (customer field as party)
    buying_query = select_fields_for_buying(filters, buying_query, invformitem.customer, invform, invformitem)
    
    buying_result = buying_query.orderby(invform.posting_date).orderby(invform.name).orderby(
        invformitem.item_name).run(as_dict=True)
    
    if buying_result:
        process_buying_result_and_totals(buying_result, data, filters)

def select_fields_for_buying(filters, buying_query, _field, invform, invformitem):
    """Select fields for buying transactions"""
    if filters.get("neglect_items"):
        buying_query = buying_query.select(_field.as_("party"), invform.name.as_("invoice_id"),
                                         invform.posting_date.as_("date"),
                                         invformitem.total)
    else:
        buying_query = buying_query.select(_field.as_("party"), invform.name.as_("invoice_id"),
                                         invform.posting_date.as_("date"), invformitem.qty, invformitem.price,
                                         invformitem.total, invformitem.item_name)
    return buying_query

def process_buying_result_and_totals(result, data, filters):
    """Process buying results and add to data structure as buying_items"""
    def calculate_buying_totals(items):
        total_qty = sum([it.get('qty') for it in items if it.get('qty')])
        total_before_tax = sum([it.get('total') for it in items if it.get('total')])
        return total_qty, total_before_tax

    invoices = set()
    for row in result:
        party = row.pop("party")
        invoice_id = row.get("invoice_id")
        data.setdefault(party, {})
        
        if invoice_id in invoices and filters.get("neglect_items"):
            # Find existing buying item and update total
            buying_items = data[party].get("buying_items", [])
            for d in buying_items:
                if d["invoice_id"] == invoice_id:
                    d["total"] += row["total"]
                    break
        else:
            data.setdefault(party, {}).setdefault("buying_items", []).append(row)
            invoices.add(row["invoice_id"])

    # Calculate and append totals for buying items
    for party_data in data.values():
        buying_items = party_data.get("buying_items", [])
        if buying_items:
            total_qty, total_before_tax = calculate_buying_totals(buying_items)
            
            # Append buying totals
            buying_items.append({
                "date": _("Total"),
                "qty": "",
                "total": total_before_tax
            })

def get_payments_details(data, filters):
    entry = frappe.qb.DocType("Payment Entry")
    payments_query = frappe.qb.from_(entry).where(entry.company == filters.get('company'))

    # Determine and apply party filters based on party type
    _filters = {"is_customer": 1} if filters.get("party_type") == "Customer" else {}
    parties = get_parties(filters, _filters)
    payments_query = payments_query.where(entry.party.isin(parties))

    # Validate and apply dates filters
    payments_query = validate_and_apply_date_filters(filters, payments_query, entry)

    # Filter for submitted (docstatus 1) payment entries
    if filters.get("consider_draft"):
        payments_query = payments_query.where(entry.docstatus.isin([0, 1]))
    else:
        payments_query = payments_query.where(entry.docstatus == 1)

    # Select relative fields base on party type
    payments_query = select_fields_for_payment(filters, payments_query, entry)

    result = payments_query.run(as_dict=True)

    if filters.get("consider_draft_payments"):
        drafts_result = get_payments_details_drafts(filters)
        result += drafts_result

    if result:
        # Construct result, appending to final data nad appending totals
        process_result_and_totals_for_payments(result, data)

    return data

def get_tax_rate():
    default_tax_template = frappe.db.get_single_value("Agriculture Settings", "default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    tax_rate = frappe.db.get_value("Sales Taxes and Charges",
                                   {"parent": default_tax_template}, "rate") or 0
    return tax_rate

def get_header_data(party_group, party):
    return {
        "party": party,
        "party_group": party_group
    }

def get_party_summary(filters, party_type, party, party_data):
    def update_balance(balance, debit, credit):
        """Helper function to calculate and update the balance."""
        return balance + flt(debit) - flt(credit)

    def get_total_sales_and_commissions(data):
        if data.get("items"):
            return data["items"][-1].get("total", 0), data["items"][-1].get("commission", 0)
        return 0, 0

    def get_total_payments(data):
        if data.get("payments"):
            return data.get("payments")[-1].get('paid_amount', 0)
        return 0

    def get_total_buying(data):
        if data.get("buying_items"):
            return data["buying_items"][-1].get("total", 0)  # Get total from last row
        return 0

    def append_summary(statement, debit, credit):
        nonlocal last_balance
        if switch_columns:
            debit, credit = credit, debit

        last_balance = update_balance(last_balance, debit, credit)
        party_summary.append({
            "statement": statement,
            "debit": flt(debit, 2) or str(debit),
            "credit": flt(credit, 2) or str(credit),
            "balance": flt(last_balance, 2) or str(last_balance)
        })

    switch_columns = True if party_type == "Customer" else False
    party_summary = []
    debit, credit, last_balance = 0, 0, 0
    from_date = filters.get('from_date')

    gl_filters = {
        "party_type": filters.get("party_type"),
        "party": party,
        "from_date": from_date
    }

    q = """ 
            SELECT 
                name, debit, credit, posting_date
            FROM 
                `tabGL Entry`
            WHERE 
                party_type=%(party_type)s 
            AND 
                party=%(party)s 
            AND 
                is_cancelled = 0
            AND 
            (posting_date < %(from_date)s OR is_opening = 'Yes')
        """

    gl_entries = frappe.db.sql(q, gl_filters, as_dict=True)

    for gl in gl_entries:
        debit += gl.debit
        credit += gl.credit

    # GET total items and payments before from date
    if filters.get("consider_draft"):
        total_items = get_draft_total_items(filters, party) or 0
        total_payments = get_draft_total_payments(filters, party) or 0
        if filters.get("party_type") == "Supplier":
            total_draft_commission = get_draft_total_commission(filters, party) or 0
            debit += total_payments + total_draft_commission
            credit += total_items
        else:
            debit += total_items
            credit += total_payments

    # Calculate totals
    total_sales, total_commission_with_taxes = get_total_sales_and_commissions(party_data)
    total_buying = get_total_buying(party_data) if filters.get("party_type") == "Supplier" else 0
    total_payments = get_total_payments(party_data)
    last_balance = debit - credit
    
    if not filters.get("calculate_opening_balance_with_totals", False):
        if abs(debit) > abs(credit):
            debit = abs(last_balance)
            credit = 0
        else:
            credit = abs(last_balance)
            debit = 0

    # Append Opening
    party_summary.append({
        "statement": _("Opening Balance"),
        "debit": flt(debit, 2) or "0",
        "credit": flt(credit, 2) or "0",
        "balance": flt(last_balance, 2) or "0"
    })

    # Append Summaries
    append_summary(_("Duration Selling"), 0, flt(total_sales, 2))

    # Add buying summary for suppliers
    if filters.get("party_type") == "Supplier" and total_buying > 0:
        append_summary(_("Duration Buying"), flt(total_buying, 2), 0)

    if filters.get("party_type") == "Supplier":
        append_summary(_("Commission") + " + " + _("VAT"), flt(total_commission_with_taxes, 2), 0)
    append_summary(_("Duration Payments"), flt(total_payments, 2), 0)

    # Calculate and append closing
    total_debit = total_commission_with_taxes + total_payments
    if filters.get("party_type") == "Supplier":
        total_debit += total_buying
    total_credit = total_sales
    if switch_columns:
        total_debit, total_credit = total_credit, total_debit

    total_debit += debit
    total_credit += credit

    party_summary.append({
        "statement": _("Total"),
        "debit": flt(total_debit, 2) or "0",
        "credit": flt(total_credit, 2) or "0",
        "balance": flt(total_debit - total_credit, 2) or "0"
    })

    return party_summary

def get_parties(filters, _filters):
    if filters.get("party"):
        parties = [filters.get("party")]
    elif filters.get("party_group"):
        party_group = "customer_group" if filters.get('party_type') == "Customer" else "supplier_group"
        _filters[party_group] = filters.get('party_group')
        parties = frappe.db.get_all(filters.get("party_type"), _filters, pluck="name")
    else:
        parties = frappe.db.get_all(filters.get("party_type"), _filters, pluck="name")

    return parties

def validate_and_apply_date_filters(filters, query, doctype):
    if filters.get("from_date") and filters.get("to_date") and (filters.get("to_date") < filters.get("from_date")):
        frappe.throw(_("To date must be after from date"))

    if filters.get("from_date"):
        query = query.where(doctype.posting_date.gte(filters.get("from_date")))

    if filters.get("to_date"):
        query = query.where(doctype.posting_date.lte(filters.get("to_date")))

    return query

def select_fields_for_invoices(filters, items_query, _field, invform, invformitem):
    if filters.get("neglect_items"):
        items_query = items_query.select(_field.as_("party"), invform.name.as_("invoice_id"),
                                         invform.posting_date.as_("date"),
                                         invformitem.total)
    else:
        items_query = items_query.select(_field.as_("party"), invform.name.as_("invoice_id"),
                                         invform.posting_date.as_("date"), invformitem.qty, invformitem.price,
                                         invformitem.total, invformitem.item_name)

    if filters.get("party_type") == "Supplier":
        items_query = items_query.select(invformitem.commission)

    return items_query

def process_result_and_totals_for_invoices(result, data, filters):
    def calculate_totals(items):
        """Calculate the total quantities, before tax, commission, and taxes."""
        total_qty = sum([it.get('qty') for it in items if it.get('qty')])
        total_before_tax = sum([it.get('total') for it in items if it.get('total')])
        total_commission = sum([it.get('commission') for it in items]) if filters.get("party_type") == "Supplier" else 0
        total_taxes = (total_commission * get_tax_rate()) / 100 if total_commission else 0
        total_commission_with_taxes = total_commission + total_taxes
        return total_qty, total_before_tax, total_commission, total_taxes, total_commission_with_taxes

    invoices = set()
    for row in result:
        party = row.pop("party")  # Extract and remove party from the row
        invoice_id = row.get("invoice_id")
        data.setdefault(party, {"items": []})
        if invoice_id in invoices and filters.get("neglect_items"):
            for d in data[party]["items"]:
                if d["invoice_id"] == invoice_id:
                    d["total"] += row["total"]
                    if filters.get("party_type") == "Supplier":
                        d["commission"] += row["commission"]
                    break
        else:
            data.setdefault(party, {}).setdefault("items", []).append(row)
            invoices.add(row["invoice_id"])

    for party_data in data.values():
        items = party_data.get("items", [])
        if items:
            total_qty, total_before_tax, total_commission, total_taxes, total_commission_with_taxes = calculate_totals(
                items)

            # Append totals
            items.append({
                "date": _("Total"),
                "qty": "",
                "total": total_before_tax,
                "commission": total_commission_with_taxes
            })

def select_fields_for_payment(filters, payments_query, entry):
    payments_query = payments_query.select(entry.party_type, entry.party, entry.name.as_("payment_id"),
                                           entry.posting_date.as_("date"), entry.mode_of_payment.as_("mop"),
                                           entry.payment_type, entry.remarks)

    # Conditionally select paid amount based on party type and payment type
    if filters.get("party_type") == "Supplier":
        payments_query = payments_query.select(
            Case().when(entry.payment_type == "Pay", entry.paid_amount).
            when(entry.payment_type == "Receive", (entry.paid_amount * -1)).
            else_(entry.paid_amount).as_("paid_amount")
        )
    elif filters.get("party_type") == "Customer":
        payments_query = payments_query.select(
            Case().when(entry.payment_type == "Receive", entry.paid_amount).
            when(entry.payment_type == "Pay", (entry.paid_amount * -1)).
            else_(entry.paid_amount).as_("paid_amount")
        )

    return payments_query

def process_result_and_totals_for_payments(result, data):
    def append_to_date(party, row):
        """Append payment row to the desired party in data"""
        if party not in data:
            data[party] = {"payments": []}
        data.setdefault(party, {}).setdefault("payments", []).append(row)

    def calculate_grand_total(payments):
        """Calculate the total paid amount"""
        total_amount = sum(p.get('paid_amount', 0) for p in payments)
        payments.append({
            "date": _("Total"),
            "paid_amount": total_amount
        })

    for row in result:
        party = row.pop("party", None)
        if party:
            append_to_date(party, row)

    # Calculate and append grand total for each party
    for party_data in data.values():
        payments = party_data.get("payments", [])
        if payments:
            calculate_grand_total(payments)

def get_draft_total_items(filters, party):
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    items_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))

    # Determine and apply party filters based on party type
    _field = invformitem.customer if filters.get("party_type") == "Customer" else invform.supplier
    items_query = items_query.where(_field == party)

    items_query = items_query.where(invform.docstatus == 0).where(invform.posting_date.lt(filters.get("from_date")))

    # Select relative fields based on party type
    result = items_query.select(Sum(invformitem.total).as_("total")).run(as_dict=True)

    total_items = sum([re["total"] for re in result if re["total"]]) or 0

    return total_items

def get_draft_total_commission(filters, party):
    invform = frappe.qb.DocType("Invoice Form")
    result = frappe.qb.from_(invform).where(invform.company == filters.get('company')).where(
        invform.supplier == party).where(invform.docstatus == 0).where(
        invform.posting_date.lt(filters.get("from_date"))).select(
        Sum(invform.total_commissions_and_taxes).as_("commission")).run(
        as_dict=True)

    total_commission = sum([re["commission"] for re in result if re["commission"]]) or 0

    return total_commission

def get_draft_total_payments(filters, party):
    entry = frappe.qb.DocType("Payment Entry")
    payments_query = frappe.qb.from_(entry).where(
        entry.company == filters.get('company')).where(
        entry.party == party).where(
        entry.posting_date.lt(filters.get("from_date"))).where(
        entry.docstatus == 0
    )

    payments_query = payments_query.select(entry.payment_type)

    # Conditionally select paid amount based on party type and payment type
    if filters.get("party_type") == "Supplier":
        payments_query = payments_query.select(
            Case().when(entry.payment_type == "Pay", Sum(entry.paid_amount)).
            when(entry.payment_type == "Receive", (Sum(entry.paid_amount * -1))).
            else_(Sum(entry.paid_amount)).as_("paid_amount")
        )
    elif filters.get("party_type") == "Customer":
        payments_query = payments_query.select(
            Case().when(entry.payment_type == "Receive", Sum(entry.paid_amount)).
            when(entry.payment_type == "Pay", (Sum(entry.paid_amount * -1))).
            else_(Sum(entry.paid_amount)).as_("paid_amount")
        )

    result = payments_query.run(as_dict=True)
    
    total_paid_amount = sum([re["paid_amount"] for re in result if re["paid_amount"]]) or 0
    
    ## for drafts of payments from receipts totals 
    if filters.get("consider_draft_payments"):
        total_draft_payments = get_draft_total_payments_from_receipts(filters, party)
        total_paid_amount += total_draft_payments

    return total_paid_amount

# ================================
# WHATSAPP FUNCTIONS (Reuse from Statement Forms)
# ================================

@frappe.whitelist()
def send_whatsapp_msg(filters):
    """Send WhatsApp message using your original logic but with supplier focus"""
    data = frappe._dict()
    file_urls = []
    whatsapp_messages = []
    letter_head = None
    if isinstance(filters, str):
        filters = json.loads(filters)

    # Force party_type to Supplier
    filters['party_type'] = 'Supplier'

    default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
    if default_letter_head:
        letter_head = frappe.get_doc("Letter Head", default_letter_head)

    # Get Data using supplier logic
    data = get_data(data, filters)
    if not data:
        return {
            "error": "No data matches the chosen criteria"
        }
    html_format = get_html_format()

    for key, value in data.items():
        # Get summary table data
        party_summary = get_party_summary(filters=filters, party_type=filters.get("party_type"), party=key,
                                          party_data=value)

        header_details = get_header_data(filters.get("party_group"), key)
        font_size = frappe.db.get_single_value("Agriculture Settings", "font_size") or 14

        context = {
            "letter_head": letter_head,
            "header": header_details,
            "summary": party_summary,
            "items": value.get("items"),
            "buying_items": value.get("buying_items"),  # Include buying items
            "payments": value.get("payments"),
            "filters": filters,
            "lang": frappe.local.lang,
            "layout_direction": "rtl" if is_rtl() else "ltr",
            "font_size": font_size
        }

        html = frappe.render_template(html_format, context)
        content = _get_pdf(html, {"orientation": "Portrait"})
        file_name = "{0}-{1}.pdf".format(key, str(random.randint(1000, 9999)))
        file_doc = frappe.new_doc("File")
        file_doc.update({
            "file_name": file_name,
            "is_private": 0,
            "content": content
        })
        file_doc.save(ignore_permissions=True)
        file_urls.append(file_doc.file_url)
        whatsapp_messages.append(create_whatsapp_messages(
            party_type=filters.get("party_type"),
            party_name=key,
            pdf_url=file_doc.file_url,
            reference_document='Page',
            document_name='supplier-statement-forms',
        ))

    frappe.db.commit()
    
    return {"success": f"WhatsApp message logged"}

@frappe.whitelist()
def create_whatsapp_messages(party_type=None, party_name=None, pdf_url=None, reference_document=None, document_name=None):
    """Create WhatsApp message entry (same as statement forms)"""
    if not (party_type and party_name and pdf_url):
        return {"error": "Missing required parameters."}

    try:
        # Fetch the WhatsApp Number from Supplier
        whatsapp_number = frappe.get_value(party_type, party_name, "whatsapp_number")
        whatsapp_message = frappe.get_value(party_type, party_name, "default_whatsapp_message")

        if not whatsapp_number:
            return {"error": f"WhatsApp number not found for {party_name}"}

        # Create a new WhatsApp Messages entry
        whatsapp_messages = frappe.get_doc({
            "doctype": "WhatsApp Messages",
            "party_type": party_type,
            "party_name": party_name,
            "phone_number": whatsapp_number,
            "has_media": 1,
            "message": whatsapp_message or "Please find your supplier statement attached.",
            "status": "Queued",
            "attach": pdf_url,
            "auto_send": 1,
            "reference_document": reference_document,
            "document_name": document_name
        })
        whatsapp_messages.insert(ignore_permissions=True)
        frappe.db.commit()
        time.sleep(3)
        return whatsapp_messages.name or None

    except Exception as e:
        frappe.log_error(message=f"Error in send_whatsapp_msg: {str(e)}", title="WhatsApp Messaging")
        return {"error": str(e)}

@frappe.whitelist()
def task_msg_creation(filters):
    """Queue WhatsApp message creation"""
    frappe.enqueue(method=send_whatsapp_msg, filters=filters, job_name="create pdf and whatsapp for Supplier Statement Forms")
    lock_invoice_update()
    return {"success": f"WhatsApp message logged"}

@frappe.whitelist()
def lock_invoice_update():
    """Lock invoice updates (same as statement forms)"""
    # Count invoices where lock_update is 0 or NULL
    count = frappe.db.sql(
        """
        SELECT COUNT(*)
        FROM `tabInvoice Form`
        WHERE IFNULL(lock_update, 0) = 0
        """
    )[0][0]

    if count:
        # Bulk update to set lock_update = 1 for all matching rows
        frappe.db.sql(
            """
            UPDATE `tabInvoice Form`
            SET lock_update = 1
            WHERE IFNULL(lock_update, 0) = 0
            """
        )
        frappe.msgprint(_(f"{count} Invoice Form locked for update"))
    else:
        frappe.msgprint(_("No Invoice Form to lock for update"))

    frappe.db.commit()
    return {"success": "Invoice Form locked for update"}

# ================================
# DRAFTS HANDLING (Keep Your Original Logic)
# ================================

def get_payments_details_drafts(filters):
    entry = frappe.qb.DocType("Payments Receipts Reference")
    parent = frappe.qb.DocType("Payments and Receipts")

    payments_query = (
        frappe.qb.from_(entry)
        .join(parent)
        .on(entry.parent == parent.name)
        .where(parent.company == filters.get('company'))
        .where(parent.docstatus == 0) 
    )

    _filters = {"is_customer": 1} if filters.get("party_type") == "Customer" else {}
    parties = get_parties(filters, _filters)
    payments_query = payments_query.where(entry.party.isin(parties))

    payments_query = validate_and_apply_date_filters_drafts(filters, payments_query, parent)

    payments_query = select_fields_for_payment_drafts(filters, payments_query, entry, parent)

    result = payments_query.run(as_dict=True)

    return result

def validate_and_apply_date_filters_drafts(filters, query, parent):
    if filters.get("from_date") and filters.get("to_date") and (filters.get("to_date") < filters.get("from_date")):
        frappe.throw(_("To date must be after from date"))

    if filters.get("from_date"):
        query = query.where(parent.posting_date.gte(filters.get("from_date")))

    if filters.get("to_date"):
        query = query.where(parent.posting_date.lte(filters.get("to_date")))

    return query

def select_fields_for_payment_drafts(filters, payments_query, entry, parent):
    payments_query = payments_query.select(
        Term.wrap_constant("Payments Receipts Reference").as_("doctype"),
        entry.party_type,
        entry.party,
        entry.name.as_("payment_id"),
        parent.posting_date.as_("date"),
        entry.mode_of_payment.as_("mop"),
        parent.payment_type,
        entry.description.as_("remarks")
    )

    if filters.get("party_type") == "Supplier":
        payments_query = payments_query.select(
            Case()
            .when(parent.payment_type == "Pay", entry.amount)
            .when(parent.payment_type == "Receive", (entry.amount * -1))
            .else_(entry.amount)
            .as_("paid_amount")
        )
    elif filters.get("party_type") == "Customer":
        payments_query = payments_query.select(
            Case()
            .when(parent.payment_type == "Receive", entry.amount)
            .when(parent.payment_type == "Pay", (entry.amount * -1))
            .else_(entry.amount)
            .as_("paid_amount")
        )

    return payments_query

def get_draft_total_payments_from_receipts(filters, party):
    parent = frappe.qb.DocType("Payments and Receipts")
    reference = frappe.qb.DocType("Payments Receipts Reference")

    # First, get all relevant records individually without aggregation
    query = (
        frappe.qb.from_(parent)
        .join(reference).on(reference.parent == parent.name)
        .where(parent.company == filters.get("company"))
        .where(parent.posting_date < filters.get("from_date"))
        .where(parent.docstatus == 0)
        .where(reference.party == party)
        .select(
            parent.payment_type,
            reference.amount
        )
    )
    
    results = query.run(as_dict=True)
    
    # Process each record individually with the correct payment type logic
    total_amount = 0
    for record in results:
        amount = record.amount
        
        # Apply logic based on party type and payment type
        if filters.get("party_type") == "Customer":
            if record.payment_type == "Receive":
                total_amount += amount
            else:  # "Pay"
                total_amount -= amount
        else:  # Supplier
            if record.payment_type == "Pay":
                total_amount += amount
            else:  # "Receive"
                total_amount -= amount
    return total_amount

# ================================
# WRAPPER FUNCTIONS FOR STATEMENT FORMS INTEGRATION
# ================================

# These functions allow the statement forms backend to work with supplier-specific PDF generation
# They are called by the statement forms queue system when generating supplier PDFs

def generate_supplier_pdf_for_statement_forms(filters, party_name):
    """Wrapper function for statement forms integration"""
    return generate_single_pdf(filters, party_name)

def generate_single_supplier_pdf(log_id, supplier_name=None, history_id=None):
    """Generate PDF for a single supplier using YOUR existing logic"""
    log_doc = None
    actual_supplier_name = supplier_name
    
    try:
        # Get the log document
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
        except frappe.DoesNotExistError:
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message="PDF log not found")
            return
        
        # Update status to processing
        log_doc.status = "Processing"
        log_doc.save(ignore_permissions=True)
        
        if history_id:
            update_history_item_status_safe(history_id, log_id, "Processing")
        
        frappe.db.commit()
        
        # Use supplier name from log if not provided
        if not actual_supplier_name:
            actual_supplier_name = log_doc.party_name
        
        # Reconstruct filters for single supplier
        try:
            filters = json.loads(log_doc.filters_json)
            filters["party"] = actual_supplier_name
            filters["party_type"] = "Supplier"  # Ensure it's Supplier
        except (json.JSONDecodeError, ValueError) as json_error:
            log_doc.status = "Failed"
            log_doc.error_message = f"Invalid filters JSON: {str(json_error)}"
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=log_doc.error_message)
            frappe.db.commit()
            return
        
        # Generate PDF using YOUR existing generate_single_pdf logic
        pdf_result = generate_single_pdf(filters, actual_supplier_name)
        
        if pdf_result.get("success"):
            log_doc.status = "Completed"
            log_doc.pdf_file = pdf_result["file_url"]
            log_doc.completion_time = now()
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Completed", pdf_file=pdf_result["file_url"])
        else:
            log_doc.status = "Failed"
            log_doc.error_message = pdf_result.get("error", "Unknown error during PDF generation")
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=log_doc.error_message)
            
    except Exception as e:
        error_msg = str(e)[:200]
        frappe.log_error(message=f"Supplier PDF Generation failed for {log_id}: {error_msg}", title="PDF Generation")
        
        if log_doc:
            try:
                log_doc.status = "Failed"
                log_doc.error_message = error_msg
                if history_id:
                    update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            except Exception as update_error:
                frappe.log_error(message=f"Failed to update error status for {log_id}: {str(update_error)[:200]}", title="PDF Generation")
                return
    
    # Save the final status
    if log_doc:
        try:
            log_doc.save(ignore_permissions=True)
            frappe.db.commit()
            
            # Update history summary counts
            if history_id:
                update_history_summary_counts_safe(history_id)
                
        except Exception as save_error:
            frappe.log_error(message=f"Failed to save final status for {log_id}: {str(save_error)[:200]}", title="PDF Generation")

def update_history_item_status_safe(history_id, log_id, status, pdf_file=None, error_message=None, whatsapp_status=None, whatsapp_message_id=None):
    """Update a specific child row via direct DB to avoid parent save conflicts"""
    max_retries = 3
    retry_count = 0

    # Prepare update fields
    update_fields = {"status": status}
    if pdf_file:
        update_fields["pdf_file"] = pdf_file
    if error_message:
        update_fields["error_message"] = error_message[:200] if len(error_message) > 200 else error_message
    if whatsapp_status:
        update_fields["whatsapp_status"] = whatsapp_status
    if whatsapp_message_id:
        update_fields["whatsapp_message_id"] = whatsapp_message_id

    while retry_count < max_retries:
        try:
            # Find the child row name
            item_name = frappe.db.get_value(
                "Statement Generation History Item",
                {"parent": history_id, "pdf_generator_log": log_id},
                "name",
            )
            
            if item_name:
                # Direct update on child row
                frappe.db.set_value("Statement Generation History Item", item_name, update_fields)
                frappe.db.commit()
                return
            else:
                # Item not found, skip silently (may have been deleted)
                return
                
        except Exception as e:
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(0.5)
                continue
            # Log error but don't raise to avoid breaking the queue
            frappe.log_error(message=f"Failed to update history item status after {retry_count} retries: {str(e)}", title="Statement Generation History")
            break

def update_history_summary_counts_safe(history_id):
    """Recompute summary counts using SQL and update parent"""
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            completed_count = frappe.db.count(
                "Statement Generation History Item",
                filters={"parent": history_id, "status": "Completed"},
            )
            failed_count = frappe.db.count(
                "Statement Generation History Item",
                filters={"parent": history_id, "status": "Failed"},
            )
            # whatsapp_sent_count: any status not 'Not Created'
            whatsapp_sent_count = frappe.db.sql(
                """
                select count(1) as cnt
                from `tabStatement Generation History Item`
                where parent=%s and ifnull(whatsapp_status, 'Not Created') <> 'Not Created'
                """,
                (history_id,),
                as_dict=True,
            )[0].cnt

            frappe.db.set_value(
                "Statement Generation History",
                history_id,
                {
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                    "whatsapp_sent_count": whatsapp_sent_count,
                },
            )
            frappe.db.commit()
            return
            
        except Exception as e:
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(0.5)
                continue
            frappe.log_error(message=f"Failed to update history summary counts after {retry_count} retries: {str(e)}", title="Statement Generation History")
            break

@frappe.whitelist()
def get_statement_generation_history(from_date=None, to_date=None, party_name=None, company=None):
    """Get Statement Generation History records with filters"""
    conditions = {}
    
    if from_date and to_date:
        conditions["from_date"] = ["between", [from_date, to_date]]
    elif from_date:
        conditions["from_date"] = [">=", from_date]
    elif to_date:
        conditions["to_date"] = ["<=", to_date]
    
    if party_name:
        # Search in child table for party name
        history_names = frappe.db.sql("""
            SELECT DISTINCT parent 
            FROM `tabStatement Generation History Item` 
            WHERE party_name LIKE %s
        """, f"%{party_name}%", as_dict=True)
        
        if history_names:
            history_ids = [h.parent for h in history_names]
            conditions["name"] = ["in", history_ids]
        else:
            return []  # No matching party names found
    
    if company:
        conditions["company"] = company
    conditions["supplier_statement"] = 1
    histories = frappe.get_all(
        "Statement Generation History",
        filters=conditions,
        fields=[
            "name", "company", "party_type", "party_group", "party", 
            "from_date", "to_date", "created_by_user", "generation_time",
            "total_parties", "completed_count", "failed_count", "whatsapp_sent_count",
            "description"
        ],
        order_by="generation_time desc",
        limit=100
    )
    
    return histories

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
    queued_jobs = []
    #suppliers_with_data.append('0010891')
    for supplier in suppliers_with_data:
        filters_for_log = dict(filters)
        filters_for_log["party"] = supplier
        filters_for_log["party_type"] = "Supplier"
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
            "filters_json": json.dumps(filters_for_log),
            "statement_generation_history": history_doc.name
        })
        log_entry.insert(ignore_permissions=True)
        log_entries.append(log_entry.name)
        queued_jobs.append((supplier, log_entry.name))

        # Add to history child table
        history_doc.append("pdf_generator_logs", {
            "pdf_generator_log": log_entry.name,
            "party_name": supplier,
            "status": "Queued",
            "whatsapp_status": "Not Created"
        })

    # Save history with child table entries
    history_doc.save(ignore_permissions=True)
    frappe.db.commit()

    # Enqueue only after the commit: a worker reads on its own connection and
    # cannot see uncommitted log rows, so enqueueing inside the loop above let
    # idle workers pop the first few jobs, hit DoesNotExistError and leave those
    # suppliers stuck on "Queued" forever.
    for supplier, log_name in queued_jobs:
        frappe.enqueue(
            method=generate_single_supplier_pdf,
            log_id=log_name,
            supplier_name=supplier,
            history_id=history_doc.name,
            job_id=f"pdf-gen-{log_name}",
            deduplicate=True,
            timeout=300,
            is_async=True
        )
    
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
    """Get PDF generation status, scoped to supplier histories."""
    if history_id:
        return frappe.call(
            "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status",
            filters=filters,
            history_id=history_id,
        )

    if isinstance(filters, str):
        filters = json.loads(filters)
    filters = filters or {}

    conditions = {
        "supplier_statement": 1,
        "party_type": "Supplier",
    }
    for key in ("company", "party_group", "party", "from_date", "to_date"):
        if filters.get(key):
            conditions[key] = filters.get(key)

    history = frappe.get_all(
        "Statement Generation History",
        filters=conditions,
        fields=["name"],
        order_by="generation_time desc",
        limit=1,
    )
    if not history:
        return []

    return frappe.call(
        "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status",
        history_id=history[0].name,
    )


@frappe.whitelist()
def cancel_whatsapp_job(job_id=None, job_name=None):
    """Cancel WhatsApp queue job using statement forms backend"""
    return frappe.call(
        method='agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.cancel_whatsapp_job',
        args={'job_id': job_id, 'job_name': job_name}
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
        frappe.errprint("party_summary: " + str(party_summary))
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
    buying_query = buying_query.where(invform.supplier != invformitem.customer)    
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

def get_buying_total_before_from_date(filters, party):
    """Fetch total buying amount for a supplier before the selected from_date."""
    if not filters.get("from_date") or filters.get("party_type") != "Supplier":
        return 0

    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")

    buying_query = (
        frappe.qb.from_(invform)
        .left_join(invformitem)
        .on(invformitem.parent == invform.name)
        .where(invform.company == filters.get("company"))
        .where(invformitem.customer == party)
        .where(invform.supplier != invformitem.customer)
        .where(invform.posting_date.lt(filters.get("from_date")))
    )

    if filters.get("consider_draft"):
        buying_query = buying_query.where(invform.docstatus.isin([0, 1]))
    else:
        buying_query = buying_query.where(invform.docstatus == 1)

    result = buying_query.select(Sum(invformitem.total).as_("total")).run(as_dict=True)
    return sum([row.get("total") for row in result if row.get("total")]) or 0

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

        debit = flt(debit)
        credit = flt(credit)

        # ✅ move negatives to the other side
        if debit < 0:
            credit += abs(debit)
            debit = 0
        if credit < 0:
            debit += abs(credit)
            credit = 0

        if switch_columns:
            debit, credit = credit, debit

        last_balance = update_balance(last_balance, debit, credit)
        party_summary.append({
            "statement": statement,
            "debit": flt(debit, 2),
            "credit": flt(credit, 2),
            "balance": flt(last_balance, 2)
        })


    switch_columns = True if party_type == "Customer" else False
    party_summary = []
    debit, credit, last_balance = 0, 0, 0
    calc_opening = int(filters.get("calculate_opening_balance_with_totals") or 0)
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
    from agricultural_marketing.agricultural_marketing.page.supplier_collection_form import supplier_collection_form as supplier_collection_form_module
    # GET total items and payments before from date
    if filters.get("consider_draft"):
        total_items = supplier_collection_form_module.get_draft_total_items(filters, party) or 0
        total_payments = supplier_collection_form_module.get_draft_total_payments(filters, party) or 0
        if filters.get("party_type") == "Supplier":
            total_draft_commission = supplier_collection_form_module.get_draft_total_commission(filters, party) or 0
            debit += total_payments + total_items.get("debit", 0) + total_draft_commission
            credit += total_items.get("credit", 0)
        else:
            debit += total_items.get("debit", 0)
            credit += total_payments + total_items.get("credit", 0)

    # if filters.get("party_type") == "Supplier":
    #     previous_buying_total = get_buying_total_before_from_date(filters, party) or 0
    #     debit += previous_buying_total
    # Calculate totals
    total_sales, total_commission_with_taxes = get_total_sales_and_commissions(party_data)
    total_buying = get_total_buying(party_data) if filters.get("party_type") == "Supplier" else 0
    total_payments = get_total_payments(party_data)
    last_balance = debit - credit
    
    if calc_opening == 0:
        # one-sided opening (old behavior)
        if last_balance > 0:
            debit = abs(last_balance)
            credit = 0
        else:
            credit = abs(last_balance)
            debit = 0
    else:
        # totals mode: keep debit/credit totals but never negative columns
        if debit < 0:
            credit += abs(debit)
            debit = 0
        if credit < 0:
            debit += abs(credit)
            credit = 0

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
    total_debit = sum(flt(r.get("debit") or 0) for r in party_summary)
    total_credit = sum(flt(r.get("credit") or 0) for r in party_summary)

    # (optional) keep totals non-negative
    if total_debit < 0:
        total_credit += abs(total_debit)
        total_debit = 0
    if total_credit < 0:
        total_debit += abs(total_credit)
        total_credit = 0

    party_summary.append({
        "statement": _("Total"),
        "debit": flt(total_debit, 2),
        "credit": flt(total_credit, 2),
        "balance": flt(total_debit - total_credit, 2)
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

def _get_current_whatsapp_status(log_doc):
    status = log_doc.whatsapp_status or ""
    if log_doc.whatsapp_message_id:
        status = frappe.db.get_value("WhatsApp Message Log", log_doc.whatsapp_message_id, "status") or status
    return status


def _is_whatsapp_already_sent(log_doc):
    status = _get_current_whatsapp_status(log_doc)
    if status in ("Sent", "Delivered", "Read"):
        return True
    if log_doc.whatsapp_sent and status and status != "Failed":
        return True
    return False


def _is_whatsapp_job_active(history_id):
    if not history_id:
        return False
    row = frappe.db.get_value(
        "Statement Generation History",
        history_id,
        ["whatsapp_job_id", "whatsapp_job_name"],
        as_dict=True,
    )
    if not row:
        return False
    from agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms import get_whatsapp_job_status
    status_info = get_whatsapp_job_status(row.get("whatsapp_job_id"), row.get("whatsapp_job_name"))
    status = (status_info or {}).get("status")
    if not status:
        return False
    active_statuses = {"queued", "started", "running", "deferred", "scheduled", "stopped"}
    return status in active_statuses


@frappe.whitelist()
def send_whatsapp_for_party(log_id):
    """Send WhatsApp message for a specific supplier PDF log with history updates"""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)

        if log_doc.status != "Completed" or not log_doc.pdf_file:
            return {"error": "PDF not ready for this party"}

        if _is_whatsapp_already_sent(log_doc):
            return {"error": "WhatsApp message already sent for this party"}

        whatsapp_result = create_whatsapp_messages(
            party_type=log_doc.party_type,
            party_name=log_doc.party_name,
            pdf_url=log_doc.pdf_file,
            reference_document='PDF Generator Log',
            document_name=log_doc.name,
        )

        if whatsapp_result and not whatsapp_result.get("error"):
            log_doc.whatsapp_sent = 1
            log_doc.whatsapp_message_id = whatsapp_result.get("log_name") or whatsapp_result
            log_doc.whatsapp_status = whatsapp_result.get("status") or "Queued"
            log_doc.save(ignore_permissions=True)

            if getattr(log_doc, 'statement_generation_history', None):
                update_history_item_status_safe(
                    log_doc.statement_generation_history,
                    log_id,
                    log_doc.status,
                    whatsapp_status=whatsapp_result.get("status") or "Queued",
                    whatsapp_message_id=(whatsapp_result.get("log_name") or whatsapp_result),
                )
                update_history_summary_counts_safe(log_doc.statement_generation_history)

            frappe.db.commit()
            return {"success": "WhatsApp message sent successfully"}

        error_message = whatsapp_result.get("error", "Failed to send WhatsApp message") if whatsapp_result else "Failed to send WhatsApp message"
        log_doc.whatsapp_sent = 0
        log_doc.whatsapp_status = "Failed"
        log_doc.error_message = error_message
        log_doc.save(ignore_permissions=True)
        if getattr(log_doc, 'statement_generation_history', None):
            update_history_item_status_safe(
                log_doc.statement_generation_history,
                log_id,
                log_doc.status,
                error_message=error_message,
                whatsapp_status="Failed",
            )
            update_history_summary_counts_safe(log_doc.statement_generation_history)
        frappe.db.commit()
        return {"error": error_message}

    except Exception as e:
        # When create_whatsapp_messages raises (e.g. gateway down, session disconnected),
        # the "error in result" path above is never run. Update log and history here.
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
            log_doc.whatsapp_sent = 0
            log_doc.whatsapp_status = "Failed"
            log_doc.error_message = str(e)
            log_doc.save(ignore_permissions=True)
            if getattr(log_doc, "statement_generation_history", None):
                update_history_item_status_safe(
                    log_doc.statement_generation_history,
                    log_id,
                    log_doc.status,
                    error_message=str(e),
                    whatsapp_status="Failed",
                )
                update_history_summary_counts_safe(log_doc.statement_generation_history)
            frappe.db.commit()
        except Exception as inner:
            frappe.log_error(message=f"Failed to update log/history after WhatsApp error: {inner}", title="Send WhatsApp for Party")
        return {"error": str(e)}


@frappe.whitelist()
def queue_whatsapp_for_party(log_id):
    """Queue WhatsApp sending for a specific PDF Generator Log to avoid UI blocking"""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)
        if log_doc.status != "Completed" or not log_doc.pdf_file:
            return {"error": "PDF not ready for this party"}
        if getattr(log_doc, 'statement_generation_history', None):
            if _is_whatsapp_job_active(log_doc.statement_generation_history):
                return {"error": "WhatsApp queue is still running for this history"}
        if _is_whatsapp_already_sent(log_doc):
            return {"error": "WhatsApp message already sent for this party"}
        current_status = _get_current_whatsapp_status(log_doc)
        if current_status in (None, "", "Not Created", "Failed"):
            log_doc.whatsapp_status = "Queued"
            log_doc.error_message = ""
            log_doc.save(ignore_permissions=True)

        if getattr(log_doc, 'statement_generation_history', None):
            update_history_item_status_safe(
                log_doc.statement_generation_history,
                log_id,
                log_doc.status,
                whatsapp_status="Queued",
            )

        safe_party_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30] if getattr(log_doc, 'party_name', None) else log_id
        frappe.enqueue(
            method=_send_whatsapp_job,
            log_id=log_id,
            job_name=f"WA-{safe_party_name}",
            timeout=300,
            is_async=True,
        )
        frappe.db.commit()
        return {"success": "WhatsApp send queued"}
    except Exception as e:
        # Update log and history so summary counts stay current (same pattern as send_whatsapp_for_party)
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
            log_doc.whatsapp_sent = 0
            log_doc.whatsapp_status = "Failed"
            log_doc.error_message = str(e)
            log_doc.save(ignore_permissions=True)
            if getattr(log_doc, "statement_generation_history", None):
                update_history_item_status_safe(
                    log_doc.statement_generation_history,
                    log_id,
                    log_doc.status,
                    error_message=str(e),
                    whatsapp_status="Failed",
                )
                update_history_summary_counts_safe(log_doc.statement_generation_history)
            frappe.db.commit()
        except Exception as inner:
            frappe.log_error(message=f"Failed to update log/history after queue error: {inner}", title="Queue WhatsApp for Party")
        return {"error": str(e)}


def _send_whatsapp_job(log_id):
    """Background worker: send WhatsApp for a specific log by reusing existing logic"""
    try:
        send_whatsapp_for_party(log_id)
    except Exception as e:
        frappe.log_error(message=f"WhatsApp job failed for {log_id}: {str(e)}", title="WhatsApp Queue")


@frappe.whitelist()
def process_whatsapp_bulk(log_ids, delay_seconds: int = 4):
    """Background worker: send WhatsApp for a list sequentially with delay between messages."""
    import json as _json
    try:
        if isinstance(log_ids, str):
            try:
                log_ids = _json.loads(log_ids)
            except Exception:
                log_ids = []
        if not isinstance(log_ids, list):
            log_ids = []
        try:
            delay_seconds = int(delay_seconds)
        except Exception:
            delay_seconds = 4
        if delay_seconds < 6:
            delay_seconds = 6
        count = 0
        for lid in log_ids:
            try:
                send_whatsapp_for_party(lid)
            except Exception as e:
                frappe.log_error(message=f"Bulk WhatsApp failed for {lid}: {str(e)}", title="WhatsApp Bulk Sender")
            count += 1
            if count < len(log_ids):
                time.sleep(delay_seconds)
        return {"success": True, "processed": len(log_ids), "delay": delay_seconds}
    except Exception as e:
        frappe.log_error(message=f"process_whatsapp_bulk error: {str(e)}", title="WhatsApp Bulk Sender")
        return {"error": str(e)}


@frappe.whitelist()
def queue_all_whatsapp(history_id=None, log_ids=None, retry_failed: int = 0):
    """Queue WhatsApp messages for all eligible parties in a history or a provided list of log IDs.
    If retry_failed is truthy, also include items whose whatsapp_status is 'Failed'."""
    try:
        queued = 0
        skipped = 0
        ids = []

        if history_id:
            if _is_whatsapp_job_active(history_id):
                return {"error": "WhatsApp queue is still running for this history"}
            items = frappe.get_all(
                "Statement Generation History Item",
                filters={"parent": history_id},
                fields=["pdf_generator_log", "status", "whatsapp_status", "pdf_file"],
            )
            if retry_failed:
                ids = [
                    it.pdf_generator_log
                    for it in items
                    if it.status == "Completed"
                    and (it.whatsapp_status in (None, "Not Created", "Failed"))
                ]
            else:
                ids = [
                    it.pdf_generator_log
                    for it in items
                    if it.status == "Completed"
                    and (not it.whatsapp_status or it.whatsapp_status == "Not Created")
                ]
        elif log_ids:
            if isinstance(log_ids, str):
                import json as _json
                try:
                    ids = _json.loads(log_ids)
                except Exception:
                    ids = []
            elif isinstance(log_ids, list):
                ids = log_ids

            if ids:
                logs = frappe.get_all(
                    "PDF Generator Log",
                    filters={"name": ["in", ids]},
                    fields=["name", "status", "pdf_file", "whatsapp_status", "whatsapp_sent"],
                )
                eligible_statuses = {"Not Created", None, ""}
                if retry_failed:
                    eligible_statuses = {"Not Created", None, "", "Failed"}
                ids = [
                    log.name
                    for log in logs
                    if log.status == "Completed"
                    and log.pdf_file
                    and not log.whatsapp_sent
                    and log.whatsapp_status in eligible_statuses
                ]

        if ids:
            frappe.db.sql(
                """
                UPDATE `tabPDF Generator Log`
                SET whatsapp_status = 'Queued',
                    whatsapp_sent = 0,
                    error_message = ''
                WHERE name IN %(ids)s
                """,
                {"ids": tuple(ids)},
            )
            if history_id:
                for log_id in ids:
                    update_history_item_status_safe(
                        history_id,
                        log_id,
                        "Completed",
                        whatsapp_status="Queued",
                        error_message="",
                    )
            frappe.db.commit()

        delay_cfg = frappe.db.get_single_value("Agriculture Settings", "delay_between_messages_seconds") or 6
        try:
            delay_seconds = int(delay_cfg)
        except Exception:
            delay_seconds = 4
        if delay_seconds < 6:
            delay_seconds = 6

        job_label = history_id or ("bulk-" + str(len(ids)))
        job_name = f"WA-Bulk-{job_label}"
        job = frappe.enqueue(
            method=process_whatsapp_bulk,
            log_ids=ids,
            delay_seconds=delay_seconds,
            job_name=job_name,
            timeout=3600,
            is_async=True,
        )
        job_id = job.get_id() if hasattr(job, "get_id") else getattr(job, "id", job)
        job_id = job.id if hasattr(job, "id") else job
        if history_id:
            frappe.db.set_value(
                "Statement Generation History",
                history_id,
                {"whatsapp_job_id": job_id, "whatsapp_job_name": job_name},
            )
            frappe.db.commit()
        return {
            "success": f"Queued {len(ids)} WhatsApp messages (rate-limited every {delay_seconds}s)",
            "queued": len(ids),
            "skipped": skipped,
            "job_id": job_id,
            "job_name": job_name,
        }
    except Exception as e:
        return {"error": str(e)}

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
    """Send WhatsApp via whatsapp_connector (unified version)."""
    if not (party_type and party_name and pdf_url):
        return {"error": "Missing required parameters."}

    try:
        whatsapp_number = frappe.get_value(party_type, party_name, "whatsapp_number")
        whatsapp_message = frappe.get_value(party_type, party_name, "default_whatsapp_message")

        if not whatsapp_number:
            return {"error": f"WhatsApp number not found for {party_name}"}

        default_session = frappe.db.get_single_value("Agriculture Settings", "whatsapp_session")
        session_name = default_session or frappe.db.get_value(
            "WhatsApp Session",
            {"status_text": ["in", ["CONNECTED", "Connected", "READY", "Ready"]]},
            "name",
        ) or frappe.db.get_value("WhatsApp Session", {}, "name")

        if not session_name:
            return {"error": "No WhatsApp Session configured"}

        from whatsapp_connector.whatsapp_connector.services.session_service import send_message as _wa_send

        log_name = _wa_send(
            session_name=session_name,
            recipient=whatsapp_number,
            message_type="Document",
            message=whatsapp_message or "Please find your supplier statement attached.",
            attachment_url=pdf_url,
            party_type=party_type,
            party_name=party_name,
            reference_document=reference_document,
            document_name=document_name,
        )

        status = None
        try:
            log_doc = frappe.get_doc("WhatsApp Message Log", log_name)
            status = log_doc.status
        except Exception:
            status = None

        frappe.db.commit()
        return {"log_name": log_name, "status": status}

    except Exception as e:
        frappe.log_error(message=f"Error sending WhatsApp via connector: {str(e)}", title="WhatsApp Messaging")
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

def _read_supplier_html_template():
    """Read the supplier_statement_forms.html template directly from disk.
    This is intentionally separate from get_html_format() and any function in
    statement_forms.py to guarantee the correct template is always used,
    even when RQ workers process many jobs from different modules."""
    template_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "supplier_statement_forms.html",
    )
    if not os.path.exists(template_file):
        frappe.throw(f"Supplier statement template not found: {template_file}")
    with open(template_file, "r") as f:
        return f.read()


def _load_pdf_log_with_retry(log_id, attempts=3, delay=2):
    """Load a PDF Generator Log, tolerating a row that is still being committed.

    Jobs are enqueued after commit now, but a worker can still pop a job a hair
    before the row is visible on its own connection. Rather than dropping the
    supplier silently, wait briefly and look again.
    """
    for attempt in range(attempts):
        if frappe.db.exists("PDF Generator Log", log_id):
            return frappe.get_doc("PDF Generator Log", log_id)
        if attempt < attempts - 1:
            time.sleep(delay)
            # Drop the transaction snapshot so the next read sees fresh rows.
            frappe.db.commit()
    raise frappe.DoesNotExistError(f"PDF Generator Log {log_id} not found")


def generate_single_supplier_pdf(log_id, supplier_name=None, history_id=None):
    """Generate PDF for a single supplier – fully self-contained.

    All template reading, data fetching and PDF rendering is inlined here so
    that there is zero chance of the RQ worker resolving a same-named function
    from another module (e.g. statement_forms.generate_single_pdf) which would
    use the wrong HTML template."""
    log_doc = None
    actual_supplier_name = supplier_name

    try:
        # ── 1. Load the PDF Generator Log ──────────────────────────────
        try:
            log_doc = _load_pdf_log_with_retry(log_id)
        except frappe.DoesNotExistError:
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message="PDF log not found")
            # Re-raise so RQ records a failed job instead of the supplier
            # vanishing from the batch with no trace anywhere.
            raise

        # ── 2. Mark as Processing ──────────────────────────────────────
        log_doc.status = "Processing"
        log_doc.save(ignore_permissions=True)
        if history_id:
            update_history_item_status_safe(history_id, log_id, "Processing")
        frappe.db.commit()

        # ── 3. Determine supplier name from the log (authoritative) ────
        actual_supplier_name = log_doc.party_name or supplier_name
        if not actual_supplier_name:
            raise ValueError("No supplier name available on log or argument")

        # ── 4. Reconstruct filters from the log ───────────────────────
        try:
            filters = json.loads(log_doc.filters_json)
        except (json.JSONDecodeError, ValueError) as json_error:
            log_doc.status = "Failed"
            log_doc.error_message = f"Invalid filters JSON: {str(json_error)}"
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=log_doc.error_message)
            frappe.db.commit()
            return

        # Force every critical field from the log itself – never trust
        # the serialised filters alone.
        filters["party"] = actual_supplier_name
        filters["party_type"] = "Supplier"
        filters["company"] = log_doc.company
        filters["party_group"] = log_doc.party_group
        filters["from_date"] = str(log_doc.from_date) if log_doc.from_date else filters.get("from_date")
        filters["to_date"] = str(log_doc.to_date) if log_doc.to_date else filters.get("to_date")

        # ── 5. Fetch data (uses THIS module's get_data) ───────────────
        data = frappe._dict()
        data = get_data(data, filters)
        if not data or actual_supplier_name not in data:
            raise ValueError(f"No data found for supplier: {actual_supplier_name}")

        value = data[actual_supplier_name]

        # ── 6. Read the SUPPLIER template directly from disk ───────────
        html_format = _read_supplier_html_template()

        # ── 7. Build context & render ──────────────────────────────────
        default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
        letter_head = frappe.get_doc("Letter Head", default_letter_head) if default_letter_head else None

        party_summary = get_party_summary(
            filters=filters,
            party_type="Supplier",
            party=actual_supplier_name,
            party_data=value,
        )
        header_details = get_header_data(filters.get("party_group"), actual_supplier_name)
        font_size = frappe.db.get_single_value("Agriculture Settings", "font_size") or 14

        context = {
            "letter_head": letter_head,
            "header": header_details,
            "summary": party_summary,
            "items": value.get("items"),
            "buying_items": value.get("buying_items"),
            "payments": value.get("payments"),
            "filters": filters,
            "lang": frappe.local.lang,
            "layout_direction": "rtl" if is_rtl() else "ltr",
            "font_size": font_size,
        }

        html = frappe.render_template(html_format, context)
        content = _get_pdf(html, {"orientation": "Portrait"})

        file_name = "{0}-{1}.pdf".format(actual_supplier_name, str(random.randint(1000, 9999)))
        file_doc = frappe.new_doc("File")
        file_doc.update({"file_name": file_name, "is_private": 0, "content": content})
        file_doc.save(ignore_permissions=True)

        # ── 8. Mark Completed ──────────────────────────────────────────
        log_doc.status = "Completed"
        log_doc.pdf_file = file_doc.file_url
        log_doc.completion_time = now()
        if history_id:
            update_history_item_status_safe(history_id, log_id, "Completed", pdf_file=file_doc.file_url)

    except Exception as e:
        error_msg = str(e)[:200]
        frappe.log_error(
            message=f"Supplier PDF Generation failed for {log_id} ({actual_supplier_name}): {error_msg}",
            title="Supplier PDF Generation",
        )
        if log_doc:
            try:
                log_doc.status = "Failed"
                log_doc.error_message = error_msg
                if history_id:
                    update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            except Exception as update_error:
                frappe.log_error(
                    message=f"Failed to update error status for {log_id}: {str(update_error)[:200]}",
                    title="Supplier PDF Generation",
                )
                return

    # ── 9. Persist final status ────────────────────────────────────────
    if log_doc:
        try:
            log_doc.save(ignore_permissions=True)
            frappe.db.commit()
            if history_id:
                update_history_summary_counts_safe(history_id)
        except Exception as save_error:
            frappe.log_error(
                message=f"Failed to save final status for {log_id}: {str(save_error)[:200]}",
                title="Supplier PDF Generation",
            )

@frappe.whitelist()
def retry_failed_pdf(log_id):
    """Retry generating PDF for a failed supplier log entry."""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)

        if log_doc.status not in ["Failed"]:
            return {"error": "Can only retry failed jobs"}

        # Reset status
        log_doc.status = "Queued"
        log_doc.error_message = ""
        log_doc.save(ignore_permissions=True)

        # Update history item safely
        if getattr(log_doc, "statement_generation_history", None):
            update_history_item_status_safe(log_doc.statement_generation_history, log_id, "Queued")

        # Queue new background job with supplier-specific generator
        safe_supplier_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30]
        frappe.enqueue(
            method=generate_single_supplier_pdf,
            log_id=log_id,
            supplier_name=log_doc.party_name,
            history_id=getattr(log_doc, "statement_generation_history", None),
            job_name=f"SupplierPDF-Retry-{safe_supplier_name}",
            timeout=300,
            is_async=True,
        )

        frappe.db.commit()
        return {"success": "Supplier PDF generation retry queued"}

    except Exception as e:
        return {"error": str(e)[:200]}


@frappe.whitelist()
def retry_all_failed_pdfs(history_id):
    """
    Retry all failed PDF Generator Logs for a given Statement Generation History
    using supplier-specific PDF generation.
    """
    try:
        if not history_id:
            return {"error": "No history_id provided"}
        history_doc = frappe.get_doc("Statement Generation History", history_id)
        failed_logs = [item.pdf_generator_log for item in history_doc.pdf_generator_logs if item.status == "Failed"]
        retried = 0
        skipped = 0

        for log_id in failed_logs:
            try:
                log_doc = frappe.get_doc("PDF Generator Log", log_id)
                if log_doc.status == "Failed":
                    # Reset status and error message
                    log_doc.status = "Queued"
                    log_doc.error_message = ""
                    log_doc.save(ignore_permissions=True)
                    # Update history item safely
                    update_history_item_status_safe(history_id, log_id, "Queued")
                    # Queue new background job with supplier-specific generator
                    safe_supplier_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30]
                    frappe.enqueue(
                        method=generate_single_supplier_pdf,
                        log_id=log_id,
                        supplier_name=log_doc.party_name,
                        history_id=history_id,
                        job_name=f"SupplierPDF-RetryAll-{safe_supplier_name}",
                        timeout=300,
                        is_async=True,
                    )
                    retried += 1
                else:
                    skipped += 1
            except Exception as e:
                frappe.log_error(message=f"Supplier retry all failed: Could not process log {log_id}: {str(e)[:200]}", title="Supplier PDF Retry All Failed")
                skipped += 1

        update_history_summary_counts_safe(history_id)
        frappe.db.commit()
        return {"success": f"Retried {retried} failed jobs, skipped {skipped}", "retried": retried, "skipped": skipped}
    except Exception as e:
        frappe.log_error(message=f"Supplier retry all failed error: {str(e)[:200]}", title="Supplier PDF Retry All Failed")
        return {"error": str(e)[:200]}


@frappe.whitelist()
def retry_all_queued_and_failed_pdf_jobs_for_history(history_id):
    """
    Retry all queued and failed PDF Generator Logs for a given Statement Generation History
    using supplier-specific PDF generation.
    """
    retried = 0
    skipped = 0
    logs = frappe.get_all(
        "PDF Generator Log",
        filters={"statement_generation_history": history_id, "status": ["in", ["Queued", "Failed"]]},
        fields=["name", "party_name", "statement_generation_history"],
    )
    for log in logs:
        try:
            frappe.enqueue(
                method=generate_single_supplier_pdf,
                log_id=log["name"],
                supplier_name=log["party_name"],
                history_id=log["statement_generation_history"],
                job_name=f"SupplierPDF-Manual-Retry-{log['name']}",
                timeout=300,
                is_async=True,
            )
            retried += 1
        except Exception as e:
            frappe.logger("pdf_generation").error(f"Failed to re-queue supplier log {log['name']}: {e}")
            skipped += 1
    return {"success": f"Re-queued {retried} jobs, skipped {skipped}", "retried": retried, "skipped": skipped}

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
                where parent=%s and whatsapp_status = 'Sent'
                """,
                (history_id,),
                as_dict=True,
            )[0].cnt
            frappe.log_error(message=f"Whatsapp sent count: {whatsapp_sent_count}", title="Statement Generation History")
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

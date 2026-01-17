import json
import os
import random
from sys import exception
import frappe
from frappe import _, error_log
from frappe.utils import getdate, flt, now
from frappe.utils.jinja_globals import is_rtl
from frappe.utils.pdf import get_pdf as _get_pdf
from frappe.query_builder.functions import Sum
from pypika import Case
from pypika.terms import Term
import time
# @frappe.whitelist()
# def get_reports(filters):
#     data = frappe._dict()
#     file_urls = []
#     letter_head = None
#     if isinstance(filters, str):
#         filters = json.loads(filters)

#     default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
#     if default_letter_head:
#         letter_head = frappe.get_doc("Letter Head", default_letter_head)

#     # Get Data
#     data = get_data(data, filters)
#     if not data:
#         return {
#             "error": "No data matches the chosen criteria"
#         }
#     html_format = get_html_format()

#     for key, value in data.items():
#         # Get summary table data
#         party_summary = get_party_summary(filters=filters, party_type=filters.get("party_type"), party=key,
#                                           party_data=value)

#         header_details = get_header_data(filters.get("party_group"), key)
#         font_size = frappe.db.get_single_value("Agriculture Settings", "font_size") or 14

#         context = {
#             "letter_head": letter_head,
#             "header": header_details,
#             "summary": party_summary,
#             "items": value.get("items"),
#             "payments": value.get("payments"),
#             "filters": filters,
#             "lang": frappe.local.lang,
#             "layout_direction": "rtl" if is_rtl() else "ltr",
#             "font_size": font_size
#         }

#         html = frappe.render_template(html_format, context)
#         content = _get_pdf(html, {"orientation": "Portrait"})
#         file_name = "{0}-{1}.pdf".format(key, str(random.randint(1000, 9999)))
#         file_doc = frappe.new_doc("File")
#         file_doc.update({
#             "file_name": file_name,
#             "is_private": 0,
#             "content": content
#         })
#         file_doc.save(ignore_permissions=True)
#         file_urls.append(file_doc.file_url)

#     return {
#         "file_urls": file_urls
#     }


def get_data(data, filters):
    data = get_items_details(data, filters)
    data = get_payments_details(data, filters)
    return data


def get_default_template_name():
    """Return the default Statement Form Template name, or any available one."""
    template_name = frappe.db.get_value("Statement Form Template", {"default": 1}, "name")
    if not template_name:
        template_name = frappe.db.get_value("Statement Form Template", {}, "name")
    return template_name


@frappe.whitelist()
def get_default_statement_form_template():
    """Expose default template name for the client side selection field."""
    return get_default_template_name()


def get_html_template_format(template_name=None):
    """
    Resolve the HTML template used for statement PDFs.
    Prefers the Statement Form Template Doctype; falls back to bundled HTML.
    """
    template_to_use = template_name or get_default_template_name()

    if template_to_use:
        try:
            template_doc = frappe.get_doc("Statement Form Template", template_to_use)
            if template_doc.template:
                return template_doc.template
        except Exception:
            # Fallback to bundled template if the doc is missing or invalid
            pass

    template_filename = os.path.join("statement_forms" + '.html')
    folder = os.path.dirname(frappe.get_module("agricultural_marketing" + "." + "agricultural_marketing" +
                                               "." + "page").__file__)
    doctype_path = os.path.join(folder, "statement_forms")
    paths_temp = os.path.join(doctype_path, template_filename)
    html_format = frappe.utils.get_html_format(paths_temp)
    return html_format


def get_items_details(data, filters):
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
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
        # Construct result, appending to final data nad appending totals
        process_result_and_totals_for_invoices(result, data, filters)

    return data


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
    if filters.get("party_type") == "Supplier":
        append_summary(_("Commission") + " + " + _("VAT"), flt(total_commission_with_taxes, 2), 0)
    append_summary(_("Duration Payments"), flt(total_payments, 2), 0)

    # Calculate and append closing
    total_debit = total_commission_with_taxes + total_payments
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
                                         invformitem.total,invform.reference_number.as_("reference_number"))
    else:
        items_query = items_query.select(_field.as_("party"), invform.name.as_("invoice_id"),
                                         invform.posting_date.as_("date"), invformitem.qty, invformitem.price,
                                         invformitem.total, invformitem.item_name, invform.reference_number.as_("reference_number"))

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


@frappe.whitelist()
def send_whatsapp_msg(filters):
    data = frappe._dict()
    file_urls = []
    whatsapp_messages = []
    letter_head = None
    if isinstance(filters, str):
        filters = json.loads(filters)

    default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
    if default_letter_head:
        letter_head = frappe.get_doc("Letter Head", default_letter_head)

    # Get Data
    data = get_data(data, filters)
    if not data:
        return {
            "error": "No data matches the chosen criteria"
        }
    html_format = get_html_template_format(filters.get("statement_form_template"))

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
            "payments": value.get("payments"),
            "filters": filters,
            "lang": frappe.local.lang,
            "layout_direction": "rtl" if is_rtl() else "ltr",
            "font_size": font_size
        }
        try:
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
                reference_document = 'Page',
                document_name = 'statement-forms',
            ))
        except Exception as e:
            frappe.log_error(message=f"Error PDF HTML Tempalte : {str(e)}", title="Error PDF HTML Tempalte")
        frappe.db.commit()
    
    return {"success": f"WhatsApp message logged"}

@frappe.whitelist()
def create_whatsapp_messages(party_type=None, party_name=None, pdf_url=None, reference_document=None, document_name=None):
    """
    Send WhatsApp via whatsapp_connector (no WhatsApp Messages DocType). Returns WhatsApp Message Log name.
    """

    if not (party_type and party_name and pdf_url):
        return {"error": "Missing required parameters."}

    try:
        # Fetch the WhatsApp Number from either Customer or Supplier
        whatsapp_number = frappe.get_value(party_type, party_name, "whatsapp_number")
        whatsapp_message = frappe.get_value(party_type, party_name, "default_whatsapp_message")

        if not whatsapp_number:
            return {"error": f"WhatsApp number not found for {party_name}"}

        # Choose session: prefer Agriculture Settings.whatsapp_session, else any connected, else any
        default_session = frappe.db.get_single_value("Agriculture Settings", "whatsapp_session")
        session_name = default_session or frappe.db.get_value(
            "WhatsApp Session",
            {"status_text": ["in", ["CONNECTED", "Connected", "READY", "Ready"]]},
            "name",
        ) or frappe.db.get_value("WhatsApp Session", {}, "name")

        if not session_name:
            return {"error": "No WhatsApp Session configured"}

        # Use connector service to send message (Document with attachment_url)
        from whatsapp_connector.whatsapp_connector.services.session_service import send_message as _wa_send

        log_name = _wa_send(
            session_name=session_name,
            recipient=whatsapp_number,
            message_type="Document",
            message=whatsapp_message or "Please find your statement attached.",
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
    frappe.enqueue(method=send_whatsapp_msg, filters=filters, job_name="create pdf and whatsapp for Statement Forms")
    lock_invoice_update()
    return {"success": f"WhatsApp message logged"}



@frappe.whitelist()
def lock_invoice_update():
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




####### New For Drafts ########
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
# NEW PDF GENERATION SYSTEM
# ================================

@frappe.whitelist()
def queue_pdf_generation(filters):
    """UPDATED - Queue PDF generation for multiple parties and create Statement Generation History"""
    if isinstance(filters, str):
        filters = json.loads(filters)
    
    # Ensure template is specified; fall back to default template if missing
    if not filters.get("statement_form_template"):
        default_template = get_default_template_name()
        if default_template:
            filters["statement_form_template"] = default_template

    # First, get ALL data to see which parties actually have data
    frappe.publish_realtime("pdf_generation_status", {"message": "Checking for parties with data..."})
    
    temp_data = frappe._dict()
    temp_data = get_data(temp_data, filters)
    
    if not temp_data:
        return {"error": "No data matches the chosen criteria"}
    
    parties_with_data = list(temp_data.keys())
    
    frappe.publish_realtime("pdf_generation_status", {
        "message": f"Found {len(parties_with_data)} parties with data",
        "parties": parties_with_data[:5]  # Show first 5 as preview
    })
    
    # Create Statement Generation History record
    history_doc = frappe.get_doc({
        "doctype": "Statement Generation History",
        "company": filters.get("company"),
        "party_type": filters.get("party_type"),
        "party_group": filters.get("party_group"),
        "party": filters.get("party"),
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date"),
        "created_by_user": frappe.session.user,
        "generation_time": now(),
        "consider_draft": filters.get("consider_draft", 0),
        "consider_draft_payments": filters.get("consider_draft_payments", 0),
        "neglect_items": filters.get("neglect_items", 0),
        "calculate_opening_balance_with_totals": filters.get("calculate_opening_balance_with_totals", 0),
        "total_parties": len(parties_with_data),
        "description": f"Statement generation for {len(parties_with_data)} parties from {filters.get('from_date')} to {filters.get('to_date')}"
    })
    history_doc.insert(ignore_permissions=True)
    
    # Create PDF Generator Log entries only for parties with actual data
    log_entries = []
    skipped_parties = []
    
    for party in parties_with_data:
        # Double-check this party has data (safety check)
        party_data = temp_data.get(party)
        if not party_data or (not party_data.get("items") and not party_data.get("payments")):
            skipped_parties.append(party)
            continue
        
        # Create new log entry
        log_entry = frappe.get_doc({
            "doctype": "PDF Generator Log",
            "party_type": filters.get("party_type"),
            "party_name": party,
            "party_group": filters.get("party_group"),
            "company": filters.get("company"),
            "from_date": filters.get("from_date"),
            "to_date": filters.get("to_date"),
            "status": "Queued",
            "created_by": frappe.session.user,
            "filters_json": json.dumps(filters),
            "statement_generation_history": history_doc.name  # Link to history
        })
        log_entry.insert(ignore_permissions=True)
        log_entries.append(log_entry.name)
        
        # Add to history child table
        history_doc.append("pdf_generator_logs", {
            "pdf_generator_log": log_entry.name,
            "party_name": party,
            "status": "Queued",
            "whatsapp_status": "Not Created"
        })
        
        # Queue background job for each party with better job naming
        safe_party_name = frappe.scrub(party).replace("_", "-")[:30]
        frappe.enqueue(
            method=generate_single_party_pdf,
            log_id=log_entry.name,
            party_name=party,
            history_id=history_doc.name,
            job_name=f"PDF-{safe_party_name}",
            timeout=300,
            is_async=True
        )
    
    # Save history with child table entries
    history_doc.save(ignore_permissions=True)
    frappe.db.commit()
    
    result_message = f"Queued {len(log_entries)} PDF generation jobs"
    if skipped_parties:
        result_message += f" (skipped {len(skipped_parties)} parties with no data)"
    
    frappe.publish_realtime("pdf_generation_status", {
        "message": "PDF generation jobs queued successfully",
        "queued": len(log_entries),
        "skipped": len(skipped_parties),
        "history_id": history_doc.name
    })
    
    return {
        "success": result_message, 
        "log_entries": log_entries,
        "parties_with_data": len(parties_with_data),
        "skipped_parties": len(skipped_parties),
        "history_id": history_doc.name
    }

def generate_single_party_pdf(log_id, party_name=None, history_id=None):
    """UPDATED - Generate PDF for a single party (background job) with history updates"""
    log_doc = None
    actual_party_name = party_name
    
    try:
        # Try to get the log document with better error handling
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
        except frappe.DoesNotExistError:
            if history_id:
                update_history_item_status(history_id, log_id, "Failed", error_message="PDF log not found (possibly deleted)")
            return
        except Exception as doc_error:
            frappe.log_error(message=f"Error loading PDF Generator Log {log_id}: {str(doc_error)}", title="PDF Generation")
            return
        
        # Update status to processing
        log_doc.status = "Processing"
        log_doc.save(ignore_permissions=True)
        
        # Update history child table status
        if history_id:
            update_history_item_status(history_id, log_id, "Processing")
        
        frappe.db.commit()
        
        # Use party name from log if not provided as parameter
        if not actual_party_name:
            actual_party_name = log_doc.party_name
        
        # Reconstruct filters for single party
        try:
            filters = json.loads(log_doc.filters_json)
            filters["party"] = actual_party_name
        except (json.JSONDecodeError, ValueError) as json_error:
            log_doc.status = "Failed"
            log_doc.error_message = f"Invalid filters JSON: {str(json_error)}"
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status(history_id, log_id, "Failed", error_message=log_doc.error_message)
            frappe.db.commit()
            return
        
        # Generate PDF using existing logic with additional validation
        pdf_result = generate_single_pdf(filters, actual_party_name)
        
        if pdf_result.get("success"):
            log_doc.status = "Completed"
            log_doc.pdf_file = pdf_result["file_url"]
            log_doc.completion_time = now()
            if history_id:
                update_history_item_status(history_id, log_id, "Completed", pdf_file=pdf_result["file_url"])
        elif pdf_result.get("error") and "No data found" in pdf_result.get("error", ""):
            # Special handling for no data case
            log_doc.status = "Failed"
            log_doc.error_message = f"No data found for party '{actual_party_name}' in the specified date range"
            if history_id:
                update_history_item_status(history_id, log_id, "Failed", error_message=log_doc.error_message)
        else:
            log_doc.status = "Failed"
            log_doc.error_message = pdf_result.get("error", "Unknown error during PDF generation")
            if history_id:
                update_history_item_status(history_id, log_id, "Failed", error_message=log_doc.error_message)
            
    except Exception as e:
        error_msg = str(e)
        frappe.log_error(message=f"PDF Generation failed for {log_id} (party: {actual_party_name}): {error_msg}", title="PDF Generation")
        
        # Only update log_doc if it exists and we can access it
        if log_doc:
            try:
                log_doc.status = "Failed"
                log_doc.error_message = error_msg[:500]  # Limit error message length
                if history_id:
                    update_history_item_status(history_id, log_id, "Failed", error_message=error_msg)
            except Exception as update_error:
                frappe.log_error(message=f"Failed to update error status for {log_id}: {str(update_error)}", title="PDF Generation")
                return
    
    # Save the final status (only if log_doc exists and no exceptions occurred)
    if log_doc:
        try:
            log_doc.save(ignore_permissions=True)
            frappe.db.commit()
            
            # Update history summary counts
            if history_id:
                update_history_summary_counts(history_id)
                
        except Exception as save_error:
            frappe.log_error(message=f"Failed to save final status for {log_id}: {str(save_error)}", title="PDF Generation")

def generate_single_pdf(filters, party_name):
    """Generate PDF for a single party - KEEP THIS AS IS OR ADD IF MISSING"""
    try:
        data = frappe._dict()
        
        # Get letter head
        default_letter_head = frappe.get_value("Company", filters.get("company"), "default_letter_head")
        letter_head = None
        if default_letter_head:
            letter_head = frappe.get_doc("Letter Head", default_letter_head)
        
        # Get data for single party
        data = get_data(data, filters)
        if not data or party_name not in data:
            return {"error": f"No data found for party: {party_name}"}
        
        html_format = get_html_template_format(filters.get("statement_form_template"))
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
            "payments": value.get("payments"),
            "filters": filters,
            "lang": frappe.local.lang,
            "layout_direction": "rtl" if is_rtl() else "ltr",
            "font_size": font_size
        }
        try:
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
        except Exception as e:
            frappe.log_error(message=f"Error PDF HTML Tempalte : {str(e)}", title="Error PDF HTML Tempalte")
        return {"success": True, "file_url": file_doc.file_url}
        
    except Exception as e:
        return {"error": str(e)}

@frappe.whitelist()
def get_pdf_generation_status(filters=None, history_id=None):
    """UPDATED - Get status of PDF generation jobs with history support"""
    
    if history_id:
        # Get status for specific history record
        try:
            history_doc = frappe.get_doc("Statement Generation History", history_id)
            logs = []
            
            for item in history_doc.pdf_generator_logs:
                # Resolve display name for party
                display_field = "supplier_name" if history_doc.party_type == "Supplier" else "customer_name"
                party_display_name = frappe.db.get_value(history_doc.party_type, item.party_name, display_field) or item.party_name
                
                log_data = {
                    "name": item.pdf_generator_log,
                    "party_name": item.party_name,
                    "party_display_name": party_display_name,
                    "party_type": history_doc.party_type,
                    "status": item.status,
                    "pdf_file": item.pdf_file,
                    "error_message": item.error_message,
                    "whatsapp_sent": 1 if item.whatsapp_status and item.whatsapp_status != "Not Created" else 0,
                    "whatsapp_message_id": item.whatsapp_message_id,
                    "whatsapp_status": item.whatsapp_status or "Not Created"
                }
                
                # Get additional data from PDF Generator Log if needed
                try:
                    pdf_log = frappe.get_doc("PDF Generator Log", item.pdf_generator_log)
                    log_data["creation_time"] = pdf_log.creation_time
                    log_data["completion_time"] = pdf_log.completion_time
                except:
                    pass
                
                logs.append(log_data)
            
            return logs
            
        except Exception as e:
            frappe.log_error(message=f"Error getting status for history {history_id}: {str(e)}", title="Statement Generation History")
            return []
    
    # Original logic for filters-based status
    conditions = {}
    
    if filters:
        if isinstance(filters, str):
            filters = json.loads(filters)
        
        if filters.get("company"):
            conditions["company"] = filters.get("company")
        if filters.get("party_type"):
            conditions["party_type"] = filters.get("party_type")
        if filters.get("from_date"):
            conditions["from_date"] = filters.get("from_date")
        if filters.get("to_date"):
            conditions["to_date"] = filters.get("to_date")
    
    logs = frappe.get_all(
        "PDF Generator Log",
        filters=conditions,
        fields=[
            "name", "party_name", "party_type", "status", "pdf_file", 
            "error_message", "creation_time", "completion_time", "whatsapp_sent",
            "whatsapp_message_id"
        ],
        order_by="creation desc",
        limit=200
    )
    
    # Get WhatsApp status and party display name for each log
    for log in logs:
        if log.whatsapp_message_id:
            whatsapp_status = frappe.db.get_value("WhatsApp Message Log", log.whatsapp_message_id, "status")
            log["whatsapp_status"] = whatsapp_status or "Unknown"
        else:
            log["whatsapp_status"] = "Not Created"
        # Resolve display name
        display_field = "supplier_name" if log.party_type == "Supplier" else "customer_name"
        log["party_display_name"] = frappe.db.get_value(log.party_type, log.party_name, display_field) or log.party_name
    
    return logs

# ================================
# MANAGEMENT FUNCTIONS
# ================================

@frappe.whitelist()
def cleanup_logs_with_no_data():
    """FIXED - Clean up PDF Generator Logs for parties that have no data with better error handling"""
    try:
        # Get all queued/processing logs
        logs_to_check = frappe.get_all("PDF Generator Log", 
            filters={"status": ["in", ["Queued", "Processing"]]},
            fields=["name", "party_name", "filters_json", "party_type", "statement_generation_history"]
        )
        
        cleaned_count = 0
        
        for log in logs_to_check:
            try:
                if not log.filters_json or not log.party_name:
                    # Mark as failed if missing critical data
                    frappe.db.set_value("PDF Generator Log", log.name, {
                        "status": "Failed",
                        "error_message": "Missing filters or party name"
                    })
                    
                    # Update history item safely
                    if log.statement_generation_history:
                        update_history_item_status_safe(
                            log.statement_generation_history, 
                            log.name, 
                            "Failed", 
                            error_message="Missing filters or party name"
                        )
                    
                    cleaned_count += 1
                    continue
                
                # Check if this party actually has data
                filters = json.loads(log.filters_json)
                filters["party"] = log.party_name
                
                # Quick data check
                temp_data = frappe._dict()
                temp_data = get_data(temp_data, filters)
                
                if not temp_data or log.party_name not in temp_data:
                    # No data found for this party
                    error_msg = f"No data found for party '{log.party_name}'"
                    frappe.db.set_value("PDF Generator Log", log.name, {
                        "status": "Failed", 
                        "error_message": error_msg
                    })
                    
                    # Update history item safely
                    if log.statement_generation_history:
                        update_history_item_status_safe(
                            log.statement_generation_history, 
                            log.name, 
                            "Failed", 
                            error_message=error_msg
                        )
                    
                    cleaned_count += 1
                elif not temp_data[log.party_name].get("items") and not temp_data[log.party_name].get("payments"):
                    # Party exists but has no items or payments
                    error_msg = f"Party '{log.party_name}' has no transactions"
                    frappe.db.set_value("PDF Generator Log", log.name, {
                        "status": "Failed",
                        "error_message": error_msg
                    })
                    
                    # Update history item safely
                    if log.statement_generation_history:
                        update_history_item_status_safe(
                            log.statement_generation_history, 
                            log.name, 
                            "Failed", 
                            error_message=error_msg
                        )
                    
                    cleaned_count += 1
                    
            except Exception as e:
                error_msg = f"Data validation failed: {str(e)[:100]}"
                frappe.log_error(message=f"Error checking data for log {log.name}: {str(e)[:200]}", title="PDF Generation Cleanup")
                
                # Mark as failed if we can't check the data
                frappe.db.set_value("PDF Generator Log", log.name, {
                    "status": "Failed",
                    "error_message": error_msg
                })
                
                # Update history item safely
                if log.statement_generation_history:
                    update_history_item_status_safe(
                        log.statement_generation_history, 
                        log.name, 
                        "Failed", 
                        error_message=error_msg
                    )
                
                cleaned_count += 1
        
        # Update summary counts for affected histories (safely)
        affected_histories = frappe.db.sql("""
            SELECT DISTINCT statement_generation_history 
            FROM `tabPDF Generator Log` 
            WHERE statement_generation_history IS NOT NULL
        """, as_dict=True)
        
        for history in affected_histories:
            if history.statement_generation_history:
                update_history_summary_counts_safe(history.statement_generation_history)
        
        frappe.db.commit()
        
        return {
            "success": True,
            "message": f"Cleaned up {cleaned_count} logs with no data",
            "cleaned_count": cleaned_count
        }
        
    except Exception as e:
        frappe.log_error(message=f"Cleanup logs with no data failed: {str(e)[:200]}", title="PDF Generation Cleanup")
        return {"success": False, "error": str(e)[:200]}

@frappe.whitelist()
def cleanup_failed_logs():
    """FIXED - Clean up failed or stuck PDF generation logs with better error handling"""
    try:
        # Find logs that have been processing for more than 10 minutes
        ten_minutes_ago = frappe.utils.add_to_date(frappe.utils.now(), minutes=-10)
        
        stuck_logs = frappe.get_all("PDF Generator Log", 
            filters={
                "status": ["in", ["Queued", "Processing"]],
                "creation": ["<", ten_minutes_ago]
            },
            fields=["name", "party_name", "status", "statement_generation_history"]
        )
        
        updated_count = 0
        for log in stuck_logs:
            try:
                log_doc = frappe.get_doc("PDF Generator Log", log.name)
                log_doc.status = "Failed"
                log_doc.error_message = "Job timed out or system error"
                log_doc.save(ignore_permissions=True)
                
                # Update history item safely
                if log.statement_generation_history:
                    update_history_item_status_safe(
                        log.statement_generation_history, 
                        log.name, 
                        "Failed", 
                        error_message="Job timed out or system error"
                    )
                
                updated_count += 1
            except Exception as e:
                frappe.log_error(message=f"Failed to cleanup log {log.name}: {str(e)[:200]}", title="PDF Generation Cleanup")
        
        # Update summary counts for affected histories (safely)
        affected_histories = list(set([log.statement_generation_history for log in stuck_logs if log.statement_generation_history]))
        for history_id in affected_histories:
            update_history_summary_counts_safe(history_id)
        
        frappe.db.commit()
        return {"success": f"Cleaned up {updated_count} stuck jobs"}
        
    except Exception as e:
        frappe.log_error(message=f"Cleanup process failed: {str(e)[:200]}", title="PDF Generation Cleanup")
        return {"error": str(e)[:200]}


@frappe.whitelist()
def retry_failed_pdf(log_id):
    """FIXED - Retry generating PDF for a failed log entry with better error handling"""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)
        
        if log_doc.status not in ["Failed"]:
            return {"error": "Can only retry failed jobs"}
        
        # Reset status
        log_doc.status = "Queued"
        log_doc.error_message = ""
        log_doc.save(ignore_permissions=True)
        
        # Update history item safely
        if hasattr(log_doc, 'statement_generation_history') and log_doc.statement_generation_history:
            update_history_item_status_safe(log_doc.statement_generation_history, log_id, "Queued")
        
        # Queue new background job
        safe_party_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30]
        frappe.enqueue(
            method=generate_single_party_pdf,
            log_id=log_id,
            party_name=log_doc.party_name,
            history_id=getattr(log_doc, 'statement_generation_history', None),
            job_name=f"PDF-Retry-{safe_party_name}",
            timeout=300,
            is_async=True
        )
        
        frappe.db.commit()
        return {"success": "PDF generation retry queued"}
        
    except Exception as e:
        return {"error": str(e)[:200]}


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
# ================================
# WHATSAPP FUNCTIONS
# ================================

@frappe.whitelist()
def send_whatsapp_for_party(log_id):
    """UPDATED - Send WhatsApp message for a specific party with history updates"""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)
        
        if log_doc.status != "Completed" or not log_doc.pdf_file:
            return {"error": "PDF not ready for this party"}

        if log_doc.whatsapp_sent or log_doc.whatsapp_status in ("Sent", "Delivered"):
            return {"error": "WhatsApp message already sent for this party"}
        
        # Send WhatsApp message
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
            log_doc.save(ignore_permissions=True)
            
            # Update history if linked
            if hasattr(log_doc, 'statement_generation_history') and log_doc.statement_generation_history:
                update_history_item_status(
                    log_doc.statement_generation_history, 
                    log_id, 
                    log_doc.status,
                    whatsapp_status=whatsapp_result.get("status") or "Queued",
                    whatsapp_message_id=(whatsapp_result.get("log_name") or whatsapp_result)
                )
                update_history_summary_counts(log_doc.statement_generation_history)
            
            frappe.db.commit()
            return {"success": "WhatsApp message sent successfully"}
        else:
            return {"error": whatsapp_result.get("error", "Failed to send WhatsApp message")}
            
    except Exception as e:
        return {"error": str(e)}
    
@frappe.whitelist()
def send_bulk_whatsapp(log_ids):
    """Send WhatsApp messages for multiple parties"""
    if isinstance(log_ids, str):
        log_ids = json.loads(log_ids)
    
    success_count = 0
    error_count = 0
    
    for log_id in log_ids:
        result = send_whatsapp_for_party(log_id)
        if result.get("success"):
            success_count += 1
        else:
            error_count += 1
    
    return {
        "success": f"WhatsApp sent: {success_count}, Failed: {error_count}",
        "success_count": success_count,
        "error_count": error_count
    }

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
            message=whatsapp_message or "Please find your statement attached.",
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

# ================================
# BULK DOWNLOAD FUNCTIONS
# ================================

@frappe.whitelist()
def download_bulk_pdfs(log_ids):
    """Create a ZIP file with multiple PDFs"""
    if isinstance(log_ids, str):
        log_ids = json.loads(log_ids)
    
    import zipfile
    import io
    
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for log_id in log_ids:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
            if log_doc.pdf_file and log_doc.status == "Completed":
                try:
                    file_doc = frappe.get_doc("File", {"file_url": log_doc.pdf_file})
                    zip_file.writestr(
                        f"{log_doc.party_name}.pdf",
                        file_doc.get_content()
                    )
                except Exception as e:
                    frappe.log_error(message=f"Error adding {log_doc.party_name} to ZIP: {str(e)}", title="ZIP File")
    
    zip_content = zip_buffer.getvalue()
    zip_buffer.close()
    
    # Create ZIP file document
    zip_filename = f"statements_{frappe.utils.now().replace(' ', '_').replace(':', '-')}.zip"
    zip_file_doc = frappe.new_doc("File")
    zip_file_doc.update({
        "file_name": zip_filename,
        "is_private": 0,
        "content": zip_content
    })
    zip_file_doc.save(ignore_permissions=True)
    
    return {"file_url": zip_file_doc.file_url}

# ================================
# LEGACY FUNCTIONS - Keep all your existing functions here
# ================================

# Legacy function for backward compatibility
@frappe.whitelist()
def get_reports(filters):
    """Legacy function - now redirects to queue system"""
    return queue_pdf_generation(filters)

# ADD ALL YOUR EXISTING FUNCTIONS BELOW THIS LINE:
# - get_data()
# - get_items_details() 
# - get_payments_details()
# - get_party_summary()
# - get_html_format()
# - etc.

# Copy all the functions from your original file that I haven't included above

# ================================
# NEW FUNCTIONS - ADD THESE TO YOUR EXISTING FILE
# ================================


def update_history_item_status(history_id, log_id, status, pdf_file=None, error_message=None, whatsapp_status=None, whatsapp_message_id=None):
    """FIXED - Update status of specific item in Statement Generation History"""
    # Use the safe version
    update_history_item_status_safe(history_id, log_id, status, pdf_file, error_message, whatsapp_status, whatsapp_message_id)

def update_history_summary_counts(history_id):
    """FIXED - Update summary counts in Statement Generation History"""
    # Use the safe version
    update_history_summary_counts_safe(history_id)
    
def generate_single_party_pdf(log_id, party_name=None, history_id=None):
    """Generate PDF for a single party (background job) - FIXED VERSION"""
    log_doc = None
    actual_party_name = party_name
    
    try:
        # Try to get the log document with better error handling
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
        except frappe.DoesNotExistError:
            if history_id:
                update_history_item_status(history_id, log_id, "Failed", error_message="PDF log not found (possibly deleted)")
            return
        except Exception as doc_error:
            frappe.log_error(message=f"Error loading PDF Generator Log {log_id}: {str(doc_error)[:200]}", title="PDF Generation")
            return
        
        # Validate that the document exists and is in correct state
        if not log_doc:
            frappe.log_error(message=f"PDF Generator Log {log_id} is None after get_doc", title="PDF Generation")
            return
            
        # Use party name from log if not provided as parameter
        if not actual_party_name:
            actual_party_name = log_doc.party_name
        
        if not actual_party_name:
            error_msg = "No party name available"
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            frappe.db.commit()
            return
        
        # Check if document is already completed or failed
        if log_doc.status in ['Completed', 'Failed']:
            frappe.log_error(message=f"PDF Generator Log {log_id} already {log_doc.status}, skipping", title="PDF Generation")
            return
        
        # Update status to processing
        log_doc.status = "Processing"
        log_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Update history child table status (with retry mechanism)
        if history_id:
            update_history_item_status_safe(history_id, log_id, "Processing")
        
        # Validate filters_json exists
        if not log_doc.filters_json:
            error_msg = "No filters available"
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            frappe.db.commit()
            return
        
        # Reconstruct filters for single party
        try:
            filters = json.loads(log_doc.filters_json)
            filters["party"] = actual_party_name
        except (json.JSONDecodeError, ValueError) as json_error:
            error_msg = f"Invalid filters JSON: {str(json_error)[:100]}"
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            frappe.db.commit()
            return
        
        # Generate PDF using existing logic with additional validation
        pdf_result = generate_single_pdf(filters, actual_party_name)
        
        if pdf_result.get("success"):
            log_doc.status = "Completed"
            log_doc.pdf_file = pdf_result["file_url"]
            log_doc.completion_time = now()
            if history_id:
                update_history_item_status_safe(
                    history_id, 
                    log_id, 
                    "Completed", 
                    pdf_file=pdf_result["file_url"]
                )
        elif pdf_result.get("error") and "No data found" in pdf_result.get("error", ""):
            # Special handling for no data case
            error_msg = f"No data found for party '{actual_party_name}'"
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
        else:
            error_msg = pdf_result.get("error", "Unknown error during PDF generation")
            # Limit error message length
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            
    except Exception as e:
        error_msg = str(e)[:200]  # Limit error message length
        frappe.log_error(message=f"PDF Generation failed for {log_id}: {error_msg}", title="PDF Generation")
        
        # Only update log_doc if it exists and we can access it
        if log_doc:
            try:
                log_doc.status = "Failed"
                log_doc.error_message = error_msg
                if history_id:
                    update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            except Exception as update_error:
                frappe.log_error(message=f"Failed to update error status for {log_id}: {str(update_error)[:200]}", title="PDF Generation")
                return
    
    # Save the final status (only if log_doc exists and no exceptions occurred)
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
    """Update a specific child row via direct DB to avoid parent save conflicts. Auto-link orphaned logs if missing. Enhanced with fallback and logging."""
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
            # Log what we're searching for
            frappe.logger("pdf_generation").info(f"Searching for child row: parent={history_id}, pdf_generator_log={log_id}")
            # Find the child row name once per attempt to be safe
            item_name = frappe.db.get_value(
                "Statement Generation History Item",
                {"parent": history_id, "pdf_generator_log": log_id},
                "name",
            )
            if not item_name:
                # Log all child rows for this parent for debugging
                all_rows = frappe.db.get_all(
                    "Statement Generation History Item",
                    filters={"parent": history_id},
                    fields=["name", "pdf_generator_log"]
                )
                frappe.logger("pdf_generation").warning(f"No exact match for log_id={log_id} in parent={history_id}. All child rows: {all_rows}")
                # Try fallback: case-insensitive and trimmed match
                for row in all_rows:
                    if row["pdf_generator_log"] and row["pdf_generator_log"].strip().lower() == log_id.strip().lower():
                        item_name = row["name"]
                        frappe.logger("pdf_generation").info(f"Fallback match found: {row}")
                        break
            if not item_name:
                # Try to auto-link the orphaned log if it exists
                try:
                    log_doc = frappe.get_doc("PDF Generator Log", log_id)
                except Exception as e:
                    frappe.logger("pdf_generation").error(f"Auto-link failed: PDF Generator Log {log_id} not found: {e}")
                    frappe.log_error(message=f"Auto-link failed: PDF Generator Log {log_id} not found: {e}", title="Statement Generation History")
                    return
                if log_doc:
                    parent_doc = frappe.get_doc("Statement Generation History", history_id)
                    parent_doc.append("pdf_generator_logs", {
                        "pdf_generator_log": log_id,
                        "party_name": getattr(log_doc, "party_name", None),
                        "status": getattr(log_doc, "status", "Queued"),
                        "pdf_file": getattr(log_doc, "pdf_file", None),
                        "error_message": getattr(log_doc, "error_message", None),
                        "whatsapp_status": getattr(log_doc, "whatsapp_status", None),
                        "whatsapp_message_id": getattr(log_doc, "whatsapp_message_id", None),
                    })
                    parent_doc.save(ignore_permissions=True)
                    frappe.db.commit()
                    # Try to get the item_name again
                    item_name = frappe.db.get_value(
                        "Statement Generation History Item",
                        {"parent": history_id, "pdf_generator_log": log_id},
                        "name",
                    )
            if not item_name:
                # Only log error, do not raise or propagate
                msg = f"[Non-blocking] Could not find or auto-link PDF log {log_id} in history {history_id} after fallback. All child rows: {all_rows}"
                frappe.logger("pdf_generation").error(msg)
                frappe.log_error(message=msg, title="Statement Generation History")
                return  # Suppress error
            # Direct update on child row to avoid parent timestamp conflicts
            frappe.db.set_value("Statement Generation History Item", item_name, update_fields)
            frappe.db.commit()
            return
        except Exception as e:
            retry_count += 1
            err = str(e)
            if retry_count < max_retries:
                time.sleep(0.5)
                continue
            frappe.logger("pdf_generation").error(f"Failed to update history item status after {retry_count} retries: {err}")
            frappe.log_error(message=f"Failed to update history item status after {retry_count} retries: {err}", title="Statement Generation History")
            break

def update_history_summary_counts_safe(history_id):
    """Recompute summary counts using SQL and update parent via set_value to avoid conflicts"""
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
            err = str(e)
            if retry_count < max_retries:
                time.sleep(0.5)
                continue
            frappe.log_error(message=f"Failed to update history summary counts after {retry_count} retries: {err}", title="Statement Generation History")
            break

@frappe.whitelist() 
def get_history_details(history_id):
    """NEW - Get detailed information for a specific Statement Generation History"""
    try:
        history_doc = frappe.get_doc("Statement Generation History", history_id)
        
        # Get updated WhatsApp statuses
        for item in history_doc.pdf_generator_logs:
            if item.whatsapp_message_id:
                current_status = frappe.db.get_value("WhatsApp Message Log", item.whatsapp_message_id, "status")
                if current_status and current_status != item.whatsapp_status:
                    item.whatsapp_status = current_status
        
        # Save any status updates
        history_doc.save(ignore_permissions=True)
        
        return {
            "history": history_doc.as_dict(),
            "logs": [item.as_dict() for item in history_doc.pdf_generator_logs]
        }
        
    except Exception as e:
        frappe.log_error(message=f"Error getting history details: {str(e)}", title="Statement Generation History")
        return {"error": str(e)}

@frappe.whitelist()
def send_all_whatsapp(history_id=None, filters=None):
    """NEW - Send WhatsApp messages to all completed parties in a history or based on filters"""
    try:
        if history_id:
            # Send to all parties in specific history
            history_doc = frappe.get_doc("Statement Generation History", history_id)
            
            success_count = 0
            error_count = 0
            
            for item in history_doc.pdf_generator_logs:
                if item.status == "Completed" and item.pdf_file and (not item.whatsapp_status or item.whatsapp_status == "Not Created"):
                    result = send_whatsapp_for_party(item.pdf_generator_log)
                    if result.get("success"):
                        success_count += 1
                    else:
                        error_count += 1
            
            return {
                "success": f"WhatsApp sent: {success_count}, Failed: {error_count}",
                "success_count": success_count,
                "error_count": error_count
            }
        
        elif filters:
            # Send based on filters (legacy support)
            if isinstance(filters, str):
                filters = json.loads(filters)
            
            # Get all completed PDFs based on filters
            conditions = {}
            if filters.get("company"):
                conditions["company"] = filters.get("company")
            if filters.get("party_type"):
                conditions["party_type"] = filters.get("party_type")
            if filters.get("from_date"):
                conditions["from_date"] = filters.get("from_date")
            if filters.get("to_date"):
                conditions["to_date"] = filters.get("to_date")
            
            conditions["status"] = "Completed"
            conditions["whatsapp_sent"] = 0
            
            completed_logs = frappe.get_all("PDF Generator Log", filters=conditions, pluck="name")
            
            success_count = 0
            error_count = 0
            
            for log_id in completed_logs:
                result = send_whatsapp_for_party(log_id)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
            
            return {
                "success": f"WhatsApp sent: {success_count}, Failed: {error_count}",
                "success_count": success_count,
                "error_count": error_count
            }
        
        else:
            return {"error": "Either history_id or filters must be provided"}
            
    except Exception as e:
        frappe.log_error(message=f"Error in send_all_whatsapp: {str(e)}", title="WhatsApp Messaging")
        return {"error": str(e)}

@frappe.whitelist()
def queue_whatsapp_for_party(log_id):
    """Queue WhatsApp sending for a specific PDF Generator Log to avoid UI blocking"""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)
        if log_doc.status != "Completed" or not log_doc.pdf_file:
            return {"error": "PDF not ready for this party"}
        if log_doc.whatsapp_sent or log_doc.whatsapp_status in ("Sent", "Delivered"):
            return {"error": "WhatsApp message already sent for this party"}

        # Mark history child whatsapp_status as Queued immediately (if linked)
        if hasattr(log_doc, 'statement_generation_history') and log_doc.statement_generation_history:
            update_history_item_status_safe(
                log_doc.statement_generation_history,
                log_id,
                log_doc.status,
                whatsapp_status="Queued",
            )

        # Enqueue background job to actually create WhatsApp Messages
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
        return {"error": str(e)}


def _send_whatsapp_job(log_id):
    """Background worker: send WhatsApp for a specific log by reusing existing logic"""
    try:
        # Reuse existing function to create message and update history
        send_whatsapp_for_party(log_id)
    except Exception as e:
        # Best-effort logging
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
        if delay_seconds < 4:
            delay_seconds = 4
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
            # Get logs from history details (child table holds pdf_generator_log)
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
        
        # Process in one background job with delay between messages (>=4s)
        delay_cfg = frappe.db.get_single_value("Agriculture Settings", "delay_between_messages_seconds") or 4
        try:
            delay_seconds = int(delay_cfg)
        except Exception:
            delay_seconds = 4
        if delay_seconds < 4:
            delay_seconds = 4

        job_label = history_id or ("bulk-" + str(len(ids)))
        frappe.enqueue(
            method=process_whatsapp_bulk,
            log_ids=ids,
            delay_seconds=delay_seconds,
            job_name=f"WA-Bulk-{job_label}",
            timeout=3600,
            is_async=True,
        )
        return {
            "success": f"Queued {len(ids)} WhatsApp messages (rate-limited every {delay_seconds}s)",
            "queued": len(ids),
            "skipped": skipped,
        }
    except Exception as e:
        return {"error": str(e)}

@frappe.whitelist()
def retry_all_failed_pdfs(history_id):
    """
    Retry all failed PDF Generator Logs for a given Statement Generation History.
    Skips missing logs and updates the history summary. Returns a summary dict.
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
                    # Queue new background job
                    safe_party_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30]
                    frappe.enqueue(
                        method=generate_single_party_pdf,
                        log_id=log_id,
                        party_name=log_doc.party_name,
                        history_id=history_id,
                        job_name=f"PDF-RetryAll-{safe_party_name}",
                        timeout=300,
                        is_async=True
                    )
                    retried += 1
                else:
                    skipped += 1
            except Exception as e:
                # Log and skip missing or broken logs
                frappe.log_error(message=f"Retry all failed: Could not process log {log_id}: {str(e)[:200]}", title="PDF Retry All Failed")
                skipped += 1
        # Update summary counts
        update_history_summary_counts_safe(history_id)
        frappe.db.commit()
        return {"success": f"Retried {retried} failed jobs, skipped {skipped}", "retried": retried, "skipped": skipped}
    except Exception as e:
        frappe.log_error(message=f"Retry all failed error: {str(e)[:200]}", title="PDF Retry All Failed")
        return {"error": str(e)[:200]}

@frappe.whitelist()
def repair_orphaned_pdf_logs():
    """
    Scan all Statement Generation History records and auto-link any orphaned PDF Generator Logs
    that are not present in the child table but are linked by statement_generation_history field.
    Returns a summary of repairs made.
    """
    repaired = 0
    checked = 0
    try:
        histories = frappe.get_all("Statement Generation History", pluck="name")
        for history_id in histories:
            # Get all logs that reference this history
            log_ids = frappe.get_all("PDF Generator Log", filters={"statement_generation_history": history_id}, pluck="name")
            parent_doc = frappe.get_doc("Statement Generation History", history_id)
            child_log_ids = set([item.pdf_generator_log for item in parent_doc.pdf_generator_logs])
            for log_id in log_ids:
                checked += 1
                if log_id not in child_log_ids:
                    log_doc = frappe.get_doc("PDF Generator Log", log_id)
                    parent_doc.append("pdf_generator_logs", {
                        "pdf_generator_log": log_id,
                        "party_name": getattr(log_doc, "party_name", None),
                        "status": getattr(log_doc, "status", "Queued"),
                        "pdf_file": getattr(log_doc, "pdf_file", None),
                        "error_message": getattr(log_doc, "error_message", None),
                        "whatsapp_status": getattr(log_doc, "whatsapp_status", None),
                        "whatsapp_message_id": getattr(log_doc, "whatsapp_message_id", None),
                    })
                    repaired += 1
            if repaired:
                parent_doc.save(ignore_permissions=True)
                frappe.db.commit()
        return {"success": True, "repaired": repaired, "checked": checked}
    except Exception as e:
        frappe.log_error(message=f"Error in repair_orphaned_pdf_logs: {str(e)[:200]}", title="Repair Orphaned PDF Logs")
        return {"error": str(e)[:200], "repaired": repaired, "checked": checked}

@frappe.whitelist()
def check_and_repair_queued_logs():
    """
    Scan all PDF Generator Logs stuck in 'Queued', print why, and auto-repair if possible.
    Returns a list of dicts with log_id, issues, and actions.
    """
    results = []
    logs = frappe.get_all("PDF Generator Log", filters={"status": "Queued"}, fields=["name", "filters_json", "party_name", "company", "statement_generation_history"])
    for log in logs:
        issues = []
        actions = []
        log_id = log.name
        # Check required fields
        if not log.filters_json:
            issues.append("Missing filters_json")
        if not log.party_name:
            issues.append("Missing party_name")
        if not log.company:
            issues.append("Missing company")
        # Check if linked to a history
        history_id = log.statement_generation_history
        if not history_id:
            issues.append("Not linked to Statement Generation History")
        else:
            # Check if present in child table
            parent_doc = frappe.get_doc("Statement Generation History", history_id)
            child_log_ids = set([item.pdf_generator_log for item in parent_doc.pdf_generator_logs])
            if log_id not in child_log_ids:
                issues.append("Not present in history child table")
                # Try to auto-link
                try:
                    parent_doc.append("pdf_generator_logs", {
                        "pdf_generator_log": log_id,
                        "party_name": log.party_name,
                        "status": "Queued",
                        "pdf_file": None,
                        "error_message": None,
                        "whatsapp_status": None,
                        "whatsapp_message_id": None,
                    })
                    parent_doc.save(ignore_permissions=True)
                    frappe.db.commit()
                    actions.append("Auto-linked to history child table")
                except Exception as e:
                    issues.append(f"Failed to auto-link: {str(e)[:100]}")
        # Optionally, try to re-queue if all data is present
        if not issues or (len(issues) == 1 and issues[0].startswith("Not present in history child table")):
            try:
                # Re-queue the job
                from frappe.utils import now
                safe_party_name = frappe.scrub(log.party_name or log_id).replace("_", "-")[:30]
                frappe.enqueue(
                    method=generate_single_party_pdf,
                    log_id=log_id,
                    party_name=log.party_name,
                    history_id=history_id,
                    job_name=f"PDF-Requeue-{safe_party_name}",
                    timeout=300,
                    is_async=True
                )
                actions.append("Re-queued background job")
            except Exception as e:
                issues.append(f"Failed to re-queue: {str(e)[:100]}")
        results.append({
            "log_id": log_id,
            "issues": issues,
            "actions": actions
        })
    return results

# Add more logging to generate_single_party_pdf

def generate_single_party_pdf(log_id, party_name=None, history_id=None):
    """UPDATED: Generate PDF for a single party (background job) with extra logging"""
    import logging
    logger = frappe.logger("pdf_generation")
    log_doc = None
    actual_party_name = party_name
    logger.info(f"[START] generate_single_party_pdf: log_id={log_id}, party_name={party_name}, history_id={history_id}")
    try:
        # Try to get the log document with better error handling
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
            logger.info(f"Loaded PDF Generator Log: {log_id}")
        except frappe.DoesNotExistError:
            logger.error(f"PDF Generator Log {log_id} does not exist")
            if history_id:
                update_history_item_status(history_id, log_id, "Failed", error_message="PDF log not found (possibly deleted)")
            return
        except Exception as doc_error:
            logger.error(f"Error loading PDF Generator Log {log_id}: {str(doc_error)}")
            frappe.log_error(message=f"Error loading PDF Generator Log {log_id}: {str(doc_error)}", title="PDF Generation")
            return
        # Validate that the document exists and is in correct state
        if not log_doc:
            logger.error(f"PDF Generator Log {log_id} is None after get_doc")
            frappe.log_error(message=f"PDF Generator Log {log_id} is None after get_doc", title="PDF Generation")
            return
        # Use party name from log if not provided as parameter
        if not actual_party_name:
            actual_party_name = log_doc.party_name
        if not actual_party_name:
            error_msg = "No party name available"
            logger.error(error_msg)
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            frappe.db.commit()
            return
        # Check if document is already completed or failed
        if log_doc.status in ['Completed', 'Failed']:
            logger.info(f"PDF Generator Log {log_id} already {log_doc.status}, skipping")
            return
        # Update status to processing
        log_doc.status = "Processing"
        log_doc.save(ignore_permissions=True)
        frappe.db.commit()
        logger.info(f"Set status to Processing for log {log_id}")
        # Update history child table status (with retry mechanism)
        if history_id:
            update_history_item_status_safe(history_id, log_id, "Processing")
        # Validate filters_json exists
        if not log_doc.filters_json:
            error_msg = "No filters available"
            logger.error(error_msg)
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            frappe.db.commit()
            return
        # Reconstruct filters for single party
        try:
            filters = json.loads(log_doc.filters_json)
            filters["party"] = actual_party_name
        except (json.JSONDecodeError, ValueError) as json_error:
            error_msg = f"Invalid filters JSON: {str(json_error)[:100]}"
            logger.error(error_msg)
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            log_doc.save(ignore_permissions=True)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            frappe.db.commit()
            return
        # Generate PDF using existing logic with additional validation
        logger.info(f"Generating PDF for party: {actual_party_name}")
        pdf_result = generate_single_pdf(filters, actual_party_name)
        if pdf_result.get("success"):
            log_doc.status = "Completed"
            log_doc.pdf_file = pdf_result["file_url"]
            log_doc.completion_time = now()
            logger.info(f"PDF generation completed for log {log_id}")
            if history_id:
                update_history_item_status_safe(
                    history_id, 
                    log_id, 
                    "Completed", 
                    pdf_file=pdf_result["file_url"]
                )
        elif pdf_result.get("error") and "No data found" in pdf_result.get("error", ""):
            # Special handling for no data case
            error_msg = f"No data found for party '{actual_party_name}'"
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            logger.warning(error_msg)
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
        else:
            error_msg = pdf_result.get("error", "Unknown error during PDF generation")
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            log_doc.status = "Failed"
            log_doc.error_message = error_msg
            logger.error(f"PDF generation failed for log {log_id}: {error_msg}")
            if history_id:
                update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
    except Exception as e:
        error_msg = str(e)[:200]  # Limit error message length
        logger.error(f"PDF Generation failed for {log_id}: {error_msg}")
        frappe.log_error(message=f"PDF Generation failed for {log_id}: {error_msg}", title="PDF Generation")
        # Only update log_doc if it exists and we can access it
        if log_doc:
            try:
                log_doc.status = "Failed"
                log_doc.error_message = error_msg
                if history_id:
                    update_history_item_status_safe(history_id, log_id, "Failed", error_message=error_msg)
            except Exception as update_error:
                logger.error(f"Failed to update error status for {log_id}: {str(update_error)[:200]}")
                frappe.log_error(message=f"Failed to update error status for {log_id}: {str(update_error)[:200]}", title="PDF Generation")
                return
    # Save the final status (only if log_doc exists and no exceptions occurred)
    if log_doc:
        try:
            log_doc.save(ignore_permissions=True)
            frappe.db.commit()
            logger.info(f"[END] generate_single_party_pdf: log_id={log_id}, status={log_doc.status}")
            # Update history summary counts
            if history_id:
                update_history_summary_counts_safe(history_id)
        except Exception as save_error:
            logger.error(f"Failed to save final status for {log_id}: {str(save_error)[:200]}")
            frappe.log_error(message=f"Failed to save final status for {log_id}: {str(save_error)[:200]}", title="PDF Generation")
        



def retry_all_queued_pdf_jobs():
    """
    Scheduled task: Retry all queued PDF Generator Logs every minute.
    """
    logs = frappe.get_all("PDF Generator Log", filters={"status": "Queued"}, fields=["name", "party_name", "statement_generation_history"])
    for log in logs:
        try:
            frappe.enqueue(
                method=generate_single_party_pdf,
                log_id=log["name"],
                party_name=log["party_name"],
                history_id=log["statement_generation_history"],
                job_name=f"PDF-Scheduled-Retry-{log['name']}",
                timeout=300,
                is_async=True
            )
            frappe.logger("pdf_generation").info(f"Re-queued PDF Generator Log {log['name']} via scheduled retry.")
        except Exception as e:
            frappe.logger("pdf_generation").error(f"Failed to re-queue {log['name']}: {e}")

@frappe.whitelist()
def retry_all_queued_and_failed_pdf_jobs_for_history(history_id):
    """
    Retry all queued and failed PDF Generator Logs for a given Statement Generation History.
    """
    retried = 0
    skipped = 0
    logs = frappe.get_all(
        "PDF Generator Log",
        filters={"statement_generation_history": history_id, "status": ["in", ["Queued", "Failed"]]},
        fields=["name", "party_name", "statement_generation_history"]
    )
    for log in logs:
        try:
            frappe.enqueue(
                method=generate_single_party_pdf,
                log_id=log["name"],
                party_name=log["party_name"],
                history_id=log["statement_generation_history"],
                job_name=f"PDF-Manual-Retry-{log['name']}",
                timeout=300,
                is_async=True
            )
            retried += 1
        except Exception as e:
            frappe.logger("pdf_generation").error(f"Failed to re-queue {log['name']}: {e}")
            skipped += 1
    return {"success": f"Re-queued {retried} jobs, skipped {skipped}", "retried": retried, "skipped": skipped}

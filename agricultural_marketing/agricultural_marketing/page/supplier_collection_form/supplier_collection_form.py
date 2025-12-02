import json
import os
import random

import frappe
from frappe import _, cint
from frappe.utils import getdate, flt
from frappe.utils.jinja_globals import is_rtl
from frappe.utils.pdf import get_pdf as _get_pdf
from frappe.query_builder.functions import Sum
from pypika import Case
from pypika.terms import Term
from frappe.contacts.doctype.address.address import get_company_address


@frappe.whitelist()
def execute(filters):
    data = frappe._dict()
    file_urls = []
    if isinstance(filters, str):
        filters = json.loads(filters)

    # Get Data
    data = get_data(data, filters)
    company_defaults = frappe.get_doc("Company", filters.get('company')).as_dict()
    letter_head = None
    default_letter_head = company_defaults["default_letter_head"]
    if default_letter_head:
        letter_head = frappe.get_doc("Letter Head", default_letter_head)
    else:
        company_defaults["address"] = get_company_address(company_defaults['name']).get("company_address_display")
        company_defaults["image"] = frappe.db.get_value("File", {"attached_to_name": company_defaults['name']},
                                                        "file_url")
    html_format = get_html_format(filters.get("new_layout"))
    font_size = frappe.db.get_single_value("Agriculture Settings", "font_size") or 14

    context = {
        "letter_head": letter_head,
        "company_defaults": company_defaults,
        "data": data,
        "filters": filters,
        "lang": frappe.local.lang,
        "layout_direction": "rtl" if is_rtl() else "ltr",
        "font_size": font_size
    }

    html = frappe.render_template(html_format, context)

    if filters.get("open_pdf"):
        return {"html": html}
    content = _get_pdf(html, {"orientation": "Portrait"})
    file_name = "{0}-{1}.pdf".format("supplier-collection-form", str(random.randint(1000, 9999)))
    file_doc = frappe.new_doc("File")
    file_doc.update({
        "file_name": file_name,
        "is_private": 0,
        "content": content
    })
    file_doc.save(ignore_permissions=True)

    return {
        "file_url": file_doc.file_url
    }


def get_data(data, filters):
    invoices_details = get_items_details(filters)
    payments_details = get_payments_details(filters)
    process_result(invoices_details, payments_details, data)
    data = get_party_summary(filters=filters, party_type=filters.get("party_type"), data=data)
    return data


def get_html_format(new_layout=False):
    if new_layout:
        template_filename = os.path.join("supplier_collection_form_new" + '.html')
    else:
        template_filename = os.path.join("supplier_collection_form" + '.html')
    folder = os.path.dirname(frappe.get_module("agricultural_marketing" + "." + "agricultural_marketing" +
                                               "." + "page").__file__)
    doctype_path = os.path.join(folder, "supplier_collection_form")
    paths_temp = os.path.join(doctype_path, template_filename)
    html_format = frappe.utils.get_html_format(paths_temp)
    return html_format


def get_items_details(filters):
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    items_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))

    # Determine and apply party filters based on party type
    _filters = {"is_customer": 1} if filters.get("party_type") == "Customer" else {}
    parties = get_parties(filters, _filters)
    
    if filters.get("party_type") == "Supplier":
        # For supplier filter: get records where:
        # 1. Direct supplier matches (normal supplier transactions)
        # 2. Customer name matches filtered supplier AND couple_customer = 1 (couple customer transactions)
        items_query = items_query.where(
            (invform.supplier.isin(parties)) | 
            ((invformitem.customer.isin(parties)) & (invformitem.couple_customer == 1))
        )
    else:
        # For customer filter: use customer field
        _field = invformitem.customer
        items_query = items_query.where(_field.isin(parties))

    # validate and apply dates filters
    items_query = validate_and_apply_date_filters(filters, items_query, invform)

    # Filter for submitted (docstatus 1) invoice forms
    if filters.get("consider_draft"):
        items_query = items_query.where(invform.docstatus.isin([0, 1]))
    else:
        items_query = items_query.where(invform.docstatus == 1)

    # Select relative fields based on party type
    items_query = select_fields_for_invoices(filters, items_query, invform, invformitem)

    result = items_query.orderby(invform.posting_date).orderby(invform.name).orderby(
        invformitem.item_name).run(as_dict=True)

    # Post-process results to ensure correct party assignment
    if filters.get("party_type") == "Supplier":
        for row in result:
            # If this is a couple customer transaction (customer name matches filter and couple_customer = 1)
            if (row.get("customer") in parties and 
                row.get("couple_customer") == 1):
                row["party"] = row.get("customer")  # Use customer name as party (which matches supplier filter)
            # If supplier directly matches our filter
            elif row.get("supplier") and row.get("supplier") in parties:
                row["party"] = row.get("supplier")  # Use supplier name as party
    else:
        # For customer filter, party is just the customer
        for row in result:
            row["party"] = row.get("customer")

    if not result:
        for party in parties:
            result.append({"party": party})
    else:
        parties_with_data = [res['party'] for res in result]
        for party in parties:
            if party not in parties_with_data:
                result.append({"party": party})
    return result

def get_payments_details(filters):
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
    return result


def get_party_summary(filters, party_type, data):
    def append_summary(doctype, reference_id, date, qty, price, statement, debit, credit):
        nonlocal last_balance

        # For Invoice Form - swap columns for customer only (match collection_form logic)
        if doctype == "Invoice Form" and party_type == "Customer":
            final_debit = flt(credit, 2) or str(credit)
            final_credit = flt(debit, 2) or str(debit)
        # For payments and drafts, keep the mapping as passed in
        elif doctype in ["Payment Entry", "Payments Receipts Reference"]:
            final_debit = flt(debit, 2) or str(debit)
            final_credit = flt(credit, 2) or str(credit)
        else:
            final_debit = flt(debit, 2) or str(debit)
            final_credit = flt(credit, 2) or str(credit)

        final_data[party].append({
            "doctype": doctype,
            "reference_id": reference_id,
            "date": date,
            "qty": cint(qty) if hide_decimal else qty,
            "price": price,
            "statement": statement,
            "debit": final_debit,
            "credit": final_credit
        })

    final_data = {}
    hide_decimal = True if filters.get("hide_decimal") else False
    from_date = filters.get('from_date')

    for party, party_data in data.items():
        debit, credit, last_balance = 0, 0, 0
        total_debit, total_credit = 0, 0
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
            draft_items = get_draft_total_items(filters, party) or {"debit": 0, "credit": 0}
            total_payments = get_draft_total_payments(filters, party) or 0
            frappe.errprint("draft_items: " + str(draft_items))
            frappe.errprint("total_payments: " + str(total_payments))
            if filters.get("party_type") == "Supplier":
                total_draft_commission = get_draft_total_commission(filters, party) or 0
                frappe.errprint("total_draft_commission: " + str(total_draft_commission))
                debit += total_payments + draft_items.get("debit", 0) + total_draft_commission
                credit += draft_items.get("credit", 0)
            else:
                debit += draft_items.get("debit", 0)
                credit += total_payments + draft_items.get("credit", 0)


        last_balance = debit - credit
        if abs(debit) > abs(credit):
            debit = abs(last_balance)
            credit = 0
        else:
            credit = abs(last_balance)
            debit = 0

        # Append Opening
        final_data.setdefault(party, []).append({
            "doctype": "",
            "reference_id": _("Opening Balance"),
            "qty": "",
            "price": "",
            "statement": "",
            "debit": flt(debit, 2) or "0",
            "credit": flt(credit, 2) or "0"
        })

        for d in party_data:
            if d.get("doctype") == "Invoice Form":
                commission_with_taxes = 0
                if filters.get("party_type") == "Supplier" and d.commission:
                    total_taxes = (d.commission * get_tax_rate()) / 100
                    commission_with_taxes = d.commission + total_taxes

                # Special handling: For supplier reports, treat only rows where the current
                # party equals the customer AND the item is marked as coupled as pure DEBIT
                # with no commission credit. Do not affect normal supplier invoices.
                if (
                    filters.get("party_type") == "Supplier"
                    and d.get("customer") == party
                    and bool(d.get("couple_customer"))
                ):
                    append_summary(d.doctype, d.reference_id, d.date, d.qty, d.price, d.item_name,
                                   abs(flt(d.total, 2)), 0)
                    total_debit += abs(flt(d.total, 2))
                    continue

                # Match collection_form logic: pass commission as debit and total as credit,
                # and let append_summary handle swap for customers.
                append_summary(d.doctype, d.reference_id, d.date, d.qty, d.price, d.item_name,
                               commission_with_taxes, d.total)

                # Update totals (match collection_form logic)
                if party_type == "Customer":
                    total_debit += d.total
                    total_credit += commission_with_taxes
                else:
                    total_credit += d.total
                    total_debit += commission_with_taxes

            elif d.get("doctype") in ["Payment Entry", "Payments Receipts Reference"]:
                statement = f"{_(d.mop)} - {d.remarks}" if d.remarks else f"{_(d.mop)}"

                # Consistent approach for both customer and supplier:
                # - "Receive" -> CREDIT
                # - "Pay" -> DEBIT
                if d.payment_type == "Receive":
                    append_summary(d.doctype, d.reference_id, d.date, "", "", statement, 0, abs(flt(d.paid_amount, 2)))
                    total_credit += abs(flt(d.paid_amount, 2))
                else:  # "Pay"
                    append_summary(d.doctype, d.reference_id, d.date, "", "", statement, abs(flt(d.paid_amount, 2)), 0)
                    total_debit += abs(flt(d.paid_amount, 2))

        # Calculate and append closing
        total_debit += debit
        total_credit += credit

        # Append totals row (keep existing bold formatting)
        final_data[party].append({
            "doctype": "",
            "reference_id": _("Total"),
            "qty": "",
            "price": "",
            "statement": f"<b> {flt(total_debit - total_credit, 2) or '0'} </b>",
            "debit": f"<b> {flt(total_debit, 2) or '0'} </b>",
            "credit": f"<b> {flt(total_credit, 2) or '0'} </b>"
        })

        if filters.get("ignore_zero_transactions") and (total_debit - total_credit) == 0:
            del final_data[party]

    return final_data

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


def select_fields_for_invoices(filters, items_query, invform, invformitem):
    items_query = items_query.select(
        Term.wrap_constant("Invoice Form").as_('doctype'),
        invform.supplier.as_("supplier"),  # Always select supplier
        invformitem.customer.as_("party"),  # Default party field
        invform.name.as_("reference_id"),
        invform.posting_date.as_("date"), 
        invformitem.qty, 
        invformitem.price,
        invformitem.total, 
        invformitem.item_name,
        invform.is_return,
        invformitem.customer,
        invformitem.couple_customer
    )
    
    if filters.get("party_type") == "Supplier":
        items_query = items_query.select(invformitem.commission)

    return items_query
    
def process_result(invoices_details, payments_details, data):
    result = invoices_details + payments_details
    result = sorted(result, key=lambda x: x['party'])
    for row in result:
        party = row.pop("party")  # Extract and remove party from the row
        data.setdefault(party, []).append(row)


def select_fields_for_payment(filters, payments_query, entry):
    payments_query = payments_query.select(Term.wrap_constant("Payment Entry").as_("doctype"),
                                           entry.party_type, entry.party, entry.name.as_("reference_id"),
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
    for row in result:
        party = row.pop("party", None)
        if party:
            if party not in data:
                data[party] = {"payments": []}
            data.setdefault(party, {}).setdefault("payments", []).append(row)


def get_tax_rate():
    default_tax_template = frappe.db.get_single_value("Agriculture Settings", "default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    tax_rate = frappe.db.get_value("Sales Taxes and Charges",
                                   {"parent": default_tax_template}, "rate") or 0
    return tax_rate


def get_draft_total_items(filters, party):
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")
    items_query = frappe.qb.from_(invform).left_join(invformitem).on(
        invformitem.parent == invform.name).where(invform.company == filters.get('company'))

    # Apply same logic as main query for couple customers
    if filters.get("party_type") == "Supplier":
        items_query = items_query.where(
            (invform.supplier == party) | 
            ((invformitem.customer == party) & (invformitem.couple_customer == 1))
        )
    else:
        items_query = items_query.where(invformitem.customer == party)

    items_query = items_query.where(invform.docstatus == 0).where(invform.posting_date.lt(filters.get("from_date")))

    result = items_query.select(
        invform.supplier.as_("supplier"),
        invformitem.customer.as_("customer"),
        invformitem.couple_customer.as_("couple_customer"),
        invformitem.total
    ).run(as_dict=True)

    totals = {"debit": 0, "credit": 0}
    if not result:
        return totals

    party_type = filters.get("party_type")
    if party_type == "Supplier":
        for row in result:
            row_total = flt(row.get("total") or 0)
            if not row_total:
                continue
            if row.get("customer") == party and bool(row.get("couple_customer")):
                totals["debit"] += abs(row_total)
            elif row.get("supplier") == party:
                totals["credit"] += row_total
    else:
        totals["debit"] = sum([flt(re.get("total") or 0) for re in result]) or 0

    return totals

def get_draft_total_commission(filters, party):
    invform = frappe.qb.DocType("Invoice Form")
    
    query = frappe.qb.from_(invform).where(
        invform.company == filters.get('company')
    ).where(
        invform.docstatus == 0
    ).where(
        invform.posting_date.lt(filters.get("from_date"))
    ).where(
        invform.supplier == party
    )
    
    result = query.select(
        Sum(invform.total_commissions_and_taxes).as_("commission")
    ).run(as_dict=True)

    return result[0].get("commission") or 0

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
        entry.name.as_("reference_id"),
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
# Copyright (c) 2025, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Sum


def execute(filters=None):
    columns, data = [], []
    columns = get_columns()
    trial_balance_settings = frappe.get_single("Trial Balance Settings")
    data = get_data(filters, trial_balance_settings)
    data = append_totals_row(data)
    return columns, data


def get_data(filters, trial_balance_settings):
    result = []
    gl_filters = {
        "company": filters.get("company"),
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date")
    }

    get_child_data_from_gl_entries(gl_filters, trial_balance_settings, "cash_section", result)
    get_customers_section_data(gl_filters, filters, trial_balance_settings, "customers_section", result)
    get_suppliers_section_data(gl_filters, filters, trial_balance_settings, "suppliers_section", result)
    get_child_data_from_gl_entries(gl_filters, trial_balance_settings, "share_capital_section", result)
    get_taxes_section_data(gl_filters, filters, trial_balance_settings, "taxes_section", result)
    get_income_section_data(gl_filters, filters, trial_balance_settings, "income_section", result)
    get_child_data_from_gl_entries(gl_filters, trial_balance_settings, "expense_section", result)

    return result


def get_opening_balances_from_gl(gl_filters):
    opening_debit, opening_credit = 0, 0
    q = """ SELECT 
                name, debit, credit, posting_date 
            FROM 
                `tabGL Entry` 
            WHERE 
                account=%(account)s 
            AND 
                is_cancelled = 0 
            AND 
                (posting_date < %(from_date)s OR is_opening = 'Yes') 
    """

    gl_entries = frappe.db.sql(q, gl_filters, as_dict=True)

    for gl in gl_entries:
        opening_debit += gl.debit
        opening_credit += gl.credit

    return opening_debit, opening_credit


def get_duration_balances_from_gl(gl_filters):
    debit, credit = 0, 0
    q = """ SELECT 
                name, debit, credit, posting_date  
            FROM  
                `tabGL Entry` 
            WHERE 
                account=%(account)s 
            AND 
                is_cancelled = 0  
            AND 
                (posting_date >= %(from_date)s AND posting_date <= %(to_date)s)
            AND
                is_opening = 'No'
    """
    gl_entries = frappe.db.sql(q, gl_filters, as_dict=True)

    for gl in gl_entries:
        debit += gl.debit
        credit += gl.credit

    return debit, credit


def get_child_data_from_gl_entries(gl_filters, trial_balance_settings, child, result):
    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = {
                "title": frappe.bold(row.get("title")),
                "opening_debit": 0,
                "opening_credit": 0,
                "debit": 0,
                "credit": 0,
                "closing_debit": 0,
                "closing_credit": 0,
                "is_parent": 1
            }
        else:
            gl_filters.update({
                "account": row.get("account")
            })
            # Get opening
            opening_debit, opening_credit = get_opening_balances_from_gl(gl_filters)
            # Get duration debit and credit
            debit, credit = get_duration_balances_from_gl(gl_filters)
            # Calculate closing balances
            closing_debit, closing_credit = calculate_closing_balance(opening_debit, debit, opening_credit, credit)

            section_data[row.get("title")] = {
                "title": row.get("title"),
                "opening_debit": opening_debit,
                "opening_credit": opening_credit,
                "debit": debit,
                "credit": credit,
                "closing_debit": closing_debit,
                "closing_credit": closing_credit,
                "is_parent": 0
            }

            if row.get("parent1"):
                parent_record = section_data[row.get("parent1")]
                parent_record.update({
                    "opening_debit": parent_record["opening_debit"] + opening_debit,
                    "opening_credit": parent_record["opening_credit"] + opening_credit,
                    "debit": parent_record["debit"] + debit,
                    "credit": parent_record["credit"] + credit,
                    "closing_debit": parent_record["closing_debit"] + closing_debit,
                    "closing_credit": parent_record["closing_credit"] + closing_credit,
                })

    for section in section_data:
        result.append(section_data[section])


def get_customers_section_data(gl_filters, filters, trial_balance_settings, child, result):
    def get_customers_draft_balance():
        invfrm = frappe.qb.DocType("Invoice Form")
        invfrmitem = frappe.qb.DocType("Invoice Form Item")
        # get opening debit
        opening_debit_query = frappe.qb.from_(invfrm).join(invfrmitem).on(invfrmitem.parent == invfrm.name).select(
            Sum(invfrmitem.total).as_("total")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.lt(filters.get("from_date"))).where(
            invfrmitem.customer.isin(customers)).where(invfrm.docstatus == 0)

        total_draft_opening_debit = opening_debit_query.run(as_dict=True)[0]["total"] or 0

        # get duration debit
        duration_debit_query = frappe.qb.from_(invfrm).join(invfrmitem).on(invfrmitem.parent == invfrm.name).select(
            Sum(invfrmitem.total).as_("total")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.gte(filters.get("from_date"))).where(
            invfrm.posting_date.lte(filters.get("to_date"))).where(
            invfrmitem.customer.isin(customers)).where(invfrm.docstatus == 0)

        total_draft_duration_debit = duration_debit_query.run(as_dict=True)[0]["total"] or 0

        return total_draft_opening_debit, total_draft_duration_debit

    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = {
                "title": frappe.bold(row.get("title")),
                "opening_debit": 0,
                "opening_credit": 0,
                "debit": 0,
                "credit": 0,
                "closing_debit": 0,
                "closing_credit": 0,
                "is_parent": 1
            }
        else:
            gl_filters.update({
                "account": row.get("account")
            })
            # Get opening
            opening_debit, opening_credit = get_opening_balances_from_gl(gl_filters)
            # Get duration debit and credit
            debit, credit = get_duration_balances_from_gl(gl_filters)
            # Check draft
            if filters.get("consider_drafts"):
                if row.get("customer_group"):
                    customers = frappe.get_all("Customer",
                                               {"customer_group": row.get("customer_group"), "is_customer": 1},
                                               pluck="name")
                    # get draft opening and duration
                    draft_opening_debit, draft_duration_debit = get_customers_draft_balance()
                    opening_debit += draft_opening_debit
                    debit += draft_duration_debit

            # Calculate closing balances
            closing_debit, closing_credit = calculate_closing_balance(opening_debit, debit, opening_credit, credit)

            section_data[row.get("title")] = {
                "title": row.get("title"),
                "opening_debit": opening_debit,
                "opening_credit": opening_credit,
                "debit": debit,
                "credit": credit,
                "closing_debit": closing_debit,
                "closing_credit": closing_credit,
                "is_parent": 0
            }

            if row.get("parent1"):
                parent_record = section_data[row.get("parent1")]
                parent_record.update({
                    "opening_debit": parent_record["opening_debit"] + opening_debit,
                    "opening_credit": parent_record["opening_credit"] + opening_credit,
                    "debit": parent_record["debit"] + debit,
                    "credit": parent_record["credit"] + credit,
                    "closing_debit": parent_record["closing_debit"] + closing_debit,
                    "closing_credit": parent_record["closing_credit"] + closing_credit,
                })
    for section in section_data:
        result.append(section_data[section])


def get_suppliers_section_data(gl_filters, filters, trial_balance_settings, child, result):
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmitem = frappe.qb.DocType("Invoice Form Item")

    def get_suppliers_draft_opening_balance():
        # get opening credit
        opening_credit_query = frappe.qb.from_(invfrm).join(invfrmitem).on(invfrmitem.parent == invfrm.name).select(
            Sum(invfrmitem.total).as_("total")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.lt(filters.get("from_date"))).where(
            invfrm.supplier.isin(suppliers)).where(invfrm.docstatus == 0)

        total_draft_opening_credit = opening_credit_query.run(as_dict=True)[0]["total"] or 0

        # get opening debit
        opening_debit_query = frappe.qb.from_(invfrm).select(
            Sum(invfrm.total_commissions_and_taxes).as_("total_commissions_and_taxes")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.lt(filters.get("from_date"))).where(
            invfrm.supplier.isin(suppliers)).where(invfrm.docstatus == 0)

        total_draft_opening_debit = opening_debit_query.run(as_dict=True)[0]["total_commissions_and_taxes"] or 0

        return total_draft_opening_credit, total_draft_opening_debit

    def get_suppliers_draft_duration_balance():
        # get duration credit
        duration_credit_query = frappe.qb.from_(invfrm).join(invfrmitem).on(invfrmitem.parent == invfrm.name).select(
            Sum(invfrmitem.total).as_("total")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.gte(filters.get("from_date"))).where(
            invfrm.posting_date.lte(filters.get("to_date"))).where(
            invfrm.supplier.isin(suppliers)).where(invfrm.docstatus == 0)

        total_duration_credit = duration_credit_query.run(as_dict=True)[0]["total"] or 0

        # get duration debit
        duration_debit_query = frappe.qb.from_(invfrm).select(
            Sum(invfrm.total_commissions_and_taxes).as_("total_commissions_and_taxes")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.gte(filters.get("from_date"))).where(
            invfrm.posting_date.lte(filters.get("to_date"))).where(
            invfrm.supplier.isin(suppliers)).where(invfrm.docstatus == 0)

        total_duration_debit = duration_debit_query.run(as_dict=True)[0]["total_commissions_and_taxes"] or 0

        return total_duration_credit, total_duration_debit

    # get all suppliers under this group
    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = {
                "title": frappe.bold(row.get("title")),
                "opening_debit": 0,
                "opening_credit": 0,
                "debit": 0,
                "credit": 0,
                "closing_debit": 0,
                "closing_credit": 0,
                "is_parent": 1
            }
        else:
            gl_filters.update({
                "account": row.get("account")
            })
            # Get opening
            opening_debit, opening_credit = get_opening_balances_from_gl(gl_filters)
            # Get duration debit and credit
            debit, credit = get_duration_balances_from_gl(gl_filters)

            if filters.get("consider_drafts"):
                if row.get("supplier_group"):
                    suppliers = frappe.get_all("Supplier", {"supplier_group": row.get("supplier_group")}, pluck="name")
                    # get draft opening and duration
                    draft_opening_credit, draft_opening_debit = get_suppliers_draft_opening_balance()
                    opening_credit += draft_opening_credit
                    opening_debit += draft_opening_debit

                    draft_duration_credit, draft_duration_debit = get_suppliers_draft_duration_balance()
                    credit += draft_duration_credit
                    debit += draft_duration_debit

            # Calculate closing balance
            closing_debit, closing_credit = calculate_closing_balance(opening_debit, debit, opening_credit, credit)

            section_data[row.get("title")] = {
                "title": row.get("title"),
                "opening_debit": opening_debit,
                "opening_credit": opening_credit,
                "debit": debit,
                "credit": credit,
                "closing_debit": closing_debit,
                "closing_credit": closing_credit,
                "is_parent": 0
            }

            if row.get("parent1"):
                parent_record = section_data[row.get("parent1")]
                parent_record.update({
                    "opening_debit": parent_record["opening_debit"] + opening_debit,
                    "opening_credit": parent_record["opening_credit"] + opening_credit,
                    "debit": parent_record["debit"] + debit,
                    "credit": parent_record["credit"] + credit,
                    "closing_debit": parent_record["closing_debit"] + closing_debit,
                    "closing_credit": parent_record["closing_credit"] + closing_credit,
                })
    for section in section_data:
        result.append(section_data[section])


def get_taxes_section_data(gl_filters, filters, trial_balance_settings, child, result):
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmcom = frappe.qb.DocType("Invoice Form Commission")
    docstatuses = [1]
    if filters.get("consider_drafts"):
        docstatuses.append(0)

    def get_taxes_opening_balance():
        commission = frappe.qb.from_(invfrm).join(invfrmcom).on(invfrmcom.parent == invfrm.name).select(
            ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.lt(filters.get("from_date"))).where(invfrm.docstatus.isin(docstatuses)).run(
            as_dict=True)

        total_commission = sum([com["total_commission"] for com in commission])
        opening_credit_from_invoices = (total_commission * get_tax_rate()) / 100
        return 0, opening_credit_from_invoices

    def get_taxes_duration_balance():
        commission = frappe.qb.from_(invfrm).join(invfrmcom).on(invfrmcom.parent == invfrm.name).select(
            ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission")).where(
            invfrm.company == filters.get("company")).where(
            invfrm.posting_date.gte(filters.get("from_date"))).where(
            invfrm.posting_date.lte(filters.get("to_date"))).where(invfrm.docstatus.isin(docstatuses)).run(as_dict=True)

        total_commission = sum([com["total_commission"] for com in commission])
        duration_credit_from_invoices = (total_commission * get_tax_rate()) / 100
        return 0, duration_credit_from_invoices

    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = {
                "title": frappe.bold(row.get("title")),
                "opening_debit": 0,
                "opening_credit": 0,
                "debit": 0,
                "credit": 0,
                "closing_debit": 0,
                "closing_credit": 0,
                "is_parent": 1
            }
        else:
            gl_filters.update({
                "account": row.get("account")
            })
            # Get opening
            opening_debit, opening_credit = get_opening_balances_from_gl(gl_filters)
            # Get duration debit and credit
            debit, credit = get_duration_balances_from_gl(gl_filters)

            invoices_opening_debit, invoices_opening_credit = get_taxes_opening_balance()
            opening_debit += invoices_opening_debit
            opening_credit += invoices_opening_credit

            invoices_duration_debit, invoices_duration_credit = get_taxes_duration_balance()
            debit += invoices_duration_debit
            credit += invoices_duration_credit

            # Calculate closing balance
            closing_debit, closing_credit = calculate_closing_balance(opening_debit, debit, opening_credit, credit)

            section_data[row.get("title")] = {
                "title": row.get("title"),
                "opening_debit": opening_debit,
                "opening_credit": opening_credit,
                "debit": debit,
                "credit": credit,
                "closing_debit": closing_debit,
                "closing_credit": closing_credit,
                "is_parent": 0
            }

            if row.get("parent1"):
                parent_record = section_data[row.get("parent1")]
                parent_record.update({
                    "opening_debit": parent_record["opening_debit"] + opening_debit,
                    "opening_credit": parent_record["opening_credit"] + opening_credit,
                    "debit": parent_record["debit"] + debit,
                    "credit": parent_record["credit"] + credit,
                    "closing_debit": parent_record["closing_debit"] + closing_debit,
                    "closing_credit": parent_record["closing_credit"] + closing_credit,
                })
    for section in section_data:
        result.append(section_data[section])


def get_income_section_data(gl_filters, filters, trial_balance_settings, child, result):
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmcom = frappe.qb.DocType("Invoice Form Commission")
    docstatuses = [1]
    if filters.get("consider_drafts"):
        docstatuses.append(0)

    def get_income_opening_balance():
        commission = frappe.qb.from_(invfrm).join(invfrmcom).on(invfrmcom.parent == invfrm.name).select(
            ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission")).where(
            invfrm.company == filters.get("company")).where(invfrmcom.item == row.commission_item).where(
            invfrm.posting_date.lt(filters.get("from_date"))).where(invfrm.docstatus.isin(docstatuses)).where(
            (invfrm.commission_invoice_reference.isnull()) | invfrm.commission_invoice_reference == "").run(
            as_dict=True)

        total_commission = sum([com["total_commission"] for com in commission])
        return 0, total_commission

    def get_income_duration_balance():
        commission = frappe.qb.from_(invfrm).join(invfrmcom).on(invfrmcom.parent == invfrm.name).select(
            ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission")).where(
            invfrm.company == filters.get("company")).where(invfrmcom.item == row.commission_item).where(
            invfrm.posting_date.gte(filters.get("from_date"))).where(
            invfrm.posting_date.lte(filters.get("to_date"))).where(invfrm.docstatus.isin(docstatuses)).where(
            (invfrm.commission_invoice_reference.isnull()) | invfrm.commission_invoice_reference == "").run(
            as_dict=True)

        total_commission = sum([com["total_commission"] for com in commission])
        return 0, total_commission

    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = {
                "title": frappe.bold(row.get("title")),
                "opening_debit": 0,
                "opening_credit": 0,
                "debit": 0,
                "credit": 0,
                "closing_debit": 0,
                "closing_credit": 0,
                "is_parent": 1
            }
        else:
            gl_filters.update({
                "account": row.get("account")
            })
            # Get opening
            opening_debit, opening_credit = get_opening_balances_from_gl(gl_filters)
            # Get duration debit and credit
            debit, credit = get_duration_balances_from_gl(gl_filters)

            if row.get("commission_item"):
                invoices_opening_debit, invoices_opening_credit = get_income_opening_balance()
                opening_debit += invoices_opening_debit
                opening_credit += invoices_opening_credit

                invoices_duration_debit, invoices_duration_credit = get_income_duration_balance()
                debit += invoices_duration_debit
                credit += invoices_duration_credit

            # Calculate closing balance
            closing_debit, closing_credit = calculate_closing_balance(opening_debit, debit, opening_credit, credit)

            section_data[row.get("title")] = {
                "title": row.get("title"),
                "opening_debit": opening_debit,
                "opening_credit": opening_credit,
                "debit": debit,
                "credit": credit,
                "closing_debit": closing_debit,
                "closing_credit": closing_credit,
                "is_parent": 0
            }

            if row.get("parent1"):
                parent_record = section_data[row.get("parent1")]
                parent_record.update({
                    "opening_debit": parent_record["opening_debit"] + opening_debit,
                    "opening_credit": parent_record["opening_credit"] + opening_credit,
                    "debit": parent_record["debit"] + debit,
                    "credit": parent_record["credit"] + credit,
                    "closing_debit": parent_record["closing_debit"] + closing_debit,
                    "closing_credit": parent_record["closing_credit"] + closing_credit,
                })
    for section in section_data:
        result.append(section_data[section])


def get_tax_rate():
    default_tax_template = frappe.db.get_single_value("Agriculture Settings", "default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    tax_rate = frappe.db.get_value("Sales Taxes and Charges",
                                   {"parent": default_tax_template}, "rate") or 0
    return tax_rate


def calculate_closing_balance(opening_debit, debit, opening_credit, credit):
    total_debit = opening_debit + debit
    total_credit = opening_credit + credit
    if total_debit > total_credit:
        closing_debit = abs(total_debit - total_credit)
        closing_credit = 0
    else:
        closing_debit = 0
        closing_credit = abs(total_debit - total_credit)

    return closing_debit, closing_credit


def get_columns():
    return [
        {
            "fieldname": "title",
            "label": _("Title"),
            "fieldtype": "Data",
            "width": 200,
        },
        {
            "fieldname": "opening_debit",
            "label": _("Opening (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        },
        {
            "fieldname": "opening_credit",
            "label": _("Opening (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        },
        {
            "fieldname": "debit",
            "label": _("Debit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150
        },
        {
            "fieldname": "credit",
            "label": _("Credit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150
        },
        {
            "fieldname": "closing_debit",
            "label": _("Closing (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150
        },
        {
            "fieldname": "closing_credit",
            "label": _("Closing (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150
        }
    ]


def append_totals_row(data):
    if not data:
        return data
    data = list(data)
    totals = {
        "title": frappe.bold("Totals"),
        "opening_debit": 0,
        "opening_credit": 0,
        "debit": 0,
        "credit": 0,
        "closing_debit": 0,
        "closing_credit": 0
    }

    for row in data:
        if not row.get("is_parent"):
            totals.update({
                "opening_debit": totals["opening_debit"] + row["opening_debit"],
                "opening_credit": totals["opening_credit"] + row["opening_credit"],
                "debit": totals["debit"] + row["debit"],
                "credit": totals["credit"] + row["credit"],
                "closing_debit": totals["closing_debit"] + row["closing_debit"],
                "closing_credit": totals["closing_credit"] + row["closing_credit"],
            })

    data.append(totals)

    return data

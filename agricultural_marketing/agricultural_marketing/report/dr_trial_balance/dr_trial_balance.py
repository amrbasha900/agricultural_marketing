# Copyright (c) 2025, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Sum


def execute(filters=None):
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
        "to_date": filters.get("to_date"),
    }

    # ---- Pre-fetch data in bulk to avoid N+1 queries ----

    # 1) Batch-fetch GL balances: 2 queries total instead of 2 per account
    all_accounts = _collect_all_accounts(trial_balance_settings)
    gl_opening, gl_duration = _batch_fetch_gl_balances(gl_filters, all_accounts)

    # 2) Pre-fetch approved invoice forms for taxes/income (1 query instead of thousands)
    approved_invoice_forms = _get_approved_invoice_forms()

    # 3) Pre-fetch party-group maps for draft payment lookups (2 queries)
    supplier_group_map = _get_supplier_group_map()
    customer_group_map = _get_customer_group_map()

    # 4) Cache the tax rate (read once)
    tax_rate = get_tax_rate()

    ctx = {
        "gl_opening": gl_opening,
        "gl_duration": gl_duration,
        "approved_invoice_forms": approved_invoice_forms,
        "supplier_group_map": supplier_group_map,
        "customer_group_map": customer_group_map,
        "tax_rate": tax_rate,
    }

    get_child_data_from_gl_entries(gl_filters, filters, trial_balance_settings, "cash_section", result, ctx)
    get_customers_section_data(gl_filters, filters, trial_balance_settings, "customers_section", result, ctx)
    get_suppliers_section_data(gl_filters, filters, trial_balance_settings, "suppliers_section", result, ctx)
    get_child_data_from_gl_entries(gl_filters, filters, trial_balance_settings, "share_capital_section", result, ctx)
    get_taxes_section_data(gl_filters, filters, trial_balance_settings, "taxes_section", result, ctx)
    get_income_section_data(gl_filters, filters, trial_balance_settings, "income_section", result, ctx)
    get_child_data_from_gl_entries(gl_filters, filters, trial_balance_settings, "expense_section", result, ctx)

    return result


# ---------------------------------------------------------------------------
# Bulk pre-fetch helpers
# ---------------------------------------------------------------------------

def _collect_all_accounts(trial_balance_settings):
    """Collect all unique account names from every section."""
    accounts = set()
    for section in ("cash_section", "customers_section", "suppliers_section",
                    "share_capital_section", "taxes_section", "income_section",
                    "expense_section"):
        for row in trial_balance_settings.get(section, []):
            if not row.get("is_parent") and row.get("account"):
                accounts.add(row.get("account"))
    return list(accounts)


def _batch_fetch_gl_balances(gl_filters, accounts):
    """Fetch opening and duration GL balances for ALL accounts in just 2 queries.

    Returns two dicts keyed by account:
        opening[account] = (debit, credit)
        duration[account] = (debit, credit)
    """
    opening = {}
    duration = {}

    if not accounts:
        return opening, duration

    # Opening balances – single query with SUM + GROUP BY
    for row in frappe.db.sql("""
        SELECT account,
               COALESCE(SUM(debit), 0) AS total_debit,
               COALESCE(SUM(credit), 0) AS total_credit
        FROM `tabGL Entry`
        WHERE account IN %(accounts)s
          AND is_cancelled = 0
          AND (posting_date < %(from_date)s OR is_opening = 'Yes')
        GROUP BY account
    """, {"accounts": accounts, "from_date": gl_filters["from_date"]}, as_dict=True):
        opening[row.account] = (row.total_debit, row.total_credit)

    # Duration balances – single query with SUM + GROUP BY
    for row in frappe.db.sql("""
        SELECT account,
               COALESCE(SUM(debit), 0) AS total_debit,
               COALESCE(SUM(credit), 0) AS total_credit
        FROM `tabGL Entry`
        WHERE account IN %(accounts)s
          AND is_cancelled = 0
          AND posting_date >= %(from_date)s
          AND posting_date <= %(to_date)s
          AND is_opening = 'No'
        GROUP BY account
    """, {"accounts": accounts, "from_date": gl_filters["from_date"],
          "to_date": gl_filters["to_date"]}, as_dict=True):
        duration[row.account] = (row.total_debit, row.total_credit)

    return opening, duration


def _get_approved_invoice_forms():
    """Pre-fetch all Invoice Form names that have at least one approved (docstatus=1)
    Sales Invoice Item.  Returns a set for O(1) membership tests."""
    rows = frappe.db.sql("""
        SELECT DISTINCT invoice_form
        FROM `tabSales Invoice Item`
        WHERE docstatus = 1
          AND IFNULL(invoice_form, '') != ''
    """, as_list=True)
    return {r[0] for r in rows}


def _get_supplier_group_map():
    """Return {supplier_name: supplier_group} dict."""
    return {d.name: d.supplier_group
            for d in frappe.get_all("Supplier", fields=["name", "supplier_group"])}


def _get_customer_group_map():
    """Return {customer_name: customer_group} dict."""
    return {d.name: d.customer_group
            for d in frappe.get_all("Customer", fields=["name", "customer_group"])}


# ---------------------------------------------------------------------------
# Shared row helpers
# ---------------------------------------------------------------------------

def _empty_parent_row(title):
    return {
        "title": frappe.bold(title),
        "opening_debit": 0,
        "opening_credit": 0,
        "debit": 0,
        "credit": 0,
        "closing_debit": 0,
        "closing_credit": 0,
        "is_parent": 1,
    }


def _make_row(title, opening_debit, opening_credit, debit, credit,
              closing_debit, closing_credit):
    return {
        "title": title,
        "opening_debit": opening_debit,
        "opening_credit": opening_credit,
        "debit": debit,
        "credit": credit,
        "closing_debit": closing_debit,
        "closing_credit": closing_credit,
        "is_parent": 0,
    }


def _accumulate_to_parent(section_data, row, opening_debit, opening_credit,
                           debit, credit, closing_debit, closing_credit):
    if row.get("parent1"):
        p = section_data[row.get("parent1")]
        p["opening_debit"] += opening_debit
        p["opening_credit"] += opening_credit
        p["debit"] += debit
        p["credit"] += credit
        p["closing_debit"] += closing_debit
        p["closing_credit"] += closing_credit


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def get_child_data_from_gl_entries(gl_filters, filters, trial_balance_settings, child, result, ctx):
    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = _empty_parent_row(row.get("title"))
        else:
            account = row.get("account")
            # Lookup from pre-fetched GL data (no per-row queries)
            opening_debit, opening_credit = ctx["gl_opening"].get(account, (0, 0))
            debit, credit = ctx["gl_duration"].get(account, (0, 0))

            # Add draft payments if this is a cash section and consider_drafts is enabled
            if child == "cash_section" and filters.get("consider_drafts") and row.get("mode_of_payment"):
                payment_filters = filters.copy()
                payment_filters["mode_of_payment"] = row.get("mode_of_payment")
                draft_payments = get_draft_payments_data(payment_filters, ctx)
                opening_debit += draft_payments["opening_credit"]
                opening_credit += draft_payments["opening_debit"]
                debit += draft_payments["credit"]
                credit += draft_payments["debit"]

            closing_debit, closing_credit = calculate_closing_balance(
                opening_debit, debit, opening_credit, credit
            )

            section_data[row.get("title")] = _make_row(
                row.get("title"), opening_debit, opening_credit,
                debit, credit, closing_debit, closing_credit
            )
            _accumulate_to_parent(section_data, row, opening_debit, opening_credit,
                                  debit, credit, closing_debit, closing_credit)

    for key in section_data:
        result.append(section_data[key])


def get_customers_section_data(gl_filters, filters, trial_balance_settings, child, result, ctx):
    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = _empty_parent_row(row.get("title"))
        else:
            account = row.get("account")
            opening_debit, opening_credit = ctx["gl_opening"].get(account, (0, 0))
            debit, credit = ctx["gl_duration"].get(account, (0, 0))

            if filters.get("consider_drafts") and row.get("customer_group"):
                customers = frappe.get_all(
                    "Customer",
                    {"customer_group": row.get("customer_group"), "is_customer": 1},
                    pluck="name",
                )
                if customers:
                    draft_opening_debit, draft_duration_debit = _get_customers_draft_balance(
                        filters, customers
                    )
                    opening_debit += draft_opening_debit
                    debit += draft_duration_debit

                payment_filters = filters.copy()
                payment_filters["party_type"] = "Customer"
                payment_filters["party_group"] = row.get("customer_group")
                draft_payments = get_draft_payments_data(payment_filters, ctx)
                opening_debit += draft_payments["opening_debit"]
                opening_credit += draft_payments["opening_credit"]
                debit += draft_payments["debit"]
                credit += draft_payments["credit"]

            closing_debit, closing_credit = calculate_closing_balance(
                opening_debit, debit, opening_credit, credit
            )

            section_data[row.get("title")] = _make_row(
                row.get("title"), opening_debit, opening_credit,
                debit, credit, closing_debit, closing_credit
            )
            _accumulate_to_parent(section_data, row, opening_debit, opening_credit,
                                  debit, credit, closing_debit, closing_credit)

    for key in section_data:
        result.append(section_data[key])


def _get_customers_draft_balance(filters, customers):
    """Get draft Invoice Form totals for the given customer list."""
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmitem = frappe.qb.DocType("Invoice Form Item")

    total_draft_opening_debit = (
        frappe.qb.from_(invfrm)
        .join(invfrmitem).on(invfrmitem.parent == invfrm.name)
        .select(Sum(invfrmitem.total).as_("total"))
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.lt(filters.get("from_date")))
        .where(invfrmitem.customer.isin(customers))
        .where(invfrm.docstatus == 0)
        .run(as_dict=True)
    )[0]["total"] or 0

    total_draft_duration_debit = (
        frappe.qb.from_(invfrm)
        .join(invfrmitem).on(invfrmitem.parent == invfrm.name)
        .select(Sum(invfrmitem.total).as_("total"))
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.gte(filters.get("from_date")))
        .where(invfrm.posting_date.lte(filters.get("to_date")))
        .where(invfrmitem.customer.isin(customers))
        .where(invfrm.docstatus == 0)
        .run(as_dict=True)
    )[0]["total"] or 0

    return total_draft_opening_debit, total_draft_duration_debit


def get_suppliers_section_data(gl_filters, filters, trial_balance_settings, child, result, ctx):
    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = _empty_parent_row(row.get("title"))
        else:
            account = row.get("account")
            opening_debit, opening_credit = ctx["gl_opening"].get(account, (0, 0))
            debit, credit = ctx["gl_duration"].get(account, (0, 0))

            if filters.get("consider_drafts") and row.get("supplier_group"):
                suppliers = frappe.get_all(
                    "Supplier", {"supplier_group": row.get("supplier_group")}, pluck="name"
                )
                if suppliers:
                    draft_opening_credit, draft_opening_debit = _get_suppliers_draft_opening_balance(
                        filters, suppliers
                    )
                    opening_credit += draft_opening_credit
                    opening_debit += draft_opening_debit

                    draft_duration_credit, draft_duration_debit = _get_suppliers_draft_duration_balance(
                        filters, suppliers
                    )
                    credit += draft_duration_credit
                    debit += draft_duration_debit

                payment_filters = filters.copy()
                payment_filters["party_type"] = "Supplier"
                payment_filters["party_group"] = row.get("supplier_group")
                draft_payments = get_draft_payments_data(payment_filters, ctx)
                opening_debit += draft_payments["opening_debit"]
                opening_credit += draft_payments["opening_credit"]
                debit += draft_payments["debit"]
                credit += draft_payments["credit"]

            closing_debit, closing_credit = calculate_closing_balance(
                opening_debit, debit, opening_credit, credit
            )

            section_data[row.get("title")] = _make_row(
                row.get("title"), opening_debit, opening_credit,
                debit, credit, closing_debit, closing_credit
            )
            _accumulate_to_parent(section_data, row, opening_debit, opening_credit,
                                  debit, credit, closing_debit, closing_credit)

    for key in section_data:
        result.append(section_data[key])


def _get_suppliers_draft_opening_balance(filters, suppliers):
    """Get draft opening balances for the given supplier list."""
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmitem = frappe.qb.DocType("Invoice Form Item")

    total_draft_opening_credit = (
        frappe.qb.from_(invfrm)
        .join(invfrmitem).on(invfrmitem.parent == invfrm.name)
        .select(Sum(invfrmitem.total).as_("total"))
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.lt(filters.get("from_date")))
        .where(invfrm.supplier.isin(suppliers))
        .where(invfrm.docstatus == 0)
        .run(as_dict=True)
    )[0]["total"] or 0

    total_draft_opening_debit = (
        frappe.qb.from_(invfrm)
        .select(Sum(invfrm.total_commissions_and_taxes).as_("total_commissions_and_taxes"))
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.lt(filters.get("from_date")))
        .where(invfrm.supplier.isin(suppliers))
        .where(invfrm.docstatus == 0)
        .run(as_dict=True)
    )[0]["total_commissions_and_taxes"] or 0

    return total_draft_opening_credit, total_draft_opening_debit


def _get_suppliers_draft_duration_balance(filters, suppliers):
    """Get draft duration balances for the given supplier list."""
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmitem = frappe.qb.DocType("Invoice Form Item")

    total_duration_credit = (
        frappe.qb.from_(invfrm)
        .join(invfrmitem).on(invfrmitem.parent == invfrm.name)
        .select(Sum(invfrmitem.total).as_("total"))
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.gte(filters.get("from_date")))
        .where(invfrm.posting_date.lte(filters.get("to_date")))
        .where(invfrm.supplier.isin(suppliers))
        .where(invfrm.docstatus == 0)
        .run(as_dict=True)
    )[0]["total"] or 0

    total_duration_debit = (
        frappe.qb.from_(invfrm)
        .select(Sum(invfrm.total_commissions_and_taxes).as_("total_commissions_and_taxes"))
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.gte(filters.get("from_date")))
        .where(invfrm.posting_date.lte(filters.get("to_date")))
        .where(invfrm.supplier.isin(suppliers))
        .where(invfrm.docstatus == 0)
        .run(as_dict=True)
    )[0]["total_commissions_and_taxes"] or 0

    return total_duration_credit, total_duration_debit


def get_taxes_section_data(gl_filters, filters, trial_balance_settings, child, result, ctx):
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmcom = frappe.qb.DocType("Invoice Form Commission")
    approved_invoice_forms = ctx["approved_invoice_forms"]
    tax_rate = ctx["tax_rate"]
    docstatuses = [1]
    if filters.get("consider_drafts"):
        docstatuses.append(0)

    def _sum_commissions_excluding_approved(commission_data):
        """Sum total_commission for records NOT in approved_invoice_forms (set lookup, no DB calls)."""
        return sum(
            c.get("total_commission") or 0
            for c in commission_data
            if not (c.get("name") and c["name"] in approved_invoice_forms)
        )

    # Fetch tax commission data ONCE (outside the row loop – values don't depend on the row)
    opening_commission_data = (
        frappe.qb.from_(invfrm)
        .join(invfrmcom).on(invfrmcom.parent == invfrm.name)
        .select(
            invfrm.name,
            ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission"),
        )
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.lt(filters.get("from_date")))
        .where(invfrm.docstatus.isin(docstatuses))
        .run(as_dict=True)
    )
    opening_total = _sum_commissions_excluding_approved(opening_commission_data)
    taxes_opening = (0, (opening_total * tax_rate) / 100)

    duration_commission_data = (
        frappe.qb.from_(invfrm)
        .join(invfrmcom).on(invfrmcom.parent == invfrm.name)
        .select(
            invfrm.name,
            ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission"),
        )
        .where(invfrm.company == filters.get("company"))
        .where(invfrm.posting_date.gte(filters.get("from_date")))
        .where(invfrm.posting_date.lte(filters.get("to_date")))
        .where(invfrm.docstatus.isin(docstatuses))
        .run(as_dict=True)
    )
    duration_total = _sum_commissions_excluding_approved(duration_commission_data)
    taxes_duration = (0, (duration_total * tax_rate) / 100)

    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = _empty_parent_row(row.get("title"))
        else:
            account = row.get("account")
            opening_debit, opening_credit = ctx["gl_opening"].get(account, (0, 0))
            debit, credit = ctx["gl_duration"].get(account, (0, 0))

            opening_debit += taxes_opening[0]
            opening_credit += taxes_opening[1]
            debit += taxes_duration[0]
            credit += taxes_duration[1]

            closing_debit, closing_credit = calculate_closing_balance(
                opening_debit, debit, opening_credit, credit
            )

            section_data[row.get("title")] = _make_row(
                row.get("title"), opening_debit, opening_credit,
                debit, credit, closing_debit, closing_credit
            )
            _accumulate_to_parent(section_data, row, opening_debit, opening_credit,
                                  debit, credit, closing_debit, closing_credit)

    for key in section_data:
        result.append(section_data[key])


def get_income_section_data(gl_filters, filters, trial_balance_settings, child, result, ctx):
    invfrm = frappe.qb.DocType("Invoice Form")
    invfrmcom = frappe.qb.DocType("Invoice Form Commission")
    approved_invoice_forms = ctx["approved_invoice_forms"]
    docstatuses = [1]
    if filters.get("consider_drafts"):
        docstatuses.append(0)

    def _sum_commissions_excluding_approved(commission_data):
        return sum(
            c.get("total_commission") or 0
            for c in commission_data
            if not (c.get("name") and c["name"] in approved_invoice_forms)
        )

    def get_income_opening_balance(commission_item):
        commission_data = (
            frappe.qb.from_(invfrm)
            .join(invfrmcom).on(invfrmcom.parent == invfrm.name)
            .select(
                invfrm.name,
                ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission"),
            )
            .where(invfrm.company == filters.get("company"))
            .where(invfrmcom.item == commission_item)
            .where(invfrm.posting_date.lt(filters.get("from_date")))
            .where(invfrm.docstatus.isin(docstatuses))
            .run(as_dict=True)
        )
        total_commission = _sum_commissions_excluding_approved(commission_data)
        return 0, total_commission

    def get_income_duration_balance(commission_item):
        commission_data = (
            frappe.qb.from_(invfrm)
            .join(invfrmcom).on(invfrmcom.parent == invfrm.name)
            .select(
                invfrm.name,
                ((invfrmcom.price * invfrmcom.commission) / 100).as_("total_commission"),
            )
            .where(invfrm.company == filters.get("company"))
            .where(invfrmcom.item == commission_item)
            .where(invfrm.posting_date.gte(filters.get("from_date")))
            .where(invfrm.posting_date.lte(filters.get("to_date")))
            .where(invfrm.docstatus.isin(docstatuses))
            .run(as_dict=True)
        )
        total_commission = _sum_commissions_excluding_approved(commission_data)
        return 0, total_commission

    section_data = {}
    for row in trial_balance_settings.get(child, []):
        if row.get("is_parent"):
            section_data[row.get("title")] = _empty_parent_row(row.get("title"))
        else:
            account = row.get("account")
            opening_debit, opening_credit = ctx["gl_opening"].get(account, (0, 0))
            debit, credit = ctx["gl_duration"].get(account, (0, 0))

            if row.get("commission_item"):
                invoices_opening_debit, invoices_opening_credit = get_income_opening_balance(
                    row.commission_item
                )
                opening_debit += invoices_opening_debit
                opening_credit += invoices_opening_credit

                invoices_duration_debit, invoices_duration_credit = get_income_duration_balance(
                    row.commission_item
                )
                debit += invoices_duration_debit
                credit += invoices_duration_credit

            closing_debit, closing_credit = calculate_closing_balance(
                opening_debit, debit, opening_credit, credit
            )

            section_data[row.get("title")] = _make_row(
                row.get("title"), opening_debit, opening_credit,
                debit, credit, closing_debit, closing_credit
            )
            _accumulate_to_parent(section_data, row, opening_debit, opening_credit,
                                  debit, credit, closing_debit, closing_credit)

    for key in section_data:
        result.append(section_data[key])


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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
        return abs(total_debit - total_credit), 0
    else:
        return 0, abs(total_debit - total_credit)


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
            totals["opening_debit"] += row["opening_debit"]
            totals["opening_credit"] += row["opening_credit"]
            totals["debit"] += row["debit"]
            totals["credit"] += row["credit"]
            totals["closing_debit"] += row["closing_debit"]
            totals["closing_credit"] += row["closing_credit"]

    data.append(totals)
    return data


# ---------------------------------------------------------------------------
# Draft payments
# ---------------------------------------------------------------------------

def get_draft_payments_data(filters, ctx=None):
    if not filters.get("consider_drafts"):
        return {
            "opening_debit": 0, "opening_credit": 0,
            "debit": 0, "credit": 0,
            "closing_debit": 0, "closing_credit": 0,
        }

    payments = frappe.qb.DocType("Payments and Receipts")
    references = frappe.qb.DocType("Payments Receipts Reference")
    docstatuses = [0]

    # Use pre-fetched maps if available, otherwise fall back to per-row queries
    supplier_group_map = ctx.get("supplier_group_map") if ctx else None
    customer_group_map = ctx.get("customer_group_map") if ctx else None

    def get_filtered_payment_data(before_date=False):
        query = (
            frappe.qb.from_(payments)
            .join(references).on(references.parent == payments.name)
            .select(
                payments.payment_type,
                references.party_type,
                references.party,
                references.mode_of_payment,
                references.amount,
            )
            .where(payments.company == filters.get("company"))
            .where(references.docstatus.isin(docstatuses))
        )

        if before_date:
            query = query.where(payments.posting_date.lt(filters.get("from_date")))
        else:
            query = query.where(payments.posting_date.gte(filters.get("from_date")))
            query = query.where(payments.posting_date.lte(filters.get("to_date")))

        if filters.get("party_type"):
            query = query.where(payments.party_type == filters.get("party_type"))

        if filters.get("mode_of_payment"):
            query = query.where(payments.mode_of_payment == filters.get("mode_of_payment"))

        payment_data = query.run(as_dict=True)

        result = []
        for payment in payment_data:
            # Use pre-fetched maps instead of per-row DB queries
            party_group = None
            if payment.party_type == "Supplier":
                if supplier_group_map is not None:
                    party_group = supplier_group_map.get(payment.party)
                else:
                    party_group = frappe.db.get_value("Supplier", payment.party, "supplier_group")
            elif payment.party_type == "Customer":
                if customer_group_map is not None:
                    party_group = customer_group_map.get(payment.party)
                else:
                    party_group = frappe.db.get_value("Customer", payment.party, "customer_group")

            if filters.get("party_group") and party_group != filters.get("party_group"):
                continue

            payment["party_group"] = party_group
            result.append(payment)
        return result

    def sum_payments(payment_data):
        total_debit, total_credit = 0, 0
        for payment in payment_data:
            if payment["payment_type"] == 'Pay':
                total_debit += payment["amount"] or 0
            elif payment["payment_type"] == 'Receive':
                total_credit += payment["amount"] or 0
        return total_debit, total_credit

    opening_debit, opening_credit = sum_payments(get_filtered_payment_data(before_date=True))
    period_debit, period_credit = sum_payments(get_filtered_payment_data(before_date=False))

    total_debit = opening_debit + period_debit
    total_credit = opening_credit + period_credit
    closing_debit = 0
    closing_credit = 0

    if total_debit > total_credit:
        closing_debit = abs(total_debit - total_credit)
    else:
        closing_credit = abs(total_credit - total_debit)

    return {
        "opening_debit": opening_debit,
        "opening_credit": opening_credit,
        "debit": period_debit,
        "credit": period_credit,
        "closing_debit": closing_debit,
        "closing_credit": closing_credit,
    }

# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt
"""Supplier charges -> a shared Payments and Receipts voucher, one row per invoice.

Pressing "Make Charge Payment" on the Invoice Form list builds a single voucher for
the whole selection, giving every invoice its own reference row - invoices of the
same supplier are never merged into one row. A normal invoice charges ``Pay``,
recording the advance owed by the supplier; a return charges the opposite
``Receive``, which clears that advance. ``payment_type`` lives on the voucher, not
the row, so a selection mixing the two produces one voucher of each - the grouping
key is (payment_type, company).

The row owns the relationship. ``Payments Receipts Reference.invoice_form`` is the
only durable link; the invoice's ``charges_payment`` and ``charge_payment_line`` are
a cache of it. That matters because the cache really does get lost: a document
loaded before the batch ran still carries empty values, and saving it writes those
back over the links while the row stays on the voucher. Every lookup therefore goes
through ``find_row``, which searches by ``invoice_form`` alone and restores the cache
whenever it finds it wrong, so an orphaned row re-adopts itself on the next save.

Row names are positional - ``Payments and Receipts.on_update`` renames every row to
``<voucher>-<NNN>`` by index and commits after each one. ``create_voucher`` names its
rows that way up front so the loop finds nothing to do, which keeps large batches
fast and leaves our savepoints intact.

The voucher is left as a draft for an accountant to submit. Once submitted its rows
are frozen, which is what makes row-level cancellation possible: each row has its own
Payment Entry named after it, so one invoice can be cancelled without disturbing the
others.
"""

import json

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import flt

PAYMENT_TYPE = "Pay"
RETURN_PAYMENT_TYPE = "Receive"
PARTY_TYPE = "Supplier"
NAMING_SERIES = "ACC-PR-.YYYY.-"
ROW_DOCTYPE = "Payments Receipts Reference"

# Selections larger than this go to a background job instead of blocking the request.
# With the rename loop skipped a batch costs about 5ms per invoice, so a hundred still
# runs well inside a request; the job is insurance for the unusually large selection.
INLINE_LIMIT = 100

# Chunk size for `in` filters, so a very large selection cannot build a huge query.
QUERY_CHUNK = 500

# Set on a voucher document while this module is the one changing it. The validate
# and on_trash guards let a change through only when they see it.
LOCK_FLAG = "from_supplier_charges"

INVOICE_FIELDS = [
    "name",
    "supplier",
    "supplier_name",
    "company",
    "posting_date",
    "docstatus",
    "apply_charges",
    "charges_amount",
    "is_return",
    "return_against",
    "charges_payment",
]


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), QUERY_CHUNK):
        yield values[start : start + QUERY_CHUNK]


# ---------------------------------------------------------------------------
# calculation
# ---------------------------------------------------------------------------

def calculate_charges(invoice):
    """Set ``apply_charges``, ``charges_precentage`` and ``charges_amount``.

    Called from ``validate`` right after the grand total is built, so the charge is
    always measured against the final total. The percentage is read live from the
    Supplier - for returns too - so a rate change on the supplier applies to every
    invoice saved after it.
    """
    if not invoice.supplier:
        invoice.apply_charges = 0
        invoice.charges_precentage = 0
        invoice.charges_amount = 0
        return

    supplier = frappe.db.get_value(
        "Supplier", invoice.supplier, ["apply_charges", "charge_percentage"], as_dict=True
    )

    invoice.apply_charges = 1 if (supplier and supplier.apply_charges) else 0
    invoice.charges_precentage = flt(supplier.charge_percentage) if supplier else 0

    if not invoice.apply_charges:
        invoice.charges_amount = 0
        return

    # Mirrors grand_total, so a return invoice carries a negative charge here. The
    # row drops the sign and the voucher flips direction instead.
    invoice.charges_amount = flt(invoice.grand_total) * flt(invoice.charges_precentage) / 100.0


def charge_amount(invoice):
    """Row amount: the charge without its sign."""
    return flt(abs(flt(invoice.charges_amount)), 2)


def payment_type_for(invoice):
    return RETURN_PAYMENT_TYPE if invoice.is_return else PAYMENT_TYPE


def get_charge_mode_of_payment(company):
    """Mode of Payment from Agriculture Settings, verified against ``company``.

    ``Payments and Receipts.get_mode_of_payment_account`` silently falls back to the
    Cash account when a mode has no mapping for the company, which would book the
    charge to the wrong account without any error. Checking here turns that into a
    clear failure instead.
    """
    mode_of_payment = frappe.db.get_single_value(
        "Agriculture Settings", "supplier_charges_mode_of_payment"
    )
    if not mode_of_payment:
        frappe.throw(
            _("Set {0} in {1} before creating supplier charge payments.").format(
                frappe.bold(_("Supplier Charges Mode of Payment")),
                frappe.bold(_("Agriculture Settings")),
            )
        )

    account = frappe.db.get_value(
        "Mode of Payment Account", {"parent": mode_of_payment, "company": company}, "default_account"
    )
    if not account:
        frappe.throw(
            _("Mode of Payment {0} has no default account for company {1}. Set one so supplier charges are not booked to Cash.").format(
                frappe.bold(mode_of_payment), frappe.bold(company)
            )
        )

    return mode_of_payment


# ---------------------------------------------------------------------------
# locating an invoice's row
# ---------------------------------------------------------------------------

def find_rows(invoice_names):
    """Map invoice -> its row on a voucher that is not cancelled.

    Searches only on ``invoice_form``, never on the invoice's cached link, so a row
    whose invoice has forgotten it is still found.
    """
    rows = []
    for chunk in _chunks(invoice_names):
        rows.extend(
            frappe.get_all(
                ROW_DOCTYPE,
                filters={"parenttype": "Payments and Receipts", "invoice_form": ["in", chunk]},
                fields=["name", "parent", "invoice_form"],
            )
        )
    if not rows:
        return {}

    docstatus = {}
    for chunk in _chunks({r.parent for r in rows}):
        for name, status in frappe.get_all(
            "Payments and Receipts",
            filters={"name": ["in", chunk]},
            fields=["name", "docstatus"],
            as_list=True,
        ):
            docstatus[name] = status

    found = {}
    for row in rows:
        # A cancelled voucher no longer owns the invoice; it may take a new row.
        if docstatus.get(row.parent) in (0, 1) and row.invoice_form not in found:
            found[row.invoice_form] = frappe._dict(
                voucher=row.parent, row=row.name, docstatus=docstatus[row.parent]
            )
    return found


def find_row(invoice_name):
    return find_rows([invoice_name]).get(invoice_name)


def clear_invoice_fields(invoice):
    invoice.db_set(
        {"charges_payment": None, "charge_payment_line": None}, update_modified=False
    )


def refresh_line_cache(voucher_name, update_modified=False):
    """Re-point every invoice on this voucher at its row's current name.

    Driven by the rows, not by the invoice links, so it both writes the links when a
    voucher is first built and corrects them after rows are renumbered. Invoices that
    still point at the voucher but no longer hold a row are released.

    ``update_modified`` is on when a batch writes the links: bumping the timestamp
    makes any form loaded beforehand fail its next save with a visible "document has
    been modified" conflict, instead of silently writing empty links back over ours.
    """
    rows = frappe.get_all(
        ROW_DOCTYPE,
        filters={"parent": voucher_name, "parenttype": "Payments and Receipts"},
        fields=["name", "invoice_form"],
    )
    by_invoice = {r.invoice_form: r.name for r in rows if r.invoice_form}

    for invoice_name, row_name in by_invoice.items():
        frappe.db.set_value(
            "Invoice Form",
            invoice_name,
            {"charges_payment": voucher_name, "charge_payment_line": row_name},
            update_modified=update_modified,
        )

    for invoice_name in frappe.get_all(
        "Invoice Form", filters={"charges_payment": voucher_name}, pluck="name"
    ):
        if invoice_name not in by_invoice:
            frappe.db.set_value(
                "Invoice Form",
                invoice_name,
                {"charges_payment": None, "charge_payment_line": None},
                update_modified=False,
            )


def save_voucher(voucher):
    """Save a voucher past the edit guard."""
    voucher.set_totals()
    voucher.flags[LOCK_FLAG] = True
    voucher.flags.ignore_permissions = True
    voucher.save()


# ---------------------------------------------------------------------------
# building the shared voucher
# ---------------------------------------------------------------------------

def build_row(invoice, mode_of_payment):
    return {
        "party_type": PARTY_TYPE,
        "party": invoice.supplier,
        "party_name": invoice.supplier_name,
        "mode_of_payment": mode_of_payment,
        "amount": charge_amount(invoice),
        "description": _("Supplier charges for {0}").format(invoice.name),
        "invoice_form": invoice.name,
    }


def create_voucher(invoices, payment_type, company):
    """One voucher for a group of invoices, one row each, left as a draft."""
    mode_of_payment = get_charge_mode_of_payment(company)
    voucher_name = make_autoname(NAMING_SERIES, "Payments and Receipts")

    voucher = frappe.new_doc("Payments and Receipts")
    voucher.naming_series = NAMING_SERIES
    voucher.company = company
    voucher.posting_date = invoices[0].posting_date
    voucher.payment_type = payment_type
    voucher.party_type = PARTY_TYPE
    voucher.mode_of_payment = mode_of_payment
    voucher.is_supplier_charge = 1

    for idx, invoice in enumerate(invoices, start=1):
        row = voucher.append("references", build_row(invoice, mode_of_payment))
        # Name the row exactly as `Payments and Receipts.on_update` would. It only
        # renames rows whose name does not already match, so naming them here skips
        # that loop entirely - along with the `frappe.db.commit()` it runs after
        # every single rename, which is what made large batches crawl and quietly
        # destroyed the savepoints this function relies on.
        row.name = f"{voucher_name}-{idx:03d}"

    # Payments and Receipts fills its totals in `on_update`, which runs after the
    # rows are written and so never reaches the database. Calling it here means
    # `total` is correct from the first insert.
    voucher.set_totals()
    voucher.flags[LOCK_FLAG] = True
    voucher.flags.ignore_permissions = True
    voucher.insert(set_name=voucher_name, set_child_names=False)

    refresh_line_cache(voucher.name, update_modified=True)
    return voucher.name


# ---------------------------------------------------------------------------
# per-invoice lifecycle
# ---------------------------------------------------------------------------

def sync_charge_row(invoice):
    """Keep this invoice's own row in step. Never creates a voucher or a row.

    Runs from ``on_update`` so it only ever acts on a save that succeeded.
    """
    found = find_row(invoice.name)

    if not found:
        if invoice.charges_payment or invoice.charge_payment_line:
            clear_invoice_fields(invoice)
        return

    if invoice.charges_payment != found.voucher or invoice.charge_payment_line != found.row:
        # Re-adopt. The cache is lost whenever a document loaded before the batch ran
        # is saved afterwards, writing its empty links back over ours. The row knows
        # better, so restore from it rather than abandoning the row.
        invoice.db_set(
            {"charges_payment": found.voucher, "charge_payment_line": found.row},
            update_modified=False,
        )

    if found.docstatus != 0:
        # Frozen. A submitted row that has drifted is reported, not rewritten.
        return

    amount = charge_amount(invoice)
    if not invoice.apply_charges or not amount:
        # Charges switched off - this invoice drops out of the voucher.
        remove_charge_row(invoice)
        return

    voucher = frappe.get_doc("Payments and Receipts", found.voucher)

    if voucher.payment_type != payment_type_for(invoice):
        # An invoice that turned into a return no longer belongs in a Pay voucher.
        remove_charge_row(invoice)
        return

    row = next((r for r in voucher.references if r.name == found.row), None)
    if not row:
        return

    changed = False
    if flt(row.amount, 2) != amount:
        row.amount = amount
        changed = True
    if row.party != invoice.supplier:
        row.party = invoice.supplier
        row.party_name = invoice.supplier_name
        changed = True

    if not changed:
        return

    # Row names already match their positions, so the voucher's own `on_update`
    # skips its rename-and-commit path and this save stays inside the invoice's
    # transaction.
    save_voucher(voucher)


def remove_charge_row(invoice):
    """Take this invoice's row off a draft voucher, deleting the voucher if it empties.

    On a submitted voucher rows cannot be removed, so the row is kept and only this
    invoice's own Payment Entry is cancelled.
    """
    found = find_row(invoice.name)
    if not found:
        clear_invoice_fields(invoice)
        return

    if found.docstatus != 0:
        cancel_row_payment_entry(invoice, found.row)
        return

    clear_invoice_fields(invoice)
    voucher = frappe.get_doc("Payments and Receipts", found.voucher)
    voucher.references = [r for r in voucher.references if r.name != found.row]

    if not voucher.references:
        # An empty voucher is not worth keeping.
        voucher.flags[LOCK_FLAG] = True
        frappe.delete_doc(
            "Payments and Receipts", voucher.name, ignore_permissions=True, flags=voucher.flags
        )
        return

    save_voucher(voucher)
    # The rows after the removed one have just been renamed.
    refresh_line_cache(voucher.name)


def cancel_row_payment_entry(invoice, row_name=None):
    """Cancel only the Payment Entry belonging to this invoice's row.

    Payment Entries are renamed to their row's name on submit, so one invoice can be
    cancelled while the rest of the voucher stays live.
    """
    if not row_name:
        found = find_row(invoice.name)
        row_name = found.row if found else invoice.charge_payment_line
    if not row_name:
        return

    for name in frappe.get_all(
        "Payment Entry", filters={"name": row_name, "docstatus": 1}, pluck="name"
    ):
        entry = frappe.get_doc("Payment Entry", name)
        entry.flags.ignore_permissions = True
        entry.cancel()


def detach_charge_row(invoice):
    """Release this invoice from its voucher, used on cancel and delete."""
    remove_charge_row(invoice)
    clear_invoice_fields(invoice)


def warn_if_charge_payment_stale(invoice):
    """Report a submitted row that no longer matches the invoice.

    Deliberately non-blocking: silently cancelling a posted Payment Entry would be
    worse than letting the user decide.
    """
    found = find_row(invoice.name)
    if not found or found.docstatus != 1:
        return

    booked = flt(frappe.db.get_value(ROW_DOCTYPE, found.row, "amount"), 2)
    expected = charge_amount(invoice)
    if booked == expected:
        return

    frappe.msgprint(
        _("Charges on this invoice are now {0}, but row {1} of charge voucher {2} is already submitted for {3}. Cancel and amend the voucher if the new amount should be booked.").format(
            frappe.format_value(expected, "Currency"),
            frappe.bold(found.row),
            frappe.bold(found.voucher),
            frappe.format_value(booked, "Currency"),
        ),
        title=_("Charge Payment Out of Date"),
        indicator="orange",
    )


# ---------------------------------------------------------------------------
# guards on the voucher itself
# ---------------------------------------------------------------------------

def _voucher_signature(voucher):
    """The values a charge voucher is not allowed to change outside this module."""
    return (
        voucher.company,
        str(voucher.posting_date),
        voucher.payment_type,
        voucher.party_type,
        tuple(
            sorted(
                (r.get("invoice_form"), r.party, flt(r.amount, 2), r.mode_of_payment)
                for r in voucher.references
            )
        ),
    )


def lock_charge_voucher(voucher, method=None):
    """Block hand edits to a charge voucher; the invoices own its contents.

    Submitting and cancelling stay open so an accountant can still post the batch -
    only the values are frozen.
    """
    if not voucher.get("is_supplier_charge") or voucher.flags.get(LOCK_FLAG):
        return

    before = voucher.get_doc_before_save()
    if not before:
        frappe.throw(
            _("Supplier charge vouchers are created from the Invoice Form list, not by hand."),
            title=_("Charge Voucher Locked"),
        )

    if _voucher_signature(voucher) != _voucher_signature(before):
        frappe.throw(
            _("{0} is built from supplier charges. Change the invoices instead - this voucher only follows them.").format(
                frappe.bold(voucher.name)
            ),
            title=_("Charge Voucher Locked"),
        )


def guard_charge_voucher_delete(voucher, method=None):
    """A charge voucher goes away when its last invoice leaves it, not by hand."""
    if voucher.get("is_supplier_charge") and not voucher.flags.get(LOCK_FLAG):
        frappe.throw(
            _("{0} is built from supplier charges. It is removed automatically once no invoice uses it.").format(
                frappe.bold(voucher.name)
            ),
            title=_("Charge Voucher Locked"),
        )


def clear_invoice_links(voucher, method=None):
    """Drop the links from any invoice pointing at a voucher being deleted."""
    for name in frappe.get_all(
        "Invoice Form", filters={"charges_payment": voucher.name}, pluck="name"
    ):
        frappe.db.set_value(
            "Invoice Form",
            name,
            {"charges_payment": None, "charge_payment_line": None},
            update_modified=False,
        )


# ---------------------------------------------------------------------------
# the list view button
# ---------------------------------------------------------------------------

@frappe.whitelist()
def make_charge_payments(invoice_names):
    """Build charge vouchers for the selected invoices.

    Small selections run inline for immediate feedback. Larger ones go to a
    background job, because the batch costs roughly a third of a second per invoice
    and would otherwise sit past the request timeout looking like a hang.
    """
    if isinstance(invoice_names, str):
        invoice_names = json.loads(invoice_names)
    invoice_names = list(dict.fromkeys(invoice_names))

    if len(invoice_names) <= INLINE_LIMIT:
        return build_charge_payments(invoice_names)

    frappe.enqueue(
        "agricultural_marketing.agricultural_marketing.doctype.invoice_form.supplier_charges.run_charge_payments_job",
        queue="long",
        timeout=3600,
        invoice_names=invoice_names,
        user=frappe.session.user,
    )
    return {"queued": True, "count": len(invoice_names)}


def run_charge_payments_job(invoice_names, user):
    """Background worker for a large selection; pushes the result back to the user."""
    result = build_charge_payments(invoice_names)
    frappe.db.commit()
    frappe.publish_realtime("supplier_charge_payments_done", message=result, user=user)


def build_charge_payments(invoice_names):
    """Group the eligible invoices and create one voucher per group.

    Everything is read in bulk: a full ``get_doc`` per invoice would load its items,
    commissions and pamper commissions to reach a dozen scalar fields.
    """
    result = {
        "vouchers": [],
        "skipped_no_charges": [],
        "skipped_already_linked": [],
        "skipped_cancelled": [],
        "skipped_no_original": [],
        "errors": [],
    }
    if not invoice_names:
        return result

    invoices = []
    for chunk in _chunks(invoice_names):
        invoices.extend(
            frappe.get_all("Invoice Form", filters={"name": ["in", chunk]}, fields=INVOICE_FIELDS)
        )

    live_rows = find_rows([i.name for i in invoices])
    originals = _originals_with_charge_rows(invoices)

    groups = {}
    for invoice in invoices:
        if invoice.docstatus == 2:
            result["skipped_cancelled"].append(invoice.name)
            continue

        if not invoice.apply_charges or not charge_amount(invoice):
            result["skipped_no_charges"].append(invoice.name)
            continue

        if invoice.name in live_rows:
            result["skipped_already_linked"].append(invoice.name)
            continue

        if invoice.is_return and invoice.return_against not in originals:
            # Nothing was ever charged on the original, so there is no advance to clear.
            result["skipped_no_original"].append(invoice.name)
            continue

        if invoice.charges_payment:
            # A stale link from a cancelled or deleted voucher can be replaced.
            frappe.db.set_value(
                "Invoice Form",
                invoice.name,
                {"charges_payment": None, "charge_payment_line": None},
                update_modified=False,
            )

        groups.setdefault((payment_type_for(invoice), invoice.company), []).append(invoice)

    for (payment_type, company), group in groups.items():
        savepoint = "charge_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(savepoint)
        try:
            voucher_name = create_voucher(group, payment_type, company)
            result["vouchers"].append(
                {
                    "payment": voucher_name,
                    "payment_type": payment_type,
                    "invoices": [i.name for i in group],
                }
            )
        except Exception as exc:
            frappe.db.rollback(save_point=savepoint)
            frappe.log_error(title="Supplier charge payment failed", message=frappe.get_traceback())
            result["errors"].append({"invoices": [i.name for i in group], "message": str(exc)})

    return result


def _originals_with_charge_rows(invoices):
    """The ``return_against`` invoices that actually carry a charge row."""
    targets = {i.return_against for i in invoices if i.is_return and i.return_against}
    if not targets:
        return set()
    return set(find_rows(targets))


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

@frappe.whitelist()
def repair_charge_links():
    """Restore invoice links for rows whose invoice has forgotten them.

    Also exposed so the damage can be repaired without waiting for the invoices to be
    saved one by one.
    """
    rows = frappe.get_all(
        ROW_DOCTYPE,
        filters={"parenttype": "Payments and Receipts", "invoice_form": ["is", "set"]},
        fields=["name", "parent", "invoice_form"],
    )
    if not rows:
        return {"repaired": []}

    docstatus = {}
    for chunk in _chunks({r.parent for r in rows}):
        for name, status in frappe.get_all(
            "Payments and Receipts",
            filters={"name": ["in", chunk]},
            fields=["name", "docstatus"],
            as_list=True,
        ):
            docstatus[name] = status

    repaired = []
    for row in rows:
        if docstatus.get(row.parent) not in (0, 1):
            continue
        current = frappe.db.get_value(
            "Invoice Form", row.invoice_form, ["charges_payment", "charge_payment_line"], as_dict=True
        )
        if not current:
            continue
        if current.charges_payment == row.parent and current.charge_payment_line == row.name:
            continue
        frappe.db.set_value(
            "Invoice Form",
            row.invoice_form,
            {"charges_payment": row.parent, "charge_payment_line": row.name},
            update_modified=False,
        )
        repaired.append({"invoice": row.invoice_form, "voucher": row.parent, "row": row.name})

    return {"repaired": repaired}

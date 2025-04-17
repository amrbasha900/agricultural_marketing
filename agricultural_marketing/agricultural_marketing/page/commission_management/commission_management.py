import json
import frappe
from frappe import _
from agricultural_marketing.standard_doctypes.invoice_form import (
    get_supplier_commission_percentage)
from frappe.utils import getdate


@frappe.whitelist()
def get_invoices(filters):
    invoices = []
    parties = []
    data = {}
    if isinstance(filters, str):
        filters = json.loads(filters)

    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    party_type = filters.get("party_type")
    party = filters.get("party")

    inv_form = frappe.qb.DocType("Invoice Form")
    inv_frmitem = frappe.qb.DocType("Invoice Form Item")
    if party_type == "Supplier":
        query = frappe.qb.from_(inv_form).select(
            inv_form.name.as_("invoice_id"), inv_form.supplier, inv_form.has_supplier_commission_invoice,
            inv_form.grand_total).where(inv_form.has_supplier_commission_invoice == 0).where(inv_form.docstatus == 1)

        if party:
            query = query.where(inv_form.supplier == party)

        prev_invoices = query.where(inv_form.posting_date.lt(from_date)).run(as_dict=True)

        if prev_invoices:
            return {
                "data": {},
                "success": False,
                "msg": _("There are commission invoices that were not created before this duration.")
            }

        invoices = query.where(inv_form.posting_date.between(from_date, to_date)).run(as_dict=True)
        if invoices:
            for invoice in invoices:
                current_supplier = invoice["supplier"]
                supplier_commission = get_supplier_commission_percentage(current_supplier)
                invoice["total"] = (supplier_commission * invoice["grand_total"]) / 100
                if current_supplier in parties:
                    data[current_supplier].append(invoice)
                else:
                    data.setdefault(current_supplier, []).append(invoice)
                    parties.append(invoice["supplier"])

    elif party_type == "Customer":
        query = frappe.qb.from_(inv_form).join(inv_frmitem).on(inv_frmitem.parent == inv_form.name).select(
            inv_frmitem.parent.as_("invoice_id"), inv_frmitem.customer,
            inv_frmitem.customer_commission.as_("total")).where(
            inv_frmitem.has_commission_invoice == 0).where(inv_form.docstatus == 1).where(
            inv_frmitem.customer_commission != 0)

        if party:
            query = query.where(inv_frmitem.customer == party)

        prev_invoices = query.where(inv_form.posting_date.lt(from_date)).groupby(inv_frmitem.parent).run(as_dict=True)

        if prev_invoices:
            return {
                "data": {},
                "success": False,
                "msg": _("There are commission invoices that were not created before this duration.")
            }

        invoices = query.where(inv_form.posting_date.between(from_date, to_date)).run(as_dict=True)

        if invoices:
            for invoice in invoices:
                if invoice.get("total"):
                    current_customer = invoice["customer"]
                    if current_customer in parties:
                        data[current_customer].append(invoice)
                    else:
                        data.setdefault(current_customer, []).append(invoice)
                        parties.append(invoice["customer"])

    return {
        "data": data,
        "success": True,
        "msg": _("Invoices retrieved successfully.")
    }


@frappe.whitelist()
def generate_commission_invoices(data, filters):
    # Parse input if provided as strings
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(filters, str):
        filters = json.loads(filters)

    posting_date = filters.get("posting_date", getdate())
    party_type = filters.get("party_type")

    failed_invoices = create_commission_invoices(data, posting_date, party_type)

    return {
        "data": failed_invoices,
        "msg": (
            "Invoices created but with some missing." if failed_invoices
            else "Invoices created successfully."
        )
    }


def create_commission_invoices(parties, posting_date, party_type):
    failed_invoices = []
    settings = frappe.get_single("Agriculture Settings")
    pos_profile = frappe.get_doc("POS Profile", settings.get("pos_profile"))
    for party in parties:
        party_invoices = parties[party]
        customer = frappe.db.get_value("Supplier", party, "related_customer") if party_type == "Supplier" else party
        if party_type == "Supplier" and not customer:
            continue

        item = settings.get("supplier_commission_item") if party_type == "Supplier" else settings.get(
            "customer_commission_item") or ''
        try:
            commission_invoice = frappe.new_doc("Sales Invoice")
            commission_invoice.update({
                "customer": customer,
                "is_pos": 1,
                "pos_profile": pos_profile.get("name"),
                "posting_date": posting_date,
                "is_commission_invoice": 1
            })
            for invoice in party_invoices:
                if invoice.get("total"):
                    commission_invoice.append("items", {
                        "item_code": item,
                        "description": item + "\n" + invoice.get("invoice_id", ""),
                        "qty": 1,
                        "rate": invoice.get("total"),
                        "invoice_form": invoice.get("invoice_id")
                    })
            default_tax_template = get_tax_template(settings)
            commission_invoice.update({
                "taxes_and_charges": default_tax_template
            })
            commission_invoice.save()
            for mop in pos_profile.get("payments", []):
                if mop.default:
                    default_mop = mop.mode_of_payment

            commission_invoice.append("payments", {
                "mode_of_payment": default_mop,
                "amount": commission_invoice.grand_total
            })
            commission_invoice.save()

            for invoice in party_invoices:
                invoice_form_doc = frappe.get_doc("Invoice Form", invoice.get("invoice_id"))
                if party_type == "Supplier":
                    frappe.db.set_value("Invoice Form", invoice_form_doc.name, "has_supplier_commission_invoice", 1)
                else:
                    for line in invoice_form_doc.items:
                        if line.get("customer") == customer:
                            frappe.db.set_value("Invoice Form Item", line.name, "has_commission_invoice", 1)
        except Exception as e:
            frappe.log_error(title="Creation Commission Invoice Faild", message=frappe.get_traceback())
            failed_invoices.append({
                "invoice_id": invoice.get("invoice_id"),
                "total": invoice.get("total")
            })
            continue

    return failed_invoices


def get_tax_template(settings):
    default_tax_template = settings.get("default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    return default_tax_template

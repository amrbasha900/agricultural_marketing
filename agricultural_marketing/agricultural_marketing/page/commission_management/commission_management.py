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
    inv_frm_comm = frappe.qb.DocType("Invoice Form Commission")
    if party_type == "Supplier":
        commission_subquery = (
        frappe.qb.from_(inv_frm_comm)
        .select(
            inv_frm_comm.parent,
            inv_frm_comm.commission
        )
        .where(inv_frm_comm.parenttype == "Invoice Form")
    )
        query = (
        frappe.qb.from_(inv_form)
        .left_join(inv_frm_comm)
        .on(inv_frm_comm.parent == inv_form.name)
        .select(
            inv_form.name.as_("invoice_id"), 
            inv_form.supplier, 
            inv_form.has_supplier_commission_invoice,
            inv_form.grand_total,
            inv_frm_comm.commission,
            inv_frm_comm.total_commission,
            inv_form.total_commissions_and_taxes
        )
        .where(inv_form.has_supplier_commission_invoice == 0)
        .where(inv_form.docstatus == 1)
        .where(inv_frm_comm.parenttype == "Invoice Form")
        .orderby(inv_form.name)
        .orderby(inv_frm_comm.idx)  # To ensure we get the first record when there are multiple
    )
        if party:
            query = query.where(inv_form.supplier == party)

        prev_invoices = query.where(inv_form.posting_date.lt(from_date)).run(as_dict=True)
        prev_invoices_with_commission = [
            inv for inv in prev_invoices 
            if inv.get("total_commissions_and_taxes", 0) > 0
        ]
        if prev_invoices_with_commission:
            return {
                "data": {},
                "success": False,
                "msg": _("There are commission invoices that were not created before this duration.")
            }

        invoices = query.where(inv_form.posting_date.between(from_date, to_date)).run(as_dict=True)
        if invoices:
            for invoice in invoices:
                current_supplier = invoice["supplier"]
                ##supplier_commission = get_supplier_commission_percentage(current_supplier)

               ## supplier_commission = invoice.get("commission", 0)
                ## invoice["total"] = (supplier_commission * invoice["grand_total"]) / 100

                supplier_commission = invoice.get("total_commission", 0)
                invoice["total"] = supplier_commission

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

        item = (settings.get("supplier_commission_item") if party_type == "Supplier" else settings.get(
            "customer_commission_item")) or ''
        
        # Group invoices by return status
        regular_invoices = []
        return_invoices = []
        
        for invoice in party_invoices:
            if invoice.get("total"):
                # Check if invoice is a return invoice
                is_return = frappe.db.get_value("Invoice Form", invoice.get("invoice_id"), "is_return")
                if is_return:
                    return_invoices.append(invoice)
                else:
                    regular_invoices.append(invoice)
        
        # Create commission invoice for regular invoices
        if regular_invoices:
            try:
                commission_invoice = frappe.new_doc("Sales Invoice")
                commission_invoice.update({
                    "customer": customer,
                    "is_pos": 1,
                    "pos_profile": pos_profile.get("name"),
                    "posting_date": posting_date,
                    "is_commission_invoice": 1
                })
                
                for invoice in regular_invoices:
                    commission_invoice.append("items", {
                        "item_code": item,
                        "description": item + "\n" + invoice.get("invoice_id", ""),
                        "qty": 1,
                        "rate": invoice.get("total"),
                        "invoice_form": invoice.get("invoice_id"),
                        "income_account": frappe.db.get_value("Item", item, "item_defaults.income_account")
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

                # Update invoice form records
                for invoice in regular_invoices:
                    invoice_form_doc = frappe.get_doc("Invoice Form", invoice.get("invoice_id"))
                    if party_type == "Supplier":
                        frappe.db.set_value("Invoice Form", invoice_form_doc.name, "has_supplier_commission_invoice", 1)
                    else:
                        for line in invoice_form_doc.items:
                            if line.get("customer") == customer:
                                frappe.db.set_value("Invoice Form Item", line.name, "has_commission_invoice", 1)
                                
            except Exception as e:
                frappe.log_error(title="Creation Commission Invoice Failed (Regular)", message=frappe.get_traceback())
                for invoice in regular_invoices:
                    failed_invoices.append({
                        "invoice_id": invoice.get("invoice_id"),
                        "total": invoice.get("total")
                    })
        
        # Create commission invoice for return invoices
        if return_invoices:
            try:
                commission_invoice = frappe.new_doc("Sales Invoice")
                commission_invoice.update({
                    "customer": customer,
                    "is_pos": 1,
                    "pos_profile": pos_profile.get("name"),
                    "posting_date": posting_date,
                    "is_commission_invoice": 1,
                    "is_return": 1
                })
                
                # Add return against references
                for invoice in return_invoices:
                    existing_commission_invoice_list = []
                    return_against = frappe.db.get_value("Invoice Form", invoice.get("invoice_id"), "return_against")
                    if return_against:
                        existing_commission_invoice = frappe.db.get_value("Sales Invoice Item", 
                                                                        {"invoice_form": return_against}, 
                                                                        "parent")
                        if existing_commission_invoice and existing_commission_invoice not in existing_commission_invoice_list:
                            commission_invoice.append("custom_return_against_additional_references", {
                                "sales_invoice": existing_commission_invoice,
                            })
                            commission_invoice.custom_return_reason = frappe.db.get_value("Invoice Form", invoice.get("invoice_id"), "return_reason")
                            existing_commission_invoice_list.append(existing_commission_invoice)
                
                for invoice in return_invoices:
                    commission_invoice.append("items", {
                        "item_code": item,
                        "description": item + "\n" + invoice.get("invoice_id", ""),
                        "qty": -1,
                        "rate": abs(invoice.get("total")),
                        "invoice_form": invoice.get("invoice_id"),
                        "income_account": frappe.db.get_value("Item", item, "item_defaults.income_account")
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

                # Update invoice form records
                for invoice in return_invoices:
                    invoice_form_doc = frappe.get_doc("Invoice Form", invoice.get("invoice_id"))
                    if party_type == "Supplier":
                        frappe.db.set_value("Invoice Form", invoice_form_doc.name, "has_supplier_commission_invoice", 1)
                    else:
                        for line in invoice_form_doc.items:
                            if line.get("customer") == customer:
                                frappe.db.set_value("Invoice Form Item", line.name, "has_commission_invoice", 1)
                                
            except Exception as e:
                frappe.log_error(title="Creation Commission Invoice Failed (Return)", message=frappe.get_traceback())
                for invoice in return_invoices:
                    failed_invoices.append({
                        "invoice_id": invoice.get("invoice_id"),
                        "total": invoice.get("total")
                    })

    return failed_invoices

def get_tax_template(settings):
    default_tax_template = settings.get("default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    return default_tax_template

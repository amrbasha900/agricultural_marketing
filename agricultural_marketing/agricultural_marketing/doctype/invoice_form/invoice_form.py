# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt
import frappe
from frappe import _
from frappe.model.document import Document
from erpnext.accounts.general_ledger import validate_accounting_period, make_entry
from erpnext.accounts.party import get_party_account
from frappe.utils import now
import copy
from settings_manager.utils.data import money_in_words


class InvoiceForm(Document):
    settings = frappe.get_single("Agriculture Settings")

    if settings.get("pos_profile") != None:
        pos_profile = frappe.get_doc("POS Profile", settings.get("pos_profile"))
    customer_commission_invoice_refs = []


    def validate(self):
        self.update_grand_total()
        self.update_customer_commission()
        self.update_commission_and_taxes()
        self.add_pamper_commission()
        if not self.is_draft and self.docstatus==0:
            self.auto_send_whatsapp_on_save()
        pamper_name = frappe.db.sql("select pamper from `tabInvoice Form Permission Details` where user = '"+frappe.session.user+"' ", as_dict=1)
        if pamper_name and not self.pamper:
            self.pamper = pamper_name[0].pamper 
        else:
            pass
        validate_customer_credit_limit(self, "validate")

    def on_submit(self):
        self.make_gl_entries()
        if self.settings.get("generate_commission_invoices_automatically"):
            self.generate_supplier_commission_invoice(self.posting_date)
            self.generate_customers_commission_invoices(self.posting_date)

    def on_cancel(self):
        self.cancel_commission_invoice()
        self.make_gl_entries_on_cancel()

    def on_trash(self):
        # delete gl entries on deletion of transaction
        if frappe.db.get_single_value("Accounts Settings", "delete_linked_ledger_entries"):
            gles = frappe.get_all("GL Entry",
                                  {"voucher_type": self.doctype, "voucher_no": self.name}, pluck="name")
            for gle in gles:
                frappe.delete_doc("GL Entry", gle, for_reload=True)
        # delete commission invoice on deletion of transaction
        self.delete_commission_invoice()

    def update_grand_total(self):
        self.grand_total = 0
        for item in self.items:
            self.grand_total += item.total

    def update_commission_and_taxes(self):
        self.total_commissions_and_taxes = 0
        supplier_commission_item = self.settings.get("supplier_commission_item")
        if supplier_commission_item:
            self.set("commissions", [])
            commission_base = self.settings.get("commission_based_on")
            self.set("commissions", [])
            commission_base = self.settings.get("commission_based_on")
            default_tax_template = get_tax_template(self)
            tax_rate = frappe.db.get_value("Sales Taxes and Charges", {"parent": default_tax_template}, "rate") or 0

            if commission_base == 'Supplier':
                supplier_commission_percentage = get_supplier_commission_percentage(self.supplier)

                default_tax_template = get_tax_template(self)
                tax_rate = frappe.db.get_value("Sales Taxes and Charges",
                                            {"parent": default_tax_template}, "rate") or 0

                commission_amount = (self.grand_total * supplier_commission_percentage) / 100
                tax_amount = (commission_amount * tax_rate) / 100
                commission_total_with_taxes = commission_amount + tax_amount
                total_commission_amount = 0
                for item in self.items:
                    item.commission = (item.total * supplier_commission_percentage) / 100
                    total_commission_amount += (item.total * supplier_commission_percentage) / 100
                self.append("commissions", {
                    "item": supplier_commission_item,
                    "price": self.grand_total,
                    "commission": supplier_commission_percentage,
                    "taxes": tax_rate,
                    "commission_total": commission_total_with_taxes,
                    "total_commission": total_commission_amount
                })
                self.total_commissions_and_taxes = commission_total_with_taxes
                for item in self.items:
                    item.commission = (item.total * supplier_commission_percentage) / 100
            elif commission_base == 'Item':
                total_commission_amount = 0
                tax_amount = 0

                for item in self.items:
                    item_commission_percentage = get_item_commission_percentage(item.item_code) or 0
                    item_commission_amount = (item.total * item_commission_percentage) / 100
                    item_tax_amount = (item_commission_amount * tax_rate) / 100
                    total_commission_amount += item_commission_amount
                    tax_amount += item_tax_amount

                    item.commission = item_commission_amount  # Set item-level commission

                self.append("commissions", {
                    "item": supplier_commission_item,
                    "price": self.grand_total,
                    "commission": round((total_commission_amount / self.grand_total) * 100, 2) if self.grand_total else 0,
                    "taxes": tax_rate,
                    "commission_total": total_commission_amount + tax_amount,
                    "total_commission": total_commission_amount
                })

                commission_total_with_taxes = total_commission_amount + tax_amount
                self.total_commissions_and_taxes = commission_total_with_taxes
                
    def make_gl_entries(self):
        gl_entries = []
        if not self.company:
            frappe.throw(_("Please Select a Company"))

        company_defaults = frappe.get_cached_doc("Company", self.company)
        # For credit entry
        self.make_supplier_gl_entry(gl_entries, company_defaults)

        # For debits entries
        self.make_customers_gl_entries(gl_entries, company_defaults)
        if self.settings.get("active_pamper_commission", 0) and self.settings.get("automatic_pamper_commission_creation", 0) and  self.pamper:
            self.make_gl_dict_for_pamper_commission(gl_entries, company_defaults)

        for entry in gl_entries:
            gle = frappe.new_doc("GL Entry")
            gle.update(entry)
            gle.flags.ignore_permissions = True
            gle.submit()

    def make_supplier_gl_entry(self, gl_entries, company_defaults):
        gl_entries.append({
            "posting_date": self.posting_date,
            "due_date": self.posting_date,
            "account": get_party_account("Supplier", self.supplier, self.company),
            "party_type": "Supplier",
            "party": self.supplier,
            "credit": self.grand_total,
            "account_currency": company_defaults.default_currency,
            "credit_in_account_currency": self.grand_total,
            "voucher_type": self.doctype,
            "voucher_no": self.name,
            "company": self.company,
            "cost_center": company_defaults.cost_center,
            "credit_in_transaction_currency": self.grand_total,
            "transaction_exchange_rate": 1
        })
        self.make_gl_dict_for_commission(gl_entries, company_defaults)

    def make_customers_gl_entries(self, gl_entries, company_defaults):
        customers = []
        for it in self.items:
            if it.couple_customer:
                couple_supplier = frappe.db.get_value('Supplier',{'related_customer': it.customer}, 'name')
                
                if couple_supplier in customers:
                    customer_record = [d for d in gl_entries if d.get("party") == couple_supplier][0]
                    customer_record.update({
                        "debit": customer_record["debit"] + it.total,
                        "debit_in_account_currency": customer_record["debit_in_account_currency"] + it.total,
                        "debit_in_transaction_currency": customer_record["debit_in_transaction_currency"] + it.total,
                    })

                else:
                    gl_entries.append({
                        "posting_date": self.posting_date,
                        "due_date": self.posting_date,
                        "account": get_party_account("Supplier", couple_supplier, self.company),
                        "party_type": "Supplier",
                        "party": couple_supplier,
                        "debit": it.total,
                        "account_currency": company_defaults.default_currency,
                        "debit_in_account_currency": it.total,
                        "voucher_type": self.doctype,
                        "voucher_no": self.name,
                        "company": self.company,
                        "cost_center": company_defaults.cost_center,
                        "debit_in_transaction_currency": it.total,
                        "transaction_exchange_rate": 1
                    })
                    customers.append(couple_supplier)
                #self.make_gl_dict_for_commission(gl_entries, company_defaults)


            else:
                if it.customer in customers:
                    customer_record = [d for d in gl_entries if d.get("party") == it.customer][0]
                    customer_record.update({
                        "debit": customer_record["debit"] + it.total,
                        "debit_in_account_currency": customer_record["debit_in_account_currency"] + it.total,
                        "debit_in_transaction_currency": customer_record["debit_in_transaction_currency"] + it.total,
                    })
                else:
                    gl_entries.append({
                        "posting_date": self.posting_date,
                        "due_date": self.posting_date,
                        "account": get_party_account("Customer", it.customer, self.company),
                        "party_type": "Customer",
                        "party": it.customer,
                        "debit": it.total,
                        "account_currency": company_defaults.default_currency,
                        "debit_in_account_currency": it.total,
                        "voucher_type": self.doctype,
                        "voucher_no": self.name,
                        "company": self.company,
                        "cost_center": company_defaults.cost_center,
                        "debit_in_transaction_currency": it.total,
                        "transaction_exchange_rate": 1
                    })
                    customers.append(it.customer)

    def make_gl_entries_on_cancel(self):
        gl_entry = frappe.qb.DocType("GL Entry")
        gl_entries = (
            frappe.qb.from_(gl_entry)
            .select("*")
            .where(gl_entry.voucher_type == self.doctype)
            .where(gl_entry.voucher_no == self.name)
            .where(gl_entry.is_cancelled == 0)
            .for_update()
        ).run(as_dict=1)
        if gl_entries:
            self.flags.ignore_links = True
            validate_accounting_period(gl_entries)
            set_as_cancel(self.doctype, self.name)

            for entry in gl_entries:
                new_gle = copy.deepcopy(entry)
                new_gle["name"] = None
                debit = new_gle.get("debit", 0)
                credit = new_gle.get("credit", 0)

                debit_in_account_currency = new_gle.get("debit_in_account_currency", 0)
                credit_in_account_currency = new_gle.get("credit_in_account_currency", 0)

                new_gle["debit"] = credit
                new_gle["credit"] = debit
                new_gle["debit_in_account_currency"] = credit_in_account_currency
                new_gle["credit_in_account_currency"] = debit_in_account_currency

                new_gle["remarks"] = "On cancellation of " + new_gle["voucher_no"]
                new_gle["is_cancelled"] = 1

                if new_gle["debit"] or new_gle["credit"]:
                    make_entry(new_gle, False, "Yes")

    def generate_supplier_commission_invoice(self, posting_date):
        if len(self.commissions) == 0:
            return

        # check supplier's related customer
        supplier_related_customer = frappe.db.get_value("Supplier", self.supplier, "related_customer")
        if not supplier_related_customer:
            frappe.throw(_("Supplier is not linked to a customer"))

        # Calculate total commission amount
        total_commission = 0
        for it in self.commissions:
            total_commission += (it.price * it.commission) / 100

        # Generate the commission sales invoice
        create_commission_invoice(self, supplier_related_customer, self.pos_profile, total_commission, posting_date,
                                  "Supplier")
        self.db_set("has_supplier_commission_invoice", 1)

    def generate_customers_commission_invoices(self, posting_date):
        customers = []
        si_entries = []
        for it in self.items:
            if it.customer in customers:
                customer_record = [d["items"][0] for d in si_entries if d.get("customer") == it.customer][0]
                customer_record.update({
                    "rate": customer_record["rate"] + it.customer_commission,
                })
            else:
                si_entries.append({
                    "customer": it.customer,
                    "is_pos": 1,
                    "pos_profile": self.pos_profile.get("name"),
                    "items": [{
                        "item_code": self.settings.get("customer_commission_item"),
                        "description": self.settings.get("customer_commission_item") + "\n" + self.name,
                        "qty": 1,
                        "rate": it.customer_commission
                    }]
                })
                customers.append(it.customer)
            frappe.db.set_value("Invoice Form Item", it.name, "has_commission_invoice", 1)

        for entry in si_entries:
            customer_total_commission = entry["items"][0]["rate"]
            if customer_total_commission:
                create_commission_invoice(self, entry["customer"], self.pos_profile, entry["items"][0]["rate"],
                                          posting_date, "Customer")

    def cancel_commission_invoice(self):

        sales_invoices_ids = frappe.get_all("Sales Invoice Item", {"invoice_form": self.name},
                                            pluck="parent")
        for invoice_id in sales_invoices_ids:
            sales_invoice = frappe.get_doc("Sales Invoice", invoice_id)
            if sales_invoice.docstatus == 0:
                delete_reference_invoice(sales_invoice)
            if sales_invoice.docstatus == 1:
                sales_invoice.cancel()

        self.db_set("has_supplier_commission_invoice", 0)
        for it in self.items:
            frappe.db.set_value("Invoice Form Item", it.name, "has_commission_invoice", 0)

    def delete_commission_invoice(self):
        sales_invoices_ids = frappe.get_all("Sales Invoice Item", {"invoice_form": self.name},
                                            pluck="parent")
        for invoice_id in sales_invoices_ids:
            sales_invoice = frappe.get_doc("Sales Invoice", invoice_id)
            if sales_invoice.docstatus in [0, 2]:
                delete_reference_invoice(sales_invoice)

            if sales_invoice.docstatus == 1:
                sales_invoice.cancel()

        self.db_set("has_supplier_commission_invoice", 0)
        for it in self.items:
            frappe.db.set_value("Invoice Form Item", it.name, "has_commission_invoice", 0)

    def make_gl_dict_for_commission(self, gl_entries, company_defaults):
        if len(self.commissions) != 0 and self.total_commissions_and_taxes:
            mops = frappe.get_doc("POS Profile", self.settings.get("pos_profile")).get("payments")
            for mop in mops:
                if mop.default:
                    default_commission_account = frappe.db.get_value(
                        "Mode of Payment Account",
                        {"parent": mop.mode_of_payment, "company": self.company},
                        "default_account",
                    )
                    break

            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": get_party_account("Supplier", self.supplier, self.company),
                "party_type": "Supplier",
                "party": self.supplier,
                "debit": self.total_commissions_and_taxes,
                "account_currency": company_defaults.default_currency,
                "debit_in_account_currency": self.total_commissions_and_taxes,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "debit_in_transaction_currency": self.total_commissions_and_taxes,
                "transaction_exchange_rate": 1
            })

            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": default_commission_account,
                "credit": self.total_commissions_and_taxes,
                "account_currency": company_defaults.default_currency,
                "credit_in_account_currency": self.total_commissions_and_taxes,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "credit_in_transaction_currency": self.total_commissions_and_taxes,
                "transaction_exchange_rate": 1
            })

    def update_customer_commission(self):
        for item in self.items:
            customer_doc = frappe.get_doc("Customer", item.customer)
            if customer_doc.commission_type and customer_doc.commission_type.lower() == "percent":
                item.customer_commission = (item.total * customer_doc.commission) / 100
            elif customer_doc.commission_type and customer_doc.commission_type.lower() == "amount":
                item.customer_commission = (item.qty * customer_doc.commission)

    #####Adding Pamper Commission Calulation#################
    def add_pamper_commission(self):
        if self.settings.get("active_pamper_commission", 0):
            self.pamper_commission = self.grand_total * (self.settings.get("pamper_commission", 0)/100)

    # In the InvoiceForm class, add this new method:
    def make_gl_dict_for_pamper_commission(self, gl_entries, company_defaults):
        if self.settings.get("active_pamper_commission", 0) and self.settings.get("automatic_pamper_commission_creation", 0) and self.pamper_commission and self.pamper:
            # Get the pamper commission account from settings
            pamper_commission_account = self.settings.get("pamper_commission_account")
            if not pamper_commission_account:
                frappe.throw(_("Pamper Commission Account not set in Agriculture Settings"))
            
            # Entry 1: Debit the pamper commission expense account (cost to the company)
            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": pamper_commission_account,  # Commission expense account
                "debit": self.pamper_commission,
                "account_currency": company_defaults.default_currency,
                "debit_in_account_currency": self.pamper_commission,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "debit_in_transaction_currency": self.pamper_commission,
                "transaction_exchange_rate": 1,
                "remarks": "Pamper commission expense"
            })
            
            # Entry 2: Credit the pamper's customer account (payable to pamper)
            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": get_party_account("Customer", self.pamper, self.company),
                "party_type": "Customer",
                "party": self.pamper,
                "credit": self.pamper_commission,
                "account_currency": company_defaults.default_currency,
                "credit_in_account_currency": self.pamper_commission,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "credit_in_transaction_currency": self.pamper_commission,
                "transaction_exchange_rate": 1,
                "remarks": "Pamper commission payable"
            })

    ###send pdf whatsapp
    def auto_send_whatsapp_on_save(self):
        """Auto send WhatsApp to enabled parties on save"""
        try:
            # Get unique customers
            customers = list(set([item.customer for item in self.items if item.customer]))
            
            # Check enabled parties
            enabled_customers = []
            for customer in customers:
                if frappe.db.get_value("Customer", customer, "send_invoice_via_whatsapp"):
                    enabled_customers.append(customer)
            
            supplier_enabled = bool(frappe.db.get_value("Supplier", self.supplier, "send_invoice_via_whatsapp"))
            
            # Send to enabled parties
            if enabled_customers or supplier_enabled:
                frappe.enqueue(
                    method='agricultural_marketing.agricultural_marketing.doctype.invoice_form.invoice_form.auto_send_whatsapp_background',
                    queue='short',
                    timeout=300,
                    invoice_name=self.name,
                    customers=enabled_customers,
                    supplier=self.supplier if supplier_enabled else None
                )
                
        except Exception as e:
            frappe.log_error(f"Auto WhatsApp send error: {str(e)}", "Auto WhatsApp Send")

def set_as_cancel(voucher_type, voucher_no):
    """
    Set is_cancelled=1 in all original gl entries for the voucher
    """
    frappe.db.sql(
        """UPDATE `tabGL Entry` SET is_cancelled = 1,
        modified=%s, modified_by=%s
        where voucher_type=%s and voucher_no=%s and is_cancelled = 0""",
        (now(), frappe.session.user, voucher_type, voucher_no),
    )


def get_supplier_commission_percentage(supplier):
    """
    Returns the commission percentage for the given `supplier`.
    Will first search in party (Supplier) record, if not found,
    will search in group (Supplier Group),
    finally will return default."""

    apply_commission = frappe.db.get_value("Supplier", supplier, "apply_commission")
    if not apply_commission:
        return 0

    # Get the percentage from the party doc
    commission_percentage = frappe.db.get_value("Supplier", supplier, "commission_percentage")
    if commission_percentage:
        return commission_percentage

    # Get the percentage from the party group doc
    party_group = frappe.db.get_value("Supplier", supplier, "supplier_group")
    commission_percentage = frappe.db.get_value("Supplier Group", party_group, "commission_percentage")
    if commission_percentage:
        return commission_percentage

    # Get the percentage from the Agriculture Settings single doc
    return frappe.get_single("Agriculture Settings").get("customer_commission_percentage", 0)


def create_commission_invoice(invoice, customer, pos_profile, total_commission, posting_date, party_type="Supplier"):
    item = invoice.settings.get("supplier_commission_item") if party_type == "Supplier" else invoice.settings.get(
        "customer_commission_item")
    commission_invoice = frappe.new_doc("Sales Invoice")
    commission_invoice.update({
        "customer": customer,
        "is_pos": 1,
        "pos_profile": pos_profile.get("name"),
        "posting_date": posting_date,
        "is_commission_invoice": 1
    })
    commission_invoice.append("items", {
        "item_code": item,
        "description": item + "\n" + invoice.name,
        "qty": 1,
        "rate": total_commission,
        "invoice_form": invoice.name
    })
    default_tax_template = get_tax_template(invoice)
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
    return commission_invoice


def get_tax_template(invoice):
    default_tax_template = invoice.settings.get("default_tax")

    if not default_tax_template:
        default_tax_template = frappe.db.get_value("Sales Taxes and Charges",
                                                   {"is_default": 1}, "name")

    return default_tax_template


def delete_reference_invoice(ref_invoice):
    ref_invoice.run_method("on_trash")
    frappe.delete_doc("Sales Invoice", ref_invoice.name, for_reload=True)


def build_pdf_template_context(filters):
    invform = frappe.qb.DocType("Invoice Form")
    invformitem = frappe.qb.DocType("Invoice Form Item")

    if filters.get("party_type") == "Supplier":
        res = [frappe.get_doc(filters.get("reference_doctype"), filters.get("reference_name")).as_dict()]
        total_commission_percentage = sum([row["commission"] for row in res[0].get("commissions", [])]) or 0
        total_taxes_rate = sum([row["taxes"] for row in res[0].get("commissions", [])]) or 0
        total_commission = (res[0].grand_total * total_commission_percentage) / 100 or 0
        total_taxes = (total_commission * total_taxes_rate) / 100 or 0
        res[0].update({
            "total_commission": total_commission,
            "total_taxes": total_taxes,
            "net_total": res[0].grand_total - res[0].total_commissions_and_taxes,
            "net_total_in_words": money_in_words((res[0].grand_total - res[0].total_commissions_and_taxes))

        })

    else:
        inv_query = frappe.qb.from_(invform).join(invformitem)

        if filters.get("customer_type") == "Customer":
            res = inv_query.on(
                (invformitem.parent == invform.name) & (invformitem.customer == filters.get("party"))).where(
                invform.name == filters.get("reference_name")).select(
                invform.supplier, invform.customer.as_('inv_customer'),
                invform.name, invform.company, invform.posting_date, invformitem.customer,
                invformitem.item_name, invformitem.qty, invformitem.price, invformitem.total).run(
                as_dict=True)
            res[0].update({
                "net_total": sum([row["total"] for row in res]) or 0,
                "net_total_in_words": money_in_words(sum([row["total"] for row in res]) or 0)
            })
        else:
            res = inv_query.on(
                (invformitem.parent == invform.name) & (invformitem.pamper == filters.get("party"))).where(
                invform.name == filters.get("reference_name")).select(
                invform.supplier, invform.customer.as_('inv_customer'),
                invform.name, invform.company, invform.posting_date, invformitem.pamper,
                invformitem.item_name, invformitem.qty, invformitem.price, invformitem.total).run(
                as_dict=True)
            res[0].update({
                "net_total": sum([row["total"] for row in res]) or 0,
                "net_total_in_words": money_in_words(sum([row["total"] for row in res]) or 0)
            })

    return res



import json
import random
import time

# Add these functions at the end of your invoice_form.py file

@frappe.whitelist()
def check_customers_whatsapp_enabled(customers):
    """Check which customers have WhatsApp sending enabled"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    enabled_customers = []
    disabled_customers = []
    
    for customer in customers:
        send_via_whatsapp = frappe.db.get_value("Customer", customer, "send_invoice_via_whatsapp")
        if send_via_whatsapp:
            enabled_customers.append(customer)
        else:
            disabled_customers.append(customer)
    
    return {
        "enabled_customers": enabled_customers,
        "disabled_customers": disabled_customers
    }

@frappe.whitelist()
def check_supplier_whatsapp_enabled(supplier):
    """Check if supplier has WhatsApp sending enabled"""
    send_via_whatsapp = frappe.db.get_value("Supplier", supplier, "send_invoice_via_whatsapp")
    return {"enabled": bool(send_via_whatsapp)}

@frappe.whitelist()
def check_all_parties_whatsapp_enabled(customers, supplier):
    """Check WhatsApp status for all parties"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    # Check customers
    enabled_customers = []
    disabled_customers = []
    for customer in customers:
        if frappe.db.get_value("Customer", customer, "send_invoice_via_whatsapp"):
            enabled_customers.append(customer)
        else:
            disabled_customers.append(customer)
    
    # Check supplier
    supplier_enabled = bool(frappe.db.get_value("Supplier", supplier, "send_invoice_via_whatsapp"))
    
    return {
        "enabled_customers": enabled_customers,
        "disabled_customers": disabled_customers,
        "supplier_enabled": supplier_enabled
    }

@frappe.whitelist()
def send_invoice_whatsapp_bulk(invoice_name, customers):
    """Send invoice via WhatsApp to multiple customers"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    try:
        invoice_doc = frappe.get_doc("Invoice Form", invoice_name)
        success_count = 0
        error_count = 0
        errors = []
        
        for customer in customers:
            try:
                result = create_and_send_customer_invoice_whatsapp(invoice_doc, customer)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{customer}: {result.get('error', 'Unknown error')}")
                time.sleep(1)
            except Exception as e:
                error_count += 1
                errors.append(f"{customer}: {str(e)}")
        
        frappe.db.commit()
        
        if success_count > 0:
            message = f"WhatsApp messages sent to {success_count} customer(s)"
            if error_count > 0:
                message += f". {error_count} failed"
            return {"success": True, "message": message}
        else:
            return {"error": f"All sends failed: {'; '.join(errors[:2])}"}
            
    except Exception as e:
        frappe.log_error(f"Error in send_invoice_whatsapp_bulk: {str(e)}", "WhatsApp Bulk Send")
        return {"error": str(e)}

@frappe.whitelist()
def send_invoice_whatsapp_supplier(invoice_name, supplier):
    """Send invoice via WhatsApp to supplier"""
    try:
        invoice_doc = frappe.get_doc("Invoice Form", invoice_name)
        result = create_and_send_supplier_invoice_whatsapp(invoice_doc, supplier)
        
        frappe.db.commit()
        
        if result.get("success"):
            return {"success": True, "message": "WhatsApp message sent to supplier"}
        else:
            return {"error": result.get("error", "Failed to send to supplier")}
            
    except Exception as e:
        frappe.log_error(f"Error in send_invoice_whatsapp_supplier: {str(e)}", "WhatsApp Supplier Send")
        return {"error": str(e)}

@frappe.whitelist()
def send_invoice_whatsapp_all(invoice_name, customers, supplier=None):
    """Send invoice via WhatsApp to all enabled parties"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    try:
        invoice_doc = frappe.get_doc("Invoice Form", invoice_name)
        success_count = 0
        error_count = 0
        errors = []
        
        # Send to customers
        for customer in customers:
            try:
                result = create_and_send_customer_invoice_whatsapp(invoice_doc, customer)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"Customer {customer}: {result.get('error', 'Unknown error')}")
                time.sleep(1)
            except Exception as e:
                error_count += 1
                errors.append(f"Customer {customer}: {str(e)}")
        
        # Send to supplier if enabled
        if supplier:
            try:
                result = create_and_send_supplier_invoice_whatsapp(invoice_doc, supplier)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"Supplier {supplier}: {result.get('error', 'Unknown error')}")
            except Exception as e:
                error_count += 1
                errors.append(f"Supplier {supplier}: {str(e)}")
        
        frappe.db.commit()
        
        if success_count > 0:
            message = f"WhatsApp messages sent to {success_count} recipient(s)"
            if error_count > 0:
                message += f". {error_count} failed"
            return {"success": True, "message": message}
        else:
            return {"error": f"All sends failed: {'; '.join(errors[:2])}"}
            
    except Exception as e:
        frappe.log_error(f"Error in send_invoice_whatsapp_all: {str(e)}", "WhatsApp All Send")
        return {"error": str(e)}

def create_and_send_customer_invoice_whatsapp(invoice_doc, customer):
    """Create PDF for specific customer and send via WhatsApp"""
    try:
        customer_doc = frappe.get_doc("Customer", customer)
        if not customer_doc.get("send_invoice_via_whatsapp"):
            return {"error": "WhatsApp sending not enabled"}
        
        whatsapp_number = customer_doc.get("whatsapp_number")
        if not whatsapp_number:
            return {"error": "WhatsApp number not found"}
        
        # Generate PDF using your existing build_pdf_template_context
        filters = {
            "reference_doctype": invoice_doc.doctype,
            "reference_name": invoice_doc.name,
            "party_type": "Customer",
            "party": customer,
            "customer_type": "Customer"
        }
        
        pdf_url = generate_customer_invoice_pdf_whatsapp(filters, customer)
        if not pdf_url:
            return {"error": "Failed to generate PDF"}
        
        whatsapp_result = create_whatsapp_message_for_customer_invoice(
            customer=customer,
            pdf_url=pdf_url,
            invoice_name=invoice_doc.name,
            customer_doc=customer_doc
        )
        
        if whatsapp_result:
            return {"success": True}
        else:
            return {"error": "Failed to create WhatsApp message"}
            
    except Exception as e:
        return {"error": str(e)}

def create_and_send_supplier_invoice_whatsapp(invoice_doc, supplier):
    """Create PDF for supplier and send via WhatsApp"""
    try:
        supplier_doc = frappe.get_doc("Supplier", supplier)
        if not supplier_doc.get("send_invoice_via_whatsapp"):
            return {"error": "WhatsApp sending not enabled"}
        
        whatsapp_number = supplier_doc.get("whatsapp_number")
        if not whatsapp_number:
            return {"error": "WhatsApp number not found"}
        
        # Generate PDF for supplier
        filters = {
            "reference_doctype": invoice_doc.doctype,
            "reference_name": invoice_doc.name,
            "party_type": "Supplier",
            "party": supplier,
            "customer_type": ""
        }
        
        pdf_url = generate_supplier_invoice_pdf_whatsapp(filters, supplier)
        if not pdf_url:
            return {"error": "Failed to generate PDF"}
        
        whatsapp_result = create_whatsapp_message_for_supplier_invoice(
            supplier=supplier,
            pdf_url=pdf_url,
            invoice_name=invoice_doc.name,
            supplier_doc=supplier_doc
        )
        
        if whatsapp_result:
            return {"success": True}
        else:
            return {"error": "Failed to create WhatsApp message"}
            
    except Exception as e:
        return {"error": str(e)}

def generate_customer_invoice_pdf_whatsapp(filters, customer):
    """Generate PDF for customer using existing template logic"""
    try:
        from agricultural_marketing.pdf import _get_pdf
        
        # Get letter head
        letter_head = None
        invoice_doc = frappe.get_doc("Invoice Form", filters['reference_name'])
        default_letter_head = frappe.get_value("Company", invoice_doc.company, "default_letter_head")
        if default_letter_head:
            letter_head = frappe.get_doc("Letter Head", default_letter_head)
        
        # Use your existing build_pdf_template_context function
        context_data = build_pdf_template_context(filters)
        
        # Use your existing template
        html = frappe.render_template("agricultural_marketing/agricultural_marketing/doctype/invoice_form/invoice_form_whatsapp_pdf.html", {
            "data": context_data,
            "filters": filters,
            "letter_head": letter_head,
            "lang": 'ar',
            "layout_direction": "rtl"
        })
        
        content = _get_pdf(html, {"orientation": "Portrait"})
        
        file_name = f"Invoice-{filters['reference_name']}-{customer}-{random.randint(1000, 9999)}.pdf"
        file_doc = frappe.new_doc("File")
        file_doc.update({
            "file_name": file_name,
            "is_private": 0,
            "content": content
        })
        file_doc.save(ignore_permissions=True)
        
        return file_doc.file_url
        
    except Exception as e:
        frappe.log_error(f"PDF generation error for customer {customer}: {str(e)}", "PDF Generation")
        return None

def generate_supplier_invoice_pdf_whatsapp(filters, supplier):
    """Generate PDF for supplier using existing template logic"""
    try:
        from agricultural_marketing.pdf import _get_pdf
        
        # Get letter head
        letter_head = None
        invoice_doc = frappe.get_doc("Invoice Form", filters['reference_name'])
        default_letter_head = frappe.get_value("Company", invoice_doc.company, "default_letter_head")
        if default_letter_head:
            letter_head = frappe.get_doc("Letter Head", default_letter_head)
        
        # Use your existing build_pdf_template_context function
        context_data = build_pdf_template_context(filters)
        
        # Use your existing template
        html = frappe.render_template("agricultural_marketing/agricultural_marketing/doctype/invoice_form/invoice_form_whatsapp_pdf.html", {
            "data": context_data,
            "filters": filters,
            "letter_head": letter_head,
            "lang": 'ar',
            "layout_direction": "rtl"
        })
        
        content = _get_pdf(html, {"orientation": "Portrait"})
        
        file_name = f"Invoice-{filters['reference_name']}-Supplier-{supplier}-{random.randint(1000, 9999)}.pdf"
        file_doc = frappe.new_doc("File")
        file_doc.update({
            "file_name": file_name,
            "is_private": 0,
            "content": content
        })
        file_doc.save(ignore_permissions=True)
        
        return file_doc.file_url
        
    except Exception as e:
        frappe.log_error(f"PDF generation error for supplier {supplier}: {str(e)}", "PDF Generation")
        return None

def create_whatsapp_message_for_customer_invoice(customer, pdf_url, invoice_name, customer_doc):
    """Create WhatsApp message entry for customer"""
    try:
        default_message = customer_doc.get("default_whatsapp_message") or f"Invoice {invoice_name} is ready"
        
        whatsapp_message = frappe.new_doc("WhatsApp Messages")
        whatsapp_message.update({
            "party_type": "Customer",
            "party_name": customer,
            "phone_number": customer_doc.get("whatsapp_number"),
            "has_media": 1,
            "auto_send": 1,
            "message": default_message,
            "status": "Queued",
            "attach": pdf_url,
            "reference_document": "Invoice Form",
            "document_name": invoice_name
        })
        
        whatsapp_message.insert(ignore_permissions=True)
        return whatsapp_message.name
        
    except Exception as e:
        frappe.log_error(f"WhatsApp message creation error for {customer}: {str(e)}")
        return None

def create_whatsapp_message_for_supplier_invoice(supplier, pdf_url, invoice_name, supplier_doc):
    """Create WhatsApp message entry for supplier"""
    try:
        default_message = supplier_doc.get("default_whatsapp_message") or f"Invoice {invoice_name} is ready"
        
        whatsapp_message = frappe.new_doc("WhatsApp Messages")
        whatsapp_message.update({
            "party_type": "Supplier",
            "party_name": supplier,
            "phone_number": supplier_doc.get("whatsapp_number"),
            "has_media": 1,
            "auto_send": 1,
            "message": default_message,
            "status": "Queued",
            "attach": pdf_url,
            "reference_document": "Invoice Form",
            "document_name": invoice_name
        })
        
        whatsapp_message.insert(ignore_permissions=True)
        return whatsapp_message.name
        
    except Exception as e:
        frappe.log_error(f"WhatsApp message creation error for supplier {supplier}: {str(e)}")
        return None


@frappe.whitelist()
def send_invoice_whatsapp_bulk_manual(invoice_name, customers):
    """Send invoice via WhatsApp to multiple customers (manual - no enable check)"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    try:
        invoice_doc = frappe.get_doc("Invoice Form", invoice_name)
        success_count = 0
        error_count = 0
        errors = []
        
        for customer in customers:
            try:
                result = create_and_send_customer_invoice_whatsapp_manual(invoice_doc, customer)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{customer}: {result.get('error', 'Unknown error')}")
                time.sleep(1)
            except Exception as e:
                error_count += 1
                errors.append(f"{customer}: {str(e)}")
        
        frappe.db.commit()
        
        if success_count > 0:
            message = f"WhatsApp messages sent to {success_count} customer(s)"
            if error_count > 0:
                message += f". {error_count} failed"
            return {"success": True, "message": message}
        else:
            return {"error": f"All sends failed: {'; '.join(errors[:2])}"}
            
    except Exception as e:
        frappe.log_error(f"Error in send_invoice_whatsapp_bulk_manual: {str(e)}", "WhatsApp Bulk Send Manual")
        return {"error": str(e)}

@frappe.whitelist()
def send_invoice_whatsapp_supplier_manual(invoice_name, supplier):
    """Send invoice via WhatsApp to supplier (manual - no enable check)"""
    try:
        invoice_doc = frappe.get_doc("Invoice Form", invoice_name)
        result = create_and_send_supplier_invoice_whatsapp_manual(invoice_doc, supplier)
        
        frappe.db.commit()
        
        if result.get("success"):
            return {"success": True, "message": "WhatsApp message sent to supplier"}
        else:
            return {"error": result.get("error", "Failed to send to supplier")}
            
    except Exception as e:
        frappe.log_error(f"Error in send_invoice_whatsapp_supplier_manual: {str(e)}", "WhatsApp Supplier Send Manual")
        return {"error": str(e)}

@frappe.whitelist()
def send_invoice_whatsapp_all_manual(invoice_name, customers, supplier=None):
    """Send invoice via WhatsApp to all parties (manual - no enable check)"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    try:
        invoice_doc = frappe.get_doc("Invoice Form", invoice_name)
        success_count = 0
        error_count = 0
        errors = []
        
        # Send to customers
        for customer in customers:
            try:
                result = create_and_send_customer_invoice_whatsapp_manual(invoice_doc, customer)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"Customer {customer}: {result.get('error', 'Unknown error')}")
                time.sleep(1)
            except Exception as e:
                error_count += 1
                errors.append(f"Customer {customer}: {str(e)}")
        
        # Send to supplier if provided
        if supplier:
            try:
                result = create_and_send_supplier_invoice_whatsapp_manual(invoice_doc, supplier)
                if result.get("success"):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"Supplier {supplier}: {result.get('error', 'Unknown error')}")
            except Exception as e:
                error_count += 1
                errors.append(f"Supplier {supplier}: {str(e)}")
        
        frappe.db.commit()
        
        if success_count > 0:
            message = f"WhatsApp messages sent to {success_count} recipient(s)"
            if error_count > 0:
                message += f". {error_count} failed"
            return {"success": True, "message": message}
        else:
            return {"error": f"All sends failed: {'; '.join(errors[:2])}"}
            
    except Exception as e:
        frappe.log_error(f"Error in send_invoice_whatsapp_all_manual: {str(e)}", "WhatsApp All Send Manual")
        return {"error": str(e)}

def create_and_send_customer_invoice_whatsapp_manual(invoice_doc, customer):
    """Create PDF for specific customer and send via WhatsApp (manual - no enable check)"""
    try:
        customer_doc = frappe.get_doc("Customer", customer)
        
        whatsapp_number = customer_doc.get("whatsapp_number")
        if not whatsapp_number:
            return {"error": "WhatsApp number not found"}
        
        # Generate PDF using your existing build_pdf_template_context
        filters = {
            "reference_doctype": invoice_doc.doctype,
            "reference_name": invoice_doc.name,
            "party_type": "Customer",
            "party": customer,
            "customer_type": "Customer"
        }
        
        pdf_url = generate_customer_invoice_pdf_whatsapp(filters, customer)
        if not pdf_url:
            return {"error": "Failed to generate PDF"}
        
        whatsapp_result = create_whatsapp_message_for_customer_invoice_manual(
            customer=customer,
            pdf_url=pdf_url,
            invoice_name=invoice_doc.name,
            customer_doc=customer_doc
        )
        
        if whatsapp_result:
            return {"success": True}
        else:
            return {"error": "Failed to create WhatsApp message"}
            
    except Exception as e:
        return {"error": str(e)}

def create_and_send_supplier_invoice_whatsapp_manual(invoice_doc, supplier):
    """Create PDF for supplier and send via WhatsApp (manual - no enable check)"""
    try:
        supplier_doc = frappe.get_doc("Supplier", supplier)
        
        whatsapp_number = supplier_doc.get("whatsapp_number")
        if not whatsapp_number:
            return {"error": "WhatsApp number not found"}
        
        # Generate PDF for supplier
        filters = {
            "reference_doctype": invoice_doc.doctype,
            "reference_name": invoice_doc.name,
            "party_type": "Supplier",
            "party": supplier,
            "customer_type": ""
        }
        
        pdf_url = generate_supplier_invoice_pdf_whatsapp(filters, supplier)
        if not pdf_url:
            return {"error": "Failed to generate PDF"}
        
        whatsapp_result = create_whatsapp_message_for_supplier_invoice_manual(
            supplier=supplier,
            pdf_url=pdf_url,
            invoice_name=invoice_doc.name,
            supplier_doc=supplier_doc
        )
        
        if whatsapp_result:
            return {"success": True}
        else:
            return {"error": "Failed to create WhatsApp message"}
            
    except Exception as e:
        return {"error": str(e)}

def create_whatsapp_message_for_customer_invoice_manual(customer, pdf_url, invoice_name, customer_doc):
    """Create WhatsApp message entry for customer (manual - no enable check)"""
    try:
        default_message = customer_doc.get("default_whatsapp_message") or f"Invoice {invoice_name} is ready"
        
        whatsapp_message = frappe.new_doc("WhatsApp Messages")
        whatsapp_message.update({
            "party_type": "Customer",
            "party_name": customer,
            "phone_number": customer_doc.get("whatsapp_number"),
            "has_media": 1,
            "auto_send": 1,
            "message": default_message,
            "status": "Queued",
            "attach": pdf_url,
            "reference_document": "Invoice Form",
            "document_name": invoice_name
        })
        
        whatsapp_message.insert(ignore_permissions=True)
        return whatsapp_message.name
        
    except Exception as e:
        frappe.log_error(f"WhatsApp message creation error for {customer}: {str(e)}")
        return None

def create_whatsapp_message_for_supplier_invoice_manual(supplier, pdf_url, invoice_name, supplier_doc):
    """Create WhatsApp message entry for supplier (manual - no enable check)"""
    try:
        default_message = supplier_doc.get("default_whatsapp_message") or f"Invoice {invoice_name} is ready"
        
        whatsapp_message = frappe.new_doc("WhatsApp Messages")
        whatsapp_message.update({
            "party_type": "Supplier",
            "party_name": supplier,
            "phone_number": supplier_doc.get("whatsapp_number"),
            "has_media": 1,
            "auto_send": 1,
            "message": default_message,
            "status": "Queued",
            "attach": pdf_url,
            "reference_document": "Invoice Form",
            "document_name": invoice_name
        })
        
        whatsapp_message.insert(ignore_permissions=True)
        return whatsapp_message.name
        
    except Exception as e:
        frappe.log_error(f"WhatsApp message creation error for supplier {supplier}: {str(e)}")
        return None

# Also add a function to get parties with WhatsApp numbers (for JS validation)
@frappe.whitelist()
def get_parties_with_whatsapp_numbers(customers, supplier=None):
    """Get parties that have WhatsApp numbers configured"""
    if isinstance(customers, str):
        customers = json.loads(customers)
    
    result = {
        "customers_with_whatsapp": [],
        "customers_without_whatsapp": [],
        "supplier_has_whatsapp": False
    }
    
    # Check customers
    for customer in customers:
        whatsapp_number = frappe.db.get_value("Customer", customer, "whatsapp_number")
        if whatsapp_number:
            result["customers_with_whatsapp"].append(customer)
        else:
            result["customers_without_whatsapp"].append(customer)
    
    # Check supplier
    if supplier:
        supplier_whatsapp = frappe.db.get_value("Supplier", supplier, "whatsapp_number")
        result["supplier_has_whatsapp"] = bool(supplier_whatsapp)
    
    return result



def get_item_commission_percentage(item):
    """
    Returns the commission percentage for the given `item`.
    Will first search in Item record, if not found,
    will search in group (Item Group),
    finally will return default."""


    # Get the percentage from the item doc
    commission_percentage = frappe.db.get_value("Item", item, "commission_percentage")
    if commission_percentage:
        return commission_percentage

    # Get the percentage from the item_group doc
    item_group = frappe.db.get_value("Item", item, "item_group")
    commission_percentage = frappe.db.get_value("Item Group", item_group, "commission_percentage")
    if commission_percentage:
        return commission_percentage

    # Get the percentage from the Agriculture Settings single doc
    return frappe.get_single("Agriculture Settings").get("customer_commission_percentage", 0)


import frappe
from frappe import _
from frappe.utils import flt, formatdate
from erpnext.accounts.party import get_party_account
from erpnext.accounts.utils import get_balance_on

@frappe.whitelist()
def get_customer_balance_with_drafts(customer, company, date=None, exclude_invoice=None):
    """
    Get customer balance including ERPNext standard balance plus draft amounts
    """
    if not date:
        date = frappe.utils.today()
    
    # Get standard ERPNext customer balance (submitted documents only)
    standard_balance = get_balance_on(
        party_type="Customer", 
        party=customer, 
        company=company,
        date=date
    )
    frappe.errprint(f"standard_balance :{standard_balance}")
    # Get draft Invoice Form balance
    draft_invoice_balance = get_draft_invoice_form_balance(customer, exclude_invoice)
    frappe.errprint(f"draft_invoice_balance :{draft_invoice_balance}")
    # Get draft Payments and Receipts balance
    draft_payment_balance = get_draft_payments_receipts_balance(customer)
    frappe.errprint(f"draft_payment_balance :{draft_payment_balance}")
    # Calculate total balance
    total_balance = standard_balance + draft_invoice_balance - draft_payment_balance
    
    return {
        "standard_balance": standard_balance,
        "draft_invoice_balance": draft_invoice_balance,
        "draft_payment_balance": draft_payment_balance,
        "total_balance": total_balance
    }

def get_draft_invoice_form_balance(customer, exclude_invoice=None):
    """
    Get total amount from draft Invoice Form documents for a customer
    Sum amounts from items where customer matches
    """
    conditions = ["inv.docstatus = 0", "ifi.customer = %s"]
    values = [customer]
    
    # Exclude current invoice if specified
    if exclude_invoice:
        conditions.append("inv.name != %s")
        values.append(exclude_invoice)
    
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(ifi.total), 0) as total
        FROM `tabInvoice Form` inv
        INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
        WHERE {conditions}
    """.format(conditions=" AND ".join(conditions)), values)
    
    return flt(result[0][0] if result else 0)

def get_submitted_invoice_form_balance(customer):
    """
    Get total amount from submitted Invoice Form documents for a customer
    Sum amounts from items where customer matches
    """
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(ifi.total), 0) as total
        FROM `tabInvoice Form` inv
        INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
        WHERE inv.docstatus = 1 AND ifi.customer = %s
    """, [customer])
    
    return flt(result[0][0] if result else 0)

def get_draft_payments_receipts_balance(customer):
    """
    Get total payment/receipt amounts from draft Payments and Receipts for a customer
    """
    # Get all draft payment references for the customer
    result = frappe.db.sql("""
        SELECT 
            pr.payment_type,
            COALESCE(SUM(prr.amount), 0) as total_amount
        FROM `tabPayments and Receipts` pr
        INNER JOIN `tabPayments Receipts Reference` prr ON pr.name = prr.parent
        WHERE pr.docstatus = 0 
        AND prr.party_type = 'Customer' 
        AND prr.party = %s
        GROUP BY pr.payment_type
    """, [customer], as_dict=True)
    
    total_payments = 0  # Money going out (increases customer balance)
    total_receipts = 0  # Money coming in (decreases customer balance)
    
    for row in result:
        if row.payment_type == "Pay":
            total_payments += flt(row.total_amount)
        elif row.payment_type == "Receive":
            total_receipts += flt(row.total_amount)
    
    # Net effect: receipts reduce balance, payments increase balance
    return total_receipts - total_payments

def get_submitted_payments_receipts_balance(customer, company):
    """
    Get balance from submitted Payments and Receipts (these create standard GL entries)
    Note: This is already included in standard ERPNext balance via GL entries
    """
    # This function is for reference only - submitted P&R create GL entries
    # which are already included in get_balance_on() function
    
    result = frappe.db.sql("""
        SELECT 
            pr.payment_type,
            COALESCE(SUM(prr.amount), 0) as total_amount
        FROM `tabPayments and Receipts` pr
        INNER JOIN `tabPayments Receipts Reference` prr ON pr.name = prr.parent
        WHERE pr.docstatus = 1 
        AND prr.party_type = 'Customer' 
        AND prr.party = %s
        AND pr.company = %s
        GROUP BY pr.payment_type
    """, [customer, company], as_dict=True)
    
    return result

@frappe.whitelist()
def check_customer_credit_limit_detailed(customer, company, current_invoice_amount=0, exclude_invoice=None):
    """
    Check customer credit limit with detailed breakdown
    """
    # Get customer credit limit for the specific company
    credit_limit = get_customer_credit_limit(customer, company)
    frappe.errprint(f"credit_limit{credit_limit}")
    
    if credit_limit <= 0:
        return {
            "has_credit_limit": False,
            "message": "No credit limit set for this customer and company"
        }
    
    # Get detailed balance breakdown
    balance_details = get_customer_balance_with_drafts(
        customer, company, exclude_invoice=exclude_invoice
    )
    
    current_invoice_amount = flt(current_invoice_amount)
    total_exposure = balance_details["total_balance"] + current_invoice_amount
    
    # Calculate available credit and excess
    available_credit = credit_limit - balance_details["total_balance"]
    excess_amount = total_exposure - credit_limit
    
    is_over_limit = total_exposure > credit_limit
    
    return {
        "has_credit_limit": True,
        "credit_limit": credit_limit,
        "standard_balance": balance_details["standard_balance"],
        "draft_invoice_balance": balance_details["draft_invoice_balance"],
        "draft_payment_balance": balance_details["draft_payment_balance"],
        "total_current_balance": balance_details["total_balance"],
        "current_invoice_amount": current_invoice_amount,
        "total_exposure": total_exposure,
        "available_credit": available_credit,
        "excess_amount": excess_amount if is_over_limit else 0,
        "is_over_limit": is_over_limit,
        "credit_utilization_percent": (total_exposure / credit_limit * 100) if credit_limit > 0 else 0
    }

def get_customer_credit_limit(customer, company):
    """
    Get customer credit limit for specific company from Customer Credit Limit table
    """
    credit_limit = frappe.db.get_value(
        "Customer Credit Limit",
        {
            "parent": customer,
            "company": company
        },
        "credit_limit"
    )
    
    return flt(credit_limit or 0)

def check_bypass_credit_limit(customer, company):
    """
    Check if credit limit bypass is enabled for customer-company combination
    """
    bypass = frappe.db.get_value(
        "Customer Credit Limit",
        {
            "parent": customer,
            "company": company
        },
        "bypass_credit_limit_check"
    )
    
    return bypass == 1

def get_invoice_total_for_customer(doc, customer):
    """
    Calculate total amount for a specific customer from invoice items
    """
    customer_total = 0
    
    if doc.items:
        for item in doc.items:
            if item.customer == customer:
                customer_total += flt(item.total or 0)
    
    return customer_total

def get_all_customers_from_invoice(doc):
    """
    Get list of all unique customers from invoice items
    """
    customers = set()
    
    if doc.items:
        for item in doc.items:
            if item.customer:
                customers.add(item.customer)
    
    return list(customers)

def validate_customer_credit_limit(doc, method):
    """
    Main validation function - validates each customer separately
    """
    
    
    # Get all customers from invoice items
    customers = get_all_customers_from_invoice(doc)
    
    if not customers:
        return
    
    validation_errors = []
    
    # Validate each customer separately
    for customer in customers:
        customer_invoice_total = get_invoice_total_for_customer(doc, customer)
        
        if customer_invoice_total <= 0:
            continue
            
        # Check if credit limit bypass is enabled
        if check_bypass_credit_limit(customer, doc.company):
            continue
        
        # Check credit limit for this customer
        credit_check = check_customer_credit_limit_detailed(
            customer=customer,
            company=doc.company,
            current_invoice_amount=customer_invoice_total,
            exclude_invoice=doc.name
        )
        
        if not credit_check["has_credit_limit"]:
            continue
        
        if credit_check["is_over_limit"]:
            customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
            validation_errors.append({
                "customer": customer,
                "customer_name": customer_name,
                "customer_invoice_total": customer_invoice_total,
                "credit_check": credit_check
            })
    
    # If any customer exceeds credit limit, show all errors
    # Pass the invoice name to the error function
    if validation_errors:
        invoice_name = doc.name if hasattr(doc, 'name') and doc.name else "New Invoice"
        show_multiple_customer_credit_errors(validation_errors, doc.company, invoice_name=invoice_name)


def show_multiple_customer_credit_errors(validation_errors, company, invoice_name=None):
    """
    Display credit limit errors for multiple customers
    """
    def format_currency(amount):
        return frappe.format_value(amount, {"fieldtype": "Currency"})
    
    # Get the setting to show details or not - with proper debugging
    show_details = frappe.db.get_single_value("Agriculture Settings", "show_customer_credit_details")
    
    # Debug the setting value
    frappe.errprint(f"Debug - show_customer_credit_details setting value: {show_details}")
    frappe.errprint(f"Debug - show_customer_credit_details type: {type(show_details)}")
    
    # Ensure proper boolean evaluation
    # The setting might be returning 1/0 instead of True/False, or might be None
    if show_details is None:
        show_details = False
    elif show_details in [1, "1", True, "true", "True"]:
        show_details = True
    else:
        show_details = False
    
    frappe.errprint(f"Debug - Final show_details value: {show_details}")
    
    if len(validation_errors) == 1:
        # Single customer error
        error = validation_errors[0]
        credit_check = error["credit_check"]
        
        if show_details:
            # Show detailed information
            header = _("Credit Limit Exceeded")
            error_message = _("""
            <div style=\"font-family: Arial, sans-serif;\">
                <h4 style=\"color: #d73527; margin-bottom: 15px;\">{header}</h4>
                <table style=\"width: 100%; border-collapse: collapse;\">
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Invoice:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{invoice_name}</td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Customer:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{customer_name}</td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Company:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{company}</td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Credit Limit:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{credit_limit}</td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Current Balance (ERPNext):</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{standard_balance}</td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Draft Invoices:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{draft_invoice_balance}</td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Draft Payments/Receipts:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{draft_payment_balance}</td>
                    </tr>
                    <tr style=\"background-color: #f8f9fa;\">
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Total Current Balance:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>{total_current_balance}</strong></td>
                    </tr>
                    <tr>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Customer's Items in Invoice:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\">{current_invoice}</td>
                    </tr>
                    <tr style=\"background-color: #fff2f0;\">
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>Total Exposure:</strong></td>
                        <td style=\"padding: 5px; border-bottom: 1px solid #ddd;\"><strong>{total_exposure}</strong></td>
                    </tr>
                    <tr style=\"background-color: #ffebe9; color: #d73527;\">
                        <td style=\"padding: 5px;\"><strong>Excess Amount:</strong></td>
                        <td style=\"padding: 5px;\"><strong>{excess_amount}</strong></td>
                    </tr>
                </table>
            </div>
            """).format(
                header=_("Credit Limit Exceeded"),
                invoice_name=invoice_name or "New Invoice",
                customer_name=error["customer_name"],
                company=company,
                credit_limit=format_currency(credit_check["credit_limit"]),
                standard_balance=format_currency(credit_check["standard_balance"]),
                draft_invoice_balance=format_currency(credit_check["draft_invoice_balance"]),
                draft_payment_balance=format_currency(credit_check["draft_payment_balance"]),
                total_current_balance=format_currency(credit_check["total_current_balance"]),
                current_invoice=format_currency(credit_check["current_invoice_amount"]),
                total_exposure=format_currency(credit_check["total_exposure"]),
                excess_amount=format_currency(credit_check["excess_amount"]) 
            )
            frappe.throw(error_message, title=_("Credit Limit Exceeded"))
        else:
            # Show only basic info: invoice, customer and excess amount
            simple_message = _(
                "<div style=\"font-family: Arial, sans-serif;\">"
                "<h4 style=\"color: #d73527; margin-bottom: 15px;\">Credit Limit Exceeded</h4>"
                "<table style=\"width: 100%; border-collapse: collapse;\">"
                "<tr><td style=\"padding: 8px; border-bottom: 1px solid #ddd;\"><strong>Invoice:</strong></td>"
                "<td style=\"padding: 8px; border-bottom: 1px solid #ddd;\">{invoice}</td></tr>"
                "<tr><td style=\"padding: 8px; border-bottom: 1px solid #ddd;\"><strong>Customer:</strong></td>"
                "<td style=\"padding: 8px; border-bottom: 1px solid #ddd;\">{customer}</td></tr>"
                "<tr style=\"background-color: #ffebe9; color: #d73527;\">"
                "<td style=\"padding: 8px;\"><strong>Excess Amount:</strong></td>"
                "<td style=\"padding: 8px;\"><strong>{excess}</strong></td></tr>"
                "</table></div>"
            ).format(
                invoice=invoice_name or "New Invoice",
                customer=error["customer_name"],
                excess=format_currency(credit_check["excess_amount"]) 
            )
            frappe.throw(simple_message, title=_("Credit Limit Exceeded"))
    
    else:
        # Multiple customers error
        if show_details:
            # Show detailed information for multiple customers
            error_html = f"""
            <div style="font-family: Arial, sans-serif;">
                <h4 style="color: #d73527; margin-bottom: 15px;">Credit Limit Exceeded for Multiple Customers</h4>
                <p style="margin-bottom: 15px;"><strong>Invoice:</strong> {invoice_name or "New Invoice"}</p>
                <p style="margin-bottom: 15px;">The following customers will exceed their credit limits:</p>
            """
            
            for i, error in enumerate(validation_errors, 1):
                credit_check = error["credit_check"]
                error_html += f"""
                <div style="margin-bottom: 20px; border: 1px solid #ffcdd2; padding: 10px; background-color: #fff5f5;">
                    <h5 style="color: #d73527; margin: 0 0 10px 0;">{i}. {error["customer_name"]}</h5>
                    <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
                        <tr>
                            <td style="padding: 3px; width: 40%;">Credit Limit:</td>
                            <td style="padding: 3px;"><strong>{format_currency(credit_check["credit_limit"])}</strong></td>
                        </tr>
                        <tr>
                            <td style="padding: 3px;">Current Balance:</td>
                            <td style="padding: 3px;">{format_currency(credit_check["total_current_balance"])}</td>
                        </tr>
                        <tr>
                            <td style="padding: 3px;">Items in Invoice:</td>
                            <td style="padding: 3px;">{format_currency(error["customer_invoice_total"])}</td>
                        </tr>
                        <tr>
                            <td style="padding: 3px;">Total Exposure:</td>
                            <td style="padding: 3px;">{format_currency(credit_check["total_exposure"])}</td>
                        </tr>
                        <tr style="background-color: #ffebe9;">
                            <td style="padding: 3px;"><strong>Excess Amount:</strong></td>
                            <td style="padding: 3px;"><strong style="color: #d73527;">{format_currency(credit_check["excess_amount"])}</strong></td>
                        </tr>
                    </table>
                </div>
                """
            
            error_html += "</div>"
            
            frappe.throw(error_html, title=_("Multiple Credit Limits Exceeded"))
        else:
            # Show only basic info for multiple customers
            error_html = f"""
            <div style="font-family: Arial, sans-serif;">
                <h4 style="color: #d73527; margin-bottom: 15px;">Credit Limit Exceeded for Multiple Customers</h4>
                <p style="margin-bottom: 15px;"><strong>Invoice:</strong> {invoice_name or "New Invoice"}</p>
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="background-color: #f8f9fa;">
                            <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Customer</th>
                            <th style="padding: 8px; border: 1px solid #ddd; text-align: right;">Excess Amount</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for error in validation_errors:
                credit_check = error["credit_check"]
                error_html += f"""
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">{error["customer_name"]}</td>
                            <td style="padding: 8px; border: 1px solid #ddd; text-align: right; color: #d73527; font-weight: bold;">
                                {format_currency(credit_check["excess_amount"])}
                            </td>
                        </tr>
                """
            
            error_html += """
                    </tbody>
                </table>
            </div>
            """
            
            frappe.throw(error_html, title=_("Multiple Credit Limits Exceeded"))


# Additional debugging function to check the setting
@frappe.whitelist()
def debug_credit_limit_setting():
    """
    Debug function to check the credit limit setting
    """
    try:
        # Check if the doctype exists
        if not frappe.db.exists("DocType", "Agriculture Settings"):
            return {"error": "Agriculture Settings DocType does not exist"}
        
        # Check if the field exists in the doctype
        field_exists = frappe.db.exists("DocField", {
            "parent": "Agriculture Settings",
            "fieldname": "show_customer_credit_details"
        })
        
        if not field_exists:
            return {"error": "Field 'show_customer_credit_details' does not exist in Agriculture Settings"}
        
        # Get the setting value
        setting_value = frappe.db.get_single_value("Agriculture Settings", "show_customer_credit_details")
        
        # Get the raw value from database
        raw_value = frappe.db.sql("""
            SELECT show_customer_credit_details 
            FROM `tabAgriculture Settings` 
            LIMIT 1
        """, as_dict=True)
        
        return {
            "field_exists": bool(field_exists),
            "setting_value": setting_value,
            "setting_type": type(setting_value).__name__,
            "raw_db_value": raw_value[0] if raw_value else None,
            "boolean_evaluation": bool(setting_value),
            "is_checked": setting_value in [1, "1", True, "true", "True"]
        }
        
    except Exception as e:
        return {"error": str(e)}


# Alternative approach - use get_single instead of get_single_value
    
@frappe.whitelist()
def get_multiple_customers_credit_summary(customers, company, invoice_items=None):
    """
    Get credit summary for multiple customers
    customers: comma-separated string or list of customer names
    invoice_items: JSON string of items with customer and total fields
    """
    import json
    
    if isinstance(customers, str):
        customers = [c.strip() for c in customers.split(',') if c.strip()]
    
    if isinstance(invoice_items, str):
        invoice_items = json.loads(invoice_items)
    
    results = []
    
    for customer in customers:
        # Calculate customer's total from invoice items
        customer_total = 0
        if invoice_items:
            for item in invoice_items:
                if item.get('customer') == customer:
                    customer_total += flt(item.get('total', 0))
        
        # Get credit check for this customer
        credit_check = check_customer_credit_limit_detailed(
            customer=customer,
            company=company,
            current_invoice_amount=customer_total
        )
        
        results.append({
            "customer": customer,
            "customer_total": customer_total,
            "credit_data": credit_check
        })
    
    return results

@frappe.whitelist()
def validate_invoice_multiple_customers(doc_json):
    """
    Validate credit limits for all customers in an invoice
    Used for client-side validation
    """
    import json
    
    if isinstance(doc_json, str):
        doc = json.loads(doc_json)
    else:
        doc = doc_json
    
    if not doc.get('company'):
        return {"valid": True, "message": "No company specified"}
    
    # Get all customers from items
    customers = set()
    customer_totals = {}
    
    for item in doc.get('items', []):
        if item.get('customer'):
            customer = item['customer']
            customers.add(customer)
            if customer not in customer_totals:
                customer_totals[customer] = 0
            customer_totals[customer] += flt(item.get('total', 0))
    
    if not customers:
        return {"valid": True, "message": "No customers in items"}
    
    # Validate each customer
    violations = []
    
    for customer in customers:
        customer_total = customer_totals[customer]
        
        if customer_total <= 0:
            continue
            
        # Check bypass
        if check_bypass_credit_limit(customer, doc['company']):
            continue
        
        # Check credit limit
        credit_check = check_customer_credit_limit_detailed(
            customer=customer,
            company=doc['company'],
            current_invoice_amount=customer_total,
            exclude_invoice=doc.get('name')
        )
        
        if credit_check.get('is_over_limit'):
            customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
            violations.append({
                "customer": customer,
                "customer_name": customer_name,
                "excess_amount": credit_check['excess_amount'],
                "credit_limit": credit_check['credit_limit'],
                "customer_total": customer_total
            })
    
    if violations:
        return {
            "valid": False,
            "violations": violations,
            "message": f"Credit limit exceeded for {len(violations)} customer(s)"
        }
    
    return {"valid": True, "message": "All customers within credit limits"}

@frappe.whitelist()
def get_customer_credit_summary_api(customer, company):
    """
    API endpoint to get customer credit summary for client-side usage
    """
    return check_customer_credit_limit_detailed(customer, company)
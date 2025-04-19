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
    pos_profile = frappe.get_doc("POS Profile", settings.get("pos_profile"))
    customer_commission_invoice_refs = []

    def validate(self):
        self.update_grand_total()
        self.update_customer_commission()
        self.update_commission_and_taxes()

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
            supplier_commission_percentage = get_supplier_commission_percentage(self.supplier)

            default_tax_template = get_tax_template(self)
            tax_rate = frappe.db.get_value("Sales Taxes and Charges",
                                           {"parent": default_tax_template}, "rate") or 0

            commission_amount = (self.grand_total * supplier_commission_percentage) / 100
            tax_amount = (commission_amount * tax_rate) / 100
            commission_total_with_taxes = commission_amount + tax_amount
            self.append("commissions", {
                "item": supplier_commission_item,
                "price": self.grand_total,
                "commission": supplier_commission_percentage,
                "taxes": tax_rate,
                "commission_total": commission_total_with_taxes
            })
            self.total_commissions_and_taxes = commission_total_with_taxes
            for item in self.items:
                item.commission = (item.total * supplier_commission_percentage) / 100

    def make_gl_entries(self):
        gl_entries = []
        if not self.company:
            frappe.throw(_("Please Select a Company"))

        company_defaults = frappe.get_cached_doc("Company", self.company)
        # For credit entry
        self.make_supplier_gl_entry(gl_entries, company_defaults)

        # For debits entries
        self.make_customers_gl_entries(gl_entries, company_defaults)

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
                    "account": company_defaults.default_receivable_account,
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

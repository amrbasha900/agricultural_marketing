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
from frappe.model.naming import make_autoname


class InvoiceForm(Document):
    settings = frappe.get_single("Agriculture Settings")

    if settings.get("pos_profile") != None:
        pos_profile = frappe.get_doc("POS Profile", settings.get("pos_profile"))
    customer_commission_invoice_refs = []

    # def autoname(self):
    #     """
    #     Custom naming for Invoice Form with RT- prefix for returns
    #     """
    #     if self.is_return:
    #         # For return invoices, use RT- prefix
    #         self.name = make_autoname("RT-INV-.YYYY.-.MM.-.#####")
    #     else:
    #         # For regular invoices, use your existing naming convention
    #         # Replace this with your current naming logic if different
    #         self.name = make_autoname("INV-.YYYY.-.MM.-.#####")

    def validate(self):
        self.update_grand_total()
        self.update_customer_commission()
        self.update_commission_and_taxes()
        self.add_pamper_commission()
        if hasattr(self, 'is_return') and self.is_return:
                self.validate_return_amounts()
                if hasattr(self, 'return_against') and self.return_against:
                    self.validate_return_invoice_enhanced()
        if self.is_return:
            self.validate_return_amounts()
            self.validate_return_invoice()
            self.validate_return_invoice_enhanced()  # NEW: Enhanced validation

        if not self.is_draft and self.docstatus==0:
            self.auto_send_whatsapp_on_save()
        pamper_name = frappe.db.sql("select pamper from `tabInvoice Form Permission Details` where user = '"+frappe.session.user+"' ", as_dict=1)
        if pamper_name and not self.pamper:
            self.pamper = pamper_name[0].pamper 
        else:
            pass
        
        # Only validate credit limits for non-return invoices
        if not self.is_return:
            validate_customer_credit_limit(self, "validate")

    def on_submit(self):
        if self.is_return and not self.return_reason:
            frappe.throw(_("Return Reason is mandatory for return invoices"))
        if self.is_return:
            self.make_return_gl_entries()
        else:
            self.make_gl_entries()
        if self.settings.get("generate_commission_invoices_automatically"):
            self.generate_supplier_commission_invoice(self.posting_date)
            self.generate_customers_commission_invoices(self.posting_date)
        
        # NEW: Update returned quantities when return invoice is submitted
        if self.is_return and self.return_against:
            self.update_original_invoice_returned_quantities()

    def on_cancel(self):
        self.cancel_commission_invoice()
        self.make_gl_entries_on_cancel()
        
        # NEW: Update returned quantities when return invoice is cancelled
        if self.is_return and self.return_against:
            self.update_original_invoice_returned_quantities()

    def on_trash(self):
        # delete gl entries on deletion of transaction
        if frappe.db.get_single_value("Accounts Settings", "delete_linked_ledger_entries"):
            gles = frappe.get_all("GL Entry",
                                  {"voucher_type": self.doctype, "voucher_no": self.name}, pluck="name")
            for gle in gles:
                frappe.delete_doc("GL Entry", gle, for_reload=True)
        # delete commission invoice on deletion of transaction
        self.delete_commission_invoice()

        def validate_return_amounts(self):
            """
            Ensure all amounts are negative for return invoices
            """
            if not getattr(self, 'is_return', False):
                return
                
            # Validate items have negative amounts
            for item in self.items:
                if item.qty > 0:
                    item.qty = -abs(item.qty)
                if item.total > 0:
                    item.total = -abs(item.total)
            
            # Ensure grand total is negative
            if self.grand_total > 0:
                self.grand_total = -abs(self.grand_total)

    def validate_return_invoice_enhanced(self):
        """
        Enhanced validation for return invoices with quantity tracking - UPDATED
        """
        if not getattr(self, 'is_return', False):
            return
            
        if not getattr(self, 'return_against', None):
            frappe.throw(_("Return Against is mandatory for return invoices"))
        
        # Get original invoice
        if not frappe.db.exists("Invoice Form", self.return_against):
            frappe.throw(_("Return Against invoice does not exist"))
            
        original_invoice = frappe.get_doc("Invoice Form", self.return_against)
        if original_invoice.docstatus != 1:
            frappe.throw(_("Can only create returns against submitted invoices"))
        
        if getattr(original_invoice, 'is_return', False):
            frappe.throw(_("Cannot create return against another return invoice"))
        
        # NEW: Validate return quantities instead of blocking multiple returns
        self.validate_return_quantities_against_original()

    def update_grand_total(self):
        self.grand_total = 0
        for item in self.items:
            self.grand_total += item.total
        
        # NEW: For return invoices, ensure grand total is negative
        if self.is_return and self.grand_total > 0:
            self.grand_total = -abs(self.grand_total)

    def update_item_available_quantities(self):
        """
        Update available quantities for all items in this invoice
        Called when the form is loaded or refreshed
        """
        if self.is_return:
            return  # Don't update for return invoices
            
        for item in self.items:
            if hasattr(item, 'returned_qty'):
                available_qty = item.qty - (item.returned_qty or 0)
                frappe.db.set_value("Invoice Form Item", item.name, "available_qty", available_qty)

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
            commission = self.grand_total * (self.settings.get("pamper_commission", 0)/100)
            if self.is_return:
                self.pamper_commission = -abs(commission)
            else:
                self.pamper_commission = commission

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


    #### Make GL For Return Invoices
    # Add these new functions to your invoice_form.py file

    def make_return_gl_entries(self):
        """
        Create GL entries for return invoices - reverses normal invoice logic
        """
        gl_entries = []
        if not self.company:
            frappe.throw(_("Please Select a Company"))

        company_defaults = frappe.get_cached_doc("Company", self.company)
        
        # For return: Debit supplier (reduce payable)
        self.make_supplier_gl_entry_return(gl_entries, company_defaults)
        
        # For return: Credit customers (reduce receivables)
        self.make_customers_gl_entries_return(gl_entries, company_defaults)
        
        # Handle commissions for returns
        if self.settings.get("active_pamper_commission", 0) and self.settings.get("automatic_pamper_commission_creation", 0) and self.pamper:
            self.make_gl_dict_for_pamper_commission_return(gl_entries, company_defaults)

        for entry in gl_entries:
            gle = frappe.new_doc("GL Entry")
            gle.update(entry)
            gle.flags.ignore_permissions = True
            gle.submit()

    def make_supplier_gl_entry_return(self, gl_entries, company_defaults):
        """
        For return: Debit supplier account (reduce what we owe them)
        """
        gl_entries.append({
            "posting_date": self.posting_date,
            "due_date": self.posting_date,
            "account": get_party_account("Supplier", self.supplier, self.company),
            "party_type": "Supplier",
            "party": self.supplier,
            "debit": abs(self.grand_total),  # Use absolute value and debit
            "credit": 0,
            "account_currency": company_defaults.default_currency,
            "debit_in_account_currency": abs(self.grand_total),
            "credit_in_account_currency": 0,
            "voucher_type": self.doctype,
            "voucher_no": self.name,
            "company": self.company,
            "cost_center": company_defaults.cost_center,
            "debit_in_transaction_currency": abs(self.grand_total),
            "credit_in_transaction_currency": 0,
            "transaction_exchange_rate": 1,
            "remarks": f"Return Invoice: {self.name}"
        })
        
        # Handle commission for returns
        self.make_gl_dict_for_commission_return(gl_entries, company_defaults)

    def make_customers_gl_entries_return(self, gl_entries, company_defaults):
        """
        For return: Credit customer accounts (reduce what they owe us)
        """
        customers = []
        for it in self.items:
            item_total = abs(it.total)  # Use absolute value
            
            if it.couple_customer:
                couple_supplier = frappe.db.get_value('Supplier', {'related_customer': it.customer}, 'name')
                
                if couple_supplier in customers:
                    customer_record = [d for d in gl_entries if d.get("party") == couple_supplier][0]
                    customer_record.update({
                        "credit": customer_record["credit"] + item_total,
                        "credit_in_account_currency": customer_record["credit_in_account_currency"] + item_total,
                        "credit_in_transaction_currency": customer_record["credit_in_transaction_currency"] + item_total,
                    })
                else:
                    gl_entries.append({
                        "posting_date": self.posting_date,
                        "due_date": self.posting_date,
                        "account": get_party_account("Supplier", couple_supplier, self.company),
                        "party_type": "Supplier",
                        "party": couple_supplier,
                        "credit": item_total,  # Credit instead of debit
                        "debit": 0,
                        "account_currency": company_defaults.default_currency,
                        "credit_in_account_currency": item_total,
                        "debit_in_account_currency": 0,
                        "voucher_type": self.doctype,
                        "voucher_no": self.name,
                        "company": self.company,
                        "cost_center": company_defaults.cost_center,
                        "credit_in_transaction_currency": item_total,
                        "debit_in_transaction_currency": 0,
                        "transaction_exchange_rate": 1,
                        "remarks": f"Return Invoice: {self.name}"
                    })
                    customers.append(couple_supplier)
            else:
                if it.customer in customers:
                    customer_record = [d for d in gl_entries if d.get("party") == it.customer][0]
                    customer_record.update({
                        "credit": customer_record["credit"] + item_total,
                        "credit_in_account_currency": customer_record["credit_in_account_currency"] + item_total,
                        "credit_in_transaction_currency": customer_record["credit_in_transaction_currency"] + item_total,
                    })
                else:
                    gl_entries.append({
                        "posting_date": self.posting_date,
                        "due_date": self.posting_date,
                        "account": get_party_account("Customer", it.customer, self.company),
                        "party_type": "Customer",
                        "party": it.customer,
                        "credit": item_total,  # Credit instead of debit
                        "debit": 0,
                        "account_currency": company_defaults.default_currency,
                        "credit_in_account_currency": item_total,
                        "debit_in_account_currency": 0,
                        "voucher_type": self.doctype,
                        "voucher_no": self.name,
                        "company": self.company,
                        "cost_center": company_defaults.cost_center,
                        "credit_in_transaction_currency": item_total,
                        "debit_in_transaction_currency": 0,
                        "transaction_exchange_rate": 1,
                        "remarks": f"Return Invoice: {self.name}"
                    })
                    customers.append(it.customer)

    def make_gl_dict_for_commission_return(self, gl_entries, company_defaults):
        """
        Handle commission GL entries for return invoices
        """
        if len(self.commissions) != 0 and self.total_commissions_and_taxes:
            commission_amount = abs(self.total_commissions_and_taxes)
            
            mops = frappe.get_doc("POS Profile", self.settings.get("pos_profile")).get("payments")
            for mop in mops:
                if mop.default:
                    default_commission_account = frappe.db.get_value(
                        "Mode of Payment Account",
                        {"parent": mop.mode_of_payment, "company": self.company},
                        "default_account",
                    )
                    break

            # For return: Credit supplier (reduce commission payable)
            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": get_party_account("Supplier", self.supplier, self.company),
                "party_type": "Supplier",
                "party": self.supplier,
                "credit": commission_amount,  # Credit instead of debit
                "debit": 0,
                "account_currency": company_defaults.default_currency,
                "credit_in_account_currency": commission_amount,
                "debit_in_account_currency": 0,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "credit_in_transaction_currency": commission_amount,
                "debit_in_transaction_currency": 0,
                "transaction_exchange_rate": 1,
                "remarks": f"Return Invoice Commission: {self.name}"
            })

            # For return: Debit commission account
            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": default_commission_account,
                "debit": commission_amount,  # Debit instead of credit
                "credit": 0,
                "account_currency": company_defaults.default_currency,
                "debit_in_account_currency": commission_amount,
                "credit_in_account_currency": 0,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "debit_in_transaction_currency": commission_amount,
                "credit_in_transaction_currency": 0,
                "transaction_exchange_rate": 1,
                "remarks": f"Return Invoice Commission: {self.name}"
            })

    def make_gl_dict_for_pamper_commission_return(self, gl_entries, company_defaults):
        """
        Handle pamper commission GL entries for return invoices
        """
        if self.settings.get("active_pamper_commission", 0) and self.settings.get("automatic_pamper_commission_creation", 0) and self.pamper_commission and self.pamper:
            pamper_commission_account = self.settings.get("pamper_commission_account")
            if not pamper_commission_account:
                frappe.throw(_("Pamper Commission Account not set in Agriculture Settings"))
            
            commission_amount = abs(self.pamper_commission)
            
            # For return: Credit the pamper commission expense account (reverse the expense)
            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": pamper_commission_account,
                "credit": commission_amount,  # Credit instead of debit
                "debit": 0,
                "account_currency": company_defaults.default_currency,
                "credit_in_account_currency": commission_amount,
                "debit_in_account_currency": 0,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "credit_in_transaction_currency": commission_amount,
                "debit_in_transaction_currency": 0,
                "transaction_exchange_rate": 1,
                "remarks": f"Return Invoice - Pamper commission reversal: {self.name}"
            })
            
            # For return: Debit the pamper's customer account (reduce payable to pamper)
            gl_entries.append({
                "posting_date": self.posting_date,
                "due_date": self.posting_date,
                "account": get_party_account("Customer", self.pamper, self.company),
                "party_type": "Customer",
                "party": self.pamper,
                "debit": commission_amount,  # Debit instead of credit
                "credit": 0,
                "account_currency": company_defaults.default_currency,
                "debit_in_account_currency": commission_amount,
                "credit_in_account_currency": 0,
                "voucher_type": self.doctype,
                "voucher_no": self.name,
                "company": self.company,
                "cost_center": company_defaults.cost_center,
                "debit_in_transaction_currency": commission_amount,
                "credit_in_transaction_currency": 0,
                "transaction_exchange_rate": 1,
                "remarks": f"Return Invoice - Pamper commission payable reversal: {self.name}"
            })

    def update_original_invoice_returned_quantities(self):
        """
        Update the returned quantities in the original invoice items
        """
        if not self.return_against:
            return
            
        try:
            original_invoice = frappe.get_doc("Invoice Form", self.return_against)
            
            for item in original_invoice.items:
                returned_qty = get_returned_quantity_for_item(self.return_against, item.idx)
                
                # Update the item's returned quantity and available quantity
                frappe.db.set_value("Invoice Form Item", item.name, {
                    "returned_qty": returned_qty,
                    "available_qty": item.qty - returned_qty
                })
            
            frappe.db.commit()
            
        except Exception as e:
            frappe.log_error(f"Error updating returned quantities: {str(e)}", "Update Returned Quantities")

    def validate_return_amounts(self):
        """
        Ensure all amounts are negative for return invoices
        """
        if not self.is_return:
            return
            
        # Validate items have negative amounts
        for item in self.items:
            if item.qty > 0:
                item.qty = -abs(item.qty)
            if item.total > 0:
                item.total = -abs(item.total)
        
        # Ensure grand total is negative
        if self.grand_total > 0:
            self.grand_total = -abs(self.grand_total)
        
        # Ensure commission amounts are negative
        for commission in self.commissions:
            if commission.commission_total > 0:
                commission.commission_total = -abs(commission.commission_total)
        
        if self.total_commissions_and_taxes > 0:
            self.total_commissions_and_taxes = -abs(self.total_commissions_and_taxes)
        
        if self.pamper_commission and self.pamper_commission > 0:
            self.pamper_commission = -abs(self.pamper_commission)

    def validate_return_invoice(self):
        """
        Validation for return invoices - MODIFIED to allow multiple returns
        """
        if not self.is_return:
            return
            
        if not self.return_against:
            frappe.throw(_("Return Against is mandatory for return invoices"))
        
        # Check if return_against invoice exists and is submitted
        if not frappe.db.exists("Invoice Form", self.return_against):
            frappe.throw(_("Return Against invoice does not exist"))
            
        original_invoice = frappe.get_doc("Invoice Form", self.return_against)
        if original_invoice.docstatus != 1:
            frappe.throw(_("Can only create returns against submitted invoices"))
        
        if original_invoice.is_return:
            frappe.throw(_("Cannot create return against another return invoice"))
        
        # REMOVED: Check for existing returns - now allowing multiple returns
        # We'll validate quantities instead of blocking multiple returns
        
        # NEW: Validate that return quantities don't exceed available quantities
        self.validate_return_quantities_against_original()


    def validate_return_quantities_against_original(self):
        """
        Validate that return quantities don't exceed what's available for return
        UPDATED: Handle identical lines properly using idx tracking
        """
        if not self.is_return or not self.return_against:
            return
        
        original_invoice = frappe.get_doc("Invoice Form", self.return_against)
        
        # Validate each return item against specific original line using idx
        for return_item in self.items:
            return_qty = abs(return_item.qty)  # Get absolute value
            
            # Get the original item line idx this return is for
            original_item_idx = getattr(return_item, 'original_item_idx', None)
            frappe.errprint(type(original_item_idx))
            
            if not original_item_idx:
                frappe.throw(_(
                    "Return item {0} missing original_item_idx reference. "
                    "Cannot determine which original line this return is for."
                ).format(return_item.item_code))
            
            # Get the specific original item line by idx
            original_item = None
            for item in original_invoice.items:
                if item.idx == int(original_item_idx):
                    original_item = item
                    break
                frappe.errprint(type(item.idx))
            if not original_item:
                frappe.throw(_(
                    "Original item line {0} not found in invoice {1}"
                ).format(original_item_idx, self.return_against))
            
            # Calculate already returned quantity for this specific line idx
            already_returned_qty = get_returned_quantity_for_specific_line(
                self.return_against, 
                original_item_idx,
                exclude_current=self.name
            )
            
            available_qty = original_item.qty - already_returned_qty
            
            if return_qty > available_qty:
                frappe.throw(_(
                    "Return quantity {0} for item {1} (line {2}) exceeds available quantity {3}. "
                    "Original quantity: {4}, Already returned: {5}"
                ).format(
                    return_qty, 
                    return_item.item_code,
                    original_item_idx,
                    available_qty,
                    original_item.qty,
                    already_returned_qty
                ))




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
    if invoice.is_return:
        commission_invoice.is_return = 1
        commission_invoice.append("custom_return_against_additional_references", {
                    "sales_invoice": frappe.db.get_value("Sales Invoice Item", {"invoice_form": invoice.return_against},"parent"),
                    })
        commission_invoice.custom_return_reason = invoice.return_reason
    commission_invoice.append("items", {
        "item_code": item,
        "description": item + "\n" + invoice.name,
        "qty": -1 if invoice.is_return else 1,
        "rate": abs(total_commission),
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
    
    # If no credit limit rows are configured for any of these customers in this company,
    # skip the validation entirely to avoid blocking save/refresh flows when limits are not used.
    if not frappe.db.exists(
        "Customer Credit Limit",
        {"parent": ["in", customers], "company": doc.company}
    ):
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


@frappe.whitelist()
def can_create_return_for_invoice(invoice_name):
    """
    Check if a return can be created for this invoice - UPDATED
    """
    if not frappe.db.exists("Invoice Form", invoice_name):
        return {"can_create": False, "reason": "Invoice not found"}
    
    # Get invoice details
    invoice = frappe.get_doc("Invoice Form", invoice_name)
    
    # Check if invoice is submitted
    if invoice.docstatus != 1:
        return {"can_create": False, "reason": "Can only create returns for submitted invoices"}
    
    # Check if invoice is already a return
    if getattr(invoice, 'is_return', False):
        return {"can_create": False, "reason": "Cannot create return for a return invoice"}
    
    # REMOVED: Check for existing returns - now allowing multiple
    
    # NEW: Check if there are returnable items
    has_returnable_items = check_if_items_available_for_return(invoice_name)
    if not has_returnable_items:
        return {"can_create": False, "reason": "All items have been fully returned"}
    
    # Check user permissions
    if not frappe.has_permission("Invoice Form", "create"):
        return {"can_create": False, "reason": "No permission to create Invoice Form"}
    
    return {"can_create": True, "reason": ""}

@frappe.whitelist()
def get_return_invoices(invoice_name):
    """
    Get all return invoices created against a specific invoice
    """
    return frappe.get_all("Invoice Form", 
        filters={
            "return_against": invoice_name,
            "is_return": 1
        },
        fields=["name", "posting_date", "grand_total", "docstatus"],
        order_by="creation desc"
    )



# Add these NEW functions to your invoice_form.py file

@frappe.whitelist()
def create_return_invoice(original_invoice_name):
    """
    Create a return invoice from an original invoice
    """
    try:
        # Validate if return can be created
        validation = can_create_return_for_invoice(original_invoice_name)
        if not validation["can_create"]:
            return {"success": False, "error": validation["reason"]}
        
        # Get original invoice
        original_invoice = frappe.get_doc("Invoice Form", original_invoice_name)
        
        # Create new return invoice
        return_invoice = frappe.new_doc("Invoice Form")
        
        # Copy basic fields from original
        return_invoice.update({
            "supplier": original_invoice.supplier,
            "customer": original_invoice.customer,
            "pamper": original_invoice.pamper,
            "company": original_invoice.company,
            "posting_date": frappe.utils.today(),
            "is_return": 1,
            "return_against": original_invoice_name,
            "commission_invoice_reference": original_invoice.commission_invoice_reference,
            "remarks": f"Return against {original_invoice_name}"
        })
        
        # Copy items with negative quantities and amounts
        for original_item in original_invoice.items:
            return_invoice.append("items", {
                "item_code": original_item.item_code,
                "item_name": original_item.item_name,
                "qty": -abs(original_item.qty),  # Make negative
                "price": original_item.price,    # Keep same price
                "total": -abs(original_item.total),  # Make negative
                "customer": original_item.customer,
                "pamper": original_item.pamper,
                "couple_customer": original_item.couple_customer,
                "customer_commission": -abs(original_item.customer_commission) if original_item.customer_commission else 0
            })
        
        # Copy commissions with negative amounts
        for original_commission in original_invoice.commissions:
            return_invoice.append("commissions", {
                "item": original_commission.item,
                "price": -abs(original_commission.price),  # Make negative
                "commission": original_commission.commission,  # Keep percentage same
                "taxes": original_commission.taxes,  # Keep percentage same
                "commission_total": -abs(original_commission.commission_total),  # Make negative
                "total_commission": -abs(original_commission.total_commission)  # Make negative
            })
        
        # Set negative totals
        return_invoice.grand_total = -abs(original_invoice.grand_total)
        return_invoice.total_commissions_and_taxes = -abs(original_invoice.total_commissions_and_taxes) if original_invoice.total_commissions_and_taxes else 0
        return_invoice.pamper_commission = -abs(original_invoice.pamper_commission) if original_invoice.pamper_commission else 0
        
        # Save the return invoice
        return_invoice.insert()
        
        return {
            "success": True, 
            "return_invoice_name": return_invoice.name,
            "message": f"Return invoice {return_invoice.name} created successfully"
        }
        
    except Exception as e:
        frappe.log_error(f"Error creating return invoice: {str(e)}", "Return Invoice Creation")
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def get_return_validation_status(invoice_name):
    """
    Get detailed validation status for return creation - UPDATED to allow multiple returns
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"can_create": False, "reason": "Invoice not found", "details": {}}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        # Basic validations
        if invoice.docstatus != 1:
            return {
                "can_create": False, 
                "reason": "Can only create returns for submitted invoices",
                "details": {"docstatus": invoice.docstatus}
            }
        
        if hasattr(invoice, 'is_return') and invoice.is_return:
            return {
                "can_create": False, 
                "reason": "Cannot create return for a return invoice",
                "details": {"is_return": True}
            }
        
        # Get existing returns for information (but don't block creation)
        existing_returns = frappe.get_all("Invoice Form",
            filters={
                "return_against": invoice_name,
                "is_return": 1,
                "docstatus": ["in", [0, 1]]
            },
            fields=["name", "docstatus", "posting_date", "grand_total"]
        )
        
        # REMOVED: Block for existing returns - now allowing multiple
        # Check if there are any items still available for return
        has_returnable_items = check_if_items_available_for_return(invoice_name)
        
        if not has_returnable_items:
            return {
                "can_create": False,
                "reason": "All items have been fully returned",
                "details": {
                    "existing_returns": existing_returns,
                    "fully_returned": True
                }
            }
        
        # Check permissions
        if not frappe.has_permission("Invoice Form", "create"):
            return {
                "can_create": False, 
                "reason": "No permission to create Invoice Form",
                "details": {"permission_error": True}
            }
        
        return {
            "can_create": True, 
            "reason": "",
            "details": {
                "original_invoice": {
                    "name": invoice.name,
                    "grand_total": invoice.grand_total,
                    "posting_date": invoice.posting_date,
                    "supplier": invoice.supplier,
                    "items_count": len(invoice.items)
                },
                "existing_returns": existing_returns,
                "existing_returns_count": len(existing_returns)
            }
        }
        
    except Exception as e:
        return {"can_create": False, "reason": str(e), "details": {"error": True}}
def check_if_items_available_for_return(invoice_name):
    """
    Check if there are any items still available for return
    """
    try:
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        for item in invoice.items:
            # Get total returned for this specific item
            returned_qty = get_total_returned_quantity_for_item_customer(
                invoice_name,
                item.item_code,
                item.customer,
                item.pamper
            )
            
            available_qty = item.qty - returned_qty
            if available_qty > 0:
                return True  # Found at least one item with available quantity
        
        return False  # No items available for return
        
    except Exception as e:
        frappe.log_error(f"Error checking returnable items: {str(e)}")
        return True  # Default to allowing returns if there's an error
    
@frappe.whitelist()
def get_invoice_return_info(invoice_name):
    """
    Get comprehensive return information for an invoice
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"exists": False}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        # Get return validation
        validation = get_return_validation_status(invoice_name)
        
        # Get existing returns
        returns = frappe.get_all("Invoice Form",
            filters={
                "return_against": invoice_name,
                "is_return": 1
            },
            fields=["name", "docstatus", "posting_date", "grand_total", "creation"],
            order_by="creation desc"
        )
        
        return {
            "exists": True,
            "invoice": {
                "name": invoice.name,
                "docstatus": invoice.docstatus,
                "is_return": invoice.is_return,
                "grand_total": invoice.grand_total,
                "posting_date": invoice.posting_date
            },
            "validation": validation,
            "returns": returns,
            "can_create_return": validation["can_create"]
        }
        
    except Exception as e:
        return {"exists": False, "error": str(e)}

# MODIFY the existing validate method by adding this line at the end:
# if not self.is_return:
#     validate_customer_credit_limit(self, "validate")

# Add these new methods to your existing invoice_form.py file

@frappe.whitelist()
def get_returnable_items(invoice_name):
    """
    Get all items from an invoice with their return status
    UPDATED: Handle identical lines properly
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"success": False, "error": "Invoice not found"}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        if invoice.docstatus != 1:
            return {"success": False, "error": "Can only return items from submitted invoices"}
        
        if hasattr(invoice, 'is_return') and invoice.is_return:
            return {"success": False, "error": "Cannot return items from a return invoice"}
        
        returnable_items = []
        
        # Process each line individually using idx
        for array_index, item in enumerate(invoice.items):
            # Get total returned quantity for this specific line
            returned_qty = get_returned_quantity_for_specific_line(invoice_name, item.idx)
            
            # Create unique identifier for identical items
            line_description = f"Line {item.idx}: {item.item_name}"
            if item.customer:
                line_description += f" - Customer: {item.customer}"
            if item.pamper:
                line_description += f" - Pamper: {item.pamper}"
            
            returnable_items.append({
                "idx": item.idx,  # Frappe row idx
                "array_index": array_index,  # JavaScript array index
                "item_code": item.item_code,
                "item_name": item.item_name,
                "line_description": line_description,  # For display
                "original_qty": item.qty,
                "returned_qty": returned_qty,
                "available_qty": item.qty - returned_qty,
                "price": item.price,
                "total": item.total,
                "customer": getattr(item, 'customer', ''),
                "pamper": getattr(item, 'pamper', ''),
                "couple_customer": getattr(item, 'couple_customer', 0),
                "customer_commission": getattr(item, 'customer_commission', 0)
            })
        
        return {"success": True, "items": returnable_items}
        
    except Exception as e:
        frappe.log_error(f"Error getting returnable items: {str(e)}", "Get Returnable Items")
        return {"success": False, "error": str(e)}


def get_returned_quantity_for_item(original_invoice_name, item_idx):
    """
    Get total returned quantity for a specific item from all return invoices
    """
    returned_qty = frappe.db.sql("""
        SELECT COALESCE(SUM(ABS(ifi.qty)), 0) as returned_qty
        FROM `tabInvoice Form` inv
        INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
        WHERE inv.return_against = %s 
        AND inv.is_return = 1
        AND inv.docstatus IN (0, 1)
        AND ifi.original_item_idx = %s
    """, [original_invoice_name, item_idx])
    
    return float(returned_qty[0][0] if returned_qty else 0)


def validate_return_items(original_invoice, return_items):
    """
    Validate that return items and quantities are valid
    """
    for return_item in return_items:
        item_idx = return_item["idx"]
        return_qty = float(return_item["return_qty"])
        
        if return_qty <= 0:
            return f"Return quantity must be greater than 0"
        
        # Find original item
        original_item = None
        for item in original_invoice.items:
            if item.idx == item_idx:
                original_item = item
                break
        
        if not original_item:
            return f"Original item with index {item_idx} not found"
        
        # Check available quantity
        returned_qty = get_returned_quantity_for_item(original_invoice.name, item_idx)
        available_qty = original_item.qty - returned_qty
        
        if return_qty > available_qty:
            return f"Return quantity ({return_qty}) exceeds available quantity ({available_qty}) for item {original_item.item_name}"
    
    return None


@frappe.whitelist()
def get_invoice_items_with_return_info(invoice_name):
    """
    Get all invoice items with their return information for display
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"success": False, "error": "Invoice not found"}
        
        # Get all items with return information
        items_data = frappe.db.sql("""
            SELECT 
                ifi.idx,
                ifi.item_code,
                ifi.item_name,
                ifi.qty as original_qty,
                ifi.price,
                ifi.total,
                ifi.customer,
                ifi.pamper,
                COALESCE(ifi.returned_qty, 0) as returned_qty,
                (ifi.qty - COALESCE(ifi.returned_qty, 0)) as available_qty
            FROM `tabInvoice Form Item` ifi
            WHERE ifi.parent = %s
            ORDER BY ifi.idx
        """, [invoice_name], as_dict=True)
        
        # Calculate return percentages and status
        for item in items_data:
            if item.original_qty > 0:
                item.return_percentage = (item.returned_qty / item.original_qty) * 100
                
                if item.returned_qty == 0:
                    item.return_status = "Not Returned"
                    item.status_color = "blue"
                elif item.returned_qty >= item.original_qty:
                    item.return_status = "Fully Returned"
                    item.status_color = "red"
                else:
                    item.return_status = "Partially Returned"
                    item.status_color = "orange"
            else:
                item.return_percentage = 0
                item.return_status = "N/A"
                item.status_color = "gray"
        
        return {"success": True, "items": items_data}
        
    except Exception as e:
        frappe.log_error(f"Error getting invoice items with return info: {str(e)}", "Invoice Items Return Info")
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def get_item_return_summary(invoice_name, item_idx):
    """
    Get detailed return summary for a specific item
    """
    try:
        # Get original item details
        original_item = frappe.db.get_value("Invoice Form Item", 
            {"parent": invoice_name, "idx": item_idx},
            ["item_code", "item_name", "qty", "price", "total"], as_dict=True)
        
        if not original_item:
            return {"success": False, "error": "Item not found"}
        
        # Get all return entries for this item
        returns = frappe.db.sql("""
            SELECT 
                inv.name as return_invoice,
                inv.posting_date,
                inv.docstatus,
                ABS(ifi.qty) as returned_qty,
                ifi.total as return_amount
            FROM `tabInvoice Form` inv
            INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
            WHERE inv.return_against = %s 
            AND inv.is_return = 1
            AND ifi.original_item_idx = %s
            ORDER BY inv.posting_date DESC, inv.creation DESC
        """, [invoice_name, item_idx], as_dict=True)
        
        total_returned_qty = sum(ret.returned_qty for ret in returns if ret.docstatus in [0, 1])
        available_qty = original_item.qty - total_returned_qty
        
        return {
            "success": True,
            "original_item": original_item,
            "returns": returns,
            "total_returned_qty": total_returned_qty,
            "available_qty": available_qty,
            "return_percentage": (total_returned_qty / original_item.qty * 100) if original_item.qty > 0 else 0
        }
        
    except Exception as e:
        frappe.log_error(f"Error getting item return summary: {str(e)}", "Item Return Summary")
        return {"success": False, "error": str(e)}
    
# Add these methods to your existing invoice_form.py file
# Place them at the END of the file, after all your existing functions

@frappe.whitelist()
def get_returnable_items(invoice_name):
    """
    Get all items from an invoice with their return status
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"success": False, "error": "Invoice not found"}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        if invoice.docstatus != 1:
            return {"success": False, "error": "Can only return items from submitted invoices"}
        
        if hasattr(invoice, 'is_return') and invoice.is_return:
            return {"success": False, "error": "Cannot return items from a return invoice"}
        
        returnable_items = []
        
        for item in invoice.items:
            # Get total returned quantity for this item
            returned_qty = get_returned_quantity_for_item(invoice_name, item.idx)
            
            returnable_items.append({
                "idx": item.idx,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "original_qty": item.qty,
                "returned_qty": returned_qty,
                "price": item.price,
                "total": item.total,
                "customer": getattr(item, 'customer', ''),
                "pamper": getattr(item, 'pamper', ''),
                "couple_customer": getattr(item, 'couple_customer', 0),
                "customer_commission": getattr(item, 'customer_commission', 0)
            })
        
        return {"success": True, "items": returnable_items}
        
    except Exception as e:
        frappe.log_error(f"Error getting returnable items: {str(e)}", "Get Returnable Items")
        return {"success": False, "error": str(e)}

def get_returned_quantity_for_item(original_invoice_name, item_idx):
    """
    Get total returned quantity for a specific item from all return invoices
    """
    try:
        returned_qty = frappe.db.sql("""
            SELECT COALESCE(SUM(ABS(ifi.qty)), 0) as returned_qty
            FROM `tabInvoice Form` inv
            INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
            WHERE inv.return_against = %s 
            AND inv.is_return = 1
            AND inv.docstatus IN (0, 1)
            AND ifi.original_item_idx = %s
        """, [original_invoice_name, item_idx])
        
        return float(returned_qty[0][0] if returned_qty else 0)
    except:
        # If original_item_idx field doesn't exist yet, return 0
        return 0

@frappe.whitelist()
def create_partial_return_invoice(original_invoice_name, return_items):
    """
    Create a partial return invoice with selected items and quantities
    COMPLETE FUNCTION: Handle identical lines properly with full commission calculations
    """
    try:
        if isinstance(return_items, str):
            import json
            return_items = json.loads(return_items)
        
        # Validate if return can be created
        validation = get_return_validation_status(original_invoice_name)
        if not validation["can_create"]:
            return {"success": False, "error": validation["reason"]}
        
        # Get original invoice
        original_invoice = frappe.get_doc("Invoice Form", original_invoice_name)
        
        # Validate return items using idx-based validation
        validation_error = validate_return_items_by_idx(original_invoice, return_items)
        if validation_error:
            return {"success": False, "error": validation_error}
        
        # Create new return invoice
        return_invoice = frappe.new_doc("Invoice Form")
        
        # Copy basic fields from original
        return_invoice.update({
            "supplier": original_invoice.supplier,
            "customer": getattr(original_invoice, 'customer', ''),
            "pamper": getattr(original_invoice, 'pamper', ''),
            "company": original_invoice.company,
            "posting_date": frappe.utils.today(),
            "is_return": 1,
            "return_against": original_invoice_name,
            "commission_invoice_reference": getattr(original_invoice, 'commission_invoice_reference', ''),
            "remarks": f"Partial return against {original_invoice_name}"
        })
        
        # Add selected items with return quantities
        grand_total = 0
        total_return_commission = 0
        
        for return_item in return_items:
            # Use array_index to get the correct original item
            array_index = int(return_item["array_index"])
            if array_index >= len(original_invoice.items):
                frappe.log_error(f"Invalid array index {array_index} for invoice {original_invoice_name}")
                continue
                
            original_item = original_invoice.items[array_index]
            return_qty = float(return_item["return_qty"])
            
            # Validate the return quantity one more time
            returned_qty = get_returned_quantity_for_specific_line(original_invoice_name, original_item.idx)
            available_qty = original_item.qty - returned_qty
            
            if return_qty > available_qty:
                return {
                    "success": False, 
                    "error": f"Return quantity {return_qty} exceeds available quantity {available_qty} for line {original_item.idx}"
                }
            
            # Calculate return amounts (all negative)
            return_total = -abs(return_qty * original_item.price)
            
            # Calculate proportional customer commission
            return_commission = 0
            if hasattr(original_item, 'customer_commission') and original_item.customer_commission:
                commission_ratio = return_qty / original_item.qty if original_item.qty > 0 else 0
                return_commission = -abs(original_item.customer_commission * commission_ratio)
            
            # Add return item with original_item_idx reference
            return_invoice.append("items", {
                "item_code": original_item.item_code,
                "item_name": original_item.item_name,
                "qty": -abs(return_qty),  # Negative quantity
                "price": original_item.price,  # Same price as original
                "total": return_total,  # Negative total
                "customer": getattr(original_item, 'customer', ''),
                "pamper": getattr(original_item, 'pamper', ''),
                "couple_customer": getattr(original_item, 'couple_customer', 0),
                "customer_commission": return_commission,  # Negative commission
                "original_item_idx": original_item.idx,  # CRITICAL: Reference to original line
                "returned_qty": 0,  # Initialize for return invoice
                "available_qty": 0  # Not applicable for return items
            })
            
            grand_total += return_total
            total_return_commission += abs(return_commission)
        
        # Calculate proportional supplier commissions for return
        total_commissions_and_taxes = 0
        if hasattr(original_invoice, 'commissions') and original_invoice.commissions and grand_total < 0:
            # Calculate commission ratio based on return amount vs original amount
            if original_invoice.grand_total != 0:
                commission_ratio = abs(grand_total) / abs(original_invoice.grand_total)
                
                for original_commission in original_invoice.commissions:
                    # Calculate proportional commission amounts (negative for return)
                    return_commission_amount = -abs(getattr(original_commission, 'total_commission', 0) * commission_ratio)
                    return_commission_taxes = -abs((getattr(original_commission, 'commission_total', 0) - getattr(original_commission, 'total_commission', 0)) * commission_ratio)
                    return_commission_total = return_commission_amount + return_commission_taxes
                    
                    return_invoice.append("commissions", {
                        "item": original_commission.item,
                        "price": grand_total,  # Already negative
                        "commission": getattr(original_commission, 'commission', 0),  # Keep percentage same
                        "taxes": getattr(original_commission, 'taxes', 0),  # Keep percentage same
                        "commission_total": return_commission_total,  # Negative total
                        "total_commission": return_commission_amount  # Negative commission
                    })
                    
                    total_commissions_and_taxes += return_commission_total
        
        # Calculate proportional pamper commission
        pamper_commission = 0
        if (hasattr(original_invoice, 'pamper_commission') and 
            original_invoice.pamper_commission and 
            grand_total < 0 and 
            original_invoice.grand_total != 0):
            
            commission_ratio = abs(grand_total) / abs(original_invoice.grand_total)
            pamper_commission = -abs(original_invoice.pamper_commission * commission_ratio)
        
        # Calculate proportional pamper commissions table
        if hasattr(original_invoice, 'pamper_commissions') and original_invoice.pamper_commissions and grand_total < 0:
            if original_invoice.grand_total != 0:
                commission_ratio = abs(grand_total) / abs(original_invoice.grand_total)
                
                for original_pamper_comm in original_invoice.pamper_commissions:
                    return_pamper_price = -abs(getattr(original_pamper_comm, 'price', 0) * commission_ratio)
                    return_pamper_commission = -abs(getattr(original_pamper_comm, 'commission', 0) * commission_ratio)
                    
                    return_invoice.append("pamper_commissions", {
                        "pamper": getattr(original_pamper_comm, 'pamper', ''),
                        "price": return_pamper_price,
                        "percentage": getattr(original_pamper_comm, 'percentage', 0),  # Keep percentage same
                        "commission": return_pamper_commission
                    })
        
        # Set all totals (negative for return)
        return_invoice.grand_total = grand_total  # Already negative
        
        # Set commission totals if they exist
        if hasattr(return_invoice, 'total_commissions_and_taxes'):
            return_invoice.total_commissions_and_taxes = total_commissions_and_taxes
        
        if hasattr(return_invoice, 'pamper_commission'):
            return_invoice.pamper_commission = pamper_commission
        
        # Copy any other relevant fields from original
        if hasattr(original_invoice, 'currency'):
            return_invoice.currency = original_invoice.currency
        
        if hasattr(original_invoice, 'conversion_rate'):
            return_invoice.conversion_rate = original_invoice.conversion_rate
        
        # Set flags for return invoice processing
        return_invoice.flags.ignore_permissions = True
        
        # Save the return invoice
        return_invoice.insert()
        
        # Update returned quantities in original invoice items
        update_original_invoice_returned_quantities(original_invoice_name)
        
        # Calculate summary information
        total_return_amount = abs(grand_total)
        item_count = len(return_items)
        total_return_qty = sum(float(item["return_qty"]) for item in return_items)
        
        return {
            "success": True,
            "return_invoice_name": return_invoice.name,
            "message": f"Partial return invoice {return_invoice.name} created successfully",
            "summary": {
                "return_amount": total_return_amount,
                "grand_total": grand_total,  # Negative value
                "item_count": item_count,
                "total_return_qty": total_return_qty,
                "commission_amount": abs(total_commissions_and_taxes) if total_commissions_and_taxes else 0,
                "pamper_commission": abs(pamper_commission) if pamper_commission else 0
            }
        }
        
    except Exception as e:
        # Log the full error for debugging
        import traceback
        error_traceback = traceback.format_exc()
        frappe.log_error(
            f"Error creating partial return invoice for {original_invoice_name}:\n"
            f"Return items: {return_items}\n"
            f"Error: {str(e)}\n"
            f"Traceback:\n{error_traceback}", 
            "Partial Return Invoice Creation"
        )
        return {
            "success": False, 
            "error": f"Failed to create return invoice: {str(e)}"
        }
def validate_return_items_by_idx(original_invoice, return_items):
    """
    Validate return items using idx-based tracking for identical lines
    """
    for return_item in return_items:
        return_qty = float(return_item["return_qty"])
        
        if return_qty <= 0:
            return f"Return quantity must be greater than 0"
        
        # Get original item using array_index
        array_index = int(return_item["array_index"])
        if array_index >= len(original_invoice.items):
            return f"Invalid line index: {array_index}"
            
        original_item = original_invoice.items[array_index]
        
        # Check available quantity for this specific line
        returned_qty = get_returned_quantity_for_specific_line(
            original_invoice.name, 
            original_item.idx
        )
        available_qty = original_item.qty - returned_qty
        
        if return_qty > available_qty:
            return (f"Return quantity ({return_qty}) exceeds available quantity ({available_qty}) "
                   f"for line {original_item.idx}: {original_item.item_name}")
    
    return None    

def validate_return_items(original_invoice, return_items):
    """
    Validate that return items and quantities are valid
    """
    for return_item in return_items:
        return_qty = float(return_item["return_qty"])
        
        if return_qty <= 0:
            return f"Return quantity must be greater than 0"
        
        # Find original item using array_index if available
        original_item = None
        if "array_index" in return_item:
            array_index = int(return_item["array_index"])
            if array_index < len(original_invoice.items):
                original_item = original_invoice.items[array_index]
        else:
            # Fallback: search by idx
            item_idx = return_item["idx"]
            for item in original_invoice.items:
                if item.idx == item_idx:
                    original_item = item
                    break
        
        if not original_item:
            return f"Original item not found"
        
        # Check available quantity
        returned_qty = get_returned_quantity_for_item(original_invoice.name, original_item.idx)
        available_qty = original_item.qty - returned_qty
        
        if return_qty > available_qty:
            return f"Return quantity ({return_qty}) exceeds available quantity ({available_qty}) for item {original_item.item_name}"
    
    return None

def update_original_invoice_returned_quantities(original_invoice_name):
    """
    Update the returned quantities in the original invoice items
    UPDATED: Use idx-based tracking
    """
    try:
        original_invoice = frappe.get_doc("Invoice Form", original_invoice_name)
        
        for item in original_invoice.items:
            # Get returned quantity for this specific line using idx
            returned_qty = get_returned_quantity_for_specific_line(original_invoice_name, item.idx)
            
            # Update the item's returned quantity field
            try:
                frappe.db.set_value("Invoice Form Item", item.name, {
                    "returned_qty": returned_qty,
                    "available_qty": item.qty - returned_qty
                })
            except:
                # Fields might not exist yet, continue
                pass
        
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(f"Error updating returned quantities: {str(e)}", "Update Returned Quantities")

@frappe.whitelist()
def get_invoice_items_with_return_info(invoice_name):
    """
    Get all invoice items with their return information for display
    UPDATED: Handle identical lines with proper idx tracking
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"success": False, "error": "Invoice not found"}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        items_data = []
        
        for item in invoice.items:
            # Get returned quantity for this specific line using idx
            returned_qty = get_returned_quantity_for_specific_line(invoice_name, item.idx)
            available_qty = item.qty - returned_qty
            
            # Create line description for identical items
            line_description = f"Line {item.idx}: {item.item_name}"
            if item.customer:
                line_description += f" - {item.customer}"
            
            # Calculate return status
            if item.qty > 0:
                return_percentage = (returned_qty / item.qty) * 100
                
                if returned_qty == 0:
                    return_status = "Not Returned"
                    status_color = "blue"
                elif returned_qty >= item.qty:
                    return_status = "Fully Returned"
                    status_color = "red"
                else:
                    return_status = "Partially Returned"
                    status_color = "orange"
            else:
                return_percentage = 0
                return_status = "N/A"
                status_color = "gray"
            
            items_data.append({
                "idx": item.idx,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "line_description": line_description,
                "original_qty": item.qty,
                "returned_qty": returned_qty,
                "available_qty": available_qty,
                "price": item.price,
                "total": item.total,
                "customer": getattr(item, 'customer', ''),
                "pamper": getattr(item, 'pamper', ''),
                "return_percentage": return_percentage,
                "return_status": return_status,
                "status_color": status_color
            })
        
        return {"success": True, "items": items_data}
        
    except Exception as e:
        frappe.log_error(f"Error getting invoice items with return info: {str(e)}", "Invoice Items Return Info")
        return {"success": False, "error": str(e)}
    
@frappe.whitelist()
def get_return_validation_status(invoice_name):
    """
    Get detailed validation status for return creation
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"can_create": False, "reason": "Invoice not found", "details": {}}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        # Basic validations
        if invoice.docstatus != 1:
            return {
                "can_create": False, 
                "reason": "Can only create returns for submitted invoices",
                "details": {"docstatus": invoice.docstatus}
            }
        
        if hasattr(invoice, 'is_return') and invoice.is_return:
            return {
                "can_create": False, 
                "reason": "Cannot create return for a return invoice",
                "details": {"is_return": True}
            }
        
        # Check existing returns
        existing_returns = frappe.get_all("Invoice Form",
            filters={
                "return_against": invoice_name,
                "is_return": 1,
                "docstatus": ["in", [0, 1]]
            },
            fields=["name", "docstatus", "posting_date", "grand_total"]
        )
        
        # For now, allow multiple returns (partial returns feature)
        # if existing_returns:
        #     return {
        #         "can_create": False,
        #         "reason": f"Existing returns found: {len(existing_returns)} return(s)",
        #         "details": {
        #             "existing_returns": existing_returns,
        #             "return_count": len(existing_returns)
        #         }
        #     }
        
        # Check permissions
        if not frappe.has_permission("Invoice Form", "create"):
            return {
                "can_create": False, 
                "reason": "No permission to create Invoice Form",
                "details": {"permission_error": True}
            }
        
        return {
            "can_create": True, 
            "reason": "",
            "details": {
                "original_invoice": {
                    "name": invoice.name,
                    "grand_total": invoice.grand_total,
                    "posting_date": invoice.posting_date,
                    "supplier": invoice.supplier,
                    "items_count": len(invoice.items)
                }
            }
        }
        
    except Exception as e:
        return {"can_create": False, "reason": str(e), "details": {"error": True}}

@frappe.whitelist()
def get_invoice_return_info(invoice_name):
    """
    Get comprehensive return information for an invoice
    """
    try:
        if not frappe.db.exists("Invoice Form", invoice_name):
            return {"exists": False}
        
        invoice = frappe.get_doc("Invoice Form", invoice_name)
        
        # Get return validation
        validation = get_return_validation_status(invoice_name)
        
        # Get existing returns
        returns = frappe.get_all("Invoice Form",
            filters={
                "return_against": invoice_name,
                "is_return": 1
            },
            fields=["name", "docstatus", "posting_date", "grand_total", "creation"],
            order_by="creation desc"
        )
        
        return {
            "exists": True,
            "invoice": {
                "name": invoice.name,
                "docstatus": invoice.docstatus,
                "is_return": getattr(invoice, 'is_return', 0),
                "grand_total": invoice.grand_total,
                "posting_date": invoice.posting_date
            },
            "validation": validation,
            "returns": returns,
            "can_create_return": validation["can_create"]
        }
        
    except Exception as e:
        return {"exists": False, "error": str(e)}

# Update existing validate method in your InvoiceForm class - ADD these lines
def validate(self):
    # ... your existing validation code ...
    
    # ADD these lines for return validation
    if hasattr(self, 'is_return') and self.is_return:
        self.validate_return_amounts()
        if hasattr(self, 'return_against') and self.return_against:
            self.validate_return_invoice_enhanced()
    
    # ... rest of your existing validation code ...

# ADD these methods to your InvoiceForm class
def validate_return_amounts(self):
    """
    Ensure all amounts are negative for return invoices
    """
    if not getattr(self, 'is_return', False):
        return
        
    # Validate items have negative amounts
    for item in self.items:
        if item.qty > 0:
            item.qty = -abs(item.qty)
        if item.total > 0:
            item.total = -abs(item.total)
    
    # Ensure grand total is negative
    if self.grand_total > 0:
        self.grand_total = -abs(self.grand_total)

def validate_return_invoice_enhanced(self):
    """
    Enhanced validation for return invoices with quantity tracking
    """
    if not getattr(self, 'is_return', False):
        return
        
    if not getattr(self, 'return_against', None):
        frappe.throw(_("Return Against is mandatory for return invoices"))
    
    # Get original invoice
    if not frappe.db.exists("Invoice Form", self.return_against):
        frappe.throw(_("Return Against invoice does not exist"))
        
    original_invoice = frappe.get_doc("Invoice Form", self.return_against)
    if original_invoice.docstatus != 1:
        frappe.throw(_("Can only create returns against submitted invoices"))
    
    if getattr(original_invoice, 'is_return', False):
        frappe.throw(_("Cannot create return against another return invoice"))

def get_total_returned_quantity_for_item_customer(original_invoice_name, item_code, customer, pamper, exclude_current=None):
    """
    Get total returned quantity for a specific item/customer combination from all return invoices
    """
    conditions = [
        "inv.return_against = %s",
        "inv.is_return = 1", 
        "inv.docstatus IN (0, 1)",  # Include draft and submitted
        "ifi.item_code = %s"
    ]
    
    values = [original_invoice_name, item_code]
    
    # Add customer condition
    if customer:
        conditions.append("ifi.customer = %s")
        values.append(customer)
    else:
        conditions.append("(ifi.customer IS NULL OR ifi.customer = '')")
    
    # Add pamper condition  
    if pamper:
        conditions.append("ifi.pamper = %s")
        values.append(pamper)
    else:
        conditions.append("(ifi.pamper IS NULL OR ifi.pamper = '')")
    
    # Exclude current return invoice if specified
    if exclude_current:
        conditions.append("inv.name != %s")
        values.append(exclude_current)
    
    result = frappe.db.sql(f"""
        SELECT COALESCE(SUM(ABS(ifi.qty)), 0) as returned_qty
        FROM `tabInvoice Form` inv
        INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
        WHERE {' AND '.join(conditions)}
    """, values)
    
    return float(result[0][0] if result else 0)
def get_returned_quantity_for_specific_line(original_invoice_name, original_item_idx, exclude_current=None):
    """
    Get total returned quantity for a specific original invoice line using idx
    """
    conditions = [
        "inv.return_against = %s",
        "inv.is_return = 1", 
        "inv.docstatus IN (0, 1)",  # Include draft and submitted
        "ifi.original_item_idx = %s"
    ]
    
    values = [original_invoice_name, original_item_idx]
    
    # Exclude current return invoice if specified
    if exclude_current:
        conditions.append("inv.name != %s")
        values.append(exclude_current)
    
    result = frappe.db.sql(f"""
        SELECT COALESCE(SUM(ABS(ifi.qty)), 0) as returned_qty
        FROM `tabInvoice Form` inv
        INNER JOIN `tabInvoice Form Item` ifi ON inv.name = ifi.parent
        WHERE {' AND '.join(conditions)}
    """, values)
    
    return float(result[0][0] if result else 0)

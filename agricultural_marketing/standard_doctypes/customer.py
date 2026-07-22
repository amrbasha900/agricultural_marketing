import frappe
from frappe import _
from frappe.utils import cint

from erpnext.selling.doctype.customer.customer import Customer


class AgricultureCustomer(Customer):
	def validate_credit_limit_on_change(self):
		if not cint(
			frappe.db.get_single_value(
				"Agriculture Settings", "allow_customer_credit_limit_below_outstanding"
			)
		):
			return super().validate_credit_limit_on_change()

		self.validate_credit_limit_companies_on_change()

	def validate_credit_limit_companies_on_change(self):
		if self.get("__islocal") or not self.credit_limits:
			return

		past_credit_limits = [
			d.credit_limit
			for d in frappe.db.get_all(
				"Customer Credit Limit",
				filters={"parent": self.name},
				fields=["credit_limit"],
				order_by="company",
			)
		]

		current_credit_limits = [d.credit_limit for d in sorted(self.credit_limits, key=lambda k: k.company)]

		if past_credit_limits == current_credit_limits:
			return

		company_record = []
		for limit in self.credit_limits:
			if limit.company in company_record:
				frappe.throw(
					_("Credit limit is already defined for the Company {0}").format(limit.company, self.name)
				)

			company_record.append(limit.company)

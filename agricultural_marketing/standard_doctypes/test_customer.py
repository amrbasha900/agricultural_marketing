from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from agricultural_marketing.standard_doctypes.customer import AgricultureCustomer


class TestAgricultureCustomer(FrappeTestCase):
	def make_customer(self, credit_limits):
		customer = AgricultureCustomer(
			{
				"doctype": "Customer",
				"name": "_Test Agriculture Customer",
				"customer_name": "_Test Agriculture Customer",
				"customer_group": "All Customer Groups",
				"territory": "All Territories",
			}
		)
		customer.credit_limits = [frappe._dict(limit) for limit in credit_limits]
		return customer

	@patch("agricultural_marketing.standard_doctypes.customer.Customer.validate_credit_limit_on_change")
	@patch("frappe.db.get_single_value", return_value=0)
	def test_default_setting_uses_erpnext_credit_limit_validation(self, get_single_value, validate):
		customer = self.make_customer([{"company": "_Test Company", "credit_limit": 100}])

		customer.validate_credit_limit_on_change()

		get_single_value.assert_called_once_with(
			"Agriculture Settings", "allow_customer_credit_limit_below_outstanding"
		)
		validate.assert_called_once()

	@patch("agricultural_marketing.standard_doctypes.customer.Customer.validate_credit_limit_on_change")
	@patch("frappe.db.get_all", return_value=[frappe._dict(credit_limit=1000)])
	@patch("frappe.db.get_single_value", return_value=1)
	def test_enabled_setting_skips_outstanding_amount_validation(
		self, get_single_value, get_all, validate
	):
		customer = self.make_customer([{"company": "_Test Company", "credit_limit": 100}])

		customer.validate_credit_limit_on_change()

		validate.assert_not_called()
		get_all.assert_called_once()

	@patch("frappe.db.get_all", return_value=[frappe._dict(credit_limit=1000)])
	@patch("frappe.db.get_single_value", return_value=1)
	def test_enabled_setting_still_rejects_duplicate_credit_limit_company(
		self, get_single_value, get_all
	):
		customer = self.make_customer(
			[
				{"company": "_Test Company", "credit_limit": 100},
				{"company": "_Test Company", "credit_limit": 200},
			]
		)

		with self.assertRaises(frappe.ValidationError):
			customer.validate_credit_limit_on_change()

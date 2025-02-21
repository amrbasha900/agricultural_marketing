import frappe


def create_related_customer(self, method):
    related_customer = frappe.new_doc("Customer")
    related_customer.update(
        {
            "customer_name": self.supplier_name,
            "customer_group": self.related_customer_group or frappe.db.get_single_value("Selling Settings",
                                                                                        "customer_group"),
            "is_farmer": 1,
            "commission_percentage": self.commission_percentage
        }
    )

    related_customer.insert(ignore_permissions=True)
    self.db_set("related_customer", related_customer.name)
    self.db_set("related_customer_group", related_customer.customer_group)


def delete_related_customer(self, method):
    if self.related_customer:
        customer = frappe.get_doc("Customer", self.related_customer)
        customer.run_method("on_trash")
        frappe.delete_doc("Customer", self.related_customer, for_reload=True)

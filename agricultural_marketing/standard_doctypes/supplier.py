import frappe


def create_related_customer(self, method):
    if self.is_farmer:
        related_customer = frappe.new_doc("Customer")
        related_customer.update(
            {
                "customer_name": self.supplier_name,
                "customer_group": self.related_customer_group or frappe.db.get_single_value("Selling Settings",
                                                                                            "customer_group"),
                "is_farmer": 0 if frappe.db.get_single_value("Agriculture Settings", "allow_couple_account") else 1,
                "is_customer": 1 if frappe.db.get_single_value("Agriculture Settings", "allow_couple_account") else 0,
                "couple_customer": 1 if frappe.db.get_single_value("Agriculture Settings", "allow_couple_account") else 0,
                "commission_percentage": self.commission_percentage
            }
        )

        related_customer.insert(ignore_permissions=True)
        related_customer.rename(name=self.name)
        related_customer.customer_name = self.supplier_name
        related_customer.save()
        self.db_set("related_customer", related_customer.name)
        self.db_set("related_customer_group", related_customer.customer_group)
        


def delete_related_customer(self, method):
    if self.related_customer:
        customer = frappe.get_doc("Customer", self.related_customer)
        customer.run_method("on_trash")
        frappe.delete_doc("Customer", self.related_customer, for_reload=True)

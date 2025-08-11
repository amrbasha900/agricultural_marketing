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


import frappe

def sync_supplier_to_customer(doc, method):
    # Check if supplier has a linked customer
    customer_link = doc.get("related_customer")  # change this to your actual fieldname
    if not customer_link:
        return

    customer = frappe.get_doc("Customer", customer_link)

    # Rename Customer if Supplier's name changed
    if customer.name != doc.name:
        # Rename customer document
        frappe.rename_doc("Customer", customer.name, doc.name, merge=False)
        customer = frappe.get_doc("Customer", doc.name)  # Re-fetch after rename

    # Update customer_name if supplier_name changed
    if customer.customer_name != doc.supplier_name:
        customer.customer_name = doc.supplier_name
        customer.save()

def rename_customer_from_supplier(doc, method, old_name, new_name, merge=False):
    # Get the old supplier document
    old_doc = frappe.get_doc("Supplier", new_name)
    
    # Get linked customer
    customer_link = old_doc.get("related_customer")  # Replace with your custom link field name

    if not customer_link:
        return

    try:
        # Rename the linked customer
        frappe.rename_doc("Customer", customer_link, new_name, merge=merge)
    except frappe.DuplicateEntryError:
        frappe.throw(f"A Customer with name {new_name} already exists.")
import frappe


def create_related_customer(self, method):
    if not self.is_farmer:
        return
    
    if self.related_customer:
        return

    settings = frappe.get_single("Agriculture Settings")

    # If configured, link to the single default customer only when the setting is enabled.
    # (This avoids accidental rename/delete of the default customer by other hooks.)
    if settings.use_default_customer_in_tax_invoice:
        if not settings.default_customer:
            frappe.throw("Please set Default Customer in Agriculture Settings.")

        customer = frappe.get_doc("Customer", settings.default_customer)
        self.db_set("related_customer", settings.default_customer)
        self.db_set("related_customer_group", customer.customer_group)
        return

    # Otherwise create (or link) a dedicated Customer for this Supplier
    if self.related_customer:
        return

    allow_couple_account = bool(frappe.db.get_single_value("Agriculture Settings", "allow_couple_account"))
    default_customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")

    related_customer = frappe.new_doc("Customer")
    related_customer.update(
        {
            "customer_name": self.supplier_name,
            "customer_group": self.related_customer_group or default_customer_group,
            "is_farmer": 0 if allow_couple_account else 1,
            "is_customer": 1 if allow_couple_account else 0,
            "couple_customer": 1 if allow_couple_account else 0,
            "commission_percentage": self.commission_percentage,
        }
    )

    related_customer.insert(ignore_permissions=True)
    # Keep Customer name aligned with Supplier name
    frappe.rename_doc(
        "Customer",
        related_customer.name,
        self.name,
        merge=False,
    )
    related_customer = frappe.get_doc("Customer", self.name)
    related_customer.customer_name = self.supplier_name
    related_customer.save(ignore_permissions=True)

    self.db_set("related_customer", related_customer.name)
    self.db_set("related_customer_group", related_customer.customer_group)
        


def delete_related_customer(self, method):
    if self.related_customer:
        settings = frappe.get_single("Agriculture Settings")
        if settings.use_default_customer_in_tax_invoice:
            return
        # delete_doc will run hooks (including on_trash) itself.
        frappe.delete_doc("Customer", self.related_customer, for_reload=True, ignore_permissions=True)

def sync_supplier_to_customer(doc, method):
    # Check if supplier has a linked customer
    settings = frappe.get_single("Agriculture Settings")
    if settings.use_default_customer_in_tax_invoice:
        return
    customer_link = doc.get("related_customer")  # change this to your actual fieldname
    if not customer_link:
        return

    customer = frappe.get_doc("Customer", customer_link)

    # Update customer_name if supplier_name changed
    if customer.customer_name != doc.supplier_name:
        customer.customer_name = doc.supplier_name
        customer.save(ignore_permissions=True)

def rename_customer_from_supplier(doc, method, old_name, new_name, merge=False):
    settings = frappe.get_single("Agriculture Settings")
    if settings.use_default_customer_in_tax_invoice:
        return

    # doc is the renamed Supplier (after_rename hook)
    supplier = doc or frappe.get_doc("Supplier", new_name)

    # Get linked customer
    customer_link = supplier.get("related_customer")

    if not customer_link:
        return

    # Never rename the configured default customer (if any).
    if settings.default_customer and customer_link == settings.default_customer:
        return

    try:
        # Rename the linked customer
        frappe.rename_doc("Customer", customer_link, new_name, merge=merge, ignore_permissions=True)
    except frappe.DuplicateEntryError:
        frappe.throw(f"A Customer with name {new_name} already exists.")
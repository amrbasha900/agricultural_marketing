# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint
from collections import defaultdict

# Above this many rows the generation runs in a background job instead of inside
# the web request. A large bulk invoice takes longer than the gateway timeout on
# a slow server, and a request that dies half way used to leave Invoice Forms
# behind that the next attempt would fill a second time.
BACKGROUND_THRESHOLD = 50

# Realtime channel used to report progress of the background job to the browser.
PROGRESS_EVENT = "bulk_invoice_forms_progress"


class BulkInvoiceForm(Document):
    def validate(self):
        self.validate_customer_is_not_the_supplier()

    def validate_customer_is_not_the_supplier(self):
        """A farmer must never be billed as the buyer of their own produce.

        Almost every supplier here has a customer record under the same code
        (2693 of 2704 on the live site), so plain code equality catches nearly
        all of it. `Supplier.related_customer` is the durable link between the
        two records -- the same one before_submit already relies on -- and it
        catches the handful of pairs whose codes differ.

        The customer picker hides these already; this is the backstop for a
        code typed straight into the quick entry bar or set by an import.
        """
        suppliers = {row.supplier for row in self.items if row.supplier}
        coupled = (
            dict(
                frappe.get_all(
                    "Supplier",
                    filters={"name": ("in", list(suppliers))},
                    fields=["name", "related_customer"],
                    as_list=True,
                )
            )
            if suppliers
            else {}
        )

        for row in self.items:
            if not row.customer or not row.supplier:
                continue
            if row.customer == row.supplier or row.customer == coupled.get(row.supplier):
                frappe.throw(
                    _("Row {0}: customer {1} is the same party as supplier {2}. Choose a different customer.").format(
                        row.idx, frappe.bold(row.customer), frappe.bold(row.supplier)
                    ),
                    title=_("Customer and supplier are the same party"),
                )

    def before_save(self):
        """Handle updates before saving - only calculate totals"""
        # Only calculate totals, no automatic invoice form operations
        self.calculate_totals()
    # def before_save(self):
    #     """Handle updates before saving with change detection"""
    #     # Get the old document for comparison
    #     old_doc = self.get_doc_before_save()
        
    #     # First calculate totals
    #     self.calculate_totals()
        
    #     # Handle changes if there's an old document
    #     if old_doc:
    #         self.handle_item_changes(old_doc)
    
    # def after_insert(self):
    #     """Handle actions after document is inserted (for new documents)"""
    #     # For new documents, auto-create invoice forms after the document has a name
    #     self.auto_create_invoice_forms_for_unlinked_items()
        
    # def after_save(self):
    #     """Handle actions after document is saved"""
    #     # For existing documents that were updated, ensure all items have invoice forms
    #     if not self.is_new():
    #         self.auto_create_invoice_forms_for_unlinked_items()

    def before_submit(self):
        for i in self.items:
            if not i.reference_invoice_form:
                frappe.throw(_('You Should Create Invoice Form For All Rows'))
            
            # Pre-check for couple customer supplier link
            couple_customer = frappe.db.get_value('Customer', i.customer, 'couple_customer')
            if couple_customer:
                couple_supplier = frappe.db.get_value('Supplier', {'related_customer': i.customer}, 'name')
                if not couple_supplier:
                    frappe.throw(_("Related Supplier not found for customer {0} (Row {1})").format(i.customer, i.idx))

    def handle_item_changes(self, old_doc):
        """Detect and handle changes in items table"""
        # Create dictionaries for easy lookup
        old_items = {item.name: item for item in old_doc.items}
        current_items = {item.name: item for item in self.items}

        # Track which items were deleted
        deleted_items = []
        for old_item_name in old_items:
            if old_item_name not in current_items:
                deleted_items.append(old_items[old_item_name])
        
        # Handle deleted items
        for deleted_item in deleted_items:
            frappe.log_error(message=f"Item deleted: {deleted_item.name}", title="Bulk Invoice Change Detection")
            self.handle_item_deletion(deleted_item)
        
        # Track which items were modified or added
        modified_items = []
        new_items = []
        
        for current_item in self.items:
            if current_item.name in old_items:
                # Existing item - check if modified
                old_item = old_items[current_item.name]
                if self.item_has_changes(old_item, current_item):
                    modified_items.append(current_item)
                    frappe.log_error(message=f"Item modified: {current_item.name}", title="Bulk Invoice Change Detection")
            else:
                # New item
                new_items.append(current_item)
                frappe.log_error(message=f"Item added: {current_item.name}", title="Bulk Invoice Change Detection")
        
        # Handle modified items
        for item in modified_items:
            self.handle_item_modification(item, old_items[item.name])
        
        # Handle new items - automatically create/append to invoice forms
        self.handle_new_items(new_items)
    
    def item_has_changes(self, old_item, current_item):
        """Check if an item has meaningful changes"""
        fields_to_check = [
            'item_code', 'item_name', 'qty', 'price', 'total',
            'supplier', 'customer', 'pamper'
        ]
        
        for field in fields_to_check:
            old_value = getattr(old_item, field, None)
            current_value = getattr(current_item, field, None)
            if old_value != current_value:
                frappe.log_error(message=f"Field {field} changed from {old_value} to {current_value}", title="Item Change Detection")
                return True
        
        return False
    
    def handle_item_deletion(self, deleted_item):
        """Handle deletion of an item"""
        if deleted_item.reference_invoice_form:
            try:
                invoice_form = frappe.get_doc("Invoice Form", deleted_item.reference_invoice_form)
                original_count = len(invoice_form.items)
                
                # Find and remove the item using multiple matching criteria
                items_to_remove = []
                for inv_item in invoice_form.items:
                    if (inv_item.name == deleted_item.reference_invoice_form_item or 
                        inv_item.bulk_invoice_item_reference == deleted_item.name):
                        items_to_remove.append(inv_item)
                
                # Remove found items
                for inv_item in items_to_remove:
                    invoice_form.items.remove(inv_item)
                
                if len(invoice_form.items) == 0:
                    # If no items left, delete the entire invoice form
                    invoice_form.delete(force=1)
                    frappe.log_error(message=f"Deleted Invoice Form {deleted_item.reference_invoice_form} as it had no items left", title="Item Deletion")
                else:
                    # Recalculate totals and save
                    invoice_form.run_method("calculate_totals")
                    invoice_form.save()
                    frappe.log_error(message=f"Removed item from Invoice Form {deleted_item.reference_invoice_form}", title="Item Deletion")
                
            except Exception as e:
                frappe.log_error(message=f"Error handling deletion of item {deleted_item.name}: {str(e)}", title="Item Deletion Error")
    
    def handle_item_modification(self, current_item, old_item):
        """Handle modification of an existing item"""
        if not current_item.reference_invoice_form:
            return
        
        try:
            # Check if supplier changed
            if old_item.supplier != current_item.supplier:
                # Handle supplier change
                self.handle_supplier_change_sync(current_item, None)
            else:
                # Update existing item in the same invoice form
                self.sync_single_item(current_item)
                
        except Exception as e:
            frappe.log_error(message=f"Error handling modification of item {current_item.name}: {str(e)}", title="Item Modification Error")
    
    def sync_single_item(self, item):
        """Sync a single item with its invoice form"""
        if not item.reference_invoice_form:
            return
        
        try:
            invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
            
            # Find and update the item
            item_found = False
            for inv_item in invoice_form.items:
                if (inv_item.bulk_invoice_item_reference == item.name or 
                    inv_item.name == item.reference_invoice_form_item):
                    # Update the item with current values
                    inv_item.item_code = item.item_code
                    inv_item.item_name = item.item_name
                    inv_item.qty = item.qty
                    inv_item.price = item.price
                    inv_item.total = item.total
                    inv_item.customer = item.customer
                    inv_item.pamper = item.pamper
                    item_found = True
                    break
            
            if item_found:
                invoice_form.run_method("calculate_totals")
                invoice_form.save()
            else:
                frappe.log_error(message=f"Could not find item {item.name} in invoice form {item.reference_invoice_form}", title="Sync Error")
                
        except Exception as e:
            frappe.log_error(message=f"Error syncing item {item.name}: {str(e)}", title="Sync Error")
                
    def handle_new_items(self, new_items):
        """Handle newly added items - automatically create or append to invoice forms"""
        if not new_items:
            return
            
        created_forms = []
        updated_forms = []
        
        for item in new_items:
            # Skip if item already has a reference (shouldn't happen for new items, but just in case)
            if item.reference_invoice_form:
                self.sync_single_item(item)
                continue
            
            # Skip if item doesn't have a supplier
            if not item.supplier:
                frappe.log_error(message=f"New item {item.name} has no supplier, skipping auto-creation", title="Auto Invoice Creation")
                continue
            
            try:
                # Find or create invoice form for this supplier
                invoice_form = find_or_create_invoice_form(self, item.supplier)
                
                # Track if this is a new form
                if invoice_form.name not in created_forms and invoice_form.name not in updated_forms:
                    # Check if this invoice form was just created or already existed
                    existing_items_count = 0
                    for existing_item in invoice_form.items:
                        if existing_item.bulk_invoice_reference != self.name:
                            existing_items_count += 1
                    
                    if existing_items_count == 0:
                        created_forms.append(invoice_form.name)
                    else:
                        updated_forms.append(invoice_form.name)
                
                # Add item to invoice form
                invoice_item = invoice_form.append("items", {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "qty": item.qty,
                    "price": item.price,
                    "total": item.total,
                    "customer": item.customer,
                    "pamper": item.pamper,
                    "bulk_invoice_reference": self.name,
                    "bulk_invoice_item_reference": item.name
                })
                
                # Save the invoice form
                invoice_form.save()

                # Update references in bulk invoice item. Only after the save,
                # a row appended in memory has no name yet.
                item.reference_invoice_form = invoice_form.name
                item.reference_invoice_form_item = invoice_item.name
                
            except Exception as e:
                frappe.log_error(message=f"Error auto-creating invoice for new item {item.name} with supplier {item.supplier}: {str(e)}", title="Auto Invoice Creation Error")
        
        # Show user feedback for auto-created/updated forms
        if created_forms or updated_forms:
            messages = []
            if created_forms:
                messages.append(f"Auto-created {len(created_forms)} new Invoice Forms: {', '.join(created_forms)}")
            if updated_forms:
                messages.append(f"Auto-updated {len(updated_forms)} existing Invoice Forms: {', '.join(updated_forms)}")
            
            frappe.msgprint("<br>".join(messages), title="Auto Invoice Creation", indicator="green")
    
    def auto_create_invoice_forms_for_unlinked_items(self):
        """Automatically create invoice forms for items without references"""
        # Only proceed if document has a name (has been saved)
        if not self.name:
            return
            
        unlinked_items = [item for item in self.items if not item.reference_invoice_form and item.supplier]
        
        if unlinked_items:
            frappe.log_error(message=f"Found {len(unlinked_items)} unlinked items, auto-creating invoice forms", title="Auto Invoice Creation")
            self.handle_new_items(unlinked_items)
            # Save the document to persist the updated references
            self.save(ignore_permissions=True)
    
        
    def sync_with_invoice_forms(self):
        """Sync changes with related invoice forms (legacy method)"""
        for item in self.items:
            if item.reference_invoice_form and item.reference_invoice_form_item:
                self.sync_single_item(item)
    
    def handle_supplier_change_sync(self, item, old_invoice_form):
        """Handle supplier change during sync"""
        if not old_invoice_form and item.reference_invoice_form:
            try:
                old_invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
            except:
                return
        
        if old_invoice_form:
            # Remove item from old invoice form
            old_invoice_form.items = [i for i in old_invoice_form.items 
                                    if i.name != item.reference_invoice_form_item and 
                                       i.bulk_invoice_item_reference != item.name]
            
            if len(old_invoice_form.items) == 0:
                # Delete old invoice form if no items left
                old_invoice_form.delete(force=1)
            else:
                old_invoice_form.save()
        
        # Find or create new invoice form for new supplier
        new_invoice_form = find_or_create_invoice_form(self, item.supplier)
        
        # Add item to new invoice form
        new_item = new_invoice_form.append("items", {
            "item_code": item.item_code,
            "item_name": item.item_name,
            "qty": item.qty,
            "price": item.price,
            "total": item.total,
            "customer": item.customer,
            "pamper": item.pamper,
            "bulk_invoice_reference": self.name,
            "bulk_invoice_item_reference": item.name
        })
        
        new_invoice_form.save()
        
        # Update references in bulk invoice
        item.reference_invoice_form = new_invoice_form.name
        item.reference_invoice_form_item = new_item.name
        
    def calculate_totals(self):
        """Calculate totals for each item row"""
        for item in self.items:
            item.total = (item.qty or 0) * (item.price or 0)
    
    def on_submit(self):
        """Handle submission - submit all related invoice forms"""
        self.submit_related_invoice_forms()
    
    def submit_related_invoice_forms(self):
        """Submit all invoice forms generated from this bulk invoice"""
        # Get all unique invoice forms referenced by items
        invoice_form_names = set()
        for item in self.items:
            if item.reference_invoice_form:
                invoice_form_names.add(item.reference_invoice_form)
        
        for invoice_form_name in invoice_form_names:
            invoice_form = frappe.get_doc("Invoice Form", invoice_form_name)
            
            # Only submit if not already submitted
            if invoice_form.docstatus == 0:
                invoice_form.submit()
                frappe.log_error(message=f"Submitted Invoice Form: {invoice_form_name}", title="Bulk Invoice Submission")

    
    def on_cancel(self):
        """Handle cancellation - cancel all related invoice forms"""
        self.cancel_related_invoice_forms()
    
    def cancel_related_invoice_forms(self):
        """Cancel all invoice forms generated from this bulk invoice"""
        # Get all unique invoice forms referenced by items
        invoice_form_names = set()
        for item in self.items:
            if item.reference_invoice_form:
                invoice_form_names.add(item.reference_invoice_form)
        
        for invoice_form_name in invoice_form_names:
            invoice_form = frappe.get_doc("Invoice Form", invoice_form_name)
            
            # Only cancel if submitted
            if invoice_form.docstatus == 1:
                invoice_form.cancel()
                frappe.log_error(message=f"Cancelled Invoice Form: {invoice_form_name}", title="Bulk Invoice Cancellation")

    
    @frappe.whitelist()
    def create_invoice_forms(self):
        """Create Invoice Forms grouped by supplier.

        Small documents are generated inline. Anything past BACKGROUND_THRESHOLD
        rows is handed to a background job, because generation on a slow server
        outlives the gateway timeout and the user then retries on top of a half
        finished run. Either way the work itself is done by build_invoice_forms(),
        which is safe to run twice: rows already present in an Invoice Form are
        never appended again.
        """
        if not self.items:
            frappe.throw(_("No items to create invoices from"))

        if self.is_new():
            frappe.throw(_("Please save the document before creating Invoice Forms"))

        # Rows the user typed but did not save yet are not in the database, so the
        # job would silently skip them. Ask for a save instead of losing them.
        if any(item.get("__islocal") or (item.name or "").startswith("new-") for item in self.items):
            frappe.throw(_("Please save the document before creating Invoice Forms"))

        if len(self.items) > BACKGROUND_THRESHOLD:
            frappe.enqueue(
                "agricultural_marketing.agricultural_marketing.doctype.bulk_invoice_form.bulk_invoice_form.create_invoice_forms_in_background",
                queue="long",
                timeout=3600,
                job_id=background_job_id(self.name),
                deduplicate=True,
                bulk_invoice=self.name,
                user=frappe.session.user,
            )
            return {"queued": True, "total_rows": len(self.items)}

        result = build_invoice_forms(self.name)
        frappe.msgprint(format_result_message(result))
        return result

    def on_trash(self):
        """Handle bulk invoice deletion"""
        # Clean up related invoice forms
        for item in self.items:
            if item.reference_invoice_form:
                try:
                    invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
                    
                    # Remove items that reference this bulk invoice
                    invoice_form.items = [i for i in invoice_form.items 
                                        if i.bulk_invoice_reference != self.name]
                    
                    if len(invoice_form.items) == 0:
                        # Delete invoice form if no items left
                        invoice_form.delete(force=1)
                    else:
                        # Clear the bulk invoice references
                        for inv_item in invoice_form.items:
                            inv_item.bulk_invoice_reference = None
                            inv_item.bulk_invoice_item_reference = None
                        invoice_form.save()
                        
                except Exception as e:
                    frappe.log_error(message=f"Error cleaning up invoice form {item.reference_invoice_form}: {str(e)}", title="Cleanup Error")

    @frappe.whitelist()
    def sync_with_invoice_forms(self):
        """Manually sync changes with related invoice forms"""
        synced_forms = []
        failed_forms = []
        
        for item in self.items:
            if item.reference_invoice_form and item.reference_invoice_form_item:
                try:
                    result = self.sync_single_item(item)
                    if result.get("success", True) and item.reference_invoice_form not in synced_forms:
                        synced_forms.append(item.reference_invoice_form)
                except Exception as e:
                    if item.reference_invoice_form not in failed_forms:
                        failed_forms.append(item.reference_invoice_form)
                    frappe.log_error(message=f"Error syncing item {item.name}: {str(e)}", title="Sync Error")
        
        # Show results
        if synced_forms:
            frappe.msgprint(_("Successfully synced {0} Invoice Forms: {1}").format(
                len(synced_forms), ", ".join(synced_forms)
            ))
        
        if failed_forms:
            frappe.msgprint(_("Failed to sync {0} Invoice Forms: {1}").format(
                len(failed_forms), ", ".join(failed_forms)
            ), indicator="red")
        
        return {"synced": synced_forms, "failed": failed_forms}
# Helper functions...

@frappe.whitelist()
def handle_item_update(bulk_invoice_name, item_idx, updated_data):
    """Handle item update with supplier change detection"""
    if isinstance(updated_data, str):
        import json
        updated_data = json.loads(updated_data)
    
    try:
        bulk_invoice = frappe.get_doc("Bulk Invoice Form", bulk_invoice_name)
        item = bulk_invoice.items[int(item_idx)]
        
        if not item.reference_invoice_form:
            return {"success": False, "message": "No reference invoice form found"}
        
        # Store old supplier to detect changes
        old_supplier = None
        if item.reference_invoice_form:
            try:
                old_invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
                old_supplier = old_invoice_form.supplier
            except:
                pass
        
        # Update the bulk invoice item first
        item.item_code = updated_data.get("item_code", item.item_code)
        item.item_name = updated_data.get("item_name", item.item_name)
        item.qty = updated_data.get("qty", item.qty)
        item.price = updated_data.get("price", item.price)
        item.total = (item.qty or 0) * (item.price or 0)  # Recalculate total
        item.supplier = updated_data.get("supplier", item.supplier)
        item.customer = updated_data.get("customer", item.customer)
        item.pamper = updated_data.get("pamper", item.pamper)
        
        supplier_changed = False
        
        # Check if supplier changed
        if old_supplier and old_supplier != item.supplier:
            supplier_changed = True
            # Handle supplier change - move item to different invoice form
            result = move_item_to_different_supplier(bulk_invoice, item, old_supplier)
            if not result.get("success", True):
                return result
        else:
            # Update existing item in the same invoice form
            result = update_item_in_same_invoice(bulk_invoice, item)
            if not result.get("success", True):
                return result
        
        # Save the bulk invoice after updating
        bulk_invoice.save()
        
        message = "Item updated successfully"
        if supplier_changed:
            message += f" and moved to Invoice Form {item.reference_invoice_form}"
        
        return {
            "success": True, 
            "message": message,
            "supplier_changed": supplier_changed
        }
        
    except Exception as e:
        frappe.log_error(message=f"Error updating item: {str(e)}", title="Bulk Invoice Item Update")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def find_dangling_invoice_links(dry_run=1, action="clear_link"):
    """Report -- and optionally repair -- rows pointing at a deleted Invoice Form.

    These are the leftovers of the old two-step delete: the Invoice Form was
    removed but the row was not, so the Link is dangling and the whole document
    fails to save with "Could not find Row #N: Reference Invoice Form: ...".

    Defaults to a dry run because the repair is a data decision:

    * ``clear_link``  keeps the row and only blanks the dead reference, so the
      document saves again and the row counts as not yet generated -- the next
      "Create Invoices" will bill it.
    * ``remove_row``  drops the row entirely, i.e. treats the original deletion
      as having been intended.
    """
    rows = frappe.db.sql(
        """
        select b.parent, b.name as row_name, b.idx, b.reference_invoice_form,
               b.supplier, b.customer, b.item_code, b.qty, b.price
        from `tabBulk Invoice Form Item` b
        left join `tabInvoice Form` f on f.name = b.reference_invoice_form
        where b.reference_invoice_form is not null
          and b.reference_invoice_form != ''
          and f.name is null
        order by b.parent, b.idx
        """,
        as_dict=True,
    )

    if cint(dry_run) or not rows:
        return {"dry_run": True, "count": len(rows), "rows": rows}

    if action not in ("clear_link", "remove_row"):
        frappe.throw(_("Unknown action: {0}").format(action))

    repaired = []
    for parent in sorted({row.parent for row in rows}):
        doc = frappe.get_doc("Bulk Invoice Form", parent)
        targets = {row.row_name for row in rows if row.parent == parent}

        for child in list(doc.items):
            if child.name not in targets:
                continue
            if action == "clear_link":
                child.reference_invoice_form = None
                child.reference_invoice_form_item = None
            else:
                doc.remove(child)
            repaired.append({"parent": parent, "row": child.name, "action": action})

        doc.save(ignore_permissions=True)

    frappe.db.commit()
    return {"dry_run": False, "count": len(repaired), "rows": repaired}


@frappe.whitelist()
def handle_item_action(bulk_invoice_name, action_type, item_name=None, item_idx=None):
    """Handle actions on bulk invoice items.

    `item_name` is the child row's docname and is what callers should send.
    `item_idx` is only kept so an older cached script keeps working: a position
    is not a stable address for a row. The browser's list carries unsaved edits
    while the server re-reads the saved document, so after any local removal the
    two disagree and index N on one side is a different row on the other --
    which meant deleting somebody else's Invoice Form.
    """
    bulk_invoice = frappe.get_doc("Bulk Invoice Form", bulk_invoice_name)
    item = _find_item(bulk_invoice, item_name, item_idx)

    if action_type == "delete":
        return delete_item(bulk_invoice, item)
    elif action_type == "edit":
        if item is None:
            return {"success": False, "message": _("Row no longer exists")}
        return edit_item(bulk_invoice, item)

    return {"success": False, "message": "Invalid action"}


def _find_item(bulk_invoice, item_name=None, item_idx=None):
    """Locate a row by docname, falling back to position for legacy callers."""
    if item_name:
        for row in bulk_invoice.items:
            if row.name == item_name:
                return row
        # Already gone -- deletion treats this as success (see delete_item).
        return None

    if item_idx is not None:
        index = cint(item_idx)
        if 0 <= index < len(bulk_invoice.items):
            return bulk_invoice.items[index]

    return None


def delete_item(bulk_invoice, item):
    """Remove a row and its Invoice Form counterpart, in one transaction.

    Both halves now happen in this single request. Previously only the Invoice
    Form was touched here and the row itself was removed by the browser in a
    second, separate save -- so a lost response, a failed save or a closed tab
    left the row behind pointing at a document that no longer existed. That
    dangling Link then made the whole Bulk Invoice Form unsavable
    ("Could not find Row #N: Reference Invoice Form: ...").

    Exceptions are deliberately not swallowed: letting them out rolls the whole
    request back, so the row and the invoice can never disagree again.
    """
    if item is None:
        # Nothing to do -- most likely a double click on a slow connection.
        return {"success": True, "message": _("Row was already removed")}

    reference = item.reference_invoice_form
    reference_item = item.reference_invoice_form_item
    row_name = item.name
    message = _("Item deleted successfully")

    # Drop the row first so the Link stops pointing at the invoice, then handle
    # the invoice itself. Same transaction, so a failure below undoes this too.
    bulk_invoice.remove(item)
    bulk_invoice.save()

    if reference:
        if not frappe.db.exists("Invoice Form", reference):
            # Idempotent: the invoice is already gone, the row is what mattered.
            return {"success": True, "message": _("Removed row; Invoice Form {0} no longer existed").format(reference)}

        invoice_form = frappe.get_doc("Invoice Form", reference)

        remaining = [
            inv_item
            for inv_item in invoice_form.items
            if not (
                inv_item.name == reference_item
                or inv_item.bulk_invoice_item_reference == row_name
            )
        ]

        if not remaining:
            invoice_form.delete(force=1)
            message = _("Deleted Invoice Form {0} as it had only one item").format(reference)
        else:
            invoice_form.items = remaining
            for position, inv_item in enumerate(invoice_form.items, start=1):
                inv_item.idx = position
            invoice_form.run_method("calculate_totals")
            invoice_form.save()
            message = _("Removed item from Invoice Form {0}").format(reference)

    return {"success": True, "message": message}


def edit_item(bulk_invoice, item):
    """Handle item editing logic"""
    try:
        if not item.reference_invoice_form:
            return {"success": False, "message": "No reference invoice form found"}
        
        # This function should be called after the item is already updated
        # We need to sync the changes to the invoice form
        return update_item_in_same_invoice(bulk_invoice, item)
            
    except Exception as e:
        frappe.log_error(message=f"Error editing item: {str(e)}", title="Edit Item Error")
        return {"success": False, "message": str(e)}


def move_item_to_different_supplier(bulk_invoice, item, old_supplier):
    """Move item to different supplier's invoice form"""
    try:
        # Remove from old invoice form
        old_invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
        old_invoice_form.items = [i for i in old_invoice_form.items if i.name != item.reference_invoice_form_item]
        
        if len(old_invoice_form.items) == 0:
            # Delete old invoice form if no items left
            old_invoice_form.delete(force=1)
        else:
            old_invoice_form.save()
        
        # Find or create new invoice form for new supplier
        new_invoice_form = find_or_create_invoice_form(bulk_invoice, item.supplier)
        
        # Add item to new invoice form
        new_item = new_invoice_form.append("items", {
            "item_code": item.item_code,
            "item_name": item.item_name,
            "qty": item.qty,
            "price": item.price,
            "total": item.total,
            "customer": item.customer,
            "pamper": item.pamper,
            "bulk_invoice_reference": bulk_invoice.name,
            "bulk_invoice_item_reference": item.name
        })
        
        new_invoice_form.save()
        
        # Update references in bulk invoice
        item.reference_invoice_form = new_invoice_form.name
        item.reference_invoice_form_item = new_item.name
        
        return {"success": True, "message": f"Item moved to Invoice Form {new_invoice_form.name}"}
        
    except Exception as e:
        frappe.log_error(message=f"Error moving item: {str(e)}", title="Move Item Error")
        return {"success": False, "message": str(e)}


def update_item_in_same_invoice(bulk_invoice, item):
    """Update item in the same invoice form"""
    try:
        invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
        
        # Find and update the item using bulk_invoice_item_reference
        item_found = False
        for inv_item in invoice_form.items:
            # Match using bulk_invoice_item_reference which should be the bulk item's name
            if inv_item.bulk_invoice_item_reference == item.name:
                inv_item.item_code = item.item_code
                inv_item.item_name = item.item_name
                inv_item.qty = item.qty
                inv_item.price = item.price
                inv_item.total = item.total
                inv_item.customer = item.customer
                inv_item.pamper = item.pamper
                item_found = True
                break
        
        if not item_found:
            # Fallback: try matching by reference_invoice_form_item
            for inv_item in invoice_form.items:
                if inv_item.name == item.reference_invoice_form_item:
                    inv_item.item_code = item.item_code
                    inv_item.item_name = item.item_name
                    inv_item.qty = item.qty
                    inv_item.price = item.price
                    inv_item.total = item.total
                    inv_item.customer = item.customer
                    inv_item.pamper = item.pamper
                    item_found = True
                    break
        
        if not item_found:
            frappe.log_error(message=f"Could not find item to update in invoice form {item.reference_invoice_form}", title="Update Item Error")
            return {"success": False, "message": "Could not find item to update in invoice form"}
        
        # Calculate totals in invoice form
        invoice_form.run_method("calculate_totals")
        
        # Save the invoice form
        invoice_form.save()
        
        return {"success": True, "message": f"Item updated in Invoice Form {item.reference_invoice_form}"}
        
    except Exception as e:
        frappe.log_error(message=f"Error updating item in invoice form: {str(e)}", title="Update Item Error")
        return {"success": False, "message": str(e)}


def background_job_id(bulk_invoice_name):
    """One job per bulk invoice, so a second click cannot queue a second run."""
    return f"create_invoice_forms::{bulk_invoice_name}"


def create_invoice_forms_in_background(bulk_invoice, user=None):
    """Background entry point for build_invoice_forms(), reporting over realtime."""
    user = user or frappe.session.user

    try:
        result = build_invoice_forms(bulk_invoice, user=user)
        frappe.db.commit()
        payload = dict(result, bulk_invoice=bulk_invoice, status="completed",
                       message=format_result_message(result))
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Bulk Invoice Form: create invoice forms",
            message=f"{bulk_invoice}\n\n{frappe.get_traceback()}",
        )
        payload = {"bulk_invoice": bulk_invoice, "status": "failed", "message": str(e)}

    frappe.publish_realtime(PROGRESS_EVENT, payload, user=user)


def build_invoice_forms(bulk_invoice_name, user=None):
    """Create/extend the Invoice Forms of a bulk invoice, one form per supplier.

    Runs as a single transaction and is idempotent: an item row is only appended
    to an Invoice Form when no row carrying its `bulk_invoice_item_reference`
    exists yet. That is what stops a retry after a timeout from producing a
    second copy of every row. Rows generated by an interrupted run but never
    linked back are repaired instead of duplicated.
    """
    # Serialise concurrent runs (double click, retry while the first is still
    # working). The lock is held until this transaction ends.
    if not frappe.db.get_value("Bulk Invoice Form", bulk_invoice_name, "name", for_update=True):
        frappe.throw(_("Bulk Invoice Form {0} not found").format(bulk_invoice_name))

    doc = frappe.get_doc("Bulk Invoice Form", bulk_invoice_name)

    if doc.docstatus != 0:
        frappe.throw(_("Invoice Forms can only be created from a draft Bulk Invoice Form"))

    live_forms, forms_by_supplier, linked_rows = get_generated_invoice_forms(doc.name)

    created = []
    updated = []
    repaired = 0
    skipped = 0
    pending_by_supplier = defaultdict(list)

    for item in doc.items:
        existing_row = linked_rows.get(item.name)

        if existing_row:
            # The row is already in an Invoice Form. If the link back into the
            # bulk invoice was lost (interrupted run), restore it - do not create
            # the row a second time.
            if (item.reference_invoice_form, item.reference_invoice_form_item) != existing_row:
                set_item_reference(item, existing_row[0], existing_row[1])
                repaired += 1
            continue

        if item.reference_invoice_form and item.reference_invoice_form in live_forms:
            # Linked to a form that is still around but has no matching row, e.g.
            # the row was removed by hand. Leave it alone, as before.
            continue

        if not item.supplier:
            skipped += 1
            continue

        pending_by_supplier[item.supplier].append(item)

    total_suppliers = len(pending_by_supplier)

    for index, (supplier, items) in enumerate(pending_by_supplier.items(), start=1):
        form_name = forms_by_supplier.get(supplier)

        if form_name:
            invoice_form = frappe.get_doc("Invoice Form", form_name)
            updated.append(invoice_form.name)
        else:
            invoice_form = create_new_invoice_form(doc, supplier)
            forms_by_supplier[supplier] = invoice_form.name
            created.append(invoice_form.name)

        for item in items:
            invoice_form.append("items", {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "qty": item.qty,
                "price": item.price,
                "total": item.total,
                "customer": item.customer,
                "pamper": item.pamper,
                "bulk_invoice_reference": doc.name,
                "bulk_invoice_item_reference": item.name,
            })

        invoice_form.save()

        # Child row names only exist after the save, which is why the reference
        # is written here and not while appending.
        row_names = {
            row.bulk_invoice_item_reference: row.name
            for row in invoice_form.items
            if row.bulk_invoice_item_reference
        }
        for item in items:
            set_item_reference(item, invoice_form.name, row_names.get(item.name))

        publish_progress(doc.name, index, total_suppliers, user)

    return {
        "created": created,
        "updated": updated,
        "rows_added": sum(len(items) for items in pending_by_supplier.values()),
        "rows_repaired": repaired,
        "rows_without_supplier": skipped,
    }


def get_generated_invoice_forms(bulk_invoice_name):
    """Read every Invoice Form generated from this bulk invoice in two queries.

    Returns (live form names, {supplier: draft form name}, {bulk item name:
    (form name, form item name)}).
    """
    forms = frappe.get_all(
        "Invoice Form",
        filters={"bulk_invoice_reference": bulk_invoice_name, "docstatus": ["<", 2]},
        fields=["name", "supplier", "docstatus"],
        order_by="creation asc",
    )

    live_forms = {form.name for form in forms}
    forms_by_supplier = {}
    for form in forms:
        # Only a draft can take more rows.
        if form.docstatus == 0 and form.supplier not in forms_by_supplier:
            forms_by_supplier[form.supplier] = form.name

    linked_rows = {}
    if live_forms:
        rows = frappe.get_all(
            "Invoice Form Item",
            filters={
                "parenttype": "Invoice Form",
                "parent": ["in", list(live_forms)],
                "bulk_invoice_item_reference": ["is", "set"],
            },
            fields=["name", "parent", "bulk_invoice_item_reference"],
        )
        for row in rows:
            linked_rows.setdefault(row.bulk_invoice_item_reference, (row.parent, row.name))

    return live_forms, forms_by_supplier, linked_rows


def set_item_reference(item, form_name, form_item_name):
    """Point a bulk invoice row at its generated Invoice Form row.

    Written straight to the row rather than through a full save of the parent:
    the parent carries hundreds of rows and re-saving it on every supplier is
    both slow and a source of timestamp conflicts with the open form.
    """
    item.reference_invoice_form = form_name
    item.reference_invoice_form_item = form_item_name

    frappe.db.set_value(
        "Bulk Invoice Form Item",
        item.name,
        {
            "reference_invoice_form": form_name,
            "reference_invoice_form_item": form_item_name,
        },
        update_modified=False,
    )


def publish_progress(bulk_invoice_name, done, total, user=None):
    if not user:
        return

    frappe.publish_realtime(
        PROGRESS_EVENT,
        {
            "bulk_invoice": bulk_invoice_name,
            "status": "in_progress",
            "done": done,
            "total": total,
        },
        user=user,
    )


def format_result_message(result):
    messages = []

    if result.get("created"):
        messages.append(_("Created {0} new Invoice Forms").format(len(result["created"])))
    if result.get("updated"):
        messages.append(_("Updated {0} existing Invoice Forms").format(len(result["updated"])))
    if result.get("rows_added"):
        messages.append(_("Added {0} item rows").format(result["rows_added"]))
    if result.get("rows_repaired"):
        messages.append(_("Re-linked {0} item rows that were already generated").format(
            result["rows_repaired"]
        ))
    if result.get("rows_without_supplier"):
        messages.append(_("Skipped {0} item rows with no supplier").format(
            result["rows_without_supplier"]
        ))

    if not messages:
        return _("All items already have Invoice Form references")

    return "<br>".join(messages)


def find_or_create_invoice_form(bulk_invoice, supplier):
    """Find the draft invoice form of this supplier from the SAME bulk invoice, or create one"""
    if bulk_invoice.name:
        existing = frappe.get_all(
            "Invoice Form",
            filters={
                "supplier": supplier,
                "bulk_invoice_reference": bulk_invoice.name,  # Only from same bulk invoice
                "docstatus": 0,  # Only draft invoices
                "company": bulk_invoice.company,
            },
            fields=["name"],
            limit=1,
        )

        if existing:
            return frappe.get_doc("Invoice Form", existing[0].name)

    return create_new_invoice_form(bulk_invoice, supplier)


def create_new_invoice_form(bulk_invoice, supplier):
    """Create a new invoice form named after the bulk invoice"""
    invoice_form = frappe.new_doc("Invoice Form")
    invoice_form.update({
        "company": bulk_invoice.company,
        "posting_date": bulk_invoice.posting_date,
        "posting_time": bulk_invoice.posting_time,
        "supplier": supplier,
        "bulk_invoice_reference": bulk_invoice.name,  # Set the bulk invoice reference (might be None for new docs)
    })

    # The name is set on insert. It used to be inserted under the naming series
    # and renamed afterwards, but rename_doc renames the record before the rest
    # of its work, so a failure anywhere in it left this object holding a name
    # that no longer existed and the next save() died with DoesNotExistError.
    # Renaming is also expensive: it rewrites every link field and clears the
    # whole cache, once per supplier.
    invoice_form.insert(
        ignore_permissions=True,
        ignore_mandatory=True,
        set_name=get_new_invoice_form_name(bulk_invoice, supplier),
    )

    return invoice_form


def get_new_invoice_form_name(bulk_invoice, supplier):
    """`<bulk invoice>-<supplier order in the items table>`, e.g. BULK-0001-003.

    Returns None for an unsaved bulk invoice so the naming series takes over.
    The counter moves on when the name is taken, so an insert never collides
    with a form left behind by an earlier run.
    """
    if not bulk_invoice.name:
        return None

    suppliers_seen = []
    for item in bulk_invoice.items:
        if item.supplier and item.supplier not in suppliers_seen:
            suppliers_seen.append(item.supplier)

    if supplier in suppliers_seen:
        supplier_order = suppliers_seen.index(supplier) + 1
    else:
        supplier_order = len(suppliers_seen) + 1

    for order in range(supplier_order, supplier_order + 100):
        name = f"{bulk_invoice.name}-{order:03d}"
        if not frappe.db.exists("Invoice Form", name):
            return name

    return None

@frappe.whitelist()
def create_bulk_invoice_from_items(items_data, company, posting_date=None):
    """Create a bulk invoice form from item data"""
    if isinstance(items_data, str):
        import json
        items_data = json.loads(items_data)
    
    bulk_invoice = frappe.new_doc("Bulk Invoice Form")
    bulk_invoice.update({
        "company": company,
        "posting_date": posting_date or frappe.utils.today(),
        "posting_time": frappe.utils.nowtime()
    })
    
    for item_data in items_data:
        bulk_invoice.append("items", item_data)
    
    bulk_invoice.insert(ignore_permissions=True)
    return bulk_invoice.name

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_filtered_customers(doctype, txt, searchfield, start, page_len, filters):
    """
    Returns filtered customers based on the following criteria:
    1. Must be a customer (is_customer = 1)
    2. Must not be frozen (is_frozen = 0)
    3. If couple_customer is checked, they must have a related supplier
    4. Must not be the party behind `filters.exclude_supplier` -- see
       is_own_customer(). The row's supplier is passed in from the grid so a
       farmer can never be picked as the buyer of their own produce.
    """
    exclude = (filters or {}).get("exclude_supplier") or ""

    # Base query for customers
    query = """
        SELECT name, customer_name
        FROM `tabCustomer`
        WHERE is_customer = 1
        AND is_frozen = 0
        AND (name LIKE %(token)s OR customer_name LIKE %(token)s)
        AND (
            couple_customer = 0
            OR EXISTS (
                SELECT 1 FROM `tabSupplier`
                WHERE related_customer = `tabCustomer`.name
            )
        )
        AND (
            %(exclude)s = ''
            OR (
                name != %(exclude)s
                AND NOT EXISTS (
                    SELECT 1 FROM `tabSupplier`
                    WHERE name = %(exclude)s
                    AND related_customer = `tabCustomer`.name
                )
            )
        )
        ORDER BY name ASC
        LIMIT %(limit)s
    """

    # One token narrows the SQL; the rest of the words are checked in Python,
    # so "سالم الموسى" finds "سالم صالح محمد الموسى". Ranking is shared with the
    # other party pickers so every form orders results identically.
    from agricultural_marketing.queries import CANDIDATE_LIMIT, candidate_filters, rank_rows

    token = candidate_filters(txt)
    rows = frappe.db.sql(query, {
        "token": "%%%s%%" % token,
        "exclude": exclude,
        "limit": CANDIDATE_LIMIT,
    })

    return rank_rows([(row[0], row[1]) for row in rows], txt, start, page_len)


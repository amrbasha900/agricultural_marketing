# Copyright (c) 2024, Muhammad Salama and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from collections import defaultdict


class BulkInvoiceForm(Document):
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
                
                # Update references in bulk invoice item
                item.reference_invoice_form = invoice_form.name
                item.reference_invoice_form_item = invoice_item.name
                
                # Save the invoice form
                invoice_form.save()
                
                frappe.log_error(message=f"Added new item {item.name} to Invoice Form {invoice_form.name}", title="Auto Invoice Creation")
                
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
        """Create Invoice Forms grouped by supplier"""
        if not self.items:
            frappe.throw(_("No items to create invoices from"))
        
        # Group items by supplier, but only for items without existing references
        supplier_items = defaultdict(list)
        existing_invoice_forms = {}
        
        for item in self.items:
            if not item.reference_invoice_form:
                # Item doesn't have a reference, needs to be added to an invoice form
                supplier_items[item.supplier].append(item)
            else:
                # Item already has a reference, track the existing invoice form
                if item.supplier not in existing_invoice_forms:
                    try:
                        existing_invoice_forms[item.supplier] = frappe.get_doc("Invoice Form", item.reference_invoice_form)
                    except:
                        # Invoice form might have been deleted, treat as new item
                        supplier_items[item.supplier].append(item)
        
        created_invoices = []
        updated_invoices = []
        
        for supplier, items in supplier_items.items():
            try:
                # Check if there's already an invoice form for this supplier
                if supplier in existing_invoice_forms:
                    # Use existing invoice form
                    invoice_form = existing_invoice_forms[supplier]
                else:
                    # Find or create invoice form for this supplier
                    invoice_form = find_or_create_invoice_form(self, supplier)
                    if invoice_form.name not in created_invoices:
                        created_invoices.append(invoice_form.name)
                
                # Add items to invoice form
                for item in items:
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
                    
                    # Update references in bulk invoice items
                    item.reference_invoice_form = invoice_form.name
                    item.reference_invoice_form_item = invoice_item.name
                
                # Save the invoice form
                invoice_form.save()
                
                if invoice_form.name not in created_invoices:
                    updated_invoices.append(invoice_form.name)
                
            except Exception as e:
                frappe.log_error(message=f"Error creating/updating invoice for supplier {supplier}: {str(e)}", title="Invoice Creation Error")
                frappe.throw(_("Error creating invoice for supplier {0}: {1}").format(supplier, str(e)))
        
        # Save the bulk invoice form with updated references
        self.save()
        
        # Build result message
        messages = []
        if created_invoices:
            messages.append(_("Created {0} new Invoice Forms: {1}").format(
                len(created_invoices), ", ".join(created_invoices)
            ))
        if updated_invoices:
            messages.append(_("Updated {0} existing Invoice Forms: {1}").format(
                len(updated_invoices), ", ".join(updated_invoices)
            ))
        
        if messages:
            frappe.msgprint("<br>".join(messages))
        else:
            frappe.msgprint(_("All items already have Invoice Form references"))
        
        return created_invoices + updated_invoices

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
def handle_item_action(bulk_invoice_name, item_idx, action_type):
    """Handle actions on bulk invoice items"""
    bulk_invoice = frappe.get_doc("Bulk Invoice Form", bulk_invoice_name)
    item = bulk_invoice.items[int(item_idx)]
    
    if action_type == "delete":
        return delete_item(bulk_invoice, item)
    elif action_type == "edit":
        return edit_item(bulk_invoice, item)
    
    return {"success": False, "message": "Invalid action"}


def delete_item(bulk_invoice, item):
    """Delete item from bulk invoice and handle references"""
    try:
        message = "Item deleted successfully"
        
        if item.reference_invoice_form:
            invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
            original_count = len(invoice_form.items)
            
            # Find and remove the item using multiple matching criteria
            items_to_remove = []
            for inv_item in invoice_form.items:
                # Try multiple ways to match the item
                if (inv_item.name == item.reference_invoice_form_item or 
                    inv_item.bulk_invoice_item_reference == item.name):
                    items_to_remove.append(inv_item)
            
            # Remove found items
            for inv_item in items_to_remove:
                invoice_form.items.remove(inv_item)
            
            if len(invoice_form.items) == 0:
                # If no items left, delete the entire invoice form
                invoice_form.delete(force=1)
                message = f"Deleted Invoice Form {item.reference_invoice_form} as it had only one item"
            else:
                # Recalculate totals and save
                invoice_form.run_method("calculate_totals")
                invoice_form.save()
                message = f"Removed item from Invoice Form {item.reference_invoice_form}"
                
            frappe.log_error(message=f"Deleted {len(items_to_remove)} items from invoice form. Original count: {original_count}, New count: {len(invoice_form.items)}", title="Delete Item Debug")
        
        return {"success": True, "message": message}
        
    except Exception as e:
        frappe.log_error(message=f"Error deleting item: {str(e)}", title="Delete Item Error")
        return {"success": False, "message": str(e)}


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


def find_or_create_invoice_form(bulk_invoice, supplier):
    """Find existing invoice form for supplier from SAME bulk invoice or create new one with specific naming"""
    
    # Only search if bulk_invoice has a name (has been saved)
    if not bulk_invoice.name:
        return create_new_invoice_form(bulk_invoice, supplier)
    
    # Check if there's already an invoice form for this supplier from THIS specific bulk invoice
    existing_invoice_form = None
    
    # Method 1: Look through items in the current bulk invoice to find existing references
    for item in bulk_invoice.items:
        if item.supplier == supplier and item.reference_invoice_form:
            try:
                # Verify the invoice form still exists and has the correct supplier and bulk reference
                invoice_form = frappe.get_doc("Invoice Form", item.reference_invoice_form)
                if (invoice_form.supplier == supplier and 
                    hasattr(invoice_form, 'bulk_invoice_reference') and 
                    invoice_form.bulk_invoice_reference == bulk_invoice.name):
                    existing_invoice_form = invoice_form
                    break
            except:
                # Invoice form might have been deleted, continue searching
                continue
    
    if existing_invoice_form:
        frappe.log_error(message=f"Found existing invoice form {existing_invoice_form.name} for supplier {supplier} from bulk invoice {bulk_invoice.name}", title="Find Invoice Form")
        return existing_invoice_form
    
    # Method 2: Search for existing invoice forms with same supplier AND same bulk_invoice_reference
    try:
        existing_invoice_forms = frappe.get_all("Invoice Form", 
            filters={
                "supplier": supplier,
                "bulk_invoice_reference": bulk_invoice.name,  # Only from same bulk invoice
                "docstatus": 0,  # Only draft invoices
                "company": bulk_invoice.company
            },
            fields=["name"],
            limit=1
        )
        
        if existing_invoice_forms:
            existing_form = frappe.get_doc("Invoice Form", existing_invoice_forms[0].name)
            frappe.log_error(message=f"Found existing invoice form {existing_form.name} for supplier {supplier} from bulk invoice {bulk_invoice.name}", title="Find Invoice Form")
            return existing_form
            
    except Exception as e:
        frappe.log_error(message=f"Error searching for existing invoice forms: {str(e)}", title="Find Invoice Form Error")
    
    # Create a new invoice form
    return create_new_invoice_form(bulk_invoice, supplier)


def create_new_invoice_form(bulk_invoice, supplier):
    """Create a new invoice form with specific naming"""
    frappe.log_error(message=f"Creating new invoice form for supplier {supplier} from bulk invoice {bulk_invoice.name or 'NEW'}", title="Create Invoice Form")
    
    # Get supplier order in the items table
    suppliers_seen = []
    supplier_order = 1
    
    for item in bulk_invoice.items:
        if item.supplier and item.supplier not in suppliers_seen:
            suppliers_seen.append(item.supplier)
            if item.supplier == supplier:
                supplier_order = len(suppliers_seen)
                break
    
    # Create the invoice form
    invoice_form = frappe.new_doc("Invoice Form")
    invoice_form.update({
        "company": bulk_invoice.company,
        "posting_date": bulk_invoice.posting_date,
        "posting_time": bulk_invoice.posting_time,
        "supplier": supplier,
        "bulk_invoice_reference": bulk_invoice.name,  # Set the bulk invoice reference (might be None for new docs)
    })
    
    # Insert first to get a system-generated name
    invoice_form.insert(ignore_permissions=True, ignore_mandatory=True)
    
    # If bulk invoice has a name, rename the invoice form to desired pattern
    if bulk_invoice.name:
        new_name = f"{bulk_invoice.name}-{supplier_order:03d}"
        
        try:
            frappe.rename_doc("Invoice Form", invoice_form.name, new_name, ignore_if_exists=False, force=True)
            invoice_form.name = new_name  # Update the object reference
            frappe.db.commit()
            frappe.log_error(message=f"Successfully created and renamed invoice form to {new_name} for bulk invoice {bulk_invoice.name}", title="Invoice Form Creation")
        except Exception as e:
            frappe.log_error(message=f"Error renaming invoice form: {str(e)}", title="Invoice Form Naming Error")
            # If renaming fails, continue with the system-generated name
    
    return invoice_form

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
    """
    # Base query for customers
    query = """
        SELECT name, customer_name 
        FROM `tabCustomer` 
        WHERE is_customer = 1 
        AND is_frozen = 0
        AND (name LIKE %(txt)s OR customer_name LIKE %(txt)s)
        AND (
            couple_customer = 0 
            OR EXISTS (
                SELECT 1 FROM `tabSupplier` 
                WHERE related_customer = `tabCustomer`.name
            )
        )
        ORDER BY name ASC
        LIMIT %(start)s, %(page_len)s
    """
    
    return frappe.db.sql(query, {
        "txt": "%%%s%%" % txt,
        "start": start,
        "page_len": page_len
    })


import frappe
from frappe import _

def update_statement_history_whatsapp_status(doc, method):
    """
    Hook to update WhatsApp status in Statement Generation History when WhatsApp Messages status changes
    This should be called on_update of WhatsApp Messages DocType
    """
    if not doc.reference_document or doc.reference_document != "PDF Generator Log":
        return
        
    try:
        # Get the PDF Generator Log
        pdf_log = frappe.get_doc("PDF Generator Log", doc.document_name)
        
        # Check if this PDF log is linked to a Statement Generation History
        if not hasattr(pdf_log, 'statement_generation_history') or not pdf_log.statement_generation_history:
            return
            
        history_id = pdf_log.statement_generation_history
        
        # Update the corresponding child table item using direct DB update to avoid parent conflicts
        item_name = frappe.db.get_value(
            "Statement Generation History Item",
            {"parent": history_id, "pdf_generator_log": doc.document_name},
            "name",
        )
        if item_name:
            frappe.db.set_value(
                "Statement Generation History Item",
                item_name,
                {"whatsapp_status": doc.status, "whatsapp_message_id": doc.name},
            )
        
        # Update summary counts directly in parent
        whatsapp_sent_count = frappe.db.sql(
            """
            select count(1) as cnt
            from `tabStatement Generation History Item`
            where parent=%s and ifnull(whatsapp_status, 'Not Created') <> 'Not Created'
            """,
            (history_id,),
            as_dict=True,
        )[0].cnt
        
        frappe.db.set_value(
            "Statement Generation History",
            history_id,
            {"whatsapp_sent_count": whatsapp_sent_count},
        )
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(message=f"Error updating statement history WhatsApp status: {str(e)}", title="WhatsApp Status Hook")

def validate_statement_generation_history(doc, method):
    """
    Validate Statement Generation History before save
    """
    # Ensure required fields are set
    if not doc.created_by_user:
        doc.created_by_user = frappe.session.user
    
    if not doc.generation_time:
        doc.generation_time = frappe.utils.now()

def update_pdf_generator_log_on_whatsapp_creation(doc, method):
    """
    Update PDF Generator Log when WhatsApp message is created
    This should be called on_insert of WhatsApp Messages DocType
    """
    if not doc.reference_document or doc.reference_document != "PDF Generator Log":
        return
        
    try:
        # Update the PDF Generator Log
        frappe.db.set_value("PDF Generator Log", doc.document_name, {
            "whatsapp_sent": 1,
            "whatsapp_message_id": doc.name
        })
        
        # Update Statement Generation History if linked
        pdf_log = frappe.get_doc("PDF Generator Log", doc.document_name)
        if hasattr(pdf_log, 'statement_generation_history') and pdf_log.statement_generation_history:
            history_id = pdf_log.statement_generation_history
            item_name = frappe.db.get_value(
                "Statement Generation History Item",
                {"parent": history_id, "pdf_generator_log": doc.document_name},
                "name",
            )
            if item_name:
                frappe.db.set_value(
                    "Statement Generation History Item",
                    item_name,
                    {"whatsapp_status": "Queued", "whatsapp_message_id": doc.name},
                )
            
            # Update summary count directly
            whatsapp_sent_count = frappe.db.sql(
                """
                select count(1) as cnt
                from `tabStatement Generation History Item`
                where parent=%s and ifnull(whatsapp_status, 'Not Created') <> 'Not Created'
                """,
                (history_id,),
                as_dict=True,
            )[0].cnt
            
            frappe.db.set_value(
                "Statement Generation History",
                history_id,
                {"whatsapp_sent_count": whatsapp_sent_count},
            )
        
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(message=f"Error updating PDF log on WhatsApp creation: {str(e)}", title="WhatsApp Creation Hook")

@frappe.whitelist()
def sync_whatsapp_statuses():
    """
    Background job to sync WhatsApp statuses for all Statement Generation History records
    Can be called manually or set up as a scheduled job
    """
    try:
        # Get all Statement Generation History records from last 30 days
        histories = frappe.get_all(
            "Statement Generation History",
            filters={
                "generation_time": [">=", frappe.utils.add_days(frappe.utils.today(), -30)]
            },
            fields=["name"]
        )
        
        updated_count = 0
        
        for history in histories:
            history_id = history.name
            history_updated = False
            
            # Fetch all child items for this history
            items = frappe.get_all(
                "Statement Generation History Item",
                filters={"parent": history_id},
                fields=["name", "whatsapp_message_id", "whatsapp_status"],
            )
            
            for item in items:
                if item.whatsapp_message_id:
                    current_status = frappe.db.get_value("WhatsApp Messages", item.whatsapp_message_id, "status")
                    if current_status and current_status != item.whatsapp_status:
                        frappe.db.set_value(
                            "Statement Generation History Item",
                            item.name,
                            {"whatsapp_status": current_status},
                        )
                        history_updated = True
            
            if history_updated:
                # Update summary counts directly
                whatsapp_sent_count = frappe.db.sql(
                    """
                    select count(1) as cnt
                    from `tabStatement Generation History Item`
                    where parent=%s and ifnull(whatsapp_status, 'Not Created') <> 'Not Created'
                    """,
                    (history_id,),
                    as_dict=True,
                )[0].cnt
                
                frappe.db.set_value(
                    "Statement Generation History",
                    history_id,
                    {"whatsapp_sent_count": whatsapp_sent_count},
                )
                updated_count += 1
        
        frappe.db.commit()
        return {"success": f"Updated {updated_count} Statement Generation History records"}
        
    except Exception as e:
        frappe.log_error(message=f"Error syncing WhatsApp statuses: {str(e)}", title="WhatsApp Status Sync")
        return {"error": str(e)}

def cleanup_old_pdf_logs():
    """
    Cleanup old PDF Generator Logs and their files (older than 90 days)
    Can be set up as a scheduled job
    """
    try:
        cutoff_date = frappe.utils.add_days(frappe.utils.today(), -90)
        
        # Get old PDF logs
        old_logs = frappe.get_all(
            "PDF Generator Log",
            filters={
                "creation": ["<", cutoff_date],
                "status": ["in", ["Completed", "Failed"]]
            },
            fields=["name", "pdf_file"]
        )
        
        deleted_count = 0
        for log in old_logs:
            try:
                # Delete the PDF file if it exists
                if log.pdf_file:
                    try:
                        file_doc = frappe.get_doc("File", {"file_url": log.pdf_file})
                        file_doc.delete(ignore_permissions=True)
                    except:
                        pass  # File might already be deleted
                
                # Delete the PDF Generator Log
                frappe.delete_doc("PDF Generator Log", log.name, ignore_permissions=True)
                deleted_count += 1
                
            except Exception as e:
                frappe.log_error(message=f"Error deleting PDF log {log.name}: {str(e)}", title="PDF Log Cleanup")
        
        frappe.db.commit()
        return {"success": f"Cleaned up {deleted_count} old PDF logs"}
        
    except Exception as e:
        frappe.log_error(message=f"Error in PDF log cleanup: {str(e)}", title="PDF Log Cleanup")
        return {"error": str(e)}
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields({
        "Supplier": [
            {
                "label": _("Related Customer"),
                "fieldname": "related_customer",
                "fieldtype": "Link",
                "options": "Customer",
                "insert_after": "country"
            },
            {
                "label": _("Related Customer Group"),
                "fieldname": "related_customer_group",
                "fieldtype": "Link",
                "options": "Customer Group",
                "insert_after": "related_customer",
            },
            {
                "label": _("Commission Percentage"),
                "fieldname": "commission_percentage",
                "fieldtype": "Percent",
                "insert_after": "is_transporter"
            }
        ],
        "Customer": [
            {
                "label": _("Is Farmer"),
                "fieldname": "is_farmer",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "customer_group",
                "depends_on": "eval:doc.is_customer==0 && doc.is_pamper==0"
            },
            {
                "label": _("Is Customer"),
                "fieldname": "is_customer",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "is_farmer",
                "depends_on": "eval:doc.is_farmer!=1"
            },
            {
                "label": _("Is Pamper"),
                "fieldname": "is_pamper",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "is_customer",
                "depends_on": "eval:doc.is_farmer!=1"
            },
            {
                "label": _("Commission Percentage"),
                "fieldname": "commission_percentage",
                "fieldtype": "Percent",
                "insert_after": "account_manager",
                "depends_on": "eval:doc.is_farmer==1"
            }
        ],
        "Customer Group": [
            {
                "label": _("Commission Percentage"),
                "fieldname": "commission_percentage",
                "fieldtype": "Percent",
                "insert_after": "is_group"
            }
        ],
        "Item": [
            {
                "label": _("Commission Item"),
                "fieldname": "commission_item",
                "fieldtype": "Check",
                "default": 0,
                "allow_in_quick_entry": 1,
                "insert_after": "stock_uom"
            }
        ]
    })

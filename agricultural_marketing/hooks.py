app_name = "agricultural_marketing"
app_title = "Agricultural Marketing"
app_publisher = "Muhammad Salama"
app_description = "Tracking agriculture marketing"
app_email = "mohamedsalamaa41@gmail.com"
app_license = "mit"
# required_apps = []

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/agricultural_marketing/css/agricultural_marketing.css"
# app_include_js = "/assets/agricultural_marketing/js/agricultural_marketing.js"

# include js, css files in header of web template
# web_include_css = "/assets/agricultural_marketing/css/agricultural_marketing.css"
# web_include_js = "/assets/agricultural_marketing/js/agricultural_marketing.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "agricultural_marketing/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Payments and Receipts": "public/js/payments_and_receipts.js",
	# Appended after bulk_invoice_form.js (see DocTypeMeta.add_code), so these
	# register their own form handlers rather than being called from it.
	"Bulk Invoice Form": ["public/js/party_catalog.js", "public/js/bulk_quick_entry.js"],
}

# Ship the Agriculture Settings desk flags with the page instead of fetching
# them asynchronously after render. See agricultural_marketing/boot.py.
extend_bootinfo = "agricultural_marketing.boot.boot_session"
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "agricultural_marketing/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "agricultural_marketing.utils.jinja_methods",
# 	"filters": "agricultural_marketing.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "agricultural_marketing.install.before_install"
# after_install = "agricultural_marketing.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "agricultural_marketing.uninstall.before_uninstall"
# after_uninstall = "agricultural_marketing.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "agricultural_marketing.utils.before_app_install"
# after_app_install = "agricultural_marketing.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "agricultural_marketing.utils.before_app_uninstall"
# after_app_uninstall = "agricultural_marketing.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "agricultural_marketing.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
    "Customer": "agricultural_marketing.standard_doctypes.customer.AgricultureCustomer",
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Supplier": {
        "after_insert": "agricultural_marketing.standard_doctypes.supplier.create_related_customer",
        "validate": "agricultural_marketing.standard_doctypes.supplier.create_related_customer",
        "on_trash": "agricultural_marketing.standard_doctypes.supplier.delete_related_customer",
        "on_update": [
            "agricultural_marketing.standard_doctypes.supplier.sync_supplier_to_customer",
            "agricultural_marketing.party_catalog.clear_catalog_cache",
        ],
        "after_rename": [
            "agricultural_marketing.standard_doctypes.supplier.rename_customer_from_supplier",
            "agricultural_marketing.party_catalog.clear_catalog_cache",
        ],
        "after_delete": "agricultural_marketing.party_catalog.clear_catalog_cache",
    },
    # Keep the quick-entry catalog fresh; see agricultural_marketing/party_catalog.py.
    "Customer": {
        "on_update": "agricultural_marketing.party_catalog.clear_catalog_cache",
        "after_rename": "agricultural_marketing.party_catalog.clear_catalog_cache",
        "after_delete": "agricultural_marketing.party_catalog.clear_catalog_cache",
    },
    "Item": {
        "on_update": "agricultural_marketing.party_catalog.clear_catalog_cache",
        "after_rename": "agricultural_marketing.party_catalog.clear_catalog_cache",
        "after_delete": "agricultural_marketing.party_catalog.clear_catalog_cache",
    },
    "Sales Invoice": {
        "on_cancel": "agricultural_marketing.standard_doctypes.sales_invoice.update_invoice_form",
        "on_trash": "agricultural_marketing.standard_doctypes.sales_invoice.update_invoice_form",
    },
    "WhatsApp Messages": {
        "on_change": "agricultural_marketing.standard_doctypes.whatsapp_messages.update_invoice_form",
        "on_update": "agricultural_marketing.agricultural_marketing.page.statement_forms.whatsapp_hooks.update_statement_history_whatsapp_status",
        "after_insert": "agricultural_marketing.agricultural_marketing.page.statement_forms.whatsapp_hooks.update_pdf_generator_log_on_whatsapp_creation"
    },
    "Statement Generation History": {
        "validate": "agricultural_marketing.agricultural_marketing.page.statement_forms.whatsapp_hooks.validate_statement_generation_history"
    },
    "Payments and Receipts": {
        "validate": "agricultural_marketing.agricultural_marketing.doctype.invoice_form.supplier_charges.lock_charge_voucher",
        "on_trash": [
            "agricultural_marketing.agricultural_marketing.doctype.invoice_form.supplier_charges.guard_charge_voucher_delete",
            "agricultural_marketing.agricultural_marketing.doctype.invoice_form.supplier_charges.clear_invoice_links",
        ]
    }
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"agricultural_marketing.tasks.all"
# 	],
# 	"daily": [
# 		"agricultural_marketing.tasks.daily"
# 	],
# 	"hourly": [
# 		"agricultural_marketing.tasks.hourly"
# 	],
# 	"weekly": [
# 		"agricultural_marketing.tasks.weekly"
# 	],
# 	"monthly": [
# 		"agricultural_marketing.tasks.monthly"
# 	],
# }
scheduler_events = {
    "daily": [
        "agricultural_marketing.agricultural_marketing.page.statement_forms.whatsapp_hooks.sync_whatsapp_statuses",
        "agricultural_marketing.agricultural_marketing.page.statement_forms.whatsapp_hooks.cleanup_old_pdf_logs"
    ],
    "cron": {
        "* * * * *": [
            "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.retry_all_queued_pdf_jobs"
        ]
    }
}


# scheduler_events = {
#     "daily": [
#         "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.cleanup_old_files"
#     ],
#     "hourly": [
#         "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.monitor_failed_batches"
#     ]
# }

# Testing
# -------

# before_tests = "agricultural_marketing.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "agricultural_marketing.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "agricultural_marketing.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["agricultural_marketing.utils.before_request"]
# after_request = ["agricultural_marketing.utils.after_request"]

# Job Events
# ----------
# before_job = ["agricultural_marketing.utils.before_job"]
# after_job = ["agricultural_marketing.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"agricultural_marketing.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
fixtures = [
    {"dt": "Custom Field", "filters": [["name", "in", [
        "Customer-custom_send_invoice_via_whatsapp",
        "Supplier-custom_send_invoice_via_whatsapp",
        "Item-custom_is_agriculture_item",
        "Supplier-custom_is_farmer",
        "Item-custom_commission_percentage",
        "Item Group-custom_commission_percentage",
        "Customer-custom_couple_customer",
        "Item Group-custom_commission_item",
        "Supplier-custom_charge_percentage",
        "Supplier-custom_apply_charges",
        "Payments Receipts Reference-invoice_form",
        "Company-custom_cr_no",
        "Company-custom_statement_address",
        "Company-custom_mobile_no"
        ]]]},
        {"dt": "Statement Form Template", "filters": [["name", "in", [
        "أساسي","نموذج 2", "نموذج 1","نموذج 3"]]]},
        
]
jinja = {
    "methods": [
        "agricultural_marketing.utils.jinja_custom_function.get_currency_in_arabic",
    ]
}


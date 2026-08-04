import frappe
from frappe.utils import cint

# Agriculture Settings flags that the desk needs *before* it can render.
#
# bulk_invoice_form.js used to fetch duplicate_last_row_in_bulk_invoice with an
# async frappe.db call, which races the first items_add(). Shipping the flags in
# bootinfo makes them synchronously available at page load for zero requests.
BOOT_SETTINGS_FIELDS = (
	"duplicate_last_row_in_bulk_invoice",
	"enable_bulk_quick_entry",
	"quick_entry_use_cache_in_grid",
)


def boot_session(bootinfo):
	"""Attach the Agricultural Marketing desk flags to frappe.boot."""
	try:
		# Redis-cached, so this costs no query on a warm cache. A never-saved
		# Single still comes back populated with the field defaults.
		settings = frappe.get_cached_doc("Agriculture Settings")
	except Exception:
		# Never let a settings read break the whole desk boot.
		frappe.log_error(title="Agricultural Marketing boot_session")
		return

	bootinfo.agricultural_marketing = {
		field: cint(settings.get(field)) for field in BOOT_SETTINGS_FIELDS
	}

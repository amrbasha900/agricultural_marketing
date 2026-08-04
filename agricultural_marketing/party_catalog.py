"""Bulk catalog of the parties and items used by the Bulk Invoice Form.

The form's Link fields used to hit `frappe.desk.search.search_link` on every
keystroke. With well under 2,000 eligible records in total, it is cheaper to
ship the whole catalog to the browser once and search it locally.

The sets here MUST stay in sync with the Link queries in
bulk_invoice_form.js -> setup_child_table_filters(), otherwise the quick-entry
bar would offer records the grid itself would reject.
"""

import frappe

# Bump when the payload shape changes -- every cached browser copy is discarded.
CATALOG_VERSION = 1

# Cached per set rather than as one blob so a Supplier edit does not invalidate
# the Item list. Long TTL: the real invalidation is the doc_events hook below.
CACHE_TTL = 24 * 60 * 60


def _cache_key(set_name):
	return f"agrimkt:party_catalog:{CATALOG_VERSION}:{set_name}"


def _suppliers():
	"""Farmers -- mirrors the `is_farmer: 1` filter on the supplier field."""
	return frappe.db.sql(
		"""
		SELECT name, supplier_name
		FROM `tabSupplier`
		WHERE is_farmer = 1
		ORDER BY name ASC
		"""
	)


def _customers():
	"""Mirrors bulk_invoice_form.get_filtered_customers().

	A customer flagged `couple_customer` is only sellable once a Supplier points
	back at it, so the EXISTS clause has to be carried over verbatim.
	"""
	return frappe.db.sql(
		"""
		SELECT name, customer_name
		FROM `tabCustomer`
		WHERE is_customer = 1
			AND is_frozen = 0
			AND (
				couple_customer = 0
				OR EXISTS (
					SELECT 1 FROM `tabSupplier`
					WHERE related_customer = `tabCustomer`.name
				)
			)
		ORDER BY name ASC
		"""
	)


def _pampers():
	"""Mirrors the `is_pamper: 1, is_frozen: 0` filter on the pamper field."""
	return frappe.db.sql(
		"""
		SELECT name, customer_name
		FROM `tabCustomer`
		WHERE is_pamper = 1 AND is_frozen = 0
		ORDER BY name ASC
		"""
	)


def _items():
	"""Mirrors the `commission_item: 0, is_agriculture_item: 1` filter.

	Disabled items are excluded here even though the grid's filter does not say
	so -- search_link drops them anyway, so including them would make the cache
	offer rows the grid would reject on validate.
	"""
	return frappe.db.sql(
		"""
		SELECT name, item_name
		FROM `tabItem`
		WHERE is_agriculture_item = 1
			AND commission_item = 0
			AND disabled = 0
		ORDER BY name ASC
		"""
	)


# set name -> (source doctype, builder). The doctype drives the read-permission
# check and the cache invalidation below.
CATALOG_SETS = {
	"supplier": ("Supplier", _suppliers),
	"customer": ("Customer", _customers),
	"pamper": ("Customer", _pampers),
	"item": ("Item", _items),
}


def _get_set(set_name):
	doctype, builder = CATALOG_SETS[set_name]

	if not frappe.has_permission(doctype, "read"):
		return None

	rows = frappe.cache().get_value(_cache_key(set_name))
	if rows is None:
		# Pairs, not dicts: the key names would otherwise be ~60% of the payload.
		rows = [[name, label or ""] for name, label in builder()]
		frappe.cache().set_value(_cache_key(set_name), rows, expires_in_sec=CACHE_TTL)

	return rows


@frappe.whitelist()
def get_catalog():
	"""Return every set the quick-entry cache needs, in one request.

	A set the user may not read comes back missing rather than empty, so the
	client can fall back to a live search instead of showing "no results".
	"""
	sets = {}
	for set_name in CATALOG_SETS:
		rows = _get_set(set_name)
		if rows is not None:
			sets[set_name] = rows

	return {
		"version": CATALOG_VERSION,
		"generated_on": frappe.utils.now(),
		"sets": sets,
	}


def clear_catalog_cache(doc=None, method=None, *args, **kwargs):
	"""Drop the cached sets sourced from `doc`'s doctype.

	`*args` absorbs after_rename's extra (old, new, merge) positional arguments.

	Wired to Supplier/Customer/Item writes so that a user who adds a supplier
	and then hits the refresh button in the form actually gets it.
	"""
	doctype = doc.doctype if doc else None

	for set_name, (source_doctype, _builder) in CATALOG_SETS.items():
		if doctype is None or source_doctype == doctype:
			frappe.cache().delete_value(_cache_key(set_name))

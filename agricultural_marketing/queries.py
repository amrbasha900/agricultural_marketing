"""Shared link-field search for parties (Supplier / Customer).

Frappe's stock link search has two problems for these forms:

* Ranking. It orders by where the text sits inside the name, so typing "1"
  lists 1001 before 0001 -- but 0001 *is* party number one.
* Matching. It compares the query as one contiguous substring, so a party named
  "سالم صالح محمد الموسى" cannot be found by typing "سالم الموسى".

Both are fixed here: the query is split into tokens that may match the code or
the name in any order, and results are ranked code-first so the party whose
number equals what was typed comes out on top.
"""

import re

import frappe
from frappe.utils import cint

#: How many rows to pull before ranking. Ranking has to happen in Python, so the
#: candidate set is capped rather than unbounded.
CANDIDATE_LIMIT = 500

_NON_DIGITS = re.compile(r"\D")


def tokenize(txt):
    """Split a query into search tokens."""
    return [token for token in (txt or "").lower().split() if token]


def _digits(value):
    return _NON_DIGITS.sub("", str(value or ""))


def matches(code, name, tokens):
    """True when every token appears in the code or the name."""
    haystack = "{0} {1}".format(code or "", name or "").lower()
    return all(token in haystack for token in tokens)


def rank(code, name, txt):
    """Lower is better. Code matches always beat name matches."""
    query = (txt or "").strip().lower()
    if not query:
        return 9

    code = (code or "").lower()
    name = (name or "").lower()
    code_digits = _digits(code)
    query_digits = _digits(query)
    numeric_query = query.isdigit() and bool(query_digits)

    if code == query:
        return 0
    # 0001 is party number 1 -- leading zeros must not hide it.
    if numeric_query and code_digits and int(code_digits) == int(query_digits):
        return 1
    if code.startswith(query):
        return 2
    if numeric_query and code_digits.startswith(query_digits):
        return 3
    if code.find(query) > -1:
        return 4
    if name.startswith(query):
        return 5
    if name.find(query) > -1:
        return 6
    return 7  # matched only as separate tokens


def sort_key(code, name, txt):
    query = (txt or "").strip().lower()
    occurrences = (code or "").lower().count(query) if query else 0
    code_digits = _digits(code)
    return (
        rank(code, name, txt),
        -occurrences,
        int(code_digits) if code_digits else 0,
        (code or ""),
    )


def rank_rows(rows, txt, start=0, page_len=10):
    """Filter `rows` [(code, name), ...] by tokens, rank them, return one page."""
    tokens = tokenize(txt)
    if tokens:
        rows = [row for row in rows if matches(row[0], row[1], tokens)]

    rows.sort(key=lambda row: sort_key(row[0], row[1], txt))

    start = cint(start)
    page_len = cint(page_len) or 10
    return rows[start : start + page_len]


def candidate_filters(txt):
    """The most selective token, for narrowing the SQL before ranking.

    Ranking cannot happen in SQL, so one token goes to the database and the
    rest are verified in Python. The longest token is used because it excludes
    the most rows.
    """
    tokens = tokenize(txt)
    return max(tokens, key=len) if tokens else ""


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def party_search(doctype, txt, searchfield, start, page_len, filters, as_dict=False, **kwargs):
    """Link query: search a party by code or by any words of its name."""
    meta = frappe.get_meta(doctype)

    conditions = {}
    if isinstance(filters, dict):
        conditions.update(filters)
    elif isinstance(filters, list):
        for item in filters:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                conditions[item[-3]] = item[-1]

    # Mirrors frappe.desk.search.search_widget -- never offer a disabled party.
    if meta.get_field("disabled"):
        conditions.setdefault("disabled", 0)

    title_field = meta.title_field if meta.title_field and meta.title_field != "name" else None
    fields = ["name"] + ([title_field] if title_field else [])

    token = candidate_filters(txt)
    or_filters = {}
    if token:
        or_filters["name"] = ["like", "%{0}%".format(token)]
        if title_field:
            or_filters[title_field] = ["like", "%{0}%".format(token)]

    rows = frappe.get_list(
        doctype,
        filters=conditions,
        or_filters=or_filters or None,
        fields=fields,
        limit_page_length=CANDIDATE_LIMIT,
        order_by="name asc",
        ignore_permissions=False,
        strict=False,
    )

    pairs = [(row.get("name"), row.get(title_field) if title_field else "") for row in rows]
    page = rank_rows(pairs, txt, start, page_len)

    if as_dict:
        return [{"value": code, "description": name or ""} for code, name in page]
    return [[code, name or ""] for code, name in page]

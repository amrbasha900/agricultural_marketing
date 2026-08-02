"""Supplier Statement Forms V2.

A self-contained re-layout of the supplier statement:

* One unified ledger table (sales + purchases + commission + payments) instead of
  four separate tables.
* Server-side pagination, so every sheet carries a full repeated header
  (company block, QR, balance box) and an accurate ``Page X of Y``.  This is
  required because the Chrome based PDF generator is invoked with
  ``--no-pdf-header-footer`` and therefore ignores the wkhtmltopdf
  ``#header-html`` / ``#footer-html`` mechanism used by V1.
* A closing totals block (حركة الاصناف / سعي الاصناف / صافي الاصناف /
  حركة ماليه / كمية الاصناف / الصافي) rendered on the last sheet only.

Nothing in this module mutates the V1 page.  All numeric logic is *imported*
from V1 so both pages can never drift apart.
"""

import base64
import io
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unicodedata

import frappe
from frappe import _
from frappe.utils import cint, flt, now
from frappe.utils.jinja_globals import is_rtl

from agricultural_marketing.agricultural_marketing.page.supplier_statement_forms import (
    supplier_statement_forms as v1,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Rows on a normal sheet. 0 means "fill the sheet" (see sheet_capacity).
DEFAULT_ROWS_PER_PAGE = 33

#: Rows on the closing sheet -- fewer, because the totals block lives there.
DEFAULT_ROWS_LAST_PAGE = 25

#: Largest row count that genuinely fits on one A4 sheet, per table font size.
#: Measured against real Chrome output rather than derived from the CSS box
#: model -- see scratchpad/calibrate2.py. Re-run it if the sheet layout changes.
#:
#: This matters for correctness, not just looks: `.sheet` is height:297mm with
#: overflow:hidden, so rows beyond the capacity are silently CLIPPED out of the
#: PDF instead of spilling onto another page. Every row count is clamped to
#: these numbers so a bad setting can never drop data.
SHEET_CAPACITY = ((7, 56), (8, 51), (9, 47), (11, 40), (13, 34))

#: The closing totals block costs exactly this many rows, at every font size.
TOTALS_BLOCK_ROWS = 10


def sheet_capacity(table_font_size, with_totals=False):
    """How many ledger rows fit on one sheet at this font size."""
    points = SHEET_CAPACITY
    size = flt(table_font_size) or points[0][0]

    # Interpolate between the measured points, and keep extrapolating along the
    # nearest segment beyond them -- clamping instead would over-estimate the
    # capacity for large fonts and start clipping rows.
    if size <= points[0][0]:
        first, second = points[0], points[1]
    elif size >= points[-1][0]:
        first, second = points[-2], points[-1]
    else:
        first, second = points[0], points[1]
        for low, high in zip(points, points[1:]):
            if low[0] <= size <= high[0]:
                first, second = low, high
                break

    (x0, y0), (x1, y1) = first, second
    capacity = int(y0 + (size - x0) * (y1 - y0) / float(x1 - x0))

    if with_totals:
        capacity -= TOTALS_BLOCK_ROWS

    return max(capacity, 1)

#: Filter keys accepted from the browser.  Anything else is dropped before it
#: reaches the query builder.
ALLOWED_FILTERS = {
    "company",
    "party",
    "party_group",
    "party_type",
    "from_date",
    "to_date",
    "consider_draft",
    "consider_draft_payments",
    "neglect_items",
    "calculate_opening_balance_with_totals",
    "session_id",
    "rows_per_page",
    "rows_last_page",
}

CHECKBOX_FILTERS = {
    "consider_draft",
    "consider_draft_payments",
    "neglect_items",
    "calculate_opening_balance_with_totals",
}

# The PDF template deliberately lives in a `templates/` subfolder rather than
# next to the page files.  Page.load_assets() turns EVERY .html sitting directly
# in a page folder into a desk JS template (frappe.templates[...] = '...'), which
# would (a) ship this 14 KB print template to the browser on every page load and
# (b) break the page outright -- frappe's scrub_html_template ends with
# `content.replace("'", "'")`, a no-op that escapes no apostrophes, so a single
# `'` anywhere in the template closes the JS string. os.listdir() there is not
# recursive, so a subfolder is invisible to it.
TEMPLATE_SUBDIR = "templates"
TEMPLATE_NAME = "supplier_statement_v2.html"


# ---------------------------------------------------------------------------
# Filters / helpers
# ---------------------------------------------------------------------------


def normalize_filters(filters):
    """Parse, allow-list and coerce the filters coming from the client."""
    if isinstance(filters, str):
        filters = json.loads(filters or "{}")

    filters = dict(filters or {})
    clean = {k: v for k, v in filters.items() if k in ALLOWED_FILTERS}

    for key in CHECKBOX_FILTERS:
        clean[key] = cint(clean.get(key))

    # This page is supplier-only, exactly like V1.
    clean["party_type"] = "Supplier"

    # 0 is meaningful here: it asks for "as many rows as the sheet holds".
    # The real values are resolved in build_context(), which knows the font size.
    clean["rows_per_page"] = cint(clean.get("rows_per_page"))
    clean["rows_last_page"] = cint(clean.get("rows_last_page"))

    return clean


def validate_filters(filters):
    missing = [key for key in ("company", "from_date") if not filters.get(key)]
    if missing:
        frappe.throw(_("Missing mandatory filters: {0}").format(", ".join(missing)))


def _hide_decimal():
    return cint(frappe.db.get_single_value("Agriculture Settings", "hide_decimal"))


def money(value, hide_decimal=None):
    """Format a currency amount as a plain LTR string (no currency symbol)."""
    if hide_decimal is None:
        hide_decimal = _hide_decimal()
    value = flt(value)
    return "{:,.0f}".format(value) if hide_decimal else "{:,.2f}".format(value)


def qty_str(value):
    value = flt(value)
    if value == int(value):
        return "{:,.0f}".format(value)
    return "{:,.2f}".format(value)


def date_str(value):
    if not value:
        return ""
    try:
        return frappe.utils.getdate(value).strftime("%Y/%m/%d")
    except Exception:
        return str(value)


def _is_total_row(row, id_field):
    """V1 appends an untranslated-safe "Total" row that carries no document id."""
    return not row.get(id_field)


def _real_rows(rows, id_field):
    return [r for r in (rows or []) if not _is_total_row(r, id_field)]


def _total_row(rows, id_field):
    if rows and _is_total_row(rows[-1], id_field):
        return rows[-1]
    return {}


# ---------------------------------------------------------------------------
# PDF generator
# ---------------------------------------------------------------------------


def get_pdf_bytes(html, options=None):
    """Render with headless Chrome so the Arabic text stays selectable/searchable.

    V1 goes through ``frappe.utils.pdf.get_pdf``, which frappe_pdf only
    redirects to Chrome when ``Print Settings.pdf_using_google_chrome`` is
    ticked.  V2 asks for Chrome directly and only falls back to wkhtmltopdf when
    the frappe_pdf app is not installed at all.
    """
    options = dict(options or {})
    options.setdefault("orientation", "Portrait")
    options.setdefault("page-size", "A4")
    # The sheet draws its own frame and padding.
    options.setdefault("margin-top", "0mm")
    options.setdefault("margin-bottom", "0mm")
    options.setdefault("margin-left", "0mm")
    options.setdefault("margin-right", "0mm")

    content = _print_with_chrome(html)

    if content is None:
        from frappe.utils.pdf import get_pdf as wkhtmltopdf_get_pdf

        return wkhtmltopdf_get_pdf(html, options)

    # Chrome's own /ToUnicode maps land on presentation forms, so the PDF is not
    # searchable in Arabic until we rewrite them.
    return make_arabic_searchable(content)


def _print_with_chrome(html):
    """Print self-contained HTML with headless Chrome. Returns None if absent.

    Deliberately does NOT go through ``frappe_pdf.utils.pdf.get_pdf``. That
    helper runs the markup through ``scrub_urls()``, whose sid-appending branch
    sits *outside* its own ``data:`` guard::

        src="data:image/png;base64,iVBORw0..."
        -> src="data:image/png;base64,iVBORw0...?sid=64c61a7692..."

    which silently destroys both the QR image and the embedded Cairo font. It
    only bites inside a web request (``frappe.local.request`` must exist), which
    is why the queued PDFs looked fine and the Download button did not.

    This statement is fully self-contained -- every asset is already a data URI,
    with no relative links to rewrite -- so URL scrubbing buys nothing here.
    """
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if not chrome:
        return None

    pdf_path = os.path.join(tempfile.gettempdir(), "{0}.pdf".format(frappe.generate_hash()))

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=True, encoding="utf-8"
        ) as html_file:
            html_file.write(html)
            html_file.flush()

            subprocess.run(
                [
                    chrome,
                    "--headless",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    "--run-all-compositor-stages-before-draw",
                    "--print-to-pdf={0}".format(pdf_path),
                    html_file.name,
                ],
                shell=False,
                check=False,
                capture_output=True,
                timeout=300,
            )

        if not os.path.exists(pdf_path):
            frappe.log_error(
                message="Chrome produced no PDF at {0}".format(pdf_path),
                title="Supplier Statement V2 - PDF",
            )
            return None

        with open(pdf_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


# --- Arabic searchability -------------------------------------------------
#
# Chrome shapes Arabic before writing the PDF and then builds each font's
# /ToUnicode CMap by reverse-mapping *glyphs*, which lands on Unicode
# presentation forms (U+FB50-FEFF) rather than the base letters.  The PDF looks
# perfect but searching it for "خيار" finds nothing, because the text really
# extracts as "ﺧﻴﺎر".
#
# The fix is to rewrite every /ToUnicode CMap, decomposing each presentation
# form back to the letters it was shaped from.  Unicode already carries that
# mapping (unicodedata.decomposition -> "<initial> 0645"), including the
# multi-letter ligatures such as lam-alef.

SHAPED_TAGS = ("<isolated>", "<initial>", "<medial>", "<final>")

_CODESPACE_RE = re.compile(rb"\d+\s+begincodespacerange(.*?)endcodespacerange", re.S)
_BFCHAR_RE = re.compile(rb"\d+\s+beginbfchar(.*?)endbfchar", re.S)
_BFRANGE_RE = re.compile(rb"\d+\s+beginbfrange(.*?)endbfrange", re.S)
_HEX_RE = re.compile(rb"<([0-9A-Fa-f]*)>")
_BFRANGE_ARRAY_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", re.S)
_BFRANGE_PLAIN_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")


def _unshape(codepoint):
    """Presentation form -> the base letter(s) it was shaped from.

    Ligature glyphs (ضر, بي, لا, ...) decompose to several letters; they are
    emitted in logical order as the PDF spec requires.  Readers apply their own
    bidi pass on top, so emitting them reversed changes nothing -- measured
    against poppler, both orders score identically.  The template also asks the
    font to suppress ligatures so most of these never arise.
    """
    decomposition = unicodedata.decomposition(chr(codepoint))
    if decomposition:
        parts = decomposition.split()
        if parts[0] in SHAPED_TAGS:
            return [int(part, 16) for part in parts[1:]]
    return [codepoint]


def _hex_to_codepoints(hex_bytes):
    raw = bytes.fromhex(hex_bytes.decode("ascii"))
    if len(raw) % 2:
        raw += b"\x00"
    return [ord(ch) for ch in raw.decode("utf-16-be", "ignore")]


def _codepoints_to_hex(codepoints):
    text = "".join(chr(cp) for cp in codepoints)
    return text.encode("utf-16-be").hex().upper().encode("ascii")


def _parse_cmap(data):
    """Flatten a ToUnicode CMap into {src_hex: [codepoints]}."""
    mapping = {}

    for block in _BFCHAR_RE.findall(data):
        tokens = _HEX_RE.findall(block)
        for i in range(0, len(tokens) - 1, 2):
            mapping[tokens[i].upper()] = _hex_to_codepoints(tokens[i + 1])

    for block in _BFRANGE_RE.findall(data):
        for lo, hi, items in _BFRANGE_ARRAY_RE.findall(block):
            start = int(lo, 16)
            width = len(lo)
            for offset, dst in enumerate(_HEX_RE.findall(items)):
                src = ("%0*X" % (width, start + offset)).encode("ascii")
                mapping[src] = _hex_to_codepoints(dst)

        without_arrays = _BFRANGE_ARRAY_RE.sub(b"", block)
        for lo, hi, dst in _BFRANGE_PLAIN_RE.findall(without_arrays):
            start, end = int(lo, 16), int(hi, 16)
            width = len(lo)
            base = _hex_to_codepoints(dst)
            if not base or end < start or (end - start) > 0xFFFF:
                continue
            for offset in range(end - start + 1):
                src = ("%0*X" % (width, start + offset)).encode("ascii")
                # Only the last unit increments across a bfrange.
                mapping[src] = base[:-1] + [base[-1] + offset]

    return mapping


def _build_cmap(codespace, mapping):
    lines = [
        b"/CIDInit /ProcSet findresource begin",
        b"12 dict begin",
        b"begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Adobe-Identity-UCS def",
        b"/CMapType 2 def",
        b"1 begincodespacerange",
        codespace.strip(),
        b"endcodespacerange",
    ]

    entries = sorted(mapping.items())
    # The spec caps a bfchar block at 100 entries.
    for start in range(0, len(entries), 100):
        chunk = entries[start : start + 100]
        lines.append(b"%d beginbfchar" % len(chunk))
        for src, codepoints in chunk:
            lines.append(b"<" + src + b"> <" + _codepoints_to_hex(codepoints) + b">")
        lines.append(b"endbfchar")

    lines += [
        b"endcmap",
        b"CMapName currentdict /CMap defineresource pop",
        b"end",
        b"end",
    ]
    return b"\n".join(lines)


def make_arabic_searchable(pdf_bytes):
    """Rewrite /ToUnicode CMaps so Arabic text extracts (and searches) correctly.

    Returns the original bytes untouched if anything goes wrong -- a
    non-searchable PDF is far better than a corrupt one.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject

        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter(clone_from=reader)

        patched = 0
        handled = set()

        for page in writer.pages:
            resources = page.get("/Resources")
            if not resources:
                continue
            fonts = resources.get("/Font")
            if not fonts:
                continue
            fonts = fonts.get_object()

            for key in list(fonts.keys()):
                font = fonts[key].get_object()
                if font.get("/ToUnicode") is None:
                    continue

                try:
                    ref = font.raw_get("/ToUnicode").idnum
                except Exception:
                    ref = None
                if ref is not None and ref in handled:
                    continue

                try:
                    data = font["/ToUnicode"].get_object().get_data()
                    codespace = _CODESPACE_RE.search(data)
                    if not codespace:
                        continue

                    mapping = _parse_cmap(data)
                    if not mapping:
                        continue

                    changed = False
                    for src, codepoints in mapping.items():
                        unshaped = []
                        for cp in codepoints:
                            base = _unshape(cp)
                            if base != [cp]:
                                changed = True
                            unshaped.extend(base)
                        mapping[src] = unshaped

                    if changed:
                        stream = DecodedStreamObject()
                        stream.set_data(_build_cmap(codespace.group(1), mapping))
                        font[NameObject("/ToUnicode")] = writer._add_object(stream)
                        patched += 1
                except Exception:
                    continue
                finally:
                    if ref is not None:
                        handled.add(ref)

        if not patched:
            return pdf_bytes

        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="Supplier Statement V2 - Arabic searchability",
        )
        return pdf_bytes


def pdf_engine_info():
    """Reported in the UI so the user always knows which engine will be used."""
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return {
            "engine": "google-chrome",
            "searchable_arabic": True,
            "message": _("PDFs are rendered with headless Google Chrome (searchable Arabic text)."),
        }

    return {
        "engine": "wkhtmltopdf",
        "searchable_arabic": False,
        "message": _(
            "Google Chrome is not installed on this server, so PDFs fall back to "
            "wkhtmltopdf and Arabic text will not be searchable."
        ),
    }


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def read_template():
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), TEMPLATE_SUBDIR, TEMPLATE_NAME
    )
    if not os.path.exists(path):
        frappe.throw(_("Statement V2 template not found: {0}").format(path))
    with open(path, encoding="utf-8") as f:
        return f.read()


def _embedded_font_css():
    """Return @font-face CSS to inline into the statement.

    Chrome renders the document from a ``file://`` temp path and cannot fetch
    remote fonts, and the only Arabic-capable font installed on the server is
    DejaVu Sans -- so the font has to travel with the HTML.

    Two sources, in order:

    1. Any ``.css`` in ``agricultural_marketing/public/fonts/`` is inlined
       verbatim.  This is how Cairo ships (``cairo.css``): one @font-face per
       unicode subset with the woff2 already base64-encoded.
    2. Otherwise a bare ``.woff2``/``.ttf`` dropped in the same folder is
       encoded on the fly and exposed as the family ``AGM Arabic``.

    Cached, because base64-ing ~110 KB on every page of every statement is
    pure waste.
    """

    def _load():
        try:
            fonts_dir = os.path.join(frappe.get_app_path("agricultural_marketing"), "public", "fonts")
        except Exception:
            return ""

        if not os.path.isdir(fonts_dir):
            return ""

        files = sorted(os.listdir(fonts_dir))

        for filename in files:
            if filename.lower().endswith(".css"):
                try:
                    with open(os.path.join(fonts_dir, filename), encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    continue

        mimes = {
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
        }
        for filename in files:
            mime = mimes.get(os.path.splitext(filename)[1].lower())
            if not mime:
                continue
            try:
                with open(os.path.join(fonts_dir, filename), "rb") as f:
                    encoded = base64.b64encode(f.read()).decode()
            except OSError:
                continue
            return (
                "@font-face {\n"
                "  font-family: 'AGM Arabic';\n"
                "  font-style: normal;\n"
                "  font-weight: 400 700;\n"
                "  src: url(data:%s;base64,%s) format('%s');\n"
                "}\n" % (mime, encoded, mime.split("/")[1])
            )

        return ""

    return frappe.cache().get_value("agm_statement_v2_font_css", _load)


# ---------------------------------------------------------------------------
# Header data
# ---------------------------------------------------------------------------


def get_company_profile(company):
    """Company block for the top-right / top-left corners of every sheet."""
    profile = {
        "name": company,
        "tax_id": "",
        "cr_no": "",
        "phone": "",
        "address_lines": [],
    }
    if not company:
        return profile

    try:
        doc = frappe.get_cached_doc("Company", company)
    except Exception:
        return profile

    profile["name"] = doc.get("company_name") or doc.name
    profile["tax_id"] = doc.get("tax_id") or ""

    for fieldname in ("commercial_registration_number", "cr_no", "registration_details"):
        value = doc.get(fieldname)
        if value:
            profile["cr_no"] = str(value).strip().splitlines()[0]
            break

    phones = [doc.get("phone_no"), doc.get("mobile_no")]
    profile["phone"] = " / ".join([p for p in phones if p])

    try:
        address_name = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": "Company",
                "link_name": company,
                "parenttype": "Address",
            },
            "parent",
        )
        if address_name:
            address = frappe.get_cached_doc("Address", address_name)
            lines = [
                address.get("address_line1"),
                address.get("address_line2"),
                " - ".join([p for p in (address.get("city"), address.get("state")) if p]),
            ]
            profile["address_lines"] = [line for line in lines if line]
            if not profile["phone"] and address.get("phone"):
                profile["phone"] = address.get("phone")
    except Exception:
        pass

    return profile


def get_supplier_profile(party):
    values = (
        frappe.db.get_value(
            "Supplier",
            party,
            ["supplier_name", "supplier_group", "mobile_no"],
            as_dict=True,
        )
        or {}
    )
    return {
        "code": party,
        "name": values.get("supplier_name") or party,
        "group": values.get("supplier_group") or "",
        "mobile": values.get("mobile_no") or "",
    }


def build_qr(company_profile, supplier, filters, closing_balance):
    """Verification QR carrying the identity of the statement.

    Never allowed to break PDF generation -- a missing QR just renders nothing.
    """
    payload = "\n".join(
        [
            company_profile.get("name") or "",
            "{}: {}".format(_("Tax ID"), company_profile.get("tax_id") or "-"),
            "{}: {} ({})".format(_("Supplier"), supplier.get("name"), supplier.get("code")),
            "{}: {} - {}".format(
                _("Period"),
                date_str(filters.get("from_date")),
                date_str(filters.get("to_date")),
            ),
            "{}: {}".format(_("Balance"), money(closing_balance)),
        ]
    )

    try:
        import qrcode

        img = qrcode.make(payload)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        frappe.log_error(
            message=frappe.get_traceback(), title="Supplier Statement V2 - QR generation"
        )
        return None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def build_ledger_rows(value, filters):
    """Flatten sales, purchases, commission and payments into one ledger.

    Returns ``(rows, aggregates)``.  Debit (مدين / عليه) and credit (دائن / له)
    follow the exact same convention as :func:`v1.get_party_summary` for a
    Supplier: sales are credit, purchases / commission / payments are debit.
    """
    hide_decimal = _hide_decimal()
    neglect_items = cint(filters.get("neglect_items"))

    selling = _real_rows(value.get("items"), "invoice_id")
    buying = _real_rows(value.get("buying_items"), "invoice_id")
    payments = _real_rows(value.get("payments"), "payment_id")

    selling_total = _total_row(value.get("items"), "invoice_id")
    buying_total = _total_row(value.get("buying_items"), "invoice_id")

    entries = []

    for item in selling:
        entries.append(
            {
                "sort_key": (item.get("date"), 0),
                "date": item.get("date"),
                "description": _describe_item(item, neglect_items),
                "price": item.get("price"),
                "debit": 0,
                "credit": flt(item.get("total")),
            }
        )

    for item in buying:
        entries.append(
            {
                "sort_key": (item.get("date"), 1),
                "date": item.get("date"),
                "description": "{} - {}".format(_("شراء"), _describe_item(item, neglect_items)),
                "price": item.get("price"),
                "debit": flt(item.get("total")),
                "credit": 0,
            }
        )

    for payment in payments:
        amount = flt(payment.get("paid_amount"))
        entries.append(
            {
                "sort_key": (payment.get("date"), 2),
                "date": payment.get("date"),
                "description": _describe_payment(payment),
                "price": None,
                "debit": amount if amount > 0 else 0,
                "credit": abs(amount) if amount < 0 else 0,
            }
        )

    entries.sort(key=lambda e: (e["sort_key"][0] or frappe.utils.getdate("1900-01-01"), e["sort_key"][1]))

    # Commission is a period aggregate, so it closes the ledger.
    commission = flt(selling_total.get("commission"))
    if commission:
        entries.append(
            {
                "sort_key": None,
                "date": filters.get("to_date"),
                "description": _("سعي الأصناف + الضريبة"),
                "price": None,
                "debit": commission,
                "credit": 0,
            }
        )

    rows = []
    for index, entry in enumerate(entries, start=1):
        debit, credit = _flip_negatives(entry["debit"], entry["credit"])
        rows.append(
            {
                "idx": index,
                "date": date_str(entry["date"]),
                "description": entry["description"],
                "price": money(entry["price"], hide_decimal) if entry["price"] else "",
                "debit": money(debit, hide_decimal),
                "credit": money(credit, hide_decimal),
                "debit_raw": debit,
                "credit_raw": credit,
            }
        )

    aggregates = {
        "total_selling": flt(selling_total.get("total")),
        "total_buying": flt(buying_total.get("total")),
        "commission": commission,
        "qty_out": sum(flt(i.get("qty")) for i in selling),
        "qty_in": sum(flt(i.get("qty")) for i in buying),
        "payments_debit": sum(flt(p.get("paid_amount")) for p in payments if flt(p.get("paid_amount")) > 0),
        "payments_credit": abs(
            sum(flt(p.get("paid_amount")) for p in payments if flt(p.get("paid_amount")) < 0)
        ),
    }

    return rows, aggregates


def _flip_negatives(debit, credit):
    """Move a negative amount to the opposite column.

    Returns and other reversals arrive as negative totals.  Showing "-700" in
    the credit column is not how the ledger reads -- it belongs in the debit
    column as "700".  This mirrors ``append_summary()`` in V1 so a row and the
    summary can never disagree about a sign.
    """
    debit = flt(debit)
    credit = flt(credit)

    if debit < 0:
        credit += abs(debit)
        debit = 0
    if credit < 0:
        debit += abs(credit)
        credit = 0

    return debit, credit


def _describe_item(item, neglect_items):
    """Build the البيان cell, e.g. "عدد 24 سكري بسعر 5 ريال"."""
    if neglect_items or not item.get("item_name"):
        return "{} {}".format(_("فاتورة رقم"), item.get("invoice_id") or "")

    return "{} {} {} {} {} {}".format(
        _("عدد"),
        qty_str(item.get("qty")),
        item.get("item_name"),
        _("بسعر"),
        qty_str(item.get("price")),
        _("ريال"),
    )


def _describe_payment(payment):
    parts = ["{} {}".format(_("سند"), payment.get("payment_id") or "")]

    mop = payment.get("mop")
    if mop:
        parts.append(str(mop))

    remarks = (payment.get("remarks") or "").strip().replace("\n", " ")
    if remarks:
        parts.append(remarks[:80])

    return " - ".join(parts)


def paginate(rows, rows_per_page, totals_rows=TOTALS_BLOCK_ROWS):
    """Split rows into sheets and decide which sheet carries the totals block.

    The totals live in the ledger table's own <tfoot>, so they simply consume
    rows out of the sheet's budget like any other content. That means there is
    a single capacity to reason about -- no separate "last page" size, and no
    reserved gap at the bottom of the earlier sheets.

    Returns a list of ``(rows, show_totals)`` tuples.
    """
    rows_per_page = max(cint(rows_per_page) or DEFAULT_ROWS_PER_PAGE, 1)
    totals_rows = max(cint(totals_rows), 0)

    if not rows:
        return [([], True)]

    chunks = [rows[i : i + rows_per_page] for i in range(0, len(rows), rows_per_page)]

    # The totals ride along on the final sheet when there is room for them,
    # otherwise they get a sheet of their own.
    if len(chunks[-1]) + totals_rows <= rows_per_page:
        return [(chunk, index == len(chunks) - 1) for index, chunk in enumerate(chunks)]

    return [(chunk, False) for chunk in chunks] + [([], True)]


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def build_context(filters, party, party_data):
    """Assemble everything the template needs for one supplier."""
    hide_decimal = _hide_decimal()

    summary = v1.get_party_summary(
        filters=filters,
        party_type="Supplier",
        party=party,
        party_data=party_data,
    )

    opening = summary[0] if summary else {}
    closing = summary[-1] if summary else {}
    opening_balance = flt(opening.get("balance"))
    closing_balance = flt(closing.get("balance"))
    period_balance = closing_balance - opening_balance

    rows, aggregates = build_ledger_rows(party_data, filters)

    company_profile = get_company_profile(filters.get("company"))
    supplier = get_supplier_profile(party)
    base_font_size = cint(frappe.db.get_single_value("Agriculture Settings", "font_size")) or 12
    table_font_size = max(base_font_size - 3, 6)

    # Resolve the row counts now that the font size is known: 0 (or missing)
    # means "fill the sheet", and anything larger than the sheet actually holds
    # is clamped, because overflow past the sheet is clipped, not paginated.
    capacity = sheet_capacity(table_font_size)

    # A single number now: the totals block is part of the table and pays for
    # itself out of this same budget. 0 (or missing) means "fill the sheet";
    # anything larger than the sheet holds is clamped, because content past the
    # sheet is clipped rather than paginated.
    rows_per_page = min(cint(filters.get("rows_per_page")) or capacity, capacity)

    pages = paginate(rows, rows_per_page)

    # صافي الاصناف = حركة الاصناف - سعي الاصناف, kept signed so it lands in the
    # correct column.  Credit-positive: sales are credit, purchases/commission debit.
    net_items = aggregates["total_selling"] - aggregates["total_buying"] - aggregates["commission"]
    net_items_debit = abs(net_items) if net_items < 0 else 0
    net_items_credit = net_items if net_items > 0 else 0

    context = {
        "company": company_profile,
        "supplier": supplier,
        "filters": filters,
        "from_date": date_str(filters.get("from_date")),
        "to_date": date_str(filters.get("to_date")),
        "printed_on": frappe.utils.format_datetime(now(), "yyyy/MM/dd HH:mm:ss"),
        "qr": build_qr(company_profile, supplier, filters, closing_balance),
        "font_css": _embedded_font_css(),
        # Base size comes from Agriculture Settings; the statement runs 2pt below
        # it, and the ledger a further 1pt below that (3pt under the base).
        "font_size": max(base_font_size - 2, 6),
        "table_font_size": table_font_size,
        "rows_per_page": rows_per_page,
        "capacity_per_page": capacity,
        "report_note": frappe.db.get_single_value("Agriculture Settings", "report_note") or "",
        "show_supplier_code": cint(
            frappe.db.get_single_value("Agriculture Settings", "show_supplier_code_in_pdf")
        ),
        "direction": "rtl" if is_rtl() else "rtl",
        "balances": {
            "opening_debit": money(opening_balance if opening_balance > 0 else 0, hide_decimal),
            "opening_credit": money(abs(opening_balance) if opening_balance < 0 else 0, hide_decimal),
            "period_debit": money(period_balance if period_balance > 0 else 0, hide_decimal),
            "period_credit": money(abs(period_balance) if period_balance < 0 else 0, hide_decimal),
            "closing_debit": money(closing_balance if closing_balance > 0 else 0, hide_decimal),
            "closing_credit": money(abs(closing_balance) if closing_balance < 0 else 0, hide_decimal),
        },
        # The block that was missing from V1 entirely.
        "totals": {
            "items_debit": money(aggregates["total_buying"], hide_decimal),
            "items_credit": money(aggregates["total_selling"], hide_decimal),
            "commission_debit": money(aggregates["commission"], hide_decimal),
            "commission_credit": money(0, hide_decimal),
            "net_items_debit": money(net_items_debit, hide_decimal),
            "net_items_credit": money(net_items_credit, hide_decimal),
            "cash_debit": money(aggregates["payments_debit"], hide_decimal),
            "cash_credit": money(aggregates["payments_credit"], hide_decimal),
            "qty_out": qty_str(aggregates["qty_out"]),
            "qty_in": qty_str(aggregates["qty_in"]),
            "net_label": _("مدين") if closing_balance > 0 else _("دائن"),
            "net_side": _("عليه") if closing_balance > 0 else _("له"),
            "net_amount": money(abs(closing_balance), hide_decimal),
        },
        "pages": [
            {
                "index": page_index,
                "rows": page_rows,
                # Which sheet prints the totals -- not necessarily the last one
                # in a degenerate layout, so it is decided by paginate().
                "is_last": show_totals,
            }
            for page_index, (page_rows, show_totals) in enumerate(pages, start=1)
        ],
        "total_pages": len(pages),
        "row_count": len(rows),
        "summary": summary,
    }

    return context


def render_statement(filters, party, party_data=None):
    """Render the statement HTML for a single supplier."""
    if party_data is None:
        scoped = dict(filters)
        scoped["party"] = party
        data = v1.get_data(frappe._dict(), scoped)
        if not data or party not in data:
            frappe.throw(_("No data found for supplier: {0}").format(party))
        party_data = data[party]
        filters = scoped

    context = build_context(filters, party, party_data)
    return frappe.render_template(read_template(), context), context


# ---------------------------------------------------------------------------
# Whitelisted API -- preview
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_parties_with_data(filters):
    """Suppliers that actually have movement in the period (for the navigator)."""
    filters = normalize_filters(filters)
    validate_filters(filters)

    data = v1.get_data(frappe._dict(), filters)
    parties = sorted(data.keys()) if data else []

    names = {}
    if parties:
        for row in frappe.get_all(
            "Supplier",
            filters={"name": ["in", parties]},
            fields=["name", "supplier_name"],
        ):
            names[row.name] = row.supplier_name or row.name

    return {
        "parties": [{"code": p, "name": names.get(p, p)} for p in parties],
        "count": len(parties),
        "engine": pdf_engine_info(),
    }


@frappe.whitelist()
def get_statement_preview(filters, party=None):
    """Render one supplier's statement as HTML for the on-screen preview."""
    filters = normalize_filters(filters)
    validate_filters(filters)

    if party:
        filters["party"] = party

    data = v1.get_data(frappe._dict(), filters)
    if not data:
        return {"error": _("No data matches the chosen criteria")}

    target = filters.get("party") if filters.get("party") in data else sorted(data.keys())[0]

    html, context = render_statement(filters, target, data[target])

    return {
        "party": target,
        "party_name": context["supplier"]["name"],
        "html": html,
        "total_pages": context["total_pages"],
        "row_count": context["row_count"],
        "closing": context["totals"]["net_amount"],
        "closing_side": context["totals"]["net_side"],
    }


@frappe.whitelist()
def download_statement_pdf(filters, party):
    """Generate one PDF immediately (no queue) and return its file URL."""
    filters = normalize_filters(filters)
    validate_filters(filters)
    filters["party"] = party

    html, _context = render_statement(filters, party)
    content = get_pdf_bytes(html)

    file_doc = frappe.new_doc("File")
    file_doc.update(
        {
            "file_name": "{0}-V2-{1}.pdf".format(party, random.randint(1000, 9999)),
            "is_private": 0,
            "content": content,
        }
    )
    file_doc.save(ignore_permissions=True)

    return {"success": True, "file_url": file_doc.file_url}


# ---------------------------------------------------------------------------
# Whitelisted API -- bulk generation
# ---------------------------------------------------------------------------


@frappe.whitelist()
def queue_pdf_generation(filters):
    """Queue one V2 PDF job per supplier that has data."""
    filters = normalize_filters(filters)
    validate_filters(filters)

    frappe.publish_realtime("pdf_generation_status", {"message": _("Checking for suppliers with data...")})

    data = v1.get_data(frappe._dict(), filters)
    if not data:
        return {"error": _("No data matches the chosen criteria")}

    suppliers = sorted(data.keys())

    history_doc = frappe.get_doc(
        {
            "doctype": "Statement Generation History",
            "company": filters.get("company"),
            "party_type": "Supplier",
            "party_group": filters.get("party_group"),
            "party": filters.get("party"),
            "from_date": filters.get("from_date"),
            "to_date": filters.get("to_date"),
            "created_by_user": frappe.session.user,
            "generation_time": now(),
            "supplier_statement": 1,
            "statement_layout_v2": 1,
            "consider_draft": filters.get("consider_draft", 0),
            "consider_draft_payments": filters.get("consider_draft_payments", 0),
            "neglect_items": filters.get("neglect_items", 0),
            "calculate_opening_balance_with_totals": filters.get(
                "calculate_opening_balance_with_totals", 0
            ),
            "total_parties": len(suppliers),
            "description": "[V2] Supplier statement generation for {0} suppliers from {1} to {2}".format(
                len(suppliers), filters.get("from_date"), filters.get("to_date")
            ),
        }
    )
    history_doc.insert(ignore_permissions=True)

    log_entries = []
    for supplier in suppliers:
        filters_for_log = dict(filters)
        filters_for_log["party"] = supplier

        log_entry = frappe.get_doc(
            {
                "doctype": "PDF Generator Log",
                "party_type": "Supplier",
                "party_name": supplier,
                "party_group": filters.get("party_group"),
                "company": filters.get("company"),
                "from_date": filters.get("from_date"),
                "to_date": filters.get("to_date"),
                "status": "Queued",
                "created_by": frappe.session.user,
                "filters_json": json.dumps(filters_for_log),
                "statement_generation_history": history_doc.name,
                "session_id": filters.get("session_id"),
            }
        )
        log_entry.insert(ignore_permissions=True)
        log_entries.append(log_entry.name)

        history_doc.append(
            "pdf_generator_logs",
            {
                "pdf_generator_log": log_entry.name,
                "party_name": supplier,
                "status": "Queued",
                "whatsapp_status": "Not Created",
            },
        )

        safe_name = frappe.scrub(supplier).replace("_", "-")[:30]
        frappe.enqueue(
            method=generate_single_supplier_pdf_v2,
            log_id=log_entry.name,
            supplier_name=supplier,
            history_id=history_doc.name,
            job_id="pdf-gen-{0}".format(log_entry.name),
            deduplicate=True,
            timeout=300,
            is_async=True,
        )

    history_doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.publish_realtime(
        "pdf_generation_status",
        {
            "message": _("Supplier statement V2 jobs queued successfully"),
            "queued": len(log_entries),
            "history_id": history_doc.name,
        },
    )

    return {
        "success": "Queued {0} supplier PDF (V2) generation jobs".format(len(log_entries)),
        "log_entries": log_entries,
        "parties_with_data": len(suppliers),
        "history_id": history_doc.name,
    }


def generate_single_supplier_pdf_v2(log_id, supplier_name=None, history_id=None):
    """Background job: render one supplier's V2 statement into a File."""
    log_doc = None
    actual_supplier = supplier_name

    try:
        try:
            log_doc = frappe.get_doc("PDF Generator Log", log_id)
        except frappe.DoesNotExistError:
            if history_id:
                v1.update_history_item_status_safe(
                    history_id, log_id, "Failed", error_message="PDF log not found"
                )
            return

        log_doc.status = "Processing"
        log_doc.save(ignore_permissions=True)
        if history_id:
            v1.update_history_item_status_safe(history_id, log_id, "Processing")
        frappe.db.commit()

        actual_supplier = log_doc.party_name or supplier_name
        if not actual_supplier:
            raise ValueError("No supplier name available on log or argument")

        try:
            filters = normalize_filters(log_doc.filters_json or "{}")
        except (json.JSONDecodeError, ValueError) as json_error:
            log_doc.status = "Failed"
            log_doc.error_message = "Invalid filters JSON: {0}".format(json_error)
            log_doc.save(ignore_permissions=True)
            if history_id:
                v1.update_history_item_status_safe(
                    history_id, log_id, "Failed", error_message=log_doc.error_message
                )
            frappe.db.commit()
            return

        # The log is authoritative -- never trust the serialised filters alone.
        filters["party"] = actual_supplier
        filters["party_type"] = "Supplier"
        filters["company"] = log_doc.company
        filters["party_group"] = log_doc.party_group
        filters["from_date"] = str(log_doc.from_date) if log_doc.from_date else filters.get("from_date")
        filters["to_date"] = str(log_doc.to_date) if log_doc.to_date else filters.get("to_date")

        data = v1.get_data(frappe._dict(), filters)
        if not data or actual_supplier not in data:
            raise ValueError("No data found for supplier: {0}".format(actual_supplier))

        html, _context = render_statement(filters, actual_supplier, data[actual_supplier])
        content = get_pdf_bytes(html)

        file_doc = frappe.new_doc("File")
        file_doc.update(
            {
                "file_name": "{0}-V2-{1}.pdf".format(actual_supplier, random.randint(1000, 9999)),
                "is_private": 0,
                "content": content,
            }
        )
        file_doc.save(ignore_permissions=True)

        log_doc.status = "Completed"
        log_doc.pdf_file = file_doc.file_url
        log_doc.completion_time = now()
        if history_id:
            v1.update_history_item_status_safe(
                history_id, log_id, "Completed", pdf_file=file_doc.file_url
            )

    except Exception as e:
        error_msg = str(e)[:200]
        frappe.log_error(
            message="Supplier PDF V2 generation failed for {0} ({1}): {2}".format(
                log_id, actual_supplier, frappe.get_traceback()
            ),
            title="Supplier Statement V2",
        )
        if log_doc:
            try:
                log_doc.status = "Failed"
                log_doc.error_message = error_msg
                if history_id:
                    v1.update_history_item_status_safe(
                        history_id, log_id, "Failed", error_message=error_msg
                    )
            except Exception:
                frappe.log_error(
                    message="Failed to update error status for {0}".format(log_id),
                    title="Supplier Statement V2",
                )
                return

    if log_doc:
        try:
            log_doc.save(ignore_permissions=True)
            frappe.db.commit()
            if history_id:
                v1.update_history_summary_counts_safe(history_id)
        except Exception:
            frappe.log_error(
                message="Failed to save final status for {0}".format(log_id),
                title="Supplier Statement V2",
            )


# ---------------------------------------------------------------------------
# Whitelisted API -- status, retries, history
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_pdf_generation_status(filters=None, history_id=None):
    """Status rows for a V2 history (falls back to the latest V2 history)."""
    if history_id:
        return frappe.call(
            "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status",
            filters=filters,
            history_id=history_id,
        )

    filters = normalize_filters(filters) if filters else {}

    conditions = {
        "supplier_statement": 1,
        "statement_layout_v2": 1,
        "party_type": "Supplier",
    }
    for key in ("company", "party_group", "party", "from_date", "to_date"):
        if filters.get(key):
            conditions[key] = filters.get(key)

    history = frappe.get_all(
        "Statement Generation History",
        filters=conditions,
        fields=["name"],
        order_by="generation_time desc",
        limit=1,
    )
    if not history:
        return []

    return frappe.call(
        "agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.get_pdf_generation_status",
        history_id=history[0].name,
    )


@frappe.whitelist()
def get_statement_generation_history(from_date=None, to_date=None, party_name=None, company=None):
    """V2 generation history only."""
    conditions = {"supplier_statement": 1, "statement_layout_v2": 1}

    if from_date and to_date:
        conditions["from_date"] = ["between", [from_date, to_date]]
    elif from_date:
        conditions["from_date"] = [">=", from_date]
    elif to_date:
        conditions["to_date"] = ["<=", to_date]

    if company:
        conditions["company"] = company

    if party_name:
        parents = frappe.get_all(
            "Statement Generation History Item",
            filters={"party_name": ["like", "%{0}%".format(party_name)]},
            pluck="parent",
            distinct=True,
        )
        if not parents:
            return []
        conditions["name"] = ["in", parents]

    return frappe.get_all(
        "Statement Generation History",
        filters=conditions,
        fields=[
            "name",
            "company",
            "party_type",
            "party_group",
            "party",
            "from_date",
            "to_date",
            "created_by_user",
            "generation_time",
            "total_parties",
            "completed_count",
            "failed_count",
            "whatsapp_sent_count",
            "description",
        ],
        order_by="generation_time desc",
        limit=100,
    )


@frappe.whitelist()
def retry_failed_pdf(log_id):
    """Re-queue a single failed V2 job."""
    try:
        log_doc = frappe.get_doc("PDF Generator Log", log_id)

        if log_doc.status != "Failed":
            return {"error": "Can only retry failed jobs"}

        log_doc.status = "Queued"
        log_doc.error_message = ""
        log_doc.save(ignore_permissions=True)

        history_id = log_doc.get("statement_generation_history")
        if history_id:
            v1.update_history_item_status_safe(history_id, log_id, "Queued")

        safe_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30]
        frappe.enqueue(
            method=generate_single_supplier_pdf_v2,
            log_id=log_id,
            supplier_name=log_doc.party_name,
            history_id=history_id,
            job_id="pdf-gen-{0}".format(log_id),
            deduplicate=True,
            timeout=300,
            is_async=True,
        )

        frappe.db.commit()
        return {"success": "Supplier statement V2 retry queued"}

    except Exception as e:
        return {"error": str(e)[:200]}


@frappe.whitelist()
def retry_all_failed_pdfs(history_id):
    """Re-queue every failed job of a V2 history."""
    if not history_id:
        return {"error": "No history_id provided"}

    try:
        history_doc = frappe.get_doc("Statement Generation History", history_id)
        failed = [item.pdf_generator_log for item in history_doc.pdf_generator_logs if item.status == "Failed"]

        retried = 0
        skipped = 0
        for log_id in failed:
            try:
                log_doc = frappe.get_doc("PDF Generator Log", log_id)
                if log_doc.status != "Failed":
                    skipped += 1
                    continue

                log_doc.status = "Queued"
                log_doc.error_message = ""
                log_doc.save(ignore_permissions=True)
                v1.update_history_item_status_safe(history_id, log_id, "Queued")

                safe_name = frappe.scrub(log_doc.party_name).replace("_", "-")[:30]
                frappe.enqueue(
                    method=generate_single_supplier_pdf_v2,
                    log_id=log_id,
                    supplier_name=log_doc.party_name,
                    history_id=history_id,
                    job_id="pdf-gen-{0}".format(log_id),
                    deduplicate=True,
                    timeout=300,
                    is_async=True,
                )
                retried += 1
            except Exception:
                frappe.log_error(
                    message="V2 retry-all could not process log {0}: {1}".format(
                        log_id, frappe.get_traceback()
                    ),
                    title="Supplier Statement V2 Retry",
                )
                skipped += 1

        v1.update_history_summary_counts_safe(history_id)
        frappe.db.commit()
        return {
            "success": "Retried {0} failed jobs, skipped {1}".format(retried, skipped),
            "retried": retried,
            "skipped": skipped,
        }
    except Exception as e:
        return {"error": str(e)[:200]}


@frappe.whitelist()
def retry_all_queued_and_failed_pdf_jobs_for_history(history_id):
    """Re-queue every queued/failed job of a V2 history."""
    retried = 0
    skipped = 0

    logs = frappe.get_all(
        "PDF Generator Log",
        filters={
            "statement_generation_history": history_id,
            "status": ["in", ["Queued", "Failed"]],
        },
        fields=["name", "party_name", "statement_generation_history"],
    )

    for log in logs:
        try:
            frappe.enqueue(
                method=generate_single_supplier_pdf_v2,
                log_id=log["name"],
                supplier_name=log["party_name"],
                history_id=log["statement_generation_history"],
                job_id="pdf-gen-{0}".format(log["name"]),
                deduplicate=True,
                timeout=300,
                is_async=True,
            )
            retried += 1
        except Exception as e:
            frappe.logger("pdf_generation").error(
                "Failed to re-queue supplier V2 log {0}: {1}".format(log["name"], e)
            )
            skipped += 1

    return {
        "success": "Re-queued {0} jobs, skipped {1}".format(retried, skipped),
        "retried": retried,
        "skipped": skipped,
    }


@frappe.whitelist()
def cancel_whatsapp_job(job_id=None, job_name=None):
    return frappe.call(
        method="agricultural_marketing.agricultural_marketing.page.statement_forms.statement_forms.cancel_whatsapp_job",
        args={"job_id": job_id, "job_name": job_name},
    )

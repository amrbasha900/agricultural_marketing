import frappe
from frappe.utils import flt,cint,get_number_format_info
import base64
import json
from frappe.utils import get_site_path
from datetime import datetime

def get_currency_in_arabic(amount, currency):
    return money_in_words(amount, currency)

def money_in_words(
    number: str | float | int,
    main_currency: str | None = None,
    fraction_currency: str | None = None,
):
    """
    Returns string in words with currency and fraction currency.
    """
    from frappe.utils import get_defaults

    _ = frappe._

    try:
        # note: `flt` returns 0 for invalid input and we don't want that
        number = float(number)
    except ValueError:
        return ""

    number = flt(number)
    if number < 0:
        return ""

    d = get_defaults()
    if not main_currency:
        main_currency = d.get("currency", "INR")
    if not fraction_currency:
        fraction_currency = frappe.db.get_value("Currency", main_currency, "fraction", cache=True) or _(
            "Cent"
        )

    number_format = (
        frappe.db.get_value("Currency", main_currency, "number_format", cache=True)
        or frappe.db.get_default("number_format")
        or "#,###.##"
    )

    fraction_length = get_number_format_info(number_format)[2]

    n = f"%.{fraction_length}f" % number

    numbers = n.split(".")
    main, fraction = numbers if len(numbers) > 1 else [n, "00"]

    if len(fraction) < fraction_length:
        zeros = "0" * (fraction_length - len(fraction))
        fraction += zeros

    in_million = True
    if number_format == "#,##,###.##":
        in_million = False

    # 0.00
    if main == "0" and fraction in ["00", "000"]:
        out = _("ريال سعودي", context="Currency") + " " + _("Zero")
    # 0.XX
    elif main == "0":
        out = in_words(fraction, in_million).title() + " " + fraction_currency
    else:
        out =in_words(main, in_million).title() + " " + _("ريال سعودي", context="Currency")
        if cint(fraction):
            out = (
                out + " " + _("و") + " " + in_words(fraction, in_million).title() + " " + "هللة"
            )

    return out + " " + _("  فقط لاغير")


#
# convert number to words
#
def in_words(integer: int, in_million=True) -> str:
    """
    Returns string in words for the given integer.
    """
    from num2words import num2words

    locale = "ar" 
    integer = int(integer)
    try:
        ret = num2words(integer, lang=locale)
    except NotImplementedError:
        ret = num2words(integer, lang="en")
    except OverflowError:
        ret = num2words(integer, lang="en")
    return ret.replace("-", " ")


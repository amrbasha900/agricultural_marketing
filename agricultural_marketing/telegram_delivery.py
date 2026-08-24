"""Sending statement PDFs to parties over Telegram.

The WhatsApp path in this app owns its whole pipeline: batching, pacing, retry
and status, spread across the statement pages. Telegram does not need any of
that rebuilt -- the Reflection Telegram app already has a paced, retrying,
logged queue -- so this module is only the bridge. It picks the eligible PDF
logs, works out who each one goes to, hands them to `Telegram Outbox`, and
reads the resulting statuses back into Statement Generation History.

One module rather than a copy per page: the three statement pages call in here
instead of each carrying their own version.

Requires the `reflection_telegram` app. Everything degrades to "disabled" when
it is absent, so this app still installs on sites without it.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate

SETTINGS = "Agriculture Settings"

# Mirrors the WhatsApp vocabulary, plus Skipped -- a party with no Telegram chat
# has not failed, and lumping them in with failures makes "retry failed"
# meaningless.
STATUS_NOT_CREATED = "Not Created"
STATUS_QUEUED = "Queued"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"
STATUS_SKIPPED = "Skipped"

# Telegram Outbox status -> ours.
OUTBOX_STATUS_MAP = {
	"Queued": STATUS_QUEUED,
	"Sending": STATUS_QUEUED,
	"Sent": STATUS_SENT,
	"Failed": STATUS_FAILED,
	"Cancelled": STATUS_NOT_CREATED,
}

DEFAULT_TEMPLATE = "كشف حساب {party_name}\nمن {from_date} إلى {to_date}"


# --------------------------------------------------------------- availability


def app_installed() -> bool:
	return "reflection_telegram" in frappe.get_installed_apps()


@frappe.whitelist()
def is_enabled() -> bool:
	"""Whether the statement pages should show Telegram at all."""
	if not app_installed():
		return False

	return bool(cint(frappe.db.get_single_value(SETTINGS, "enable_telegram_reports")))


@frappe.whitelist()
def get_config() -> dict:
	"""What the pages need to decide how to render the Telegram controls."""
	if not app_installed():
		return {"enabled": False, "reason": _("The Reflection Telegram app is not installed")}

	settings = frappe.get_cached_doc(SETTINGS)
	if not cint(settings.enable_telegram_reports):
		return {"enabled": False, "reason": _("Telegram reports are turned off in Agriculture Settings")}

	if not settings.telegram_settings:
		return {"enabled": False, "reason": _("No Telegram bot is selected in Agriculture Settings")}

	return {
		"enabled": True,
		"bot": settings.telegram_settings,
		"rate": cint(settings.telegram_messages_per_minute),
	}


def _require_enabled() -> dict:
	config = get_config()
	if not config.get("enabled"):
		frappe.throw(config.get("reason") or _("Telegram reports are not available"))
	return config


# ----------------------------------------------------------------- recipients


def resolve_recipient(party_type: str, party: str, bot: str) -> tuple[str | None, str | None]:
	"""Find the Telegram User Settings record for a party.

	Returns (recipient, skip_reason). A missing link or a blocked chat is a skip,
	not a failure: no number of retries fixes either, and the fix is to print the
	party a QR code.
	"""
	from reflection_telegram import api

	recipient = api.resolve_party(party_type, party, bot)
	if not recipient:
		return None, _("{0} has not scanned a Telegram QR code yet").format(party)

	status = frappe.db.get_value("Telegram User Settings", recipient, "chat_status")
	if status in ("Blocked", "Left"):
		return None, _("{0} has blocked the bot").format(party)

	return recipient, None


def build_caption(log) -> str:
	template = frappe.db.get_single_value(SETTINGS, "telegram_message_template") or DEFAULT_TEMPLATE

	try:
		return template.format(
			party_name=log.get("party_name") or "",
			party=log.get("party_name") or "",
			from_date=frappe.format_value(log.get("from_date"), {"fieldtype": "Date"})
			if log.get("from_date")
			else "",
			to_date=frappe.format_value(log.get("to_date"), {"fieldtype": "Date"})
			if log.get("to_date")
			else "",
			company=log.get("company") or "",
		)
	except (KeyError, IndexError):
		# A typo in the template must not stop a run of statements going out.
		frappe.log_error(
			title="Agriculture: bad Telegram message template", message=frappe.get_traceback()
		)
		return DEFAULT_TEMPLATE.format(
			party_name=log.get("party_name") or "", from_date="", to_date=""
		)


# --------------------------------------------------------------------- queue


def _candidate_logs(history_id=None, log_ids=None, retry_failed=0) -> list[dict]:
	"""The PDF logs a run should consider, before recipients are resolved."""
	names = []

	if history_id:
		names = [
			item.pdf_generator_log
			for item in frappe.get_all(
				"Statement Generation History Item",
				filters={"parent": history_id},
				fields=["pdf_generator_log", "status"],
			)
			if item.status == "Completed" and item.pdf_generator_log
		]
	elif log_ids:
		names = _as_list(log_ids)

	if not names:
		return []

	# Re-read status from the log rather than trusting the history item: the two
	# can drift when a PDF is regenerated on its own.
	eligible = {STATUS_NOT_CREATED, None, ""}
	if cint(retry_failed):
		eligible |= {STATUS_FAILED, STATUS_SKIPPED}

	return [
		log
		for log in frappe.get_all(
			"PDF Generator Log",
			filters={"name": ["in", names]},
			fields=[
				"name",
				"party_type",
				"party_name",
				"company",
				"from_date",
				"to_date",
				"pdf_file",
				"status",
				"telegram_status",
				"telegram_sent",
			],
		)
		if log.status == "Completed"
		and log.pdf_file
		and not cint(log.telegram_sent)
		and log.telegram_status in eligible
	]


@frappe.whitelist()
def queue_all(history_id=None, log_ids=None, retry_failed: int = 0) -> dict:
	"""Hand every eligible statement to the Telegram queue.

	Nothing is sent inline. `Telegram Outbox` paces the run and retries what is
	worth retrying, and `refresh_status` reads the outcome back.
	"""
	config = _require_enabled()
	bot = config["bot"]

	logs = _candidate_logs(history_id, log_ids, retry_failed)
	if not logs:
		return {"queued": 0, "skipped": 0, "message": _("Nothing eligible to send")}

	messages, skipped = [], []

	for log in logs:
		recipient, reason = resolve_recipient(log.party_type or "Supplier", log.party_name, bot)

		if not recipient:
			skipped.append((log.name, reason))
			continue

		messages.append(
			{
				"telegram_user": recipient,
				"message": build_caption(log),
				"file_url": log.pdf_file,
				"reference_doctype": "PDF Generator Log",
				"reference_name": log.name,
			}
		)

	for log_name, reason in skipped:
		_set_status(history_id, log_name, STATUS_SKIPPED, error=reason)

	broadcast = None
	if messages:
		from reflection_telegram import api

		result = api.send_bulk(
			messages,
			telegram_settings=bot,
			title=_("Statements for {0}").format(history_id or _("selected parties")),
			rate=config.get("rate") or 0,
		)
		broadcast = result.get("broadcast")
		_attach_outbox_rows(history_id, broadcast)

	# Only when there is something to record: an empty values dict builds an
	# UPDATE with no columns, which MySQL rejects outright. Sending to a party
	# that turns out to be unlinked produces exactly that -- no broadcast, and
	# nothing to write.
	if history_id and broadcast:
		frappe.db.set_value(
			"Statement Generation History",
			history_id,
			{"telegram_broadcast": broadcast},
			update_modified=False,
		)

	frappe.db.commit()
	_refresh_counts(history_id)

	return {
		"queued": len(messages),
		"skipped": len(skipped),
		"broadcast": broadcast,
		"message": _("Queued {0}, skipped {1}").format(len(messages), len(skipped)),
	}


@frappe.whitelist()
def queue_for_party(log_id: str) -> dict:
	"""Send one statement. Same path as a bulk run, so status behaves the same."""
	history_id = frappe.db.get_value("PDF Generator Log", log_id, "statement_generation_history")
	return queue_all(history_id=history_id, log_ids=[log_id], retry_failed=1)


def _attach_outbox_rows(history_id, broadcast):
	"""Record which queue row carries which statement.

	The link is what lets `refresh_status` answer without guessing, and what makes
	a stuck message traceable from the report back to the queue.
	"""
	if not broadcast:
		return

	for row in frappe.get_all(
		"Telegram Outbox",
		filters={"broadcast": broadcast},
		fields=["name", "reference_name"],
	):
		if not row.reference_name:
			continue

		frappe.db.set_value(
			"PDF Generator Log",
			row.reference_name,
			{"telegram_outbox": row.name, "telegram_status": STATUS_QUEUED, "telegram_error": None},
			update_modified=False,
		)
		_set_history_item(history_id, row.reference_name, STATUS_QUEUED, outbox=row.name, error=None)


def _as_list(value) -> list[str]:
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			value = [value]
	return list(value or [])


# -------------------------------------------------------------------- status


def _set_history_item(history_id, log_id, status, outbox=None, error=None):
	if not history_id:
		return

	item = frappe.db.get_value(
		"Statement Generation History Item",
		{"parent": history_id, "pdf_generator_log": log_id},
		"name",
	)
	if not item:
		return

	values = {"telegram_status": status, "telegram_error": error}
	if outbox:
		values["telegram_outbox"] = outbox

	frappe.db.set_value("Statement Generation History Item", item, values, update_modified=False)


def _set_status(history_id, log_id, status, outbox=None, error=None):
	"""Write a status to both places the pages read it from."""
	values = {"telegram_status": status, "telegram_error": error}
	if outbox:
		values["telegram_outbox"] = outbox
	if status == STATUS_SENT:
		values["telegram_sent"] = 1

	frappe.db.set_value("PDF Generator Log", log_id, values, update_modified=False)
	_set_history_item(history_id, log_id, status, outbox=outbox, error=error)


@frappe.whitelist()
def refresh_status(history_id: str) -> dict:
	"""Pull current queue statuses into the history, and return the counts.

	Pull rather than push: `Telegram Outbox` updates itself with
	`update_modified=False` for speed, which means no document events fire, so
	nothing can notify us. The pages poll this while a run is in flight.
	"""
	if not app_installed():
		return {"enabled": False}

	items = frappe.get_all(
		"Statement Generation History Item",
		filters={"parent": history_id},
		fields=["name", "pdf_generator_log", "telegram_status", "telegram_outbox"],
	)
	tracked = {item.telegram_outbox: item for item in items if item.telegram_outbox}

	if tracked:
		for row in frappe.get_all(
			"Telegram Outbox",
			filters={"name": ["in", list(tracked)]},
			fields=["name", "status", "error"],
		):
			item = tracked[row.name]
			status = OUTBOX_STATUS_MAP.get(row.status, STATUS_QUEUED)

			if status == item.telegram_status:
				continue

			_set_status(history_id, item.pdf_generator_log, status, error=row.error)

		frappe.db.commit()

	return _refresh_counts(history_id)


def _refresh_counts(history_id) -> dict:
	if not history_id:
		return {}

	counts = {
		row.telegram_status or STATUS_NOT_CREATED: row.n
		for row in frappe.get_all(
			"Statement Generation History Item",
			filters={"parent": history_id},
			fields=["telegram_status", "count(name) as n"],
			group_by="telegram_status",
		)
	}

	sent = counts.get(STATUS_SENT, 0)
	skipped = counts.get(STATUS_SKIPPED, 0)

	frappe.db.set_value(
		"Statement Generation History",
		history_id,
		{"telegram_sent_count": sent, "telegram_skipped_count": skipped},
		update_modified=False,
	)
	frappe.db.commit()

	return {
		"enabled": True,
		"sent": sent,
		"skipped": skipped,
		"failed": counts.get(STATUS_FAILED, 0),
		"queued": counts.get(STATUS_QUEUED, 0),
		"not_created": counts.get(STATUS_NOT_CREATED, 0),
		"running": counts.get(STATUS_QUEUED, 0) > 0,
	}


@frappe.whitelist()
def cancel(history_id: str) -> dict:
	"""Stop whatever has not gone out yet. Sent messages cannot be recalled."""
	broadcast = frappe.db.get_value("Statement Generation History", history_id, "telegram_broadcast")
	if not broadcast:
		return {"cancelled": 0}

	frappe.get_doc("Telegram Broadcast", broadcast).cancel_pending()
	return refresh_status(history_id)


@frappe.whitelist()
def get_unlinked_parties(history_id: str) -> list[dict]:
	"""Parties in this run with no Telegram chat, so they can be given a QR code."""
	config = get_config()
	if not config.get("enabled"):
		return []

	rows = frappe.get_all(
		"Statement Generation History Item",
		filters={"parent": history_id, "telegram_status": STATUS_SKIPPED},
		fields=["pdf_generator_log", "party_name", "telegram_error"],
	)

	return [
		{
			"party": row.party_name,
			"log_id": row.pdf_generator_log,
			"reason": row.telegram_error,
		}
		for row in rows
	]


@frappe.whitelist()
def annotate_link_state(logs, party_type: str = None) -> list[dict]:
	"""Mark each row with whether its party can receive on Telegram.

	Send status alone cannot answer this: an unlinked party reads "Not Created",
	exactly like one that simply has not been sent to yet. The pages need to show
	the difference before anyone clicks, so this resolves it up front -- in one
	query for the whole page rather than one per row.
	"""
	logs = logs or []
	if not logs or not is_enabled():
		return logs

	bot = frappe.db.get_single_value(SETTINGS, "telegram_settings")
	if not bot:
		return logs

	parties = {cstr(log.get("party_name")) for log in logs if log.get("party_name")}
	if not parties:
		return logs

	linked = {
		row.telegram_user
		for row in frappe.get_all(
			"Telegram User Settings",
			filters={
				"telegram_settings": bot,
				"telegram_user": ["in", list(parties)],
				"telegram_chat_id": ["is", "set"],
				"chat_status": ["not in", ["Blocked", "Left"]],
			},
			fields=["telegram_user"],
		)
	}

	for log in logs:
		log["telegram_linked"] = 1 if cstr(log.get("party_name")) in linked else 0

	return logs

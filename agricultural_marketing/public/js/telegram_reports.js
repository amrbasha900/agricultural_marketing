/**
 * Telegram delivery controls, shared by the three statement pages.
 *
 * The WhatsApp path grew a copy per page. This is deliberately one file the
 * pages call into instead: a page adds a placeholder to its toolbar and calls
 * `AgriTelegram.bind()` once, and everything else -- whether Telegram is on at
 * all, the buttons, polling, the per-row badge -- lives here.
 *
 * Loaded via app_include_js, so `window.AgriTelegram` exists on every desk page.
 */

frappe.provide('AgriTelegram');

(function () {
	const METHOD = 'agricultural_marketing.telegram_delivery';
	const STATUS_COLORS = {
		Sent: 'green',
		Queued: 'orange',
		Failed: 'red',
		Skipped: 'gray',
		'Not Created': 'gray',
	};

	let configPromise = null;
	let config = null;
	let pollTimer = null;
	// Redrawing the page re-runs bind(), which refreshes again. Without this the
	// two would call each other forever, so onRefresh only fires on real change.
	let lastSignature = null;
	// Set by the most recent bind(), so the delegated row handler knows how to
	// redraw whichever page it fired on.
	let activeRedraw = null;

	/** Resolved once per page load; every caller shares the same request. */
	function loadConfig() {
		if (!configPromise) {
			configPromise = frappe
				.call({ method: `${METHOD}.get_config` })
				.then((r) => {
					config = r.message || { enabled: false };
					return config;
				})
				.catch(() => {
					config = { enabled: false };
					return config;
				});
		}
		return configPromise;
	}

	/**
	 * Placeholder for a page's toolbar. Returns markup synchronously because
	 * pages build their HTML in one string; it is filled in once the config
	 * request resolves, and stays empty when Telegram is off.
	 */
	function actionsHtml() {
		return '<span id="agri-telegram-actions"></span>';
	}

	// The Telegram mark, drawn inline: no icon-font dependency, and `currentColor`
	// lets it inherit whatever colour the chip is using.
	const PLANE =
		'M9.04 15.47L8.7 20.2c.5 0 .72-.21.98-.47l2.35-2.24 4.87 3.56c.9.5 1.53.24 1.77-.83' +
		'l3.2-15.02c.29-1.33-.48-1.85-1.35-1.53L1.2 9.9c-1.3.5-1.28 1.23-.22 1.56l4.96 1.55' +
		'L17.4 6.1c.54-.35 1.03-.16.63.2z';

	/**
	 * Status vocabulary, deliberately the same glyphs the WhatsApp column uses --
	 * clock for waiting, tick for sent, crossed circle for failed -- so one column
	 * teaches you how to read the other. The plane keeps them apart at a glance.
	 *
	 * There is no delivered or read state: Telegram gives bots no receipt beyond
	 * "accepted", so a single tick is as far as this can honestly go.
	 */
	const STATES = {
		unlinked: { icon: 'fa-ban', tone: 'gray', faded: true, label: __('No Telegram user') },
		'Not Created': { icon: 'fa-circle-o', tone: 'gray', label: __('Not sent') },
		Queued: { icon: 'fa-clock-o', tone: 'orange', label: __('Queued') },
		Sent: { icon: 'fa-check', tone: 'green', label: __('Sent') },
		Failed: { icon: 'fa-times-circle', tone: 'red', label: __('Failed') },
		Skipped: { icon: 'fa-ban', tone: 'gray', faded: true, label: __('Skipped') },
	};

	function injectStyles() {
		if (document.getElementById('agri-tg-styles')) return;

		// Only sizing. The colours come from Frappe's own indicator-pill classes so
		// the chip matches the rest of the desk and follows the theme.
		$(`<style id="agri-tg-styles">
			.agri-tg { gap: 4px; white-space: nowrap; padding: 3px 8px; height: auto; }
			.agri-tg svg { width: 12px; height: 12px; flex: 0 0 auto; }
			.agri-tg .fa { font-size: 11px; }
		</style>`).appendTo(document.head);
	}

	function chip(state, tooltip) {
		injectStyles();

		// The plane fades rather than being struck through: at 12px a diagonal
		// stroke is illegible, and fa-ban already says "not available".
		return `<span class="indicator-pill no-indicator-dot ${state.tone} agri-tg" title="${frappe.utils.escape_html(tooltip)}">
			<svg viewBox="0 0 24 24" fill="currentColor"${state.faded ? ' opacity="0.45"' : ''}>
				<path d="${PLANE}"/>
			</svg>
			<i class="fa ${state.icon}" aria-hidden="true"></i>
			<span>${state.label}</span>
		</span>`;
	}

	/**
	 * One status chip per row.
	 *
	 * A party with no linked chat is called out before anyone clicks: send status
	 * alone cannot show it, because an unlinked party reads "Not Created" exactly
	 * like one nobody has sent to yet.
	 */
	function badge(log) {
		if (!config || !config.enabled) return '';

		// undefined means the row predates link annotation; fall back to status.
		if (log.telegram_linked === 0) {
			return chip(STATES.unlinked, __('No Telegram user linked — print this party a QR code'));
		}

		const status = log.telegram_status || 'Not Created';
		const state = STATES[status] || STATES['Not Created'];

		let tooltip = `${__('Telegram')}: ${state.label}`;
		if (status === 'Sent') {
			tooltip += ` — ${__('Telegram confirms it accepted the message; it gives bots no delivery or read receipt.')}`;
		}
		if (log.telegram_error) {
			tooltip += ` — ${log.telegram_error}`;
		}

		return chip(state, tooltip);
	}

	/**
	 * Per-row button, mirroring the WhatsApp one: send when nothing has been
	 * tried, retry when it failed or was skipped, plain text once it is sent.
	 */
	function rowButton(log) {
		if (!config || !config.enabled) return '';
		if (log.status !== 'Completed' || !log.pdf_file) return '';

		// Offering "Send" to a party with no chat would just queue a guaranteed
		// skip. Point at the thing that actually fixes it instead.
		if (log.telegram_linked === 0) {
			return ` <button class="btn btn-sm btn-default agri-telegram-qr" title="${__('No Telegram user linked')}">${__('Telegram QR')}</button>`;
		}

		const status = log.telegram_status || 'Not Created';

		if (status === 'Sent') {
			return `<span class="text-success" style="margin-inline-start:6px">${__('Telegram Sent')}</span>`;
		}

		if (status === 'Queued') {
			return `<span class="text-muted" style="margin-inline-start:6px">${__('Telegram Queued')}</span>`;
		}

		const label = status === 'Not Created' ? __('Send Telegram') : __('Retry Telegram');
		const style = status === 'Not Created' ? 'btn-primary' : 'btn-warning';
		let html = ` <button class="btn btn-sm ${style} agri-send-telegram" data-log-id="${log.name}">${label}</button>`;

		if (log.telegram_error) {
			html += `<small class="text-muted" style="margin-inline-start:6px">${frappe.utils.escape_html(log.telegram_error)}</small>`;
		}

		return html;
	}

	/** Options for a Telegram status filter, matching the WhatsApp one. */
	function filterHtml(id) {
		if (!config || !config.enabled) return '';

		const options = ['', 'Not Created', 'Queued', 'Sent', 'Failed', 'Skipped'];
		return `
			<select class="form-control" id="${id || 'telegram-status-filter'}">
				${options
					.map(
						(o) =>
							`<option value="${o}">${o ? __(o) : __('All Telegram Statuses')}</option>`
					)
					.join('')}
			</select>`;
	}

	// Delegated once, at module load: the tables are rebuilt on every refresh, and
	// rebinding per render is how duplicate handlers creep in.
	$(document).on('click', '.agri-telegram-qr', function () {
		frappe.set_route('telegram-qr');
	});

	$(document).on('click', '.agri-send-telegram', function () {
		const $button = $(this);
		const logId = $button.data('log-id');
		if (!logId) return;

		$button.prop('disabled', true);

		frappe.call({
			method: `${METHOD}.queue_for_party`,
			args: { log_id: logId },
			callback: (r) => {
				const res = r.message || {};

				if (res.queued) {
					frappe.show_alert({ message: __('Queued for Telegram'), indicator: 'green' });
				} else {
					// A skip is the common case here, and the reason is the useful part.
					frappe.msgprint({
						title: __('Not sent'),
						indicator: 'orange',
						message:
							(res.skipped
								? __('This party has no linked Telegram chat. Print them a QR code first.')
								: res.message) || __('Nothing was queued'),
					});
				}

				if (activeRedraw) activeRedraw();
				else $button.prop('disabled', false);
			},
			error: () => $button.prop('disabled', false),
		});
	});

	/**
	 * Wire the controls into a page.
	 *
	 * @param {function} getHistoryId returns the history currently on screen
	 * @param {function} onRefresh    called after anything changes, to redraw
	 */
	function bind({ getHistoryId, onRefresh }) {
		activeRedraw = onRefresh ? () => onRefresh({}) : null;

		loadConfig().then((cfg) => {
			const $slot = $('#agri-telegram-actions');
			if (!$slot.length) return;

			if (!cfg.enabled) {
				$slot.empty();
				return;
			}

			$slot.html(`
				<button class="btn btn-sm sf-action-btn btn-primary" id="tg-send-all" style="margin:5px">
					${__('Send All Telegram')}
				</button>
				<button class="btn btn-sm sf-action-btn btn-outline-primary" id="tg-retry-failed" style="margin:5px">
					${__('Retry Telegram Failed')}
				</button>
				<button class="btn btn-sm sf-action-btn btn-outline-danger" id="tg-cancel" style="margin:5px">
					${__('Cancel Telegram Queue')}
				</button>
				<span id="tg-counts" class="text-muted" style="margin-inline-start:6px"></span>
			`);

			$slot.find('#tg-send-all').on('click', () => queue(getHistoryId, onRefresh, 0));
			$slot.find('#tg-retry-failed').on('click', () => queue(getHistoryId, onRefresh, 1));
			$slot.find('#tg-cancel').on('click', () => cancel(getHistoryId, onRefresh));

			refresh(getHistoryId, onRefresh);
		});
	}

	function queue(getHistoryId, onRefresh, retryFailed) {
		const historyId = getHistoryId && getHistoryId();
		if (!historyId) {
			frappe.msgprint(__('No history loaded'));
			return;
		}

		const question = retryFailed
			? __('Send Telegram again for everything that failed or was skipped?')
			: __('Send the statements to Telegram for every completed party?');

		frappe.confirm(question, () => {
			frappe.call({
				method: `${METHOD}.queue_all`,
				args: { history_id: historyId, retry_failed: retryFailed },
				freeze: true,
				freeze_message: __('Queueing...'),
				callback: (r) => {
					const res = r.message || {};
					frappe.show_alert(
						{
							message: __('Queued {0}, skipped {1}', [res.queued || 0, res.skipped || 0]),
							indicator: res.queued ? 'green' : 'orange',
						},
						7
					);

					// Skipped parties are the actionable half of the result, so say who
					// rather than leaving a bare count on screen.
					if (res.skipped) showSkipped(historyId);

					refresh(getHistoryId, onRefresh);
					startPolling(getHistoryId, onRefresh);
				},
			});
		});
	}

	function cancel(getHistoryId, onRefresh) {
		const historyId = getHistoryId && getHistoryId();
		if (!historyId) return;

		frappe.confirm(__('Stop the Telegram messages that have not gone out yet?'), () => {
			frappe.call({
				method: `${METHOD}.cancel`,
				args: { history_id: historyId },
				freeze: true,
				callback: () => refresh(getHistoryId, onRefresh),
			});
		});
	}

	function showSkipped(historyId) {
		frappe.call({
			method: `${METHOD}.get_unlinked_parties`,
			args: { history_id: historyId },
			callback: (r) => {
				const rows = r.message || [];
				if (!rows.length) return;

				frappe.msgprint({
					title: __('Skipped — no Telegram chat'),
					indicator: 'orange',
					message: `
						<p>${__('These parties were not sent to. They are not failures — print them a QR code from the Telegram QR Codes page.')}</p>
						<table class="table table-bordered" style="margin-bottom:10px">
							<thead><tr><th>${__('Party')}</th><th>${__('Reason')}</th></tr></thead>
							<tbody>
								${rows
									.map(
										(row) =>
											`<tr><td>${frappe.utils.escape_html(row.party || '')}</td>
											     <td>${frappe.utils.escape_html(row.reason || '')}</td></tr>`
									)
									.join('')}
							</tbody>
						</table>
						<button class="btn btn-sm btn-primary" onclick="frappe.set_route('telegram-qr')">
							${__('Open Telegram QR Codes')}
						</button>`,
				});
			},
		});
	}

	function refresh(getHistoryId, onRefresh) {
		const historyId = getHistoryId && getHistoryId();
		if (!historyId || !config || !config.enabled) return;

		frappe.call({
			method: `${METHOD}.refresh_status`,
			args: { history_id: historyId },
			callback: (r) => {
				const counts = r.message || {};
				$('#tg-counts').text(
					__('Telegram — sent {0}, queued {1}, failed {2}, skipped {3}', [
						counts.sent || 0,
						counts.queued || 0,
						counts.failed || 0,
						counts.skipped || 0,
					])
				);

				$('#tg-cancel').prop('disabled', !counts.running);

				const signature = [counts.sent, counts.queued, counts.failed, counts.skipped].join('|');
				const changed = lastSignature !== null && lastSignature !== signature;
				lastSignature = signature;

				if (changed && onRefresh) onRefresh(counts);
				if (!counts.running) stopPolling();
			},
		});
	}

	/** Poll only while something is in flight; the queue drains a minute at a time. */
	function startPolling(getHistoryId, onRefresh) {
		stopPolling();
		pollTimer = setInterval(() => refresh(getHistoryId, onRefresh), 15000);
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	Object.assign(AgriTelegram, {
		loadConfig,
		actionsHtml,
		badge,
		rowButton,
		filterHtml,
		bind,
		refresh,
		stopPolling,
		isEnabled: () => !!(config && config.enabled),
	});
})();

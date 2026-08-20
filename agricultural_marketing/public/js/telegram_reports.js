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

	function badge(log) {
		if (!config || !config.enabled) return '';

		const status = log.telegram_status || 'Not Created';
		const colour = STATUS_COLORS[status] || 'gray';
		const title = log.telegram_error ? ` title="${frappe.utils.escape_html(log.telegram_error)}"` : '';

		return `<span class="indicator-pill ${colour}"${title}>${__('TG')}: ${__(status)}</span>`;
	}

	/**
	 * Wire the controls into a page.
	 *
	 * @param {function} getHistoryId returns the history currently on screen
	 * @param {function} onRefresh    called after anything changes, to redraw
	 */
	function bind({ getHistoryId, onRefresh }) {
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
		bind,
		refresh,
		stopPolling,
		isEnabled: () => !!(config && config.enabled),
	});
})();

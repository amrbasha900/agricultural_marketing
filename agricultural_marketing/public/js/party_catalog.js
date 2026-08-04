// Client-side catalog of the parties and items used by the Bulk Invoice Form.
//
// Every keystroke in a Link field costs a POST to frappe.desk.search.search_link
// (see frappe/public/js/frappe/form/controls/link.js). Frappe's own $input.cache
// is per input element, so each new grid row starts cold and it never actually
// suppresses the request. With well under 2,000 eligible records in total it is
// far cheaper to hold the whole catalog in the browser and search it locally.
//
// Server side: agricultural_marketing/party_catalog.py

frappe.provide("agrimkt.party_catalog");

(() => {
	// Bump to discard every cached browser copy. Must match party_catalog.py.
	const CATALOG_VERSION = 1;

	const SET_NAMES = ["supplier", "customer", "pamper", "item"];

	// Used only when the local catalog has no match for what was typed -- see
	// search_with_fallback(). Mirrors setup_child_table_filters() exactly.
	const LIVE_SEARCH_ARGS = {
		supplier: { doctype: "Supplier", filters: { is_farmer: 1 } },
		customer: {
			doctype: "Customer",
			query: "agricultural_marketing.agricultural_marketing.doctype.bulk_invoice_form.bulk_invoice_form.get_filtered_customers",
		},
		pamper: { doctype: "Customer", filters: { is_pamper: 1, is_frozen: 0 } },
		item: { doctype: "Item", filters: { commission_item: 0, is_agriculture_item: 1 } },
	};

	// Namespaced per user so two people sharing a machine never read each
	// other's catalog. It still lives on disk until refreshed or cleared.
	function storage_key() {
		return `agrimkt:party_catalog:v${CATALOG_VERSION}:${frappe.session.user}`;
	}

	// -----------------------------------------------------------------------
	// Text normalisation
	// -----------------------------------------------------------------------
	//
	// Applied once per record when the catalog is loaded, and once per query.
	// Without it "احمد" does not match "أحمد" and a number typed on an Arabic
	// keypad does not match a code stored in ASCII digits.

	// Written as escapes: these are invisible or direction-flipping characters
	// that editors and diff tools like to mangle.
	const DIACRITICS = /[\u064B-\u0652\u0670\u0640]/g; // tashkeel, dagger alef, tatweel
	const ALEF = /[\u0623\u0625\u0622\u0671]/g; // أ إ آ ٱ
	const TEH_MARBUTA = /\u0629/g; // ة
	const YEH = /[\u0649\u06CC\u0626]/g; // ى ی ئ
	const WAW_HAMZA = /\u0624/g; // ؤ
	const ARABIC_INDIC = /[\u0660-\u0669]/g; // ٠-٩
	const EXT_ARABIC_INDIC = /[\u06F0-\u06F9]/g; // ۰-۹

	function normalize(text) {
		if (text === null || text === undefined) return "";

		return String(text)
			.toLowerCase()
			.replace(DIACRITICS, "")
			.replace(ALEF, "\u0627") // -> ا
			.replace(TEH_MARBUTA, "\u0647") // -> ه
			.replace(YEH, "\u064A") // -> ي
			.replace(WAW_HAMZA, "\u0648") // -> و
			.replace(ARABIC_INDIC, (d) => String.fromCharCode(d.charCodeAt(0) - 0x0660 + 48))
			.replace(EXT_ARABIC_INDIC, (d) => String.fromCharCode(d.charCodeAt(0) - 0x06f0 + 48))
			.replace(/\s+/g, " ")
			.trim();
	}

	// -----------------------------------------------------------------------
	// State
	// -----------------------------------------------------------------------

	// set name -> { records: [{code, label, ncode, nsearch}], by_code: Map }
	let indexes = null;
	let generated_on = null;
	let pending_fetch = null;

	function build_index(pairs) {
		// Server order is `ORDER BY name ASC`, and every search preserves it,
		// which is what makes typing "1" list 0001, 0011, ... 1000 in order.
		const records = pairs.map(([code, label]) => {
			const ncode = normalize(code);
			return {
				code: code,
				label: label || "",
				ncode: ncode,
				nsearch: label ? `${ncode} ${normalize(label)}` : ncode,
			};
		});

		const by_code = new Map();
		records.forEach((r) => by_code.set(r.code, r));

		return { records: records, by_code: by_code };
	}

	function build_indexes(payload) {
		const built = {};
		SET_NAMES.forEach((set_name) => {
			const pairs = (payload.sets && payload.sets[set_name]) || null;
			// A missing set means "no read permission"; keep it null so callers
			// fall through to a live search instead of showing an empty list.
			built[set_name] = pairs ? build_index(pairs) : null;
		});

		indexes = built;
		generated_on = payload.generated_on || null;
	}

	// -----------------------------------------------------------------------
	// Persistence
	// -----------------------------------------------------------------------

	function read_storage() {
		let raw;
		try {
			raw = window.localStorage.getItem(storage_key());
		} catch (e) {
			return null; // storage disabled (private mode, blocked cookies)
		}
		if (!raw) return null;

		try {
			const payload = JSON.parse(raw);
			if (payload && payload.version === CATALOG_VERSION && payload.sets) {
				return payload;
			}
		} catch (e) {
			// Corrupt entry -- drop it and refetch.
		}

		write_storage(null);
		return null;
	}

	function write_storage(payload) {
		try {
			if (payload === null) {
				window.localStorage.removeItem(storage_key());
			} else {
				window.localStorage.setItem(storage_key(), JSON.stringify(payload));
			}
		} catch (e) {
			// Over quota or storage disabled. The in-memory index still works
			// for this page load; it just will not survive a reload.
			console.warn("agrimkt: could not persist party catalog", e);
		}
	}

	function fetch_catalog() {
		if (pending_fetch) return pending_fetch;

		pending_fetch = frappe
			.call({ method: "agricultural_marketing.party_catalog.get_catalog", no_spinner: true })
			.then((r) => {
				const payload = r && r.message;
				if (!payload || !payload.sets) throw new Error("empty catalog response");

				build_indexes(payload);
				write_storage(payload);
				return payload;
			})
			.always(() => {
				pending_fetch = null;
			});

		return pending_fetch;
	}

	// -----------------------------------------------------------------------
	// Recently used
	// -----------------------------------------------------------------------
	//
	// What a field offers when it is focused with nothing typed yet. In a bulk
	// invoice the same handful of suppliers and customers repeat all day, so the
	// last few picks are a far better opening list than the first few codes.
	//
	// Kept in its own storage entry, and on its own version, so refreshing the
	// catalog does not cost the user their history.

	const MRU_VERSION = 1;
	const MRU_LIMIT = 20; // stored; only the first few are ever shown

	let mru = null; // { set name: [code, ...] }, most recent first

	function mru_key() {
		return `agrimkt:party_mru:v${MRU_VERSION}:${frappe.session.user}`;
	}

	function read_mru() {
		if (mru) return mru;

		try {
			mru = JSON.parse(window.localStorage.getItem(mru_key()));
		} catch (e) {
			mru = null;
		}

		if (!mru || typeof mru !== "object") mru = {};
		return mru;
	}

	function write_mru() {
		try {
			window.localStorage.setItem(mru_key(), JSON.stringify(mru));
		} catch (e) {
			// In-memory only for this page load.
		}
	}

	// -----------------------------------------------------------------------
	// Public API
	// -----------------------------------------------------------------------

	Object.assign(agrimkt.party_catalog, {
		normalize: normalize,

		/** True once the catalog is searchable in memory. */
		is_ready() {
			return Boolean(indexes);
		},

		/** When the cached copy was generated on the server, or null. */
		generated_on() {
			return generated_on;
		},

		/**
		 * Make the catalog available.
		 *
		 * Resolves immediately off localStorage when a copy is already stored --
		 * per the agreed design the cache is only refreshed on explicit request,
		 * so this costs zero requests on every load but the first.
		 */
		ready() {
			if (indexes) return Promise.resolve(true);

			const stored = read_storage();
			if (stored) {
				build_indexes(stored);
				return Promise.resolve(true);
			}

			return Promise.resolve(fetch_catalog()).then(
				() => true,
				() => false
			);
		},

		/** Discard the stored copy and download a fresh one. */
		refresh() {
			write_storage(null);
			indexes = null;
			generated_on = null;
			return Promise.resolve(fetch_catalog());
		},

		/** Forget the cached copy without downloading a replacement. */
		clear() {
			write_storage(null);
			indexes = null;
			generated_on = null;

			// This is the "leave nothing behind" entry point, so the pick
			// history goes too -- it names customers just as the catalog does.
			mru = {};
			try {
				window.localStorage.removeItem(mru_key());
			} catch (e) {
				// nothing to do
			}
		},

		/** Record a pick, so it leads the list next time the field is focused. */
		remember(set_name, code) {
			if (!code || !SET_NAMES.includes(set_name)) return;

			const store = read_mru();
			const without_it = (store[set_name] || []).filter((c) => c !== code);

			store[set_name] = [code].concat(without_it).slice(0, MRU_LIMIT);
			write_mru();
		},

		/**
		 * What to offer before anything is typed: the most recent picks, topped
		 * up from the head of the catalog (ascending by code) so the list is
		 * always full -- including the very first time, when there is no history.
		 *
		 * Recent entries carry `recent: true` so the UI can label them.
		 */
		suggestions(set_name, limit) {
			const index = indexes && indexes[set_name];
			if (!index) return null;

			limit = limit || 5;

			const out = [];
			const seen = new Set();

			for (const code of read_mru()[set_name] || []) {
				if (out.length >= limit) break;
				// A code picked before the catalog changed may be gone now;
				// skip it rather than offer a dead row.
				const record = index.by_code.get(code);
				if (!record || seen.has(code)) continue;

				seen.add(code);
				out.push(Object.assign({ recent: true }, record));
			}

			for (const record of index.records) {
				if (out.length >= limit) break;
				if (seen.has(record.code)) continue;

				seen.add(record.code);
				out.push(record);
			}

			return out;
		},

		/** Total number of cached records, for the status line. */
		count(set_name) {
			const index = indexes && indexes[set_name];
			return index ? index.records.length : 0;
		},

		/** Resolve a code to its cached record, or undefined. */
		get(set_name, code) {
			const index = indexes && indexes[set_name];
			return index ? index.by_code.get(code) : undefined;
		},

		/**
		 * Search the cached set.
		 *
		 * Matching is substring, on the code and the name, after normalisation;
		 * a query with several words requires all of them to match. Results are
		 * ordered: exact code, then codes that contain the query, then names
		 * that do -- each group ascending by code.
		 *
		 * Returns null (not []) when the set is not cached, so the caller can
		 * tell "nothing matched" apart from "nothing to match against".
		 */
		search(set_name, query, limit) {
			const index = indexes && indexes[set_name];
			if (!index) return null;

			limit = limit || 50;

			const nq = normalize(query);
			if (!nq) return index.records.slice(0, limit);

			const tokens = nq.split(" ");
			const exact = [];
			const by_code = [];
			const by_name = [];

			for (const record of index.records) {
				if (tokens.every((t) => record.ncode.includes(t))) {
					// Pin an exact code hit above the codes that merely contain it,
					// so typing a full code in full never buries it.
					(record.ncode === nq ? exact : by_code).push(record);
				} else if (tokens.every((t) => record.nsearch.includes(t))) {
					by_name.push(record);
				}

				// Cannot stop early: a later record may still be an exact match.
			}

			return exact.concat(by_code, by_name).slice(0, limit);
		},

		/**
		 * search(), but falls back to one live server query when the cached set
		 * yields nothing.
		 *
		 * This is what keeps manual-only refresh safe: a supplier created after
		 * the cache was built is still findable by searching for it, without
		 * costing a request on page load.
		 */
		search_with_fallback(set_name, query, limit) {
			const local = this.search(set_name, query, limit);
			if (local && local.length) return Promise.resolve(local);

			const args = LIVE_SEARCH_ARGS[set_name];
			if (!args || !String(query || "").trim()) {
				return Promise.resolve(local || []);
			}

			return frappe
				.call({
					method: "frappe.desk.search.search_link",
					type: "POST",
					no_spinner: true,
					args: Object.assign({ txt: query, page_length: limit || 20 }, args),
				})
				.then(
					(r) =>
						// build_for_autosuggest() puts the party name in `label`
						// when the doctype shows a title in links, and in
						// `description` otherwise. `value` is always the code.
						(r.message || []).map((d) => ({
							code: d.value,
							label: d.label || d.description || "",
							stale: true, // not in the cache -- shown with a hint
						})),
					() => local || []
				);
		},
	});
})();

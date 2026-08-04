// Keyboard-first entry bar above the items table of the Bulk Invoice Form.
//
// Enabled by "Enable Quick Entry Bar in Bulk Invoice" in Agriculture Settings,
// which is read from frappe.boot (see agricultural_marketing/boot.py) so the bar
// can be mounted on the first render rather than after a round-trip.
//
// The pickers deliberately do NOT use frappe.ui.form.ControlLink: that control
// posts to search_link on every keystroke, which is the whole reason this bar
// exists. They read agrimkt.party_catalog instead, which is entirely in memory.

frappe.provide("agrimkt.bulk_quick_entry");

(() => {
	const CHILD_DOCTYPE = "Bulk Invoice Form Item";
	const MAX_SUGGESTIONS = 50;
	// How many rows a field offers before anything is typed.
	const SUGGESTION_COUNT = 5;

	// A row with none of these set holds nothing a user typed. Names and totals
	// are excluded on purpose: they are derived, so they cannot make a row real.
	const BLANK_ROW_FIELDS = ["item_code", "qty", "price", "supplier", "customer", "pamper"];

	function settings() {
		return (frappe.boot && frappe.boot.agricultural_marketing) || {};
	}

	function label_for(fieldname, fallback) {
		const df = frappe.meta.get_docfield(CHILD_DOCTYPE, fieldname);
		return __(df && df.label ? df.label : fallback);
	}

	// Bar columns, in tab order. `set` names the agrimkt.party_catalog set a
	// picker searches; `name_field` is the child-table column that caches the
	// resolved party name so the row needs no fetch_from round-trip.
	function field_defs() {
		return [
			{
				fieldname: "item_code",
				label: label_for("item_code", "Item"),
				type: "link",
				set: "item",
				name_field: "item_name",
				reqd: true,
				grow: 2,
			},
			{ fieldname: "qty", label: label_for("qty", "Quantity"), type: "number", reqd: true, grow: 1 },
			{ fieldname: "price", label: label_for("price", "Price"), type: "number", reqd: true, grow: 1 },
			{
				fieldname: "supplier",
				label: label_for("supplier", "Supplier Code"),
				type: "link",
				set: "supplier",
				name_field: "supplier_name",
				reqd: true,
				grow: 2,
			},
			{
				fieldname: "customer",
				label: label_for("customer", "Customer Code"),
				type: "link",
				set: "customer",
				name_field: "customer_name",
				reqd: true,
				grow: 2,
			},
			// Pamper is deliberately not a column here: it is optional and, when
			// it applies at all, it is the same for the whole document. commit()
			// takes it from the parent's Default Pamper instead, which is what
			// items_add() does for rows added through the grid.
		];
	}

	// -----------------------------------------------------------------------
	// Saved column order
	// -----------------------------------------------------------------------
	//
	// Everyone reads their paperwork in a different order, so the bar's columns
	// are draggable and the arrangement is remembered per user.

	const ORDER_VERSION = 1;

	function order_key() {
		return `agrimkt:qe_field_order:v${ORDER_VERSION}:${frappe.session.user}`;
	}

	function read_order() {
		try {
			const saved = JSON.parse(window.localStorage.getItem(order_key()));
			return Array.isArray(saved) ? saved : null;
		} catch (e) {
			return null;
		}
	}

	function write_order(fieldnames) {
		try {
			window.localStorage.setItem(order_key(), JSON.stringify(fieldnames));
		} catch (e) {
			// Not worth telling the user about; the order just will not persist.
		}
	}

	/** Saved order, reconciled against the columns that actually exist today. */
	function ordered_defs() {
		const defs = field_defs();
		const saved = read_order();
		if (!saved) return defs;

		const by_name = new Map(defs.map((d) => [d.fieldname, d]));
		const out = [];

		// Saved names that no longer exist are dropped (pamper, for one), and
		// columns added later are appended rather than silently hidden.
		saved.forEach((fieldname) => {
			const def = by_name.get(fieldname);
			if (def && !out.includes(def)) out.push(def);
		});
		defs.forEach((def) => {
			if (!out.includes(def)) out.push(def);
		});

		return out;
	}

	// -----------------------------------------------------------------------
	// Styles
	// -----------------------------------------------------------------------

	const STYLE_ID = "agrimkt-qe-style";
	const STYLES = `
.agrimkt-qe { border: 1px solid var(--border-color); border-radius: var(--border-radius-md);
	background: var(--fg-color); padding: 10px 12px; margin-bottom: 12px; }

/* One line: the fields, then Add Row and Refresh. Wraps only when it must.
   Every column -- fields and buttons alike -- is label / control / resolved,
   and all columns are top-aligned, so the control bands line up by structure
   rather than by a hand-tuned offset. */
.agrimkt-qe-row { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; }
/* flex-basis 0, NOT auto. A wrapping flex container breaks lines on each item's
   basis and only shrinks afterwards, and the auto basis here is enormous: the
   resolved names below the inputs are white-space: nowrap, so a long supplier
   name inflates this container's max-content width and pushes the buttons onto
   a second line. A zero basis keeps them on the inputs' line at any width. */
.agrimkt-qe-fields { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap;
	flex: 1 1 0; min-width: 0; }
.agrimkt-qe-actions { display: flex; flex-direction: column; flex: 0 0 auto; }
.agrimkt-qe-actions > label { visibility: hidden; }
/* The band is the height of an input, and the buttons centre inside it, so they
   sit on the input's centre line rather than on the label's. */
.agrimkt-qe-buttons { display: flex; gap: 6px; align-items: center; min-height: 26px; }
/* 26px is .input-xs in frappe's desk/global.scss. */
.agrimkt-qe-buttons .btn { height: 26px; display: inline-flex; align-items: center;
	white-space: nowrap; }
.agrimkt-qe-status { color: var(--text-muted); font-size: var(--text-sm); margin-top: 6px;
	overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.agrimkt-qe-cell { position: relative; display: flex; flex-direction: column; min-width: 110px; }
.agrimkt-qe-cell > label,
.agrimkt-qe-actions > label { font-size: var(--text-sm); color: var(--text-muted);
	margin-bottom: 2px; display: flex; align-items: center; gap: 4px; }
.agrimkt-qe-cell input { width: 100%; }
.agrimkt-qe-cell.is-invalid input { border-color: var(--red-400); }
.agrimkt-qe-resolved { font-size: var(--text-sm); color: var(--text-muted); margin-top: 2px;
	min-height: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agrimkt-qe-resolved.is-stale { color: var(--orange-500); }

/* Drag handle -- on the label, so dragging never fights with typing. */
.agrimkt-qe-handle { cursor: grab; color: var(--text-muted); opacity: 0.45; user-select: none;
	line-height: 1; font-size: var(--text-md); }
.agrimkt-qe-cell:hover .agrimkt-qe-handle { opacity: 1; }
.agrimkt-qe-ghost { opacity: 0.4; }
.agrimkt-qe-drag { background: var(--highlight-color); }

/* While the cursor is in the bar, the items table is drawn newest-first so the
   row just entered is the one on top. Display only -- idx and the stored order
   are untouched, which is why the row numbers stay as they are. */
.agrimkt-qe-flip .grid-body .rows { display: flex; flex-direction: column-reverse; }
/* grid.scss gives .grid-row:last-child no border and rounded bottom corners, to
   close off the table. Reversed, that child is the one drawn at the top -- so
   the treatment has to move to the first child, which is now the bottom row. */
.agrimkt-qe-flip .grid-body .rows > .grid-row:last-child {
	border-bottom: 1px solid var(--table-border-color); border-radius: 0; }
.agrimkt-qe-flip .grid-body .rows > .grid-row:first-child {
	border-bottom: none;
	border-bottom-left-radius: var(--border-radius);
	border-bottom-right-radius: var(--border-radius); }

.agrimkt-qe-list { position: absolute; z-index: 10; top: 100%; inset-inline-start: 0; min-width: 100%;
	max-height: 260px; overflow-y: auto; background: var(--fg-color); border: 1px solid var(--border-color);
	border-radius: var(--border-radius-md); box-shadow: var(--shadow-md); margin-top: 2px; }
.agrimkt-qe-list[hidden] { display: none; }
.agrimkt-qe-option { padding: 4px 8px; cursor: pointer; display: flex; gap: 8px; align-items: baseline; }
.agrimkt-qe-option.is-active { background: var(--highlight-color); }
.agrimkt-qe-option .qe-code { font-family: var(--font-stack-mono, monospace); font-weight: 600; }
.agrimkt-qe-option .qe-label { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis;
	white-space: nowrap; }
.agrimkt-qe-empty { padding: 6px 8px; color: var(--text-muted); font-size: var(--text-sm); }
.agrimkt-qe-group { padding: 4px 8px; color: var(--text-muted); font-size: var(--text-sm);
	border-bottom: 1px solid var(--border-color); }
`;

	function inject_styles() {
		if (document.getElementById(STYLE_ID)) return;
		$("<style>").attr("id", STYLE_ID).text(STYLES).appendTo(document.head);
	}

	// -----------------------------------------------------------------------
	// Picker: one input backed by a cached catalog set
	// -----------------------------------------------------------------------

	class Picker {
		constructor(opts) {
			Object.assign(this, opts); // $input, $list, $resolved, def, on_commit, on_advance
			this.value = null;
			this.results = [];
			this.active = -1;
			this.bind();
		}

		bind() {
			this.$input.on("input", () => this.on_type());
			this.$input.on("keydown", (e) => this.on_keydown(e));
			this.$input.on("focus", () => {
				this.$input.select();
				// Arriving at an empty field opens on the recent picks. A field
				// that kept its value from the last row stays quiet -- that value
				// is usually the one wanted again.
				if (!this.$input.val()) this.show_suggestions();
			});
			// mousedown, not click: it fires before the input's blur.
			this.$list.on("mousedown", ".agrimkt-qe-option", (e) => {
				e.preventDefault();
				this.choose(Number($(e.currentTarget).attr("data-idx")));
			});
			this.$input.on("blur", () => this.on_blur());
		}

		on_type() {
			const query = this.$input.val();
			this.value = null;
			this.set_resolved("");

			// Deleting back to empty returns to the recent picks rather than
			// dumping the head of the catalog.
			if (!query) {
				this.show_suggestions();
				return;
			}

			const local = agrimkt.party_catalog.search(this.def.set, query, MAX_SUGGESTIONS);
			this.render(local === null ? [] : local);

			if (!local || !local.length) {
				// Nothing cached matched -- ask the server for this term only.
				// Guarded by the query so a stale response cannot overwrite a
				// newer one.
				this.pending_query = query;
				agrimkt.party_catalog
					.search_with_fallback(this.def.set, query, MAX_SUGGESTIONS)
					.then((remote) => {
						if (this.pending_query !== query) return;
						if (this.$input.val() !== query) return;
						this.render(remote);
					});
			}
		}

		render(results, header) {
			this.results = results || [];
			this.active = this.results.length ? 0 : -1;

			if (!this.results.length) {
				this.$list
					.empty()
					.append($("<div class='agrimkt-qe-empty'></div>").text(__("No matches")))
					.prop("hidden", false);
				return;
			}

			const $header = header
				? $("<div class='agrimkt-qe-group'></div>").text(header)
				: null;

			const html = this.results
				.map((r, i) => {
					const $option = $("<div class='agrimkt-qe-option'></div>")
						.attr("data-idx", i)
						.toggleClass("is-active", i === this.active);
					$option.append($("<span class='qe-code'></span>").text(r.code));
					if (r.label) $option.append($("<span class='qe-label'></span>").text(r.label));
					return $option;
				});

			this.$list.empty();
			if ($header) this.$list.append($header);
			this.$list.append(html).prop("hidden", false);
		}

		/** The opening list: recent picks first, topped up by code order. */
		show_suggestions() {
			const list = agrimkt.party_catalog.suggestions(this.def.set, SUGGESTION_COUNT);
			if (!list || !list.length) return;

			this.render(list, list[0].recent ? __("Recently used") : null);
		}

		move(delta) {
			if (!this.results.length) return;
			this.active = (this.active + delta + this.results.length) % this.results.length;
			this.$list
				.find(".agrimkt-qe-option")
				.removeClass("is-active")
				.filter(`[data-idx=${this.active}]`)
				.addClass("is-active")[0]
				?.scrollIntoView({ block: "nearest" });
		}

		choose(index) {
			const record = this.results[index];
			if (!record) return false;

			this.value = record.code;
			this.$input.val(record.code);
			this.set_resolved(record.label, record.stale);
			this.close();
			this.set_invalid(false);
			agrimkt.party_catalog.remember(this.def.set, record.code);
			return true;
		}

		close() {
			this.$list.prop("hidden", true).empty();
			this.results = [];
			this.active = -1;
		}

		on_blur() {
			// A single unambiguous match is accepted without the extra Enter --
			// otherwise leaving the field half-typed would silently drop it.
			if (!this.value && this.results.length === 1) {
				this.choose(0);
				return;
			}
			this.close();
			if (!this.value && this.$input.val()) this.set_invalid(true);
		}

		on_keydown(e) {
			const key = e.key;

			if (key === "ArrowDown" || key === "ArrowUp") {
				if (!this.results.length) return;
				e.preventDefault();
				e.stopPropagation();
				this.move(key === "ArrowDown" ? 1 : -1);
				return;
			}

			if (key === "Escape") {
				// Only swallow Escape while a list is open -- otherwise it still
				// belongs to whatever is around us.
				if (!this.results.length) return;
				e.preventDefault();
				e.stopPropagation();
				this.close();
				return;
			}

			if (key !== "Enter") return;

			// Keep Enter away from bulk_invoice_form.js's enter-as-tab handler,
			// which is bound on frm.$wrapper and would move focus a second time.
			e.preventDefault();
			e.stopPropagation();

			if (this.results.length && this.active >= 0) {
				this.choose(this.active);
			} else if (!this.value && this.$input.val()) {
				// Typed something that resolves to nothing -- do not advance.
				this.set_invalid(true);
				return;
			}

			if (e.ctrlKey || e.metaKey) this.on_commit();
			else this.on_advance();
		}

		set_resolved(text, stale) {
			this.$resolved.text(text || "").toggleClass("is-stale", Boolean(stale));
		}

		set_invalid(flag) {
			this.$input.closest(".agrimkt-qe-cell").toggleClass("is-invalid", Boolean(flag));
		}

		get_value() {
			return this.value;
		}

		get_label() {
			return this.$resolved.text();
		}

		set_value(code) {
			if (!code) {
				this.value = null;
				this.$input.val("");
				this.set_resolved("");
				return;
			}
			const record = agrimkt.party_catalog.get(this.def.set, code);
			this.value = code;
			this.$input.val(code);
			this.set_resolved(record ? record.label : "");
			this.set_invalid(false);
		}

		clear() {
			this.set_value(null);
			this.set_invalid(false);
			this.close();
		}

		focus() {
			this.$input.trigger("focus");
		}
	}

	// -----------------------------------------------------------------------
	// Number cell: same keyboard contract as Picker, no dropdown
	// -----------------------------------------------------------------------

	class NumberCell {
		constructor(opts) {
			Object.assign(this, opts); // $input, def, on_commit, on_advance
			this.$input.on("focus", () => this.$input.select());
			this.$input.on("input", () => this.set_invalid(false));
			this.$input.on("keydown", (e) => {
				if (e.key !== "Enter") return;
				e.preventDefault();
				e.stopPropagation();
				if (e.ctrlKey || e.metaKey) this.on_commit();
				else this.on_advance();
			});
		}

		// Accepts Arabic-Indic digits, which normalize() folds to ASCII.
		get_value() {
			const raw = agrimkt.party_catalog.normalize(this.$input.val());
			return raw === "" ? null : flt(raw);
		}

		set_value(value) {
			this.$input.val(value === null || value === undefined ? "" : value);
		}

		set_invalid(flag) {
			this.$input.closest(".agrimkt-qe-cell").toggleClass("is-invalid", Boolean(flag));
		}

		clear() {
			this.set_value(null);
			this.set_invalid(false);
		}

		focus() {
			this.$input.trigger("focus");
		}
	}

	// -----------------------------------------------------------------------
	// The bar
	// -----------------------------------------------------------------------

	class QuickEntryBar {
		constructor(frm) {
			this.frm = frm;
			this.defs = ordered_defs();
			this.cells = {};
			this.rows_added = 0;
			this.render();
		}

		render() {
			inject_styles();

			this.$wrapper = $("<div class='agrimkt-qe'></div>");

			const $row = $("<div class='agrimkt-qe-row'></div>").appendTo(this.$wrapper);
			this.$fields = $("<div class='agrimkt-qe-fields'></div>").appendTo($row);

			this.defs.forEach((def) => {
				const $cell = $("<div class='agrimkt-qe-cell'></div>")
					.attr("data-qe-cell", def.fieldname)
					.css("flex", `${def.grow} 1 0`)
					.appendTo(this.$fields);

				$("<label></label>")
					.append(
						$("<span class='agrimkt-qe-handle'></span>")
							.text("⠿")
							.attr("title", __("Drag to reorder"))
					)
					.append(document.createTextNode(def.reqd ? `${def.label} *` : def.label))
					.appendTo($cell);

				const $input = $("<input class='form-control input-xs' type='text' autocomplete='off'>")
					.attr("data-qe-field", def.fieldname)
					.appendTo($cell);

				const common = {
					$input: $input,
					def: def,
					on_advance: () => this.advance(def.fieldname),
					on_commit: () => this.commit(),
				};

				if (def.type === "link") {
					const $list = $("<div class='agrimkt-qe-list' hidden></div>").appendTo($cell);
					const $resolved = $("<div class='agrimkt-qe-resolved'></div>").appendTo($cell);
					this.cells[def.fieldname] = new Picker(
						Object.assign({ $list: $list, $resolved: $resolved }, common)
					);
				} else {
					$input.attr("inputmode", "decimal");
					$("<div class='agrimkt-qe-resolved'></div>").appendTo($cell);
					this.cells[def.fieldname] = new NumberCell(common);
				}
			});

			// Buttons sit outside the sortable container so a column can never
			// be dropped between them. The column is built with the same three
			// bands as a field cell -- label, control, resolved-name -- which is
			// what puts the buttons on the inputs' line without a magic offset.
			const $actions = $("<div class='agrimkt-qe-actions'></div>").appendTo($row);

			// Same markup as a real label, including the handle, so it measures
			// identically. Hidden rather than empty, for exactly that reason.
			$("<label aria-hidden='true'></label>")
				.append($("<span class='agrimkt-qe-handle'></span>").text("⠿"))
				.append(document.createTextNode(" "))
				.appendTo($actions);

			const $buttons = $("<div class='agrimkt-qe-buttons'></div>").appendTo($actions);
			$("<button class='btn btn-xs btn-primary'></button>")
				.text(__("Add Row"))
				.attr("title", __("Ctrl+Enter"))
				.on("click", () => this.commit())
				.appendTo($buttons);
			$("<button class='btn btn-xs btn-default'></button>")
				.text("↻")
				.attr("title", __("Refresh Cached Data"))
				.on("click", () => this.refresh_catalog())
				.appendTo($buttons);

			$("<div class='agrimkt-qe-resolved'></div>").appendTo($actions);

			this.$status = $("<div class='agrimkt-qe-status'></div>").appendTo(this.$wrapper);

			this.setup_sortable();
			this.setup_grid_flip();
			this.update_status();
		}

		setup_sortable() {
			if (typeof Sortable === "undefined") return; // desk bundle not loaded

			this.sortable = new Sortable(this.$fields.get(0), {
				handle: ".agrimkt-qe-handle",
				draggable: ".agrimkt-qe-cell",
				animation: 150,
				ghostClass: "agrimkt-qe-ghost",
				chosenClass: "agrimkt-qe-drag",
				onEnd: () => this.save_order(),
			});
		}

		/** Re-read the column order from the DOM after a drag, and remember it. */
		save_order() {
			const order = this.$fields
				.find(".agrimkt-qe-cell")
				.map((_i, el) => $(el).attr("data-qe-cell"))
				.get();

			// this.defs drives advance(), so reordering it is what makes Enter
			// follow the new visual order.
			const by_name = new Map(this.defs.map((d) => [d.fieldname, d]));
			this.defs = order.map((fieldname) => by_name.get(fieldname)).filter(Boolean);

			write_order(order);
		}

		// ------------------------------------------------------------------
		// Newest row on top, while the cursor is in the bar
		// ------------------------------------------------------------------

		setup_grid_flip() {
			this.$wrapper.on("focusin", () => this.set_grid_flip(true));

			this.$wrapper.on("focusout", (e) => {
				// focusout fires before the next element takes focus, and
				// relatedTarget is that element -- so moving between two fields
				// of the bar does not flicker the table.
				if (e.relatedTarget && this.$wrapper.get(0).contains(e.relatedTarget)) return;
				this.set_grid_flip(false);
			});
		}

		/** Purely visual: a CSS class on the grid, no change to idx or to items. */
		set_grid_flip(on) {
			const grid = this.frm.fields_dict.items && this.frm.fields_dict.items.grid;
			// The class lives on the grid wrapper, which survives refresh_field().
			grid && grid.wrapper && grid.wrapper.toggleClass("agrimkt-qe-flip", on);
		}

		mount() {
			const field = this.frm.fields_dict.items;
			if (!field || !field.$wrapper) return false;
			field.$wrapper.prepend(this.$wrapper);
			return true;
		}

		destroy() {
			// Leave the grid the way we found it -- the class outlives the bar.
			this.set_grid_flip(false);
			this.sortable && this.sortable.destroy();
			this.$wrapper.remove();
		}

		// ------------------------------------------------------------------

		update_status(extra) {
			const counts = [
				`${__("Suppliers")}: ${agrimkt.party_catalog.count("supplier")}`,
				`${__("Customers")}: ${agrimkt.party_catalog.count("customer")}`,
				`${__("Items")}: ${agrimkt.party_catalog.count("item")}`,
			].join(" · ");

			const generated = agrimkt.party_catalog.generated_on();
			const as_of = generated
				? ` — ${__("cached")} ${frappe.datetime.str_to_user(generated)}`
				: "";

			this.$status.text(extra ? `${extra} — ${counts}${as_of}` : `${counts}${as_of}`);
		}

		refresh_catalog() {
			frappe.show_alert({ message: __("Refreshing cached data..."), indicator: "blue" });
			agrimkt.party_catalog.refresh().then(
				() => {
					// Re-resolve the names shown under any codes already typed.
					this.defs.forEach((def) => {
						if (def.type !== "link") return;
						const cell = this.cells[def.fieldname];
						const code = cell.get_value();
						if (code) cell.set_value(code);
					});
					this.update_status();
					frappe.show_alert({ message: __("Cached data updated"), indicator: "green" });
				},
				() => frappe.show_alert({ message: __("Could not refresh cached data"), indicator: "red" })
			);
		}

		advance(fieldname) {
			const index = this.defs.findIndex((d) => d.fieldname === fieldname);

			if (index === this.defs.length - 1) {
				// Past the last column: this is what turns a run of Enters into
				// a committed row.
				this.commit();
				return;
			}

			this.cells[this.defs[index + 1].fieldname].focus();
		}

		collect() {
			const values = {};
			let first_bad = null;

			this.defs.forEach((def) => {
				const cell = this.cells[def.fieldname];
				const value = cell.get_value();

				const missing = value === null || value === "" || (def.type === "number" && !value);
				cell.set_invalid(def.reqd && missing);
				if (def.reqd && missing && !first_bad) first_bad = def.fieldname;

				values[def.fieldname] = value;
				// Carry the resolved name so the row needs no fetch_from lookup.
				if (def.name_field) values[def.name_field] = cell.get_label();
			});

			if (first_bad) {
				this.cells[first_bad].focus();
				frappe.show_alert({
					message: __("{0} is required", [
						this.defs.find((d) => d.fieldname === first_bad).label,
					]),
					indicator: "orange",
				});
				return null;
			}

			// Not a column in the bar -- carried from the document, exactly as
			// items_add() does for a row added through the grid.
			if (this.frm.doc.default_pamper) values.pamper = this.frm.doc.default_pamper;

			values.total = flt(values.qty) * flt(values.price);
			return values;
		}

		/**
		 * Is this row an untouched placeholder?
		 *
		 * `items` is a required Table, so frappe seeds every new document with
		 * one empty row (create_new.js -> create_mandatory_children). The bar
		 * appends after it, which would leave that blank row sitting at the top
		 * of the invoice for good.
		 */
		is_blank_row(row) {
			// Already turned into an Invoice Form: not ours to remove.
			if (row.reference_invoice_form) return false;

			// Someone may be part-way through typing into it in the grid.
			const open = frappe.ui.form.get_open_grid_form();
			if (open && open.doc && open.doc.name === row.name) return false;

			return !BLANK_ROW_FIELDS.some((fieldname) => row[fieldname]);
		}

		drop_blank_rows() {
			const blank = (this.frm.doc.items || []).filter((row) => this.is_blank_row(row));

			// clear_doc() detaches the row from the parent and renumbers idx.
			blank.forEach((row) => frappe.model.clear_doc(row.doctype, row.name));

			return blank.length;
		}

		commit() {
			if (this.frm.doc.docstatus !== 0) return;

			const values = this.collect();
			if (!values) return;

			// Before paginating, so the page count reflects the real row count.
			this.drop_blank_rows();

			const grid = this.frm.fields_dict.items.grid;
			// Mirrors grid.add_new_row(): without this the row lands on a page
			// the user is not looking at once the table passes one page.
			grid.grid_pagination && grid.grid_pagination.go_to_last_page_to_add_row();

			// frm.add_child() $.extend()s the values straight onto the row, so
			// no set_value fires: no per-field events, and no server round-trip
			// to resolve item_name / supplier_name / customer_name.
			this.frm.add_child("items", values);
			this.frm.dirty();
			this.frm.refresh_field("items");

			this.rows_added += 1;
			this.update_status(
				__("Added {0}: {1} × {2}", [values.item_code, values.qty, values.price])
			);

			this.after_commit();
		}

		after_commit() {
			// Values stay put and the first field is re-selected: supplier and
			// customer usually repeat across rows, and anything that does change
			// is overwritten by simply typing.
			this.cells.qty.set_invalid(false);
			this.cells.price.set_invalid(false);
			this.cells[this.defs[0].fieldname].focus();
		}

		/** Seed the empty pickers from the parent's Default Supplier/Customer. */
		apply_defaults() {
			const map = {
				supplier: this.frm.doc.default_supplier,
				customer: this.frm.doc.default_customer,
			};

			Object.keys(map).forEach((fieldname) => {
				// Skip anything that is not a column in the bar -- pamper, today.
				const cell = this.cells[fieldname];
				if (cell && map[fieldname] && !cell.get_value()) cell.set_value(map[fieldname]);
			});
		}
	}

	// -----------------------------------------------------------------------
	// Mounting
	// -----------------------------------------------------------------------

	function should_show(frm) {
		return Boolean(settings().enable_bulk_quick_entry) && frm.doc.docstatus === 0;
	}

	function setup(frm) {
		if (!should_show(frm)) {
			if (frm.__quick_entry_bar) {
				frm.__quick_entry_bar.destroy();
				frm.__quick_entry_bar = null;
			}
			return;
		}

		// refresh() fires repeatedly; re-mount only if the grid was re-rendered
		// out from under us.
		if (frm.__quick_entry_bar && frm.__quick_entry_bar.$wrapper.closest("body").length) {
			frm.__quick_entry_bar.apply_defaults();
			return;
		}

		agrimkt.party_catalog.ready().then((ok) => {
			if (!ok) {
				frappe.show_alert({
					message: __("Could not load the quick entry data"),
					indicator: "red",
				});
				return;
			}
			if (!should_show(frm)) return;
			if (frm.__quick_entry_bar) frm.__quick_entry_bar.destroy();

			const bar = new QuickEntryBar(frm);
			if (bar.mount()) {
				frm.__quick_entry_bar = bar;
				bar.apply_defaults();
			}
		});
	}

	// -----------------------------------------------------------------------
	// Cached search inside the items table
	// -----------------------------------------------------------------------
	//
	// The bar only speeds up *new* rows. Editing an existing row still goes
	// through frappe.ui.form.ControlLink, so point those controls at the same
	// cache. Gated by "Use Cached Search Inside Items Table".

	const CACHED_LINK_FIELDS = {
		"Bulk Invoice Form Item": {
			item_code: "item",
			supplier: "supplier",
			customer: "customer",
			pamper: "pamper",
		},
		"Bulk Invoice Form": {
			default_supplier: "supplier",
			default_customer: "customer",
			default_pamper: "pamper",
		},
	};

	function install_cached_link_search(control) {
		if (!settings().enable_bulk_quick_entry) return;
		if (!settings().quick_entry_use_cache_in_grid) return;

		const by_field = CACHED_LINK_FIELDS[control.df && control.df.parent];
		const set_name = by_field && by_field[control.df.fieldname];
		if (!set_name) return;

		// Nothing cached yet (first ever load, or the fetch failed): leave the
		// control exactly as frappe built it.
		if (!agrimkt.party_catalog.is_ready()) return;

		// ControlLink sets trigger_change_on_input_event = false, so the handler
		// setup_awesomeplete() just bound is the only "input" listener on this
		// element. Dropping it is what stops the per-keystroke search_link POST.
		control.$input.off("input");

		// Same shape frappe.desk.search.build_for_autosuggest returns, so the
		// awesomplete item renderer draws these identically to a normal Link.
		const to_list = (records) =>
			(records || []).map((r) =>
				control.is_title_link()
					? { value: r.code, label: r.label || r.code, description: r.code }
					: { value: r.code, description: r.label }
			);

		// ControlLink triggers "input" on focus when the field is empty
		// (link.js), so this is also what opens the list on arrival.
		control.$input.on("input", function () {
			const term = this.value;

			if (!term) {
				const opening = agrimkt.party_catalog.suggestions(set_name, SUGGESTION_COUNT);
				if (opening && opening.length) {
					control.awesomplete.list = to_list(opening);
					return;
				}
			}

			const local = agrimkt.party_catalog.search(set_name, term, MAX_SUGGESTIONS);
			if (local && local.length) {
				control.awesomplete.list = to_list(local);
				return;
			}

			// Cache miss -- one live query for this term, so records created
			// since the cache was built are still reachable.
			agrimkt.party_catalog
				.search_with_fallback(set_name, term, MAX_SUGGESTIONS)
				.then((remote) => {
					if (control.$input.val() !== term) return;
					if (!control.$input.is(":focus")) return;
					control.awesomplete.list = to_list(remote);
				});
		});

		// Picks made in the grid feed the same history as picks made in the bar,
		// so the two never disagree about what was used most recently.
		control.$input.on("awesomplete-selectcomplete", (e) => {
			const picked = e.originalEvent && e.originalEvent.text;
			if (picked && picked.value) agrimkt.party_catalog.remember(set_name, picked.value);
		});
	}

	function patch_link_control() {
		const proto = frappe.ui.form.ControlLink.prototype;
		if (proto.__agrimkt_cached_search) return;
		proto.__agrimkt_cached_search = true;

		// Frappe spells it "awesomeplete"; keep the typo or nothing is patched.
		const original = proto.setup_awesomeplete;

		proto.setup_awesomeplete = function () {
			original.apply(this, arguments);
			try {
				install_cached_link_search(this);
			} catch (e) {
				// A broken cache must never take a Link field down with it.
				console.warn("agrimkt: cached link search not installed", e);
			}
		};
	}

	patch_link_control();

	agrimkt.bulk_quick_entry.setup = setup;

	frappe.ui.form.on("Bulk Invoice Form", {
		refresh(frm) {
			setup(frm);
		},
		default_supplier(frm) {
			frm.__quick_entry_bar && frm.__quick_entry_bar.apply_defaults();
		},
		default_customer(frm) {
			frm.__quick_entry_bar && frm.__quick_entry_bar.apply_defaults();
		},
		// No default_pamper handler: pamper is not a column in the bar, it is
		// read off the document at commit time.
	});
})();

// Method 1: Custom List View Button with Bulk PDF Download + Update Status (No Confirmation)
frappe.listview_settings['Invoice Form'] = {
    
    onload: function(listview) {
        // Add custom button to list view
        listview.page.add_inner_button('Print & Mark as Printed', () => {
            let selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint('Please select records first');
                return;
            }

            // Prepare names array for bulk PDF download
            let names = selected.map(doc => doc.name);
            
            // Create bulk PDF download URL using the same API with default letterhead
            let bulk_print_url = `/api/method/frappe.utils.print_format.download_multi_pdf?` +
                `doctype=Invoice%20Form&` +
                `name=${encodeURIComponent(JSON.stringify(names))}&` +
                `format=Supplier%20Invoice&` +
                `letterhead=inv00001&` +
                `options=${encodeURIComponent(JSON.stringify({"page-size": "A5"}))}`;
            
            // Download the bulk PDF
            window.open(bulk_print_url, '_blank');
            
            // Update all selected records as printed using custom server method
            frappe.call({
                method: 'agricultural_marketing.api.mark_invoices_as_printed',  // Replace 'your_app' with your actual app name
                args: {
                    invoice_names: names
                },
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({
                            message: `${r.message.updated_count} of ${r.message.total_count} invoices marked as printed`,
                            indicator: 'green'
                        });
                        listview.refresh();
                    } else {
                        frappe.msgprint({
                            title: 'Error',
                            message: 'Some records could not be updated',
                            indicator: 'red'
                        });
                    }
                }
            });
        });

        // Create a charge voucher for each selected invoice that has charges applied
        listview.page.add_inner_button(__('Make Charge Payment'), () => {
            let selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint(__('Please select records first'));
                return;
            }

            frappe.call({
                method: 'agricultural_marketing.agricultural_marketing.doctype.invoice_form.supplier_charges.make_charge_payments',
                args: {
                    invoice_names: selected.map(doc => doc.name)
                },
                freeze: true,
                freeze_message: __('Creating charge payments...'),
                callback: function(r) {
                    if (!r.message) return;
                    let res = r.message;

                    // Large selections run in the background so the request cannot
                    // time out; the result arrives over realtime instead.
                    if (res.queued) {
                        frappe.show_alert({
                            message: __('Building charge payments for {0} invoices in the background...', [res.count]),
                            indicator: 'blue'
                        }, 10);
                        return;
                    }

                    show_charge_payment_result(res, listview);
                }
            });
        });

        frappe.realtime.on('supplier_charge_payments_done', res => {
            show_charge_payment_result(res, listview);
        });

        function show_charge_payment_result(res, listview) {
            let lines = [];

            // One voucher per (payment type, company); every invoice keeps
            // its own row inside it.
            if (res.vouchers.length) {
                lines.push(`<b>${__('Created')} (${res.vouchers.length})</b><ul>` +
                    res.vouchers.map(v =>
                        `<li><a href="/app/payments-and-receipts/${encodeURIComponent(v.payment)}">` +
                        `${frappe.utils.escape_html(v.payment)}</a> ` +
                        `&mdash; ${__(v.payment_type)}, ${v.invoices.length} ${__('rows')}<br>` +
                        `<small>${frappe.utils.escape_html(v.invoices.join(', '))}</small></li>`
                    ).join('') + '</ul>');
            }

            const skipped = [
                [res.skipped_already_linked, __('Already linked to a charge payment')],
                [res.skipped_no_charges, __('No charges to apply')],
                [res.skipped_no_original, __('Original invoice has no charge payment to reverse')],
                [res.skipped_cancelled, __('Cancelled invoice')]
            ];
            skipped.forEach(([names, label]) => {
                if (names && names.length) {
                    lines.push(`<b>${label} (${names.length})</b><br>` +
                        frappe.utils.escape_html(names.join(', ')));
                }
            });

            if (res.errors.length) {
                lines.push(`<b class="text-danger">${__('Failed')} (${res.errors.length})</b><ul>` +
                    res.errors.map(e =>
                        `<li>${frappe.utils.escape_html(e.invoices.join(', '))}: ` +
                        `${frappe.utils.escape_html(e.message)}</li>`
                    ).join('') + '</ul>');
            }

            frappe.msgprint({
                title: __('Charge Payments'),
                message: lines.join('<hr>'),
                indicator: res.errors.length ? 'red' : (res.vouchers.length ? 'green' : 'orange')
            });
            listview.refresh();
        }

        // Add Show Printed button
        listview.page.add_inner_button('Show Printed', () => {
            // Clear existing is_printed filters first
            listview.filter_area.filter_list.filters.forEach((filter, index) => {
                if (filter[1] === 'is_printed') {
                    listview.filter_area.filter_list.remove_filter(filter[0], filter[1]);
                }
            });
            
            // Add printed filter
            listview.filter_area.add([[listview.doctype, 'is_printed', '=', 1]]);
        });

        // Add Show Unprinted button
        listview.page.add_inner_button('Show Unprinted', () => {
            // Clear existing is_printed filters first
            listview.filter_area.filter_list.filters.forEach((filter, index) => {
                if (filter[1] === 'is_printed') {
                    listview.filter_area.filter_list.remove_filter(filter[0], filter[1]);
                }
            });
            
            // Add unprinted filter
            listview.filter_area.add([[listview.doctype, 'is_printed', '=', 0]]);
        });

        // Add Show All button
        listview.page.add_inner_button('Show All', () => {
            // Clear existing is_printed filters
            listview.filter_area.filter_list.filters.forEach((filter, index) => {
                if (filter[1] === 'is_printed') {
                    listview.filter_area.filter_list.remove_filter(filter[0], filter[1]);
                }
            });
        });
    },add_fields: ["is_return", "return_against", "docstatus"],
    get_indicator: function(doc) {
        if (doc.is_return) {
            if (doc.docstatus === 1) {
                return [__("Return"), "red", "is_return,=,Yes|docstatus,=,1"];
            } else if (doc.docstatus === 0) {
                return [__("Return Draft"), "orange", "is_return,=,Yes|docstatus,=,0"];
            }
        } else {
            if (doc.docstatus === 1) {
                return [__("Submitted"), "green", "docstatus,=,1"];
            } else if (doc.docstatus === 0) {
                return [__("Draft"), "orange", "docstatus,=,0"];
            }
        }
    }
};

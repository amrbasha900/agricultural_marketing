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
    }
};
// Copyright (c) 2024, Muhammad Salama and contributors
// For license information, please see license.txt

frappe.ui.form.on("Agriculture Settings", {
    refresh(frm) {
        frm.set_query("commission_item", function () {
            return {
                filters: {
                    commission_item: 1,
                },
            };
        });
        frm.set_query("default_tax", function () {
            return {
                filters: {
                    is_default: 1,
                },
            };
        });
    },
});

/** @odoo-module **/

/* Read-only webmail tour (PR-1b-ii).  Drives the /odoo/webmail client
 * action in a real browser: 3 panes, folder tree + unread badge, date
 * sort toggle, sanitized reading pane (no <script>), attachment list,
 * cid: inline-image resolution and the Refresh button (deterministic
 * UserError: the seeded mailbox has no credential — no network).
 *
 * Odoo 19: tours are registered in the "web_tour.tours" registry
 * (web.assets_tests bundle). */

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("mail_management_webmail_tour", {
    url: "/odoo/webmail",
    steps: () => [
        {
            trigger: ".o_webmail_folder_tree",
            content: "The folder tree pane is rendered",
        },
        {
            trigger:
                ".o_webmail_folder_tree .o_webmail_folder_item[data-role='inbox'] span.o_webmail_folder_name:contains('Inbox')",
            content: "The Inbox folder is listed",
            run: "click",
        },
        {
            trigger: ".o_webmail_message_list .o_webmail_message_row",
            content: "The message list pane shows messages",
        },
        {
            trigger:
                ".o_webmail_message_list .o_webmail_message_row:first-child .o_webmail_msg_subject:contains('Webmail Tour Subject')",
            content: "Newest message first (date desc)",
        },
        {
            trigger:
                ".o_webmail_folder_tree .o_webmail_folder_item[data-role='inbox'] .o_webmail_unread_badge:contains('1')",
            content: "The Inbox shows the unread badge",
        },
        {
            trigger: ".o_webmail_message_row:first-child",
            content: "Open the newest message",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_reading_pane .o_webmail_reading_subject:contains('Webmail Tour Subject')",
            content: "The reading pane shows the subject",
        },
        {
            trigger:
                ".o_webmail_reading_pane .o_webmail_reading_body p:contains('Hello webmail tour')",
            content: "The sanitized body renders as HTML",
        },
        {
            trigger:
                ".o_webmail_reading_pane .o_webmail_reading_body img[src*='/mail_management/attachment/']",
            content: "The cid: inline image resolves to the download route",
        },
        {
            trigger: ".o_webmail_reading_pane .o_webmail_reading_body",
            content: "No script tag survives sanitization",
            run: () => {
                const scripts = document.querySelectorAll(
                    ".o_webmail_reading_pane .o_webmail_reading_body script",
                );
                if (scripts.length) {
                    throw new Error(
                        "script tag found in the reading pane body",
                    );
                }
            },
        },
        {
            trigger:
                ".o_webmail_reading_pane .o_webmail_attachment a:contains('tour.txt')",
            content: "The attachment is listed with a download link",
        },
        {
            trigger:
                ".o_webmail_reading_pane .o_webmail_compose_btn[data-mode='reply']",
            content: "PR-2c: open the rich-text compose dialog",
            run: "click",
        },
        {
            trigger: ".o_webmail_compose_wysiwyg .odoo-editor-editable",
            content: "PR-2c: the WYSIWYG editor mounts inside the dialog",
        },
        {
            trigger: ".o_webmail_compose_signature",
            content: "PR-2c: the signature picker is present",
        },
        {
            trigger: ".o_webmail_cancel_btn",
            content: "PR-2c: close the compose dialog",
            run: "click",
        },
        {
            trigger: ".o_webmail_sort_menu_btn",
            content: "PR-2e: open the single Sort menu",
            run: "click",
        },
        {
            trigger: ".o_webmail_sort_menu_item[data-sort-key='date']",
            content: "PR-2e: pick 'Date (oldest first)'",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_message_list .o_webmail_message_row:first-child .o_webmail_msg_subject:contains('Older tour message')",
            content: "Oldest message first after the sort pick",
        },
        {
            trigger:
                ".o_webmail_message_row:first-child .o_webmail_star_toggle",
            content: "PR-2a: star the first message",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_message_row:first-child .o_webmail_star_toggle .oi-filled[data-icon='star']",
            content: "PR-2a: the star icon is now solid",
        },
        {
            trigger: ".o_webmail_select_all",
            content: "PR-2a: select all messages",
            run: "click",
        },
        {
            trigger: ".o_webmail_bulk_bar",
            content: "PR-2a: the bulk action bar appears",
        },
        {
            trigger: ".o_webmail_bulk_unread",
            content: "PR-2a: bulk mark both messages unread",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_folder_tree .o_webmail_folder_item[data-role='inbox'] .o_webmail_unread_badge:contains('2')",
            content: "PR-2a: the unread badge recomputes to 2",
        },
        {
            trigger: ".o_webmail_search_input",
            content: "PR-2d: type a search query (debounced apply)",
            run: "fill Webmail Tour",
        },
        {
            trigger:
                ".o_webmail_search_indicator:contains(\"Searching: 'Webmail Tour'\")",
            content: "PR-2d: the active search shows a visible indicator",
        },
        {
            trigger:
                ".o_webmail_message_list .o_webmail_message_row:first-child .o_webmail_msg_subject:contains('Webmail Tour Subject')",
            content:
                "PR-2d: mailbox-wide search results replace the folder list",
        },
        {
            trigger: ".o_webmail_search_clear",
            content: "PR-2d: clear the search",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_message_list .o_webmail_message_row:first-child .o_webmail_msg_subject:contains('Older tour message')",
            content:
                "PR-2d: clearing returns to the folder view (both messages back, still date asc from the earlier toggle)",
        },
        {
            trigger: ".o_webmail_manage_btn",
            content: "PR-2d: open the mailbox management menu",
            run: "click",
        },
        {
            trigger: ".o_webmail_add_mailbox_item",
            content: "PR-2d: the Add mailbox entry is available",
        },
        {
            trigger: ".o_webmail_manage_btn",
            content: "PR-2d: close the management menu",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_message_list .o_webmail_message_row .o_webmail_msg_subject:contains('Webmail Tour Subject')",
            content: "PR-2e: open the message with the remote image",
            run: "click",
        },
        {
            trigger: ".o_webmail_reading_pane .o_webmail_images_blocked_bar",
            content: "PR-2e: remote images are blocked by default (bar)",
        },
        {
            trigger: ".o_webmail_reading_pane .o_webmail_show_images_btn",
            content: "PR-2e: reveal the images for THIS message only",
            run: "click",
        },
        {
            trigger:
                ".o_webmail_reading_pane .o_webmail_reading_body img[src*='tracking.example.com']",
            content: "PR-2e: the remote image renders after the reveal",
        },
        {
            trigger: ".o_webmail_settings_btn",
            content: "PR-2e: open the settings panel (gear)",
            run: "click",
        },
        {
            trigger: ".o_webmail_settings_dialog",
            content: "PR-2e: the settings dialog renders",
        },
        {
            trigger: ".o_webmail_settings_remote_images",
            content: "PR-2e: allow remote images globally",
            run: "click",
        },
        {
            trigger: '.o_webmail_settings_tab[data-tab="message_list"]',
            content: "PR-2f: switch to the Message list tab",
            run: "click",
        },
        {
            trigger: ".o_webmail_settings_messages_per_page",
            content: "PR-2f: the tabbed controls render",
        },
        {
            trigger: ".o_webmail_settings_show_checkboxes",
            content: "PR-2f: hide the checkbox column",
            run: "click",
        },
        {
            trigger: ".o_webmail_settings_save",
            content: "PR-2e: save the settings",
            run: "click",
        },
        {
            trigger: ".o_notification:contains('Settings saved')",
            content: "PR-2e: the save is confirmed",
        },
        {
            trigger: ".o_webmail_message_rows",
            content:
                "PR-2f: the list re-renders WITHOUT the select-all checkbox",
            run: () => {
                if (document.querySelector(".o_webmail_select_all")) {
                    throw new Error("select-all checkbox should be hidden");
                }
            },
        },
        {
            trigger: ".o_webmail_add_folder_btn",
            content: "PR-2b: open the create-folder dialog",
            run: "click",
        },
        {
            trigger: ".o_webmail_folder_dialog_name",
            content: "PR-2b: type a folder name",
            run: "fill Projects",
        },
        {
            trigger: ".o_webmail_folder_dialog_confirm",
            content:
                "PR-2b: confirm — the credentialless seed mailbox fails deterministically (IMAP-first)",
            run: "click",
        },
        {
            trigger: ".o_notification:contains('credential')",
            content:
                "PR-2b: the create-folder error surfaces as a notification",
        },
        {
            trigger: ".o_webmail_manage_btn",
            content: "PR-2e: open the management menu for Refresh all",
            run: "click",
        },
        {
            trigger: ".o_webmail_refresh_all_item",
            content:
                "PR-2e: Refresh all (consolidated) — deterministic failure here",
            run: "click",
        },
        {
            trigger: ".o_notification:contains('credential')",
            content: "The refresh error surfaces as a notification",
        },
    ],
});

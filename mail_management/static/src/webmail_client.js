/** @odoo-module **/

/* PR-1b-ii — read-only webmail client action (design §G.1).
 *
 * WebmailClient (registered as "mail_management.webmail") renders the
 * 3-pane read surface: FolderTree | MessageList | ReadingPane.
 *
 * - Data comes EXCLUSIVELY from the PR-1b-i JSON controllers, which run
 *   through request.env → the per-user ir.rules apply (no client-side
 *   user filtering).
 * - The stored body_html is already html_sanitize'd at sync time; the
 *   reading pane renders it via t-out on a Markup value (never t-raw).
 * - PR-2f: the reading pipeline composes the PURE policy helpers of
 *   utils/message_policy (prefer_html → strip styles → collapse quotes)
 *   with the existing prepareBody image/cid policy; the message-list
 *   page size + default sort come from the per-user settings until the
 *   user changes them for the session.
 */

import {
    Component,
    markup,
    onWillStart,
    onWillUnmount,
    useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

import { FolderTree } from "@mail_management/components/folder_tree";
import { MessageList } from "@mail_management/components/message_list";
import { ReadingPane } from "@mail_management/components/reading_pane";
import { ComposeDialog } from "@mail_management/components/compose_dialog";
import { FolderDialog } from "@mail_management/components/folder_dialog";
import { SettingsDialog } from "@mail_management/components/settings_dialog";
import {
    prepareBody,
    containsRemoteImages,
} from "@mail_management/utils/prepare_body";
import {
    chooseBodyHtml,
    collapseQuotes,
    markReadDelayMs,
    nextMessageId,
    senderInWhitelist,
    stripMessageStyles,
} from "@mail_management/utils/message_policy";

import { AssistantPanel } from "@mail_management/components/assistant_panel";

export class WebmailClient extends Component {
    /** PR-2i: map the theme key to the scss root class. */
    get themeClass() {
        const map = {
            dark: "o_webmail_dark",
            night: "o_webmail_theme_night",
            sepia: "o_webmail_theme_sepia",
            ocean: "o_webmail_theme_ocean",
            forest: "o_webmail_theme_forest",
        };
        return map[this.state.settings.theme] || "";
    }

    // ---- PR-2j: draggable pane splitters (user request 2026-09) -----------
    // OWL 2: t-ref is NOT allowed on components — the pane element is found
    // via DOM relations (splitter's previousElementSibling) and the root ref.

    applySavedWidths() {
        const rootEl = this.root && this.root.el;
        if (!rootEl) {
            return;
        }
        this._applyWidthTo(
            rootEl.querySelector(".o_webmail_folder_tree"),
            this._savedWidth("tree"),
        );
        this._applyWidthTo(
            rootEl.querySelector(".o_webmail_message_list"),
            this._savedWidth("list"),
        );
    }

    _savedWidth(which) {
        const saved = parseInt(
            localStorage.getItem("o_webmail." + which + "_width") || "",
            10,
        );
        return Number.isNaN(saved) ? null : saved;
    }

    _applyWidthTo(el, px) {
        if (el && px !== null) {
            el.style.flex = "0 0 " + px + "px";
        }
    }

    _paneBounds(which) {
        return which === "tree"
            ? { min: 160, max: 460 }
            : { min: 280, max: 760 };
    }

    startSplitterDrag(which, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        // The pane is the splitter's previous sibling (plain DOM relation —
        // no component refs involved).
        const el = ev.target.previousElementSibling;
        if (!el) {
            return;
        }
        const bounds = this._paneBounds(which);
        const startX = ev.clientX;
        const startWidth = el.getBoundingClientRect().width;
        document.body.classList.add("o_webmail_dragging");
        const onMove = (move) => {
            const delta = move.clientX - startX;
            const px = Math.round(
                Math.min(bounds.max, Math.max(bounds.min, startWidth + delta)),
            );
            el.style.flex = "0 0 " + px + "px";
        };
        const onUp = () => {
            document.removeEventListener("pointermove", onMove);
            document.removeEventListener("pointerup", onUp);
            document.body.classList.remove("o_webmail_dragging");
            const finalPx = Math.round(el.getBoundingClientRect().width);
            localStorage.setItem(
                "o_webmail." + which + "_width",
                String(finalPx),
            );
        };
        document.addEventListener("pointermove", onMove);
        document.addEventListener("pointerup", onUp);
    }

    static template = "mail_management.WebmailClient";
    static components = {
        FolderTree,
        MessageList,
        ReadingPane,
        AssistantPanel,
    };
    /** Reachable directly at /odoo/webmail (action service path handler). */
    static path = "webmail";

    setup() {
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        // PR-2f: once the user picks a sort/priority for the session, the
        // saved default sort no longer overrides it on folder open.
        this.sortTouched = false;
        // PR-2f: pending deferred mark-read timer (mark_read_after_seconds
        // > 0) — cancelled whenever the user navigates away.
        this._markReadTimer = null;

        this.state = useState({
            mailboxes: [],
            selectedMailboxId: false,
            assistantOpen:
                localStorage.getItem("o_webmail.assistant_open") === "1",
            selectedFolderId: false,
            selectedFolderName: "",
            messages: [],
            selectedMessageId: false,
            selectedMessage: false,
            // PR-2a message-list power: sort key ('date_desc'…),
            // priority toggles and the active quick filter.
            messageSort: "date_desc",
            messagePriority: [],
            messageFilter: "",
            // PR-2d: the APPLIED local search query ("" = folder view;
            // non-empty → the list shows mailbox-wide search results).
            searchQuery: "",
            // PR-2e + PR-2f: the user's webmail settings (loaded once at
            // start; saving re-applies the display policy without a reload).
            settings: {
                allowRemoteImages: false,
                signaturePosition: "below",
                messagesPerPage: 50,
                defaultSort: "date_desc",
                showCheckboxes: true,
                markReadAfterSeconds: 0,
                collapseQuotedText: true,
                maxQuoteLevels: 3,
                stripMessageStyles: false,
                preferHtml: true,
                simpleAttachmentList: false,
                showNextOnMoveDelete: true,
                imageWhitelist: [],
                // PR-2g: compose + general settings (folders tab data is
                // loaded separately — see _loadFoldersForSettings).
                composeEditor: "html",
                replySaveFolder: "sent",
                requestReadReceipt: false,
                requestDeliveryReceipt: false,
                enableSpellcheck: true,
                showUnreadBadge: true,
                theme: "light",
                trashRetentionDays: 30,
            },
            // PR-2e: message ids whose remote images were revealed with
            // the per-message "Show images" bar (never a setting change).
            revealedImageIds: [],
            selectedIds: [],
            loadingFolders: false,
            loadingMessages: false,
            loadingMessage: false,
            refreshing: false,
        });

        onWillStart(async () => {
            await Promise.all([this.loadFolders(), this.loadSettings()]);
        });
        // PR-2f: never leave a deferred mark-read timer running behind the
        // user's back after the client unmounts.
        onWillUnmount(() => this._cancelMarkReadTimer());
    }

    // ------------------------------------------------------------------
    // PR-2e/PR-2f — per-user webmail settings; saving re-applies the
    // display policy live.
    // ------------------------------------------------------------------

    async loadSettings() {
        try {
            const payload = await rpc("/mail_management/settings", {});
            this.state.settings = this._settingsFromPayload(payload);
        } catch {
            // Defaults on failure — the webmail stays usable.
        }
    }

    _settingsFromPayload(payload) {
        return {
            allowRemoteImages: Boolean(payload.allow_remote_images),
            signaturePosition:
                payload.signature_position === "above" ? "above" : "below",
            messagesPerPage: payload.messages_per_page || 50,
            defaultSort: payload.default_sort || "date_desc",
            showCheckboxes: payload.show_checkboxes !== false,
            markReadAfterSeconds:
                typeof payload.mark_read_after_seconds === "number"
                    ? payload.mark_read_after_seconds
                    : 0,
            collapseQuotedText: payload.collapse_quoted_text !== false,
            maxQuoteLevels: payload.max_quote_levels || 3,
            stripMessageStyles: Boolean(payload.strip_message_styles),
            preferHtml: payload.prefer_html !== false,
            simpleAttachmentList: Boolean(payload.simple_attachment_list),
            showNextOnMoveDelete: payload.show_next_on_move_delete !== false,
            imageWhitelist: Array.isArray(payload.image_whitelist)
                ? payload.image_whitelist
                : [],
            composeEditor:
                payload.compose_editor === "plain" ? "plain" : "html",
            replySaveFolder:
                payload.reply_save_folder === "same" ? "same" : "sent",
            requestReadReceipt: Boolean(payload.request_read_receipt),
            requestDeliveryReceipt: Boolean(payload.request_delivery_receipt),
            enableSpellcheck: payload.enable_spellcheck !== false,
            showUnreadBadge: payload.show_unread_badge !== false,
            theme: [
                "light",
                "dark",
                "night",
                "sepia",
                "ocean",
                "forest",
            ].includes(payload.theme)
                ? payload.theme
                : "light",
            trashRetentionDays: payload.trash_retention_days || 30,
        };
    }

    async saveSettings(payload) {
        try {
            const saved = await rpc("/mail_management/settings/save", {
                allow_remote_images: payload.allowRemoteImages,
                signature_position: payload.signaturePosition,
                messages_per_page: payload.messagesPerPage,
                default_sort: payload.defaultSort,
                show_checkboxes: payload.showCheckboxes,
                mark_read_after_seconds: payload.markReadAfterSeconds,
                collapse_quoted_text: payload.collapseQuotedText,
                max_quote_levels: payload.maxQuoteLevels,
                strip_message_styles: payload.stripMessageStyles,
                prefer_html: payload.preferHtml,
                simple_attachment_list: payload.simpleAttachmentList,
                show_next_on_move_delete: payload.showNextOnMoveDelete,
                image_whitelist: payload.imageWhitelistText,
                compose_editor: payload.composeEditor,
                reply_save_folder: payload.replySaveFolder,
                request_read_receipt: payload.requestReadReceipt,
                request_delivery_receipt: payload.requestDeliveryReceipt,
                enable_spellcheck: payload.enableSpellcheck,
                show_unread_badge: payload.showUnreadBadge,
                theme: payload.theme,
                trash_retention_days: payload.trashRetentionDays,
            });
            // The server CLAMPS/normalizes — the response is the truth.
            this.state.settings = this._settingsFromPayload(saved);
            this.notification.add(_t("Settings saved."), { type: "info" });
            // Apply LIVE: re-apply the (possibly new) default sort and
            // re-render the open message with the new display policy (no
            // reload, no list re-fetch).  The theme re-renders through the
            // reactive root class binding (PR-2g).
            this._applyDefaultSort();
            this._reprepareOpenBody();
        } catch (error) {
            this._notifyError(error);
        }
    }

    // ------------------------------------------------------------------
    // PR-2g — Folders settings tab data (ALL folders incl. hidden) and
    // the mapping/visibility actions.  Each action returns the FRESH
    // payload so the dialog re-renders its local copy without closing.
    // ------------------------------------------------------------------

    async _loadFoldersForSettings() {
        try {
            return await rpc("/mail_management/folders", {
                include_hidden: true,
            });
        } catch (error) {
            this._notifyError(error);
            return [];
        }
    }

    async setFolderRole(folderId, role) {
        try {
            const result = await rpc("/mail_management/folder/set_role", {
                folder_id: folderId,
                role,
            });
            if (result && result.demoted_folder_name) {
                this.notification.add(
                    _t(
                        "'%s' was demoted to Other (one folder per role).",
                        result.demoted_folder_name,
                    ),
                    { type: "info" },
                );
            }
            await this.loadFolders(); // tree badges/labels follow the roles
            return await this._loadFoldersForSettings();
        } catch (error) {
            this._notifyError(error);
            return false;
        }
    }

    async toggleFolderVisible(folderId, visible) {
        try {
            await rpc("/mail_management/folder/set_visible", {
                folder_id: folderId,
                visible,
            });
            await this.loadFolders(); // hidden folders leave the tree
            return await this._loadFoldersForSettings();
        } catch (error) {
            this._notifyError(error);
            return false;
        }
    }

    hideFolder(folder) {
        return this.toggleFolderVisible(folder.id, false);
    }

    openSettings() {
        this._loadFoldersForSettings().then((folders) => {
            this.dialog.add(SettingsDialog, {
                settings: { ...this.state.settings },
                folders,
                onSave: (payload) => this.saveSettings(payload),
                onOpenSignatures: () => this.openSignatures(),
                onSetFolderRole: (folderId, role) =>
                    this.setFolderRole(folderId, role),
                onToggleFolderVisible: (folderId, visible) =>
                    this.toggleFolderVisible(folderId, visible),
                // PR-2h: Folders-tab create/rename/delete actions.
                onCreateFolder: (mailboxId) =>
                    this.settingsCreateFolder(mailboxId),
                onRenameFolder: (folder) => this.settingsRenameFolder(folder),
                onDeleteFolder: (folder) => this.settingsDeleteFolder(folder),
            });
        });
    }

    openSignatures() {
        // The EXISTING signature management action (the same views/menu
        // reachable from Settings ▸ Signatures), opened straight from
        // the webmail so users create/edit signatures in place.
        this.actionService.doAction(
            "mail_management.action_mail_management_signature",
        );
    }

    // ------------------------------------------------------------------
    // Data loading (read controllers only — ir.rules enforce isolation)
    // ------------------------------------------------------------------

    async loadFolders() {
        this.state.loadingFolders = true;
        try {
            this.state.mailboxes = await rpc("/mail_management/folders");
            this._keepOrAutoSelect();
        } catch (error) {
            this.state.mailboxes = [];
            this._notifyError(error);
        } finally {
            this.state.loadingFolders = false;
        }
    }

    _keepOrAutoSelect() {
        // Keep the current selection if it still exists; else select the
        // first folder of the first mailbox (nice default UX).
        const stillThere = this.state.mailboxes.some((mb) =>
            mb.folders.some((f) => f.id === this.state.selectedFolderId),
        );
        if (!stillThere) {
            const firstMb = this.state.mailboxes[0];
            const inbox =
                firstMb && firstMb.folders.find((f) => f.role === "inbox");
            const firstFolder = inbox || (firstMb && firstMb.folders[0]);
            if (firstMb && firstFolder) {
                this.state.selectedMailboxId = firstMb.id;
                this.state.selectedFolderId = firstFolder.id;
                this.state.selectedFolderName = firstFolder.name;
            } else {
                this.state.selectedMailboxId = false;
                this.state.selectedFolderId = false;
                this.state.selectedFolderName = "";
            }
        }
    }

    async loadMessages() {
        if (!this.state.selectedFolderId) {
            this.state.messages = [];
            this.state.selectedIds = [];
            return;
        }
        this.state.loadingMessages = true;
        try {
            // PR-2f: NO explicit limit — the caller's messages_per_page
            // setting drives the server-side page size.
            const params = {
                folder_id: this.state.selectedFolderId,
                offset: 0,
            };
            // PR-2a: the controller whitelists each of these — a value
            // it does not know falls back to the safe default.
            if (this.state.messageSort) {
                params.sort = this.state.messageSort;
            }
            if (this.state.messagePriority.length) {
                params.priority = [...this.state.messagePriority];
            }
            if (this.state.messageFilter) {
                params.filter = this.state.messageFilter;
            }
            if (this.state.searchQuery) {
                // PR-2d: mailbox-wide search replaces the folder scope
                // (the controller whitelists/escapes the pattern).
                params.search = this.state.searchQuery;
            }
            this.state.messages = await rpc(
                "/mail_management/messages",
                params,
            );
            // Drop selections that are no longer displayed (filter change).
            const visible = new Set(this.state.messages.map((m) => m.id));
            this.state.selectedIds = this.state.selectedIds.filter((id) =>
                visible.has(id),
            );
        } catch (error) {
            this.state.messages = [];
            this._notifyError(error);
        } finally {
            this.state.loadingMessages = false;
        }
    }

    // ------------------------------------------------------------------
    // Selection handlers (bound into the child components)
    // ------------------------------------------------------------------

    /** PR-2j: click a mailbox label in the tree to switch mailbox —
     *  selects its Inbox (or first visible folder) and loads it. */
    /** PR-3a: toggle the assistant 4th column (persisted). */
    toggleAssistant() {
        this.state.assistantOpen = !this.state.assistantOpen;
        localStorage.setItem(
            "o_webmail.assistant_open",
            this.state.assistantOpen ? "1" : "0",
        );
    }

    /** AI draft -> compose dialog prefilled (never auto-sent). */
    openAiDraft(draft) {
        this.openCompose("new", { aiDraft: draft });
    }

    async selectMailbox(mailboxId) {
        const mailbox = this.state.mailboxes.find((m) => m.id === mailboxId);
        if (!mailbox) {
            return;
        }
        if (!mailbox.folders.length) {
            this.notification.add(
                _t(
                    "This mailbox has no folders yet. Run 'Probar conexión' " +
                        "(auto-discovers them) or the gear → 'Buscar carpetas'.",
                ),
                { type: "warning" },
            );
            return;
        }
        const inbox = mailbox.folders.find((f) => f.role === "inbox");
        const target = inbox || mailbox.folders[0];
        if (
            mailboxId === this.state.selectedMailboxId &&
            target.id === this.state.selectedFolderId
        ) {
            return;
        }
        await this.selectFolder(mailboxId, target.id);
    }

    async selectFolder(mailboxId, folderId) {
        const mailbox = this.state.mailboxes.find((m) => m.id === mailboxId);
        const folder =
            mailbox && mailbox.folders.find((f) => f.id === folderId);
        this.state.selectedMailboxId = mailboxId;
        this.state.selectedFolderId = folderId;
        this.state.selectedFolderName = folder ? folder.name : "";
        this.state.selectedMessage = false;
        this.state.selectedMessageId = false;
        this.state.selectedIds = [];
        // Selecting a folder returns to the plain folder view.
        this.state.searchQuery = "";
        // PR-2f: folder open applies the saved default sort until the user
        // picks one for this session; a pending mark-read is moot now.
        this._applyDefaultSort();
        this._cancelMarkReadTimer();
        await this.loadMessages();
    }

    // ------------------------------------------------------------------
    // PR-2f — default sort from the settings (key + optional priority
    // suffix), applied on folder open until the user overrides it.
    // ------------------------------------------------------------------

    _parseDefaultSort(value) {
        const [sort = "date_desc", priority = ""] = String(value || "").split(
            "+",
        );
        return [sort, priority ? [priority] : []];
    }

    _applyDefaultSort() {
        if (this.sortTouched) {
            return;
        }
        const [sort, priority] = this._parseDefaultSort(
            this.state.settings.defaultSort,
        );
        this.state.messageSort = sort;
        this.state.messagePriority = priority;
    }

    // ------------------------------------------------------------------
    // PR-2d — local search (mailbox-wide via the controller `search`
    // param; results replace the folder list until cleared)
    // ------------------------------------------------------------------

    async applySearch(query) {
        this.state.searchQuery = (query || "").trim();
        this.state.selectedIds = [];
        this.state.selectedMessage = false;
        this.state.selectedMessageId = false;
        await this.loadMessages();
    }

    async clearSearch() {
        await this.applySearch("");
    }

    // ------------------------------------------------------------------
    // PR-2a — sorting, priority toggles, quick filters
    // (all delegated to /mail_management/messages whitelists)
    // ------------------------------------------------------------------

    _defaultSortFor(field) {
        return field === "date" ? "date_desc" : field;
    }

    /** PR-2e: absolute sort from the sort menu (the menu items carry
     * the full PR-2a whitelisted key — asc/desc is part of the label). */
    async setSort(sortKey) {
        // PR-2f: an explicit user choice wins over the default for the
        // rest of the session.
        this.sortTouched = true;
        this.state.messageSort = sortKey;
        await this.loadMessages();
    }

    async togglePriority(key) {
        this.sortTouched = true;
        const list = this.state.messagePriority;
        this.state.messagePriority = list.includes(key)
            ? list.filter((k) => k !== key)
            : [...list, key];
        await this.loadMessages();
    }

    async setFilter(filter) {
        this.state.messageFilter = filter || "";
        this.state.selectedIds = [];
        await this.loadMessages();
    }

    async openMessage(message) {
        // PR-2f: navigating to another message cancels any pending
        // deferred mark-read of the previous one.
        this._cancelMarkReadTimer();
        this.state.selectedMessageId = message.id;
        this.state.loadingMessage = true;
        try {
            const payload = await rpc("/mail_management/message_body", {
                message_id: message.id,
            });
            const rawBodyHtml = payload.body_html || "";
            const bodyText = payload.body_text || "";
            // PR-2f: prefer_html picks the rendered source (kept raw on the
            // message so a settings change can re-prepare in place).
            const sourceHtml = chooseBodyHtml(
                rawBodyHtml,
                bodyText,
                this.state.settings.preferHtml,
            );
            this.state.selectedMessage = {
                ...payload,
                rawBodyHtml,
                sourceHtml,
                hasRemoteImages: containsRemoteImages(sourceHtml),
                bodyMarkup: this._buildBodyMarkup(
                    sourceHtml,
                    payload.attachments || [],
                    message.id,
                ),
            };
            if (!message.flag_seen) {
                this._scheduleMarkRead(message);
            }
        } catch (error) {
            this.state.selectedMessage = false;
            this._notifyError(error);
        } finally {
            this.state.loadingMessage = false;
        }
    }

    // ------------------------------------------------------------------
    // PR-2f — mark-as-read timing (0 = instant as before, N = deferred
    // timer cancelled on navigation, -1 = never: no seen call at all).
    // ------------------------------------------------------------------

    _scheduleMarkRead(message) {
        const delay = markReadDelayMs(this.state.settings.markReadAfterSeconds);
        if (delay === null) {
            // -1 = NEVER auto-mark as read.
            return;
        }
        const messageId = message.id;
        const fire = async () => {
            this._markReadTimer = null;
            // The user may have navigated away before the timer fired.
            const open = this.state.selectedMessage;
            if (!open || open.id !== messageId) {
                return;
            }
            const row = this.state.messages.find((m) => m.id === messageId);
            if (row) {
                row.flag_seen = true;
            }
            try {
                // Optimistic local update, then mirror through the
                // controller (ir.rule-checked write).
                await rpc("/mail_management/set_flags", {
                    message_id: messageId,
                    seen: true,
                });
                this._decrementUnread();
            } catch (error) {
                this._notifyError(error);
            }
        };
        if (delay === 0) {
            fire();
            return;
        }
        this._markReadTimer = setTimeout(fire, delay);
    }

    _cancelMarkReadTimer() {
        if (this._markReadTimer) {
            clearTimeout(this._markReadTimer);
            this._markReadTimer = null;
        }
    }

    _decrementUnread() {
        for (const mb of this.state.mailboxes) {
            for (const folder of mb.folders) {
                if (folder.id === this.state.selectedFolderId) {
                    folder.unread_count = Math.max(0, folder.unread_count - 1);
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // PR-2e/PR-2f — remote-image policy: the setting applies to every
    // message; the per-message "Show images" bar reveals ONE message
    // only; whitelisted senders are allowed even when globally blocked.
    // ------------------------------------------------------------------

    _remoteImagesAllowedFor(messageId) {
        const message = this.state.selectedMessage;
        const sender =
            message && message.id === messageId ? message.email_from || "" : "";
        return (
            this.state.settings.allowRemoteImages ||
            this.state.revealedImageIds.includes(messageId) ||
            senderInWhitelist(sender, this.state.settings.imageWhitelist)
        );
    }

    get imagesBlockedForOpen() {
        const message = this.state.selectedMessage;
        return Boolean(
            message &&
                message.hasRemoteImages &&
                !this._remoteImagesAllowedFor(message.id),
        );
    }

    showImages() {
        const message = this.state.selectedMessage;
        if (!message || this._remoteImagesAllowedFor(message.id)) {
            return;
        }
        // Reveal THIS message only (never a setting change, never other
        // messages — the id list is per-message by construction).
        this.state.revealedImageIds = [
            ...this.state.revealedImageIds,
            message.id,
        ];
        this._reprepareOpenBody();
    }

    /** Shared body preparation: the PR-2f pure policy pipeline (strip
     * styles → collapse quotes) on top of the chosen source, then the
     * PR-2e remote-image policy (setting, reveal or whitelist) + cid
     * resolution.  The result is wrapped as a Markup for t-out. */
    _buildBodyMarkup(rawBodyHtml, attachments, messageId) {
        let html = rawBodyHtml;
        if (this.state.settings.stripMessageStyles) {
            html = stripMessageStyles(html);
        }
        if (this.state.settings.collapseQuotedText) {
            html = collapseQuotes(html, {
                collapse: true,
                maxLevels: this.state.settings.maxQuoteLevels,
                quoteLabel: _t("Show quoted text"),
                moreLabel: _t("Show more quote"),
            });
        }
        const bodyHtml = prepareBody(html, attachments, {
            allowRemoteImages: this._remoteImagesAllowedFor(messageId),
        });
        return markup(bodyHtml);
    }

    _reprepareOpenBody() {
        const message = this.state.selectedMessage;
        if (!message || message.rawBodyHtml === undefined) {
            return;
        }
        // prefer_html may have changed → re-choose the source first.
        message.sourceHtml = chooseBodyHtml(
            message.rawBodyHtml,
            message.body_text || "",
            this.state.settings.preferHtml,
        );
        message.hasRemoteImages = containsRemoteImages(message.sourceHtml);
        message.bodyMarkup = this._buildBodyMarkup(
            message.sourceHtml,
            message.attachments || [],
            message.id,
        );
    }

    // ------------------------------------------------------------------
    // PR-2a — star toggles, mark-unread, selection + bulk actions
    // (local-first via /mail_management/message/set_flags; the IMAP
    // mirror is best-effort server-side and never fails the request)
    // ------------------------------------------------------------------

    async toggleStar(message) {
        if (!message) {
            return;
        }
        const next = !message.flag_flagged;
        // Optimistic flip (list row + reading pane), reverted on error.
        message.flag_flagged = next;
        this._syncSelectedMessageFlag(message.id, "flag_flagged", next);
        try {
            await rpc("/mail_management/message/set_flags", {
                message_ids: [message.id],
                flags: { flagged: next },
            });
        } catch (error) {
            message.flag_flagged = !next;
            this._syncSelectedMessageFlag(message.id, "flag_flagged", !next);
            this._notifyError(error);
        }
    }

    async markUnread(message) {
        const target = message || this.state.selectedMessage;
        if (!target) {
            return;
        }
        // PR-2f: the user explicitly wants it unread — a pending deferred
        // mark-read must not fight back.
        this._cancelMarkReadTimer();
        try {
            await rpc("/mail_management/message/set_flags", {
                message_ids: [target.id],
                flags: { seen: false },
            });
            const listMsg = this.state.messages.find((m) => m.id === target.id);
            if (listMsg) {
                listMsg.flag_seen = false;
            }
            this._syncSelectedMessageFlag(target.id, "flag_seen", false);
            await this.loadFolders(); // unread badges recomputed server-side
        } catch (error) {
            this._notifyError(error);
        }
    }

    _syncSelectedMessageFlag(messageId, key, value) {
        const selected = this.state.selectedMessage;
        if (selected && selected.id === messageId) {
            selected[key] = value;
        }
    }

    toggleSelect(messageId, checked) {
        const selected = new Set(this.state.selectedIds);
        if (checked) {
            selected.add(messageId);
        } else {
            selected.delete(messageId);
        }
        this.state.selectedIds = [...selected];
    }

    toggleSelectAll(checked) {
        this.state.selectedIds = checked
            ? this.state.messages.map((m) => m.id)
            : [];
    }

    async bulkSetFlags(flags) {
        const ids = [...this.state.selectedIds];
        if (!ids.length) {
            return;
        }
        try {
            await rpc("/mail_management/message/set_flags", {
                message_ids: ids,
                flags,
            });
            this.state.selectedIds = [];
            await this.loadFolders(); // unread_count badges
            await this.loadMessages(); // list rows reflect the new flags
        } catch (error) {
            this._notifyError(error);
        }
    }

    // ------------------------------------------------------------------
    // PR-2b — folders & movement (IMAP-first server endpoints; a server
    // failure surfaces as a notification and nothing changes locally)
    // ------------------------------------------------------------------

    get moveTargets() {
        const mailbox = this.state.mailboxes.find(
            (mb) => mb.id === this.state.selectedMailboxId,
        );
        if (!mailbox) {
            return [];
        }
        return mailbox.folders
            .filter((f) => f.id !== this.state.selectedFolderId)
            .map((f) => ({ id: f.id, label: f.label || f.name }));
    }

    get viewingTrash() {
        const mailbox = this.state.mailboxes.find(
            (mb) => mb.id === this.state.selectedMailboxId,
        );
        const folder =
            mailbox &&
            mailbox.folders.find((f) => f.id === this.state.selectedFolderId);
        return Boolean(folder && folder.role === "trash");
    }

    // -- folder CRUD (tree header "+" + per-folder menu) -------------------

    openCreateFolderDialog() {
        if (!this.state.mailboxes.length) {
            return;
        }
        this.dialog.add(FolderDialog, {
            mode: "create",
            mailboxes: this.state.mailboxes.map((mb) => ({
                id: mb.id,
                name: mb.name,
            })),
            mailboxId: this.state.selectedMailboxId,
            onConfirm: (payload) => this.createFolder(payload),
        });
    }

    async createFolder({ mailboxId, name }) {
        try {
            await rpc("/mail_management/folder/create", {
                mailbox_id: mailboxId,
                name,
            });
            this.notification.add(_t("Folder created."), { type: "info" });
            await this.loadFolders();
        } catch (error) {
            this._notifyError(error);
        }
    }

    openRenameFolderDialog(folder) {
        this.dialog.add(FolderDialog, {
            mode: "rename",
            folder,
            onConfirm: (payload) => this.renameFolder(payload),
        });
    }

    async renameFolder({ folder, name }) {
        try {
            await rpc("/mail_management/folder/rename", {
                folder_id: folder.id,
                name,
            });
            this.notification.add(_t("Folder renamed."), { type: "info" });
            await this.loadFolders();
        } catch (error) {
            this._notifyError(error);
        }
    }

    onDeleteFolder(folder) {
        // PR-2h: GMail-style soft-delete confirmation — the message
        // states the retention window read from the SAVED setting.
        this._confirmDeleteFolder(folder).then((confirmed) => {
            if (confirmed) {
                this.deleteFolder(folder);
            }
        });
    }

    /** PR-2h: the confirm body names the folder, its message count and
     * the retention window (days) from the user's saved settings. */
    _folderDeleteBody(folder) {
        return _t(
            "Delete the folder '%s' with %s message(s)? Messages will " +
                "be moved to Trash and automatically deleted after %s days.",
            folder.label || folder.name,
            folder.message_count,
            this.state.settings.trashRetentionDays,
        );
    }

    _confirmDeleteFolder(folder) {
        return new Promise((resolve) => {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Delete folder"),
                body: this._folderDeleteBody(folder),
                confirmLabel: _t("Delete"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            });
        });
    }

    async deleteFolder(folder) {
        try {
            const result = await rpc("/mail_management/folder/delete", {
                folder_id: folder.id,
            });
            const moved = (result && result.moved_count) || 0;
            if (moved) {
                this.notification.add(
                    _t("Folder deleted. %s message(s) moved to Trash.", moved),
                    { type: "info" },
                );
            } else {
                this.notification.add(_t("Folder deleted."), { type: "info" });
            }
            await this.loadFolders(); // _keepOrAutoSelect re-selects when
            // the deleted folder was the current view.
            await this.loadMessages();
        } catch (error) {
            this._notifyError(error);
        }
    }

    // -- PR-2h: Folders-tab actions (Settings ▸ Folders).  Each resolves
    // the FRESH all-folders payload so the dialog re-renders its local
    // copy without closing (the same contract as setFolderRole). ------

    settingsCreateFolder(mailboxId) {
        return new Promise((resolve) => {
            this.dialog.add(FolderDialog, {
                mode: "create",
                mailboxes: this.state.mailboxes.map((mb) => ({
                    id: mb.id,
                    name: mb.name,
                })),
                mailboxId,
                onConfirm: async (payload) => {
                    await this.createFolder(payload);
                    resolve(await this._loadFoldersForSettings());
                },
            });
        });
    }

    settingsRenameFolder(folder) {
        return new Promise((resolve) => {
            this.dialog.add(FolderDialog, {
                mode: "rename",
                folder,
                onConfirm: async (payload) => {
                    await this.renameFolder(payload);
                    resolve(await this._loadFoldersForSettings());
                },
            });
        });
    }

    async settingsDeleteFolder(folder) {
        const confirmed = await this._confirmDeleteFolder(folder);
        if (!confirmed) {
            return false;
        }
        try {
            await rpc("/mail_management/folder/delete", {
                folder_id: folder.id,
            });
            this.notification.add(_t("Folder deleted."), { type: "info" });
            await this.loadFolders(); // tree follows the deletion
            return await this._loadFoldersForSettings();
        } catch (error) {
            this._notifyError(error);
            return false;
        }
    }

    // -- message movement (bulk bar + reading pane) ----------------------

    /** PR-2f: which message to re-open after a move/delete removes the
     * currently open one (next in list; previous when it was the last). */
    _captureNextAfterRemoval(ids) {
        const openId = this.state.selectedMessageId;
        if (
            !this.state.settings.showNextOnMoveDelete ||
            !openId ||
            !ids.includes(openId)
        ) {
            return false;
        }
        return nextMessageId(
            this.state.messages.map((m) => m.id),
            openId,
        );
    }

    async _selectAfterRemoval(nextId) {
        if (!nextId) {
            return;
        }
        const target = this.state.messages.find((m) => m.id === nextId);
        if (target) {
            await this.openMessage(target);
        }
    }

    async moveMessages(ids, folderId) {
        if (!ids.length || !folderId) {
            return;
        }
        const reopen = this._captureNextAfterRemoval(ids);
        try {
            await rpc("/mail_management/message/move", {
                message_ids: ids,
                folder_id: folderId,
            });
            this.notification.add(_t("Message(s) moved."), { type: "info" });
            this.state.selectedIds = [];
            this.state.selectedMessage = false;
            this.state.selectedMessageId = false;
            this._cancelMarkReadTimer();
            await this.loadFolders();
            await this.loadMessages();
            await this._selectAfterRemoval(reopen);
        } catch (error) {
            this._notifyError(error);
        }
    }

    async bulkMoveToFolder(folderId) {
        await this.moveMessages([...this.state.selectedIds], folderId);
    }

    async moveOpenMessage(folderId) {
        await this.moveMessages([this.state.selectedMessageId], folderId);
    }

    async deleteMessages(ids) {
        if (!ids.length) {
            return;
        }
        const reopen = this._captureNextAfterRemoval(ids);
        try {
            await rpc("/mail_management/message/delete", {
                message_ids: ids,
            });
            this.notification.add(_t("Message(s) deleted."), {
                type: "info",
            });
            this.state.selectedIds = [];
            this.state.selectedMessage = false;
            this.state.selectedMessageId = false;
            this._cancelMarkReadTimer();
            await this.loadFolders();
            await this.loadMessages();
            await this._selectAfterRemoval(reopen);
        } catch (error) {
            this._notifyError(error);
        }
    }

    async bulkDeleteMessages() {
        if (this.viewingTrash) {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Delete permanently"),
                body: _t(
                    "Permanently delete the %s selected message(s)? This cannot be undone.",
                    this.state.selectedIds.length,
                ),
                confirmLabel: _t("Delete permanently"),
                confirm: () => this.deleteMessages([...this.state.selectedIds]),
                cancel: () => {},
            });
        } else {
            await this.deleteMessages([...this.state.selectedIds]);
        }
    }

    async deleteOpenMessage() {
        if (this.viewingTrash) {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Delete permanently"),
                body: _t(
                    "Permanently delete this message? This cannot be undone.",
                ),
                confirmLabel: _t("Delete permanently"),
                confirm: () =>
                    this.deleteMessages([this.state.selectedMessageId]),
                cancel: () => {},
            });
        } else {
            await this.deleteMessages([this.state.selectedMessageId]);
        }
    }

    // ------------------------------------------------------------------
    // On-demand refresh (PR-2e: consolidated into the tree header ⋯
    // menu — Refresh all runs every mailbox of the current user through
    // the existing sync engine via /mail_management/refresh).
    // ------------------------------------------------------------------

    async refreshAll() {
        if (this.state.refreshing) {
            return;
        }
        this.state.refreshing = true;
        try {
            // No mailbox_id → every mailbox of the CURRENT user (the
            // controller runs through request.env → per-user ir.rules);
            // per-mailbox sync failures are caught server-side.
            await rpc("/mail_management/refresh", {});
            this.notification.add(_t("All mailboxes refreshed."), {
                type: "info",
            });
            await this.loadFolders();
            await this.loadMessages();
        } catch (error) {
            this._notifyError(error);
        } finally {
            this.state.refreshing = false;
        }
    }

    openAddMailbox() {
        // The STANDARD mail.management.mailbox form in a dialog on top of
        // the webmail (the full config stays in My Mailboxes).  The tree
        // reloads when the dialog closes so a created mailbox appears.
        this.actionService.doAction(
            {
                type: "ir.actions.act_window",
                name: _t("Add mailbox"),
                res_model: "mail.management.mailbox",
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
            },
            { on_close: () => this.loadFolders() },
        );
    }

    /** PR-2j: per-mailbox config — opens THAT mailbox's form. */
    configureMailbox(mailboxId) {
        this.actionService.doAction(
            "mail_management.action_mail_management_mailbox",
            {
                res_id: mailboxId,
                views: [[false, "form"]],
            },
        );
    }

    openMailboxConfig() {
        this.actionService.doAction(
            "mail_management.action_mail_management_mailbox",
        );
    }

    // ------------------------------------------------------------------
    // Compose (PR-1c + PR-2c): reply / reply-all / forward / new
    // ------------------------------------------------------------------

    openCompose(mode, extra = {}) {
        if (!this.state.selectedMailboxId) {
            return;
        }
        // 'new' needs no opened message — it composes from scratch with
        // the mailbox default signature.
        if (mode !== "new" && !this.state.selectedMessage) {
            return;
        }
        this.dialog.add(ComposeDialog, {
            ...extra,
            mode,
            message: mode === "new" ? {} : this.state.selectedMessage,
            mailboxId: this.state.selectedMailboxId,
            signaturePosition: this.state.settings.signaturePosition,
            // PR-2g: compose settings — editor mode + spellcheck attr.
            composeEditor: this.state.settings.composeEditor,
            enableSpellcheck: this.state.settings.enableSpellcheck,
            onSent: async () => {
                // Refresh so the Sent list shows the new local mirror and
                // the original shows its \Answered state after a reply.
                await this.loadFolders();
                await this.loadMessages();
            },
        });
    }

    // ------------------------------------------------------------------
    // Errors
    // ------------------------------------------------------------------

    _notifyError(error) {
        const message =
            (error && error.data && error.data.message) ||
            (error && error.message) ||
            _t("Something went wrong. Check the server logs.");
        this.notification.add(message, {
            type: "danger",
            title: _t("Webmail"),
        });
    }
}

registry.category("actions").add("mail_management.webmail", WebmailClient);

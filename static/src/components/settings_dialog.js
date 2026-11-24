/** @odoo-module **/

/* PR-2e + PR-2f + PR-2g — SettingsDialog: the SnappyMail-style webmail
 * settings panel, TABBED:
 *
 * - "General" — theme, unread badge, remote images, signature position,
 *   the compose settings (editor, reply save folder, read/delivery
 *   receipts, browser spellchecker);
 * - "Folders" (PR-2g) — per-mailbox system-folder MAPPING (role dropdowns;
 *   the Inbox role is fixed) + hide/restore toggles (hidden folders are
 *   listed here and stay synced);
 * - "Message list" — messages per page, default sort (+ optional priority),
 *   show checkboxes;
 * - "Message view" — mark-read delay, collapse quoted text + max levels,
 *   remove message colors, prefer HTML, simple attachment list, show next
 *   on move/delete, and the per-sender image whitelist;
 * - "Accounts & signatures" — the EXISTING signature management action.
 *
 * Purely presentational for the SAVE path: it edits a local copy of the
 * settings and hands it to the parent on Save (the parent owns the RPC +
 * live application; the server clamps/normalizes every value).  The Folders
 * tab actions (role/visibility) apply IMMEDIATELY through the parent
 * callbacks — each returns the fresh all-folders payload which replaces the
 * local copy (mapping is a per-folder action, not a form submit).
 */

import { Component, proxy, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

// PR-2h — mapped system roles whose Delete action is disabled (the
// server refuses them; unmap the role first).
const SYSTEM_FOLDER_ROLES = ["inbox", "sent", "drafts", "trash", "junk"];

export class SettingsDialog extends Component {
    static template = "mail_management.SettingsDialog";
    static components = { Dialog };
    props = useProps({
        /** Current settings (camelCase, imageWhitelist is an array). */
        settings: t.object(),
        /** PR-2g: ALL folders (incl. hidden) per mailbox, each with
         * role/visible, plus the server-translated role_labels map. */
        folders: t.array(t.any()).optional(),
        /** PR-2g: apply a role change; resolves the fresh folders payload. */
        onSetFolderRole: t.function().optional(),
        /** PR-2g: hide/restore a folder; resolves the fresh payload. */
        onToggleFolderVisible: t.function().optional(),
        /** PR-2h: create a folder in a mailbox; resolves the fresh payload. */
        onCreateFolder: t.function().optional(),
        /** PR-2h: rename a folder; resolves the fresh payload. */
        onRenameFolder: t.function().optional(),
        /** PR-2h: delete a folder (soft semantics); resolves the fresh
         * payload (false when the confirmation was cancelled). */
        onDeleteFolder: t.function().optional(),
        /** Called with the new settings on Save. */
        onSave: t.function(),
        /** Opens the existing signature management action. */
        onOpenSignatures: t.function().optional(),
        /** Injected automatically by the dialog service. */
        close: t.function().optional(),
    });

    setup() {
        const s = this.props.settings;
        // Local draft — nothing persists until Save (Folders tab actions
        // apply immediately instead, see onSetFolderRole).
        this.state = proxy({
            activeTab: "general",
            allowRemoteImages: Boolean(s.allowRemoteImages),
            signaturePosition:
                s.signaturePosition === "above" ? "above" : "below",
            messagesPerPage: s.messagesPerPage ?? 50,
            defaultSortKey: "date_desc",
            defaultPriority: "",
            showCheckboxes: s.showCheckboxes !== false,
            markReadAfterSeconds: s.markReadAfterSeconds ?? 0,
            collapseQuotedText: s.collapseQuotedText !== false,
            maxQuoteLevels: s.maxQuoteLevels ?? 3,
            stripMessageStyles: Boolean(s.stripMessageStyles),
            preferHtml: s.preferHtml !== false,
            simpleAttachmentList: Boolean(s.simpleAttachmentList),
            showNextOnMoveDelete: s.showNextOnMoveDelete !== false,
            imageWhitelistText: (s.imageWhitelist || []).join("\n"),
            // PR-2g: compose + general settings.
            composeEditor: s.composeEditor === "plain" ? "plain" : "html",
            replySaveFolder: s.replySaveFolder === "same" ? "same" : "sent",
            requestReadReceipt: Boolean(s.requestReadReceipt),
            requestDeliveryReceipt: Boolean(s.requestDeliveryReceipt),
            enableSpellcheck: s.enableSpellcheck !== false,
            showUnreadBadge: s.showUnreadBadge !== false,
            theme: [
                "light",
                "dark",
                "night",
                "sepia",
                "ocean",
                "forest",
            ].includes(s.theme)
                ? s.theme
                : "light",
            // PR-2h: Trash retention window (days, clamped 1-365 by the
            // server; saved with the other settings on Save).
            trashRetentionDays: s.trashRetentionDays ?? 30,
            // PR-2g: local copy of the all-folders payload (the parent's
            // fresh response replaces it after each mapping action).
            folders: this.props.folders || [],
        });
        // The stored default_sort is "<key>" or "<key>+<priority>".
        const [sortKey, priority] = String(s.defaultSort || "date_desc").split(
            "+",
        );
        this.state.defaultSortKey = sortKey || "date_desc";
        this.state.defaultPriority = priority || "";
    }

    // -- tabs ------------------------------------------------------------

    get tabs() {
        return [
            { id: "general", label: _t("General") },
            { id: "folders", label: _t("Folders") },
            { id: "message_list", label: _t("Message list") },
            { id: "message_view", label: _t("Message view") },
            { id: "accounts", label: _t("Accounts & signatures") },
        ];
    }

    setActive(tabId) {
        this.state.activeTab = tabId;
    }

    isActive(tabId) {
        return this.state.activeTab === tabId;
    }

    // -- Folders tab (PR-2g) ----------------------------------------------

    /** Mappable roles for the dropdown: every server role EXCEPT the fixed
     * Inbox.  Labels come translated from the server payload (one source of
     * truth — no duplicate msgids in JS). */
    folderRoleOptions(mailbox) {
        const labels = mailbox.role_labels || {};
        return ["sent", "drafts", "trash", "junk", "archive", "all", "other"]
            .filter((role) => labels[role])
            .map((role) => ({ key: role, label: labels[role] }));
    }

    async onFolderRoleChange(folder, ev) {
        const role = ev.target.value;
        // Optimistically show the choice; the parent's fresh payload (or the
        // old value on error) replaces the whole local copy.
        folder.role = role;
        const fresh = await this.props.onSetFolderRole?.(folder.id, role);
        if (Array.isArray(fresh)) {
            this.state.folders = fresh;
        }
    }

    async onFolderVisibleChange(folder, ev) {
        const visible = ev.target.checked;
        folder.visible = visible;
        const fresh = await this.props.onToggleFolderVisible?.(
            folder.id,
            visible,
        );
        if (Array.isArray(fresh)) {
            this.state.folders = fresh;
        }
    }

    // -- PR-2h: per-mailbox create + per-folder rename/delete ----------

    /** Mapped system folders refuse deletion server-side — the Delete
     * action is disabled with an explanatory tooltip. */
    isSystemFolder(folder) {
        return SYSTEM_FOLDER_ROLES.includes(folder.role);
    }

    async createFolderIn(mailbox) {
        const fresh = await this.props.onCreateFolder?.(mailbox.id);
        if (Array.isArray(fresh)) {
            this.state.folders = fresh;
        }
    }

    async renameFolder(folder) {
        const fresh = await this.props.onRenameFolder?.(folder);
        if (Array.isArray(fresh)) {
            this.state.folders = fresh;
        }
    }

    async deleteFolder(folder) {
        const fresh = await this.props.onDeleteFolder?.(folder);
        if (Array.isArray(fresh)) {
            this.state.folders = fresh;
        }
    }

    get hiddenLabel() {
        return _t("Hidden");
    }

    get createFolderLabel() {
        return _t("Create folder");
    }

    get renameLabel() {
        return _t("Rename");
    }

    get deleteLabel() {
        return _t("Delete");
    }

    get systemFolderDeleteTooltip() {
        return _t(
            "Mapped system folders cannot be deleted. Unmap the role first.",
        );
    }

    get trashRetentionLabel() {
        return _t("Trash retention (days)");
    }

    get trashRetentionHelp() {
        return _t(
            "Trashed messages are permanently deleted after this many days (1-365).",
        );
    }

    get foldersHelp() {
        return _t(
            "Map which folder is used for Sent, Drafts, Junk, Trash and " +
                "Archive (the Inbox is fixed). Hidden folders stay synced " +
                "but leave the folder tree.",
        );
    }

    // -- labels (General + compose, PR-2g) ---------------------------------

    get title() {
        return _t("Settings");
    }

    get themeLabel() {
        return _t("Theme");
    }

    get lightLabel() {
        return _t("Light");
    }

    get darkLabel() {
        return _t("Dark");
    }

    get nightLabel() {
        return _t("Night (OLED)");
    }

    get sepiaLabel() {
        return _t("Sepia");
    }

    get oceanLabel() {
        return _t("Ocean");
    }

    get forestLabel() {
        return _t("Forest");
    }

    get unreadBadgeLabel() {
        return _t("Show unread count badge");
    }

    get composeEditorLabel() {
        return _t("Default editor");
    }

    get htmlEditorLabel() {
        return _t("HTML editor");
    }

    get plainEditorLabel() {
        return _t("Plain text");
    }

    get replySaveLabel() {
        return _t("Save replies in");
    }

    get replySaveSentLabel() {
        return _t("Sent folder");
    }

    get replySaveSameLabel() {
        return _t("Folder of the message being replied to");
    }

    get readReceiptLabel() {
        return _t("Request read confirmation");
    }

    get deliveryReceiptLabel() {
        return _t("Request delivery confirmation");
    }

    get spellcheckLabel() {
        return _t("Use browser spellchecker");
    }

    get remoteImagesLabel() {
        return _t("Allow remote images");
    }

    get remoteImagesHelp() {
        return _t(
            "Remote images can be used to track you. They are blocked by default.",
        );
    }

    get signaturePositionLabel() {
        return _t("Signature position");
    }

    get belowLabel() {
        return _t("Below the quote");
    }

    get aboveLabel() {
        return _t("Above the quote");
    }

    // -- labels (Message list / Message view — PR-2f) -----------------------

    get messagesPerPageLabel() {
        return _t("Messages per page");
    }

    get messagesPerPageHelp() {
        return _t("How many messages the list shows per page (10-200).");
    }

    get defaultSortLabel() {
        return _t("Default sort");
    }

    get priorityLabel() {
        return _t("Priority");
    }

    get noneLabel() {
        return _t("None");
    }

    get sortOptions() {
        // The same whitelisted keys (and labels) as the list's Sort menu.
        return [
            { key: "date_desc", label: _t("Date (newest first)") },
            { key: "date", label: _t("Date (oldest first)") },
            { key: "from", label: _t("From (A→Z)") },
            { key: "from_desc", label: _t("From (Z→A)") },
            { key: "subject", label: _t("Subject (A→Z)") },
            { key: "subject_desc", label: _t("Subject (Z→A)") },
        ];
    }

    get priorityOptions() {
        return [
            { key: "unread_first", label: _t("Unread first") },
            { key: "flagged_first", label: _t("Starred first") },
        ];
    }

    get showCheckboxesLabel() {
        return _t("Show checkboxes");
    }

    get showCheckboxesHelp() {
        return _t(
            "Show the selection checkboxes and the bulk-actions bar in the list.",
        );
    }

    get markReadLabel() {
        return _t("Mark as read after (seconds)");
    }

    get markReadHelp() {
        return _t(
            "0 marks the message as read immediately; a positive number waits " +
                "that many seconds; -1 never marks it automatically.",
        );
    }

    get collapseQuotesLabel() {
        return _t("Collapse quoted text");
    }

    get maxQuoteLevelsLabel() {
        return _t("Max quote levels");
    }

    get stripStylesLabel() {
        return _t("Remove message colors");
    }

    get stripStylesHelp() {
        return _t("Remove background and text colors from message bodies.");
    }

    get preferHtmlLabel() {
        return _t("Prefer HTML over plain text");
    }

    get simpleAttachmentsLabel() {
        return _t("Simple attachment list");
    }

    get nextOnDeleteLabel() {
        return _t("Show next message after move/delete");
    }

    get whitelistLabel() {
        return _t("Trusted senders (images)");
    }

    get whitelistHelp() {
        return _t(
            "One email address per line. Remote images from these senders " +
                "are always shown.",
        );
    }

    get manageSignaturesLabel() {
        return _t("Manage signatures…");
    }

    get saveLabel() {
        return _t("Save");
    }

    get closeLabel() {
        return _t("Close");
    }

    // -- handlers --------------------------------------------------------

    toggleRemoteImages(ev) {
        this.state.allowRemoteImages = ev.target.checked;
    }

    /** Generic switch handler — the input carries data-setting. */
    toggleSetting(ev) {
        const key = ev.target.dataset.setting;
        this.state[key] = ev.target.checked;
    }

    /** Generic selection handler — the select carries data-setting. */
    setSelection(ev) {
        const key = ev.target.dataset.setting;
        this.state[key] = ev.target.value;
    }

    setPosition(ev) {
        this.state.signaturePosition = ev.target.value;
    }

    setNumber(ev) {
        const key = ev.target.dataset.setting;
        this.state[key] = Number(ev.target.value);
    }

    setSortKey(ev) {
        this.state.defaultSortKey = ev.target.value;
    }

    setPriority(ev) {
        this.state.defaultPriority = ev.target.value;
    }

    setWhitelist(ev) {
        this.state.imageWhitelistText = ev.target.value;
    }

    openSignatures() {
        this.props.onOpenSignatures?.();
    }

    save() {
        this.props.onSave({
            allowRemoteImages: this.state.allowRemoteImages,
            signaturePosition: this.state.signaturePosition,
            messagesPerPage: this.state.messagesPerPage,
            defaultSort: this.state.defaultPriority
                ? `${this.state.defaultSortKey}+${this.state.defaultPriority}`
                : this.state.defaultSortKey,
            showCheckboxes: this.state.showCheckboxes,
            markReadAfterSeconds: this.state.markReadAfterSeconds,
            collapseQuotedText: this.state.collapseQuotedText,
            maxQuoteLevels: this.state.maxQuoteLevels,
            stripMessageStyles: this.state.stripMessageStyles,
            preferHtml: this.state.preferHtml,
            simpleAttachmentList: this.state.simpleAttachmentList,
            showNextOnMoveDelete: this.state.showNextOnMoveDelete,
            imageWhitelistText: this.state.imageWhitelistText,
            composeEditor: this.state.composeEditor,
            replySaveFolder: this.state.replySaveFolder,
            requestReadReceipt: this.state.requestReadReceipt,
            requestDeliveryReceipt: this.state.requestDeliveryReceipt,
            enableSpellcheck: this.state.enableSpellcheck,
            showUnreadBadge: this.state.showUnreadBadge,
            theme: this.state.theme,
            trashRetentionDays: this.state.trashRetentionDays,
        });
        this.props.close?.();
    }
}

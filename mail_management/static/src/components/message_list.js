/** @odoo-module **/

/* PR-1b-ii pane 2 + PR-2a message-list power + PR-2e consolidation.
 *
 * PR-2e: the 3 clickable column headers and the 2 separate priority
 * toggles are replaced by ONE "Sort" dropdown menu (date/from/subject ×
 * asc/desc + "Unread first"/"Starred first" checkboxes).  The CONTROLLER
 * contract is unchanged — the menu still emits the PR-2a whitelisted keys
 * through the same onSetSort/onTogglePriority props.
 *
 * Quick-filter chips (All/Unread/Starred/Attachments), per-row star toggle,
 * checkboxes + select-all and the bulk bar (PR-2e: icon-only buttons with
 * tooltips) stay.  Pure presentational: every action is delegated to the
 * parent through the on* props. */

import { Component, onWillUnmount, proxy, t, useProps } from "@odoo/owl";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";

export class MessageList extends Component {
    static template = "mail_management.MessageList";
    props = useProps({
        messages: t.array(t.any()),
        loading: t.boolean(),
        selectedMessageId: t.any().optional(),
        sortBy: t.string().optional(),
        priority: t.array(t.any()).optional(),
        filter: t.string().optional(),
        selectedIds: t.array(t.any()).optional(),
        folderName: t.string().optional(),
        /** PR-2f: settings.show_checkboxes — hides the checkbox column
         * and the bulk bar when False. */
        showCheckboxes: t.boolean().optional(),
        /** PR-2b: move destinations ([{id, label}]) — excludes the current
         * folder; shared Delete handler (trash semantics server-side). */
        moveTargets: t.array(t.any()).optional(),
        isTrash: t.boolean().optional(),
        onOpenMessage: t.function(),
        /** PR-2e: absolute sort key from the sort menu (PR-2a whitelist). */
        onSetSort: t.function().optional(),
        onTogglePriority: t.function().optional(),
        onSetFilter: t.function().optional(),
        onToggleStar: t.function().optional(),
        onToggleSelect: t.function().optional(),
        onToggleSelectAll: t.function().optional(),
        onBulkSetFlags: t.function().optional(),
        onMoveToFolder: t.function().optional(),
        onDeleteMessages: t.function().optional(),
        /** PR-2c: open a fresh compose dialog ('new' mode). */
        onComposeNew: t.function().optional(),
        /** PR-2d: the APPLIED search query ("" = plain folder view); the
         * component keeps only a local input DRAFT (debounced apply). */
        searchQuery: t.string().optional(),
        onSearch: t.function().optional(),
        onClearSearch: t.function().optional(),
    });

    setup() {
        // PR-2d search box: local draft + debounce timer.  The applied
        // query lives in the parent (state.searchQuery) — every fetch goes
        // through the parent's RPC so the component stays presentational.
        this.search = proxy({ draft: "", timer: null });
        // PR-2e sort menu: local open state only.
        this.menu = proxy({ openSort: false });
        onWillUnmount(() => this._clearSearchTimer());
    }

    // -- PR-2e sort menu ----------------------------------------------------

    get sortMenuLabel() {
        return _t("Sort");
    }

    get sortItems() {
        return [
            { key: "date_desc", label: _t("Date (newest first)") },
            { key: "date", label: _t("Date (oldest first)") },
            { key: "from", label: _t("From (A→Z)") },
            { key: "from_desc", label: _t("From (Z→A)") },
            { key: "subject", label: _t("Subject (A→Z)") },
            { key: "subject_desc", label: _t("Subject (Z→A)") },
        ];
    }

    get unreadFirstLabel() {
        return _t("Unread first");
    }

    get starredFirstLabel() {
        return _t("Starred first");
    }

    toggleSortMenu() {
        this.menu.openSort = !this.menu.openSort;
    }

    isSortActive(key) {
        return (this.props.sortBy || "date_desc") === key;
    }

    isPriorityActive(key) {
        return (this.props.priority || []).includes(key);
    }

    applySort(key) {
        this.menu.openSort = false;
        this.props.onSetSort?.(key);
    }

    togglePriority(key) {
        this.props.onTogglePriority?.(key);
    }

    // -- filter chips --------------------------------------------------------

    isFilterActive(chip) {
        return (this.props.filter || "") === chip;
    }

    // -- selection ---------------------------------------------------------

    get showCheckboxes() {
        return this.props.showCheckboxes !== false;
    }

    get selectedCount() {
        return (this.props.selectedIds || []).length;
    }

    isSelected(messageId) {
        return (this.props.selectedIds || []).includes(messageId);
    }

    get allSelected() {
        return (
            this.props.messages.length > 0 &&
            this.props.messages.every((m) => this.isSelected(m.id))
        );
    }

    // -- PR-2b movement (PR-2e: icon-only bulk bar with tooltips) -----------

    get moveTargets() {
        return this.props.moveTargets || [];
    }

    get deleteLabel() {
        return this.props.isTrash ? _t("Delete permanently") : _t("Delete");
    }

    get filterAllLabel() {
        return _t("All");
    }

    get filterUnreadLabel() {
        return _t("Unread");
    }

    get filterStarredLabel() {
        return _t("Starred");
    }

    get filterAttachmentsLabel() {
        return _t("Attachments");
    }

    get fromLabel() {
        return _t("From");
    }

    get subjectLabel() {
        return _t("Subject");
    }

    get dateLabel() {
        return _t("Date");
    }

    get selectedLabel() {
        return _t("selected");
    }

    get markReadLabel() {
        return _t("Mark read");
    }

    get markUnreadLabel() {
        return _t("Mark unread");
    }

    get starLabel() {
        return _t("Star");
    }

    get unstarLabel() {
        return _t("Unstar");
    }

    get moveToLabel() {
        return _t("Move to…");
    }

    bulkMove(ev) {
        const folderId = Number(ev.target.value);
        if (folderId) {
            this.props.onMoveToFolder?.(folderId);
        }
        ev.target.value = "";
    }

    // -- PR-2d search -------------------------------------------------------

    get searchPlaceholder() {
        return _t("Search in mailbox…");
    }

    get searchLabel() {
        return _t("Search");
    }

    get searchIndicatorLabel() {
        return _t("Searching: '%s'", this.props.searchQuery || "");
    }

    get clearSearchLabel() {
        return _t("Clear search");
    }

    onSearchInput(ev) {
        this.search.draft = ev.target.value;
        this._clearSearchTimer();
        // Debounced auto-apply while typing; Enter / the button apply at
        // once (they cancel the pending timer first).
        this.search.timer = setTimeout(() => this._applyDraft(), 400);
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.applySearchNow();
        }
    }

    applySearchNow() {
        this._clearSearchTimer();
        this._applyDraft();
    }

    _applyDraft() {
        const query = (this.search.draft || "").trim();
        if (query !== (this.props.searchQuery || "")) {
            this.props.onSearch?.(query);
        }
    }

    clearSearch() {
        this._clearSearchTimer();
        this.search.draft = "";
        this.props.onClearSearch?.();
    }

    _clearSearchTimer() {
        if (this.search.timer) {
            clearTimeout(this.search.timer);
            this.search.timer = null;
        }
    }

    // -- misc --------------------------------------------------------------

    displayDate(isoDate) {
        // Safe list date: "" on missing/invalid input (never render
        // "Invalid DateTime"); compact "HH:mm" today, "DD/MM" otherwise.
        if (!isoDate) {
            return "";
        }
        let dt;
        try {
            dt = deserializeDateTime(isoDate);
        } catch {
            return "";
        }
        if (!dt || !dt.isValid) {
            return "";
        }
        const d = dt.toJSDate ? dt.toJSDate() : new Date(dt);
        if (Number.isNaN(d.getTime())) {
            return "";
        }
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        const sameDay =
            d.getFullYear() === now.getFullYear() &&
            d.getMonth() === now.getMonth() &&
            d.getDate() === now.getDate();
        return sameDay
            ? `${pad(d.getHours())}:${pad(d.getMinutes())}`
            : `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
    }

    displaySender(raw) {
        // '"Instagram" <a@b.c>' -> 'Instagram'; bare 'a@b.c' stays.
        if (!raw) {
            return "";
        }
        const text = String(raw).trim();
        const match = text.match(/^"?(.*?)"?\s*<([^>]+)>$/);
        if (match) {
            const name = match[1].trim().replace(/^"|"$/g, "");
            return name || match[2].trim();
        }
        return text;
    }

    get hasAttachmentLabel() {
        return _t("Has attachment");
    }

    starTitle(msg) {
        return msg.flag_flagged ? _t("Unstar") : _t("Star");
    }

    get emptyLabel() {
        if (this.props.searchQuery) {
            return _t("No messages match your search.");
        }
        if (this.props.filter) {
            return _t("No messages match this filter.");
        }
        return this.props.folderName
            ? _t("No messages in this folder.")
            : _t("Select a folder.");
    }
}

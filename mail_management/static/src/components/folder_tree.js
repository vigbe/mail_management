/** @odoo-module **/

/* PR-1b-ii — pane 1: the folder tree (mailboxes ▸ folders, unread
 * badges).  Pure presentational component; all data comes from the
 * parent (WebmailClient) via props.
 *
 * PR-2b — folder management surface: a "+" header button (create-folder
 * dialog, mailbox preselected) and a per-folder overflow menu
 * (rename / delete with the message count).  The menu stays open for
 * exactly one folder (local presentational state only); every action is
 * delegated to the parent through the on* props.  Drag-and-drop stays
 * out of v1 by design. */

import { Component, proxy, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const FOLDER_ICONS = {
    inbox: "inbox",
    sent: "send",
    drafts: "description",
    trash: "delete",
    junk: "block",
    all: "archive",
    archive: "archive",
    other: "folder",
};

export class FolderTree extends Component {
    static template = "mail_management.FolderTree";
    props = useProps({
        mailboxes: t.array(t.any()),
        selectedMailboxId: t.any().optional(),
        selectedFolderId: t.any().optional(),
        loading: t.boolean(),
        refreshing: t.boolean(),
        onSelectFolder: t.function(),
        onSelectMailbox: t.function(),
        onConfigureMailbox: t.function(),
        onConfigure: t.function(),
        /** PR-2b folder CRUD (all delegated to WebmailClient). */
        onCreateFolder: t.function().optional(),
        onRenameFolder: t.function().optional(),
        onDeleteFolder: t.function().optional(),
        /** PR-2d mailbox management (tree header menu). */
        onAddMailbox: t.function().optional(),
        onRefreshAll: t.function().optional(),
        /** PR-2e settings panel (gear ⚙ in the header). */
        onOpenSettings: t.function().optional(),
        /** PR-2g: hide a folder from the tree (Settings ▸ Folders restores). */
        onHideFolder: t.function().optional(),
        /** PR-2g: gate the unread badge on the show_unread_badge setting. */
        showUnreadBadge: t.boolean().optional(),
    });

    setup() {
        // PR-2j: per-mailbox collapse (expanded by default).
        this.collapsed = proxy({});

        // Overflow menu open state — purely presentational.  The header
        // "manage" menu (PR-2d: Add mailbox / Refresh all) follows the
        // same one-open-menu rule as the per-folder menu.
        this.menu = proxy({ openFolderId: false, openManage: false });
    }

    isCollapsed(mailboxId) {
        return Boolean(this.collapsed[mailboxId]);
    }

    toggleCollapse(mailboxId) {
        this.collapsed[mailboxId] = !this.collapsed[mailboxId];
    }

    /** Label click: expand the mailbox AND select it. */
    selectMailbox(mailboxId) {
        this.collapsed[mailboxId] = false;
        this.props.onSelectMailbox(mailboxId);
    }

    folderIcon(role) {
        return FOLDER_ICONS[role] || FOLDER_ICONS.other;
    }

    // -- PR-2d mailbox management menu -------------------------------------

    get manageMenuLabel() {
        return _t("Manage");
    }

    get addMailboxLabel() {
        return _t("Add mailbox");
    }

    get refreshAllLabel() {
        return _t("Refresh all");
    }

    // -- PR-2e settings (gear ⚙) ------------------------------------------

    get settingsLabel() {
        return _t("Settings");
    }

    get addFolderLabel() {
        return _t("Add folder");
    }

    /** PR-2g — "Hide folder" menu label (kept visible in the tree only
     * through the settings dialog). */
    get hideFolderLabel() {
        return _t("Hide folder");
    }

    openSettings() {
        this.props.onOpenSettings?.();
    }

    toggleManageMenu() {
        this.menu.openManage = !this.menu.openManage;
    }

    addMailbox() {
        this.menu.openManage = false;
        this.props.onAddMailbox?.();
    }

    refreshAll() {
        this.menu.openManage = false;
        this.props.onRefreshAll?.();
    }

    toggleMenu(folderId) {
        this.menu.openFolderId =
            this.menu.openFolderId === folderId ? false : folderId;
    }

    renameFolder(folder) {
        this.menu.openFolderId = false;
        this.props.onRenameFolder?.(folder);
    }

    deleteFolder(folder) {
        this.menu.openFolderId = false;
        this.props.onDeleteFolder?.(folder);
    }

    hideFolder(folder) {
        this.menu.openFolderId = false;
        this.props.onHideFolder?.(folder);
    }

    /** PR-2g: unread badge renders only when the setting allows it. */
    get showUnreadBadge() {
        return this.props.showUnreadBadge !== false;
    }
}

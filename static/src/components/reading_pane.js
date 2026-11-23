/** @odoo-module **/

/* PR-1b-ii — pane 3: the reading pane.  Renders the stored (already
 * sanitized) body via t-out on the Markup value prepared by the parent.
 * Remote images follow the PR-2e policy (blocked unless allowed/revealed);
 * cid: images are always resolved to the /mail_management/attachment/<id>
 * download route at prepare time.
 *
 * PR-2e — simplified actions: left group [Reply][Reply-all][Forward] +
 * [star]; right group a compact overflow ⋯ menu (Mark unread / Move to
 * folder / Delete / Show images when blocked). */

import { Component, useState } from "@odoo/owl";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";

export class ReadingPane extends Component {
    static template = "mail_management.ReadingPane";
    static props = {
        onToggleAssistant: { type: Function, optional: true },
        assistantOpen: { type: Boolean, optional: true },
        message: { type: [Object, Boolean], optional: true },
        loading: Boolean,
        /** PR-1c: opens the ComposeDialog ('reply' | 'reply_all' | 'forward'). */
        onCompose: { type: Function, optional: true },
        /** PR-2a: star toggle + mark-as-unread (delegated to the parent). */
        onToggleStar: { type: Function, optional: true },
        onMarkUnread: { type: Function, optional: true },
        /** PR-2b: move the open message to another folder + delete it
         * (trash semantics server-side; "Delete permanently" in Trash). */
        moveTargets: { type: Array, optional: true },
        isTrash: { type: Boolean, optional: true },
        onMoveToFolder: { type: Function, optional: true },
        onDeleteMessage: { type: Function, optional: true },
        /** PR-2e: per-message remote-image reveal (bar shown by the parent
         * when the policy blocks images for THIS message). */
        imagesBlocked: { type: Boolean, optional: true },
        onShowImages: { type: Function, optional: true },
        /** PR-2f: settings.simple_attachment_list — compact list instead
         * of preview cards. */
        simpleAttachmentList: { type: Boolean, optional: true },
    };

    setup() {
        // Overflow menu open state — purely presentational.
        this.menu = useState({ open: false });
    }

    compose(mode) {
        this.props.onCompose?.(mode);
    }

    toggleStar() {
        if (this.props.message) {
            this.props.onToggleStar?.(this.props.message);
        }
    }

    markUnread() {
        this.menu.open = false;
        if (this.props.message) {
            this.props.onMarkUnread?.(this.props.message);
        }
    }

    // -- PR-2e overflow menu --------------------------------------------------

    get replyLabel() {
        return _t("Reply");
    }

    get replyAllLabel() {
        return _t("Reply all");
    }

    get forwardLabel() {
        return _t("Forward");
    }

    get starLabel() {
        return _t("Star");
    }

    get unstarLabel() {
        return _t("Unstar");
    }

    get menuLabel() {
        return _t("More actions");
    }

    get markUnreadLabel() {
        return _t("Mark as unread");
    }

    get moveToLabel() {
        return _t("Move to folder");
    }

    get showImagesLabel() {
        return _t("Show images");
    }

    get imagesBlockedLabel() {
        return _t("Images blocked");
    }

    get deleteLabel() {
        return this.props.isTrash ? _t("Delete permanently") : _t("Delete");
    }

    toggleMenu() {
        this.menu.open = !this.menu.open;
    }

    moveTo(folderId) {
        this.menu.open = false;
        if (folderId) {
            this.props.onMoveToFolder?.(folderId);
        }
    }

    showImages() {
        this.menu.open = false;
        this.props.onShowImages?.();
    }

    deleteCurrent() {
        this.menu.open = false;
        this.props.onDeleteMessage?.(this.props.message);
    }

    get moveTargets() {
        return this.props.moveTargets || [];
    }

    displayDate(isoDate) {
        if (!isoDate) {
            return "";
        }
        try {
            return formatDateTime(deserializeDateTime(isoDate));
        } catch {
            return isoDate;
        }
    }

    displaySize(bytes) {
        if (typeof bytes !== "number") {
            return "";
        }
        if (bytes < 1024) {
            return `${bytes} B`;
        }
        return `${(bytes / 1024).toFixed(1)} kB`;
    }

    attachmentUrl(attachment) {
        return `/mail_management/attachment/${attachment.id}`;
    }

    get noMessageLabel() {
        return _t("Select a message to read it.");
    }
}

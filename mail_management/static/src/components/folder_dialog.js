/** @odoo-module **/

/* PR-2b — FolderDialog: small create/rename folder modal.
 *
 * Collects the folder name (and the target mailbox when several mailboxes
 * exist and the dialog is in 'create' mode).  Purely presentational: on
 * confirm it delegates to the parent through ``onConfirm`` — the IMAP-first
 * server endpoints do the rest (a failure surfaces as a notification and
 * nothing changes locally). */

import { Component, proxy, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class FolderDialog extends Component {
    static template = "mail_management.FolderDialog";
    static components = { Dialog };
    props = useProps({
        /** 'create' | 'rename' — drives the title/labels. */
        mode: t.string(),
        /** Mailboxes the user may target: [{id, name}] (create only). */
        mailboxes: t.array(t.any()).optional(),
        /** Preselected mailbox id (create only). */
        mailboxId: t.number().optional(),
        /** Folder being renamed: {id, name} (rename only). */
        folder: t.object().optional(),
        /** Called with {name, mailboxId, folder} on confirm. */
        onConfirm: t.function().optional(),
        /** Injected automatically by the dialog service. */
        close: t.function().optional(),
    });

    setup() {
        this.state = proxy({
            name: this.props.folder ? this.props.folder.name || "" : "",
            mailboxId:
                this.props.mailboxId ||
                (this.props.mailboxes && this.props.mailboxes.length
                    ? this.props.mailboxes[0].id
                    : false),
        });
    }

    get title() {
        return this.props.mode === "rename"
            ? _t("Rename folder")
            : _t("Create folder");
    }

    get confirmLabel() {
        return this.props.mode === "rename" ? _t("Rename") : _t("Create");
    }

    get isCreate() {
        return this.props.mode === "create";
    }

    get canConfirm() {
        return this.state.name.trim().length > 0;
    }

    setName(ev) {
        this.state.name = ev.target.value;
    }

    setMailbox(ev) {
        this.state.mailboxId = Number(ev.target.value);
    }

    /** Collect the input and hand it to the parent (which owns the RPC). */
    onConfirm() {
        if (!this.canConfirm) {
            return;
        }
        this.props.onConfirm?.({
            name: this.state.name.trim(),
            mailboxId: this.state.mailboxId,
            folder: this.props.folder || false,
        });
        this.props.close?.();
    }
}

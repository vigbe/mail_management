/** @odoo-module **/

/* PR-3a — 4th column: the AI assistant panel (per-mailbox chat session). */

import {
    Component,
    markup,
    onWillUpdateProps,
    proxy,
    signal,
    t,
    useProps,
} from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

export class AssistantPanel extends Component {
    static template = "mail_management.AssistantPanel";
    props = useProps({
        mailboxId: t.number().optional(),
        mailboxName: t.string().optional(),
        selectedMessage: t.any().optional(),
        onClose: t.function(),
        onOpenMessage: t.function().optional(),
        onDraftReady: t.function().optional(),
    });

    // OWL 3: bound by the template (t-ref="this.assistantScrollEl");
    // a signal ref — call it to read the element.
    assistantScrollEl = signal.ref();

    setup() {
        this.state = proxy({
            messages: [],
            input: "",
            loading: false,
            mode: "chat", // chat | triage | search
            triage: [],
            searchResults: [],
            searchQuery: "",
            error: "",
        });
        // Populated by t-ref="this.assistantScrollEl" after mount.
        // (kept for clarity; the signal ref field above holds the binding)
        this._loadHistory();
        onWillUpdateProps((nextProps) => {
            if (nextProps.mailboxId !== this.props.mailboxId) {
                this._loadHistory(nextProps.mailboxId);
            }
        });
    }

    async _loadHistory(mailboxId) {
        const mbId = mailboxId || this.props.mailboxId;
        if (!mbId) {
            return;
        }
        try {
            const result = await rpc("/mail_management/assistant/history", {
                mailbox_id: mbId,
            });
            this.state.messages = result.history || [];
            this.state.mode = "chat";
            this._scrollDown();
        } catch (e) {
            this.state.error = String(e.message || e);
        }
    }

    _scrollDown() {
        const el = this.assistantScrollEl();
        if (el) {
            el.scrollTop = el.scrollHeight;
        }
    }

    _rpcGuard(error) {
        this.state.error =
            (error && error.data && error.data.message) ||
            String(error && error.message ? error.message : error);
        this.state.loading = false;
    }

    async send() {
        const text = this.state.input.trim();
        if (!text || !this.props.mailboxId) {
            return;
        }
        this.state.input = "";
        this.state.loading = true;
        this.state.error = "";
        this.state.mode = "chat";
        try {
            const result = await rpc("/mail_management/assistant/chat", {
                mailbox_id: this.props.mailboxId,
                text,
            });
            this.state.messages = result.history || [];
        } catch (e) {
            this._rpcGuard(e);
        }
        this.state.loading = false;
        this._scrollDown();
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send();
        }
    }

    async summarizeThread() {
        const message = this.props.selectedMessage;
        if (!message || !message.id) {
            this.state.error = _t(
                "Abrí primero un correo para resumir su hilo.",
            );
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await rpc("/mail_management/assistant/summarize", {
                message_id: message.id,
            });
            this.state.mode = "chat";
            this.state.messages = [
                ...this.state.messages,
                {
                    role: "user",
                    content:
                        _t("Resumí el hilo de: ") + (message.subject || ""),
                },
                { role: "assistant", content: result.summary },
            ];
        } catch (e) {
            this._rpcGuard(e);
        }
        this.state.loading = false;
        this._scrollDown();
    }

    async draftReply() {
        const message = this.props.selectedMessage;
        if (!message || !message.id) {
            this.state.error = _t(
                "Abrí primero el correo que querés responder.",
            );
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const draft = await rpc("/mail_management/assistant/draft", {
                message_id: message.id,
            });
            if (this.props.onDraftReady) {
                this.props.onDraftReady(draft);
            }
            this.state.messages = [
                ...this.state.messages,
                {
                    role: "assistant",
                    content:
                        _t("Preparé un borrador de respuesta a «") +
                        (message.subject || "") +
                        _t(
                            "» — se abrió en la ventana de redacción. " +
                                "Revisalo y enviá cuando esté listo.",
                        ),
                },
            ];
        } catch (e) {
            this._rpcGuard(e);
        }
        this.state.loading = false;
        this._scrollDown();
    }

    async triage() {
        if (!this.props.mailboxId) {
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await rpc("/mail_management/assistant/triage", {
                mailbox_id: this.props.mailboxId,
            });
            this.state.triage = result.mensajes || [];
            this.state.mode = this.state.triage.length ? "triage" : "chat";
            if (!this.state.triage.length) {
                this.state.messages = [
                    ...this.state.messages,
                    {
                        role: "assistant",
                        content: _t(
                            "El triaje no devolvió resultados " +
                                "(probá de nuevo o hay pocos correos).",
                        ),
                    },
                ];
            }
        } catch (e) {
            this._rpcGuard(e);
        }
        this.state.loading = false;
    }

    async search() {
        const query = this.state.searchQuery.trim();
        if (!query || !this.props.mailboxId) {
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await rpc("/mail_management/assistant/search", {
                mailbox_id: this.props.mailboxId,
                query,
            });
            this.state.searchResults = result.results || [];
            this.state.mode = this.state.searchResults.length
                ? "search"
                : "chat";
            this.state.messages = [
                ...this.state.messages,
                { role: "user", content: _t("Buscar: ") + query },
                { role: "assistant", content: result.summary },
            ];
        } catch (e) {
            this._rpcGuard(e);
        }
        this.state.loading = false;
        this._scrollDown();
    }

    searchKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.search();
        }
    }

    openTriageMessage(item) {
        if (this.props.onOpenMessage && item && item.id) {
            this.props.onOpenMessage(item.id);
        }
    }

    openSearchResult(item) {
        if (this.props.onOpenMessage && item && item.id) {
            this.props.onOpenMessage(item.id);
        }
    }

    backToChat() {
        this.state.mode = "chat";
    }

    categoryClass(category) {
        if (category === "atencion") {
            return "bg-danger-subtle text-danger-emphasis";
        }
        if (category === "asistente") {
            return "bg-primary-subtle text-primary-emphasis";
        }
        return "bg-secondary-subtle text-secondary-emphasis";
    }

    categoryLabel(category) {
        if (category === "atencion") {
            return "⚠ Atención";
        }
        if (category === "asistente") {
            return "🤖 Asistente";
        }
        return "🔇 Ruido";
    }

    formatContent(content) {
        // Handle BOTH new (clean markdown) and old (HTML-tagged) messages:
        // 1. strip/convert HTML tags to markdown equivalents
        // 2. escape remaining angle brackets
        // 3. render **bold** and newlines
        let text = String(content || "");
        // Convert common HTML to markdown
        text = text.replace(/<br\s*\/?>/gi, "\n");
        text = text.replace(/<\/?b>/gi, "**");
        text = text.replace(/<\/?strong>/gi, "**");
        text = text.replace(/<\/?i>/gi, "*");
        text = text.replace(/<\/?em>/gi, "*");
        text = text.replace(/<\/p>\s*<p>/gi, "\n\n");
        text = text.replace(/<\/?p>/gi, "");
        text = text.replace(/<\/?div>/gi, "\n");
        text = text.replace(/<li[^>]*>/gi, "- ");
        text = text.replace(/<\/li>\s*/gi, "\n");
        text = text.replace(/<\/?ul>\s*/gi, "");
        text = text.replace(/<[^>]+>/g, "");
        // Escape remaining angle brackets
        text = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        // Render bold + newlines
        // Render bold + newlines + clickable [id]
        let html = text
            .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
            .replace(/\n/g, "<br/>");
        html = html.replace(
            /\[(\d+)\]/g,
            '<span class="o_webmail_msg_ref badge text-bg-primary" ' +
                'data-message-id="$1" role="button" ' +
                'title="Clic para abrir este correo">📧 $1</span>',
        );
        return markup(html);
    }

    /** Click on a [NN] badge -> open that message. */
    _onBodyClick(ev) {
        const badge = ev.target.closest(".o_webmail_msg_ref");
        if (badge && badge.dataset.messageId) {
            const id = parseInt(badge.dataset.messageId, 10);
            if (this.props.onOpenMessage) {
                this.props.onOpenMessage({ id });
            }
        }
    }
}

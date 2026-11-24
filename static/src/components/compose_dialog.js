/** @odoo-module **/

/* PR-2c — ComposeDialog: rich-text reply / reply-all / forward / new with
 * the sanctioned Odoo 19 WYSIWYG (``@html_editor/wysiwyg`` — the same
 * component mail's Composer and website_profile's ProfileDialog embed in
 * dialogs) + HTML signatures.
 *
 * - The body is edited as HTML; on send the dialog passes ``body_html`` to
 *   /mail_management/send, which SANITIZES it server-side before it leaves
 *   (the client is never trusted).
 * - On open, the default signature for the mailbox (mailbox-specific first,
 *   then any user-global default) is appended UNDER the quoted content,
 *   behind the standard ``-- `` separator.  The toolbar dropdown lets the
 *   user switch signature (or none); switching REPLACES the previously
 *   inserted block (marked with ``data-o-mail-signature``).
 * - Signature content is stored sanitized-on-write server-side; inserting it
 *   as HTML here matches the reading-pane trust level (sanitized server
 *   data, never user-raw input).
 */

import { Component, markup, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Wysiwyg } from "@html_editor/wysiwyg";

const MODE_TITLES = {
  reply: _t("Reply"),
  reply_all: _t("Reply all"),
  forward: _t("Forward"),
  new: _t("Compose"),
};

/** Marker attribute wrapping the inserted signature block. */
const SIGNATURE_ATTR = "data-o-mail-signature";

/** PR-2g — the standard mail signature separator (dash dash space). */
const SIGNATURE_SEPARATOR = "-- ";

/** PR-2e — pure part-ordering for the signature position setting:
 * 'above' inserts the signature BEFORE the quoted block; 'below' (the
 * traditional default) appends it UNDER the quote. */
export function composeBodyHtml(quoteHtml, signatureHtml, signaturePosition) {
  if (signaturePosition === "above") {
    return signatureHtml + quoteHtml;
  }
  return quoteHtml + signatureHtml;
}

/** PR-2g — TEXT rendition of an HTML signature for the plain editor:
 * block tags become newlines, every remaining tag is stripped and the
 * basic entities decode back to characters (no HTML ever reaches the
 * plain body; the source stays the STORED-SANITIZED server signature). */
export function htmlSignatureToText(html) {
  if (!html) {
    return "";
  }
  return String(html)
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|tr|table|ul|ol)>/gi, "\n")
    .replace(/<\/(h[1-6]|li)>/gi, "\n")
    .replace(/<li[^>]*>/gi, "• ")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\n{3,}/g, "\n\n")
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .trim();
}

/** PR-2g — plain-mode body assembly: quote + signature (text rendition)
 * ordered by the position setting. */
export function composeBodyText(quoteText, signatureText, signaturePosition) {
  const sig = signatureText ? `${SIGNATURE_SEPARATOR}\n${signatureText}` : "";
  if (signaturePosition === "above") {
    return [sig, quoteText].filter(Boolean).join("\n\n");
  }
  return [quoteText, sig].filter(Boolean).join("\n\n");
}

/** PR-2g — replace the signature block inside an edited plain body.
 *
 * The "-- " separator line marks the block in BOTH positions: 'below'
 * replaces from the separator to the end; 'above' replaces from the
 * start through the separator's trailing newline.  A body without a
 * separator gets the new block appended/prepended.
 */
export function replacePlainSignature(
  bodyText,
  signatureText,
  signaturePosition,
) {
  const text = String(bodyText || "");
  const sigBlock = signatureText
    ? `${SIGNATURE_SEPARATOR}\n${signatureText}`
    : "";
  const markerIdx = text.indexOf(SIGNATURE_SEPARATOR);
  if (markerIdx === -1) {
    if (signaturePosition === "above") {
      return [sigBlock, text].filter(Boolean).join("\n\n");
    }
    return [text, sigBlock].filter(Boolean).join("\n\n");
  }
  if (signaturePosition === "above") {
    const after = text
      .slice(markerIdx + SIGNATURE_SEPARATOR.length)
      .replace(/^\n+/, "");
    return [sigBlock, after].filter(Boolean).join("\n\n");
  }
  const before = text.slice(0, markerIdx).replace(/\n+$/, "");
  return [before, sigBlock].filter(Boolean).join("\n\n");
}

export class ComposeDialog extends Component {
  static template = "mail_management.ComposeDialog";
  static components = { Dialog, Wysiwyg };
  props = useProps({
    aiDraft: t.object().optional(),
    /** 'reply' | 'reply_all' | 'forward' | 'new' */
    mode: t.string(),
    /** Payload from /mail_management/message_body (id, subject,
     * email_from, email_cc, body_text, date, attachments…).  Empty for
     * the 'new' mode. */
    message: t.object().optional(),
    mailboxId: t.number(),
    /** PR-2e: where the signature goes relative to the quote
     * ('below' default | 'above') — from the user's webmail settings. */
    signaturePosition: t.string().optional(),
    /** PR-2g: 'html' (Wysiwyg, default) | 'plain' (plain textarea;
     * single-part text send). */
    composeEditor: t.string().optional(),
    /** PR-2g: browser spellcheck attribute on the editor surface. */
    enableSpellcheck: t.boolean().optional(),
    onSent: t.function().optional(),
    /** Injected automatically by the dialog service. */
    close: t.function().optional(),
  });

  setup() {
    this.notification = useService("notification");
    /** @type { import("@html_editor/wysiwyg").Wysiwyg } */
    this.editorComponent = null;
    /** @type { import("@html_editor/editor").Editor } */
    this.editor = null;
    const message = this.props.message || {};
    const isReplyAll = this.props.mode === "reply_all";
    const isNew = this.props.mode === "new";
    this.state = proxy({
      to:
        isNew || this.props.mode === "forward" ? "" : message.email_from || "",
      cc: isReplyAll ? message.email_cc || "" : "",
      bcc: "",
      subject: this._prefillSubject(),
      // Placeholder quote-only body; re-built with the default
      // signature once /mail_management/signatures resolves (still
      // before the first mount — onWillStart).
      bodyHtml: this._initialBodyHtml(null),
      // PR-2g: plain-editor body (TEXT — signature as a tags-
      // stripped rendition; sent as single-part text).
      bodyText: this._initialBodyText(null),
      newFiles: [],
      sending: false,
      signatures: [],
      selectedSignatureId: false,
    });
    // PR-3a: AI draft prefill (mode "new" from the assistant).
    if (this.props.mode === "new" && this.props.aiDraft) {
      const d = this.props.aiDraft;
      this.state.to = d.to || "";
      this.state.subject = d.subject || this.state.subject;
      this.state.bodyText = d.body_text || "";
      this.state.bodyHtml =
        "<p>" +
        String(d.body_text || "")
          .split("\n")
          .join("</p><p>") +
        "</p>";
      this._aiDraftApplied = true;
    }

    onWillStart(async () => {
      await this._loadSignatures();
    });
  }

  get title() {
    return _t(MODE_TITLES[this.props.mode] || "Compose");
  }

  /** Original attachments, re-attached server-side on forward. */
  get forwardAttachments() {
    return this.props.mode === "forward"
      ? (this.props.message && this.props.message.attachments) || []
      : [];
  }

  get sendLabel() {
    return this.state.sending ? _t("Sending…") : _t("Send");
  }

  /** PR-2e: normalized signature placement ('above' | 'below'). */
  get signaturePosition() {
    return this.props.signaturePosition === "above" ? "above" : "below";
  }

  /** PR-2g: plain-text editor mode ('plain' | anything else = html). */
  get isPlainEditor() {
    return this.props.composeEditor === "plain";
  }

  /** PR-2g: the spellcheck attribute value for the editor surface. */
  get spellcheckAttr() {
    return this.props.enableSpellcheck === false ? "false" : "true";
  }

  /** PR-2g: shared body placeholder (Wysiwyg config + textarea). */
  get placeholderLabel() {
    return _t("Write your message…");
  }

  /** Label for the signature picker options. */
  signatureLabel(signature) {
    return signature.mailbox_name
      ? _t("%s (%s)", signature.name, signature.mailbox_name)
      : _t("%s (all mailboxes)", signature.name);
  }

  // ------------------------------------------------------------------
  // Wysiwyg embed (sanctioned Odoo 19 pattern — see file header)
  // ------------------------------------------------------------------

  get wysiwygConfig() {
    return {
      content: markup(this.state.bodyHtml || "<p></p>"),
      placeholder: this.placeholderLabel,
      classList: ["o_webmail_compose_editor"],
      onChange: () => {
        // The editor content is the single source of truth from now
        // on; it is sanitized server-side on send.
        this.state.bodyHtml = this.editor.getContent();
      },
    };
  }

  onLoadWysiwyg(editor) {
    this.editor = editor;
  }

  // ------------------------------------------------------------------
  // Prefill helpers
  // ------------------------------------------------------------------

  _prefillSubject() {
    const subject = (this.props.message && this.props.message.subject) || "";
    if (this.props.mode === "new") {
      return "";
    }
    if (this.props.mode === "forward") {
      return /^(fwd|fw):/i.test(subject) ? subject : `Fwd: ${subject}`;
    }
    return /^re:/i.test(subject) ? subject : `Re: ${subject}`;
  }

  _escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  _quoteHtml() {
    const message = this.props.message || {};
    const when = (message.date || "").slice(0, 10);
    const who = message.email_from || "";
    const body = (message.body_text || "").replace(/\r/g, "");
    const header = when ? `On ${when}, ${who} wrote:` : `${who} wrote:`;
    const quoted = body
      .split("\n")
      .map((line) => `&gt; ${this._escapeHtml(line)}`)
      .join("<br/>");
    return (
      `<p><br/></p>` +
      `<p>${this._escapeHtml(header)}</p>` +
      `<blockquote><p>${quoted || "<br/>"}</p></blockquote>`
    );
  }

  /** PR-2g: the plain-text quote for the plain editor ("On … wrote:"
   * + "> " lines — mirrors _quoteHtml without HTML). */
  _quoteText() {
    const message = this.props.message || {};
    const when = (message.date || "").slice(0, 10);
    const who = message.email_from || "";
    const body = (message.body_text || "").replace(/\r/g, "");
    const header = when ? `On ${when}, ${who} wrote:` : `${who} wrote:`;
    const quoted = body
      .split("\n")
      .map((line) => `> ${line}`)
      .join("\n");
    return `\n${header}\n${quoted || ""}`;
  }

  _signatureHtml(signature) {
    if (!signature) {
      return "";
    }
    // Standard "-- " separator (dash dash space) before the signature.
    return (
      `<div class="o_webmail_signature" ${SIGNATURE_ATTR}="1">` +
      `<p>--&nbsp;</p>` +
      `${signature.html_body || ""}` +
      `</div>`
    );
  }

  _initialBodyHtml(defaultSignature) {
    const quoteHtml = this.props.mode === "new" ? "" : this._quoteHtml();
    return composeBodyHtml(
      quoteHtml,
      this._signatureHtml(defaultSignature),
      this.signaturePosition,
    );
  }

  /** PR-2g — signature rendered as TEXT (tags stripped) for the plain
   * editor; kept un-cached on the dialog for switch/rebuild. */
  _signatureText(signature) {
    if (!signature) {
      return "";
    }
    return htmlSignatureToText(signature.html_body || "");
  }

  _initialBodyText(defaultSignature) {
    const quoteText = this.props.mode === "new" ? "" : this._quoteText();
    return composeBodyText(
      quoteText,
      this._signatureText(defaultSignature),
      this.signaturePosition,
    );
  }

  async _loadSignatures() {
    try {
      const data = await rpc("/mail_management/signatures", {
        mailbox_id: this.props.mailboxId,
      });
      this.state.signatures = data.signatures || [];
      this.state.selectedSignatureId = data.default_id || false;
    } catch {
      // No signatures reachable — compose stays usable without one.
      this.state.signatures = [];
      this.state.selectedSignatureId = false;
    }
    const defaultSignature =
      this.state.signatures.find(
        (sig) => sig.id === this.state.selectedSignatureId,
      ) || null;
    // Initial editor content = quote + default signature (before mount).
    this.state.bodyHtml = this._initialBodyHtml(defaultSignature);
    if (!this._aiDraftApplied) {
      this.state.bodyText = this._initialBodyText(defaultSignature);
    }
  }

  // ------------------------------------------------------------------
  // Signature picker: switching replaces the inserted block
  // ------------------------------------------------------------------

  onSignatureChange(ev) {
    const value = ev.target.value;
    this.state.selectedSignatureId = value ? parseInt(value, 10) : false;
    if (this.isPlainEditor) {
      // PR-2g: plain mode — swap the "-- " signature block in the
      // TEXT body (no editor instance involved).
      const signature = this.state.signatures.find(
        (sig) => sig.id === this.state.selectedSignatureId,
      );
      this.state.bodyText = replacePlainSignature(
        this.state.bodyText,
        this._signatureText(signature || null),
        this.signaturePosition,
      );
      return;
    }
    this._replaceSignature(this.state.selectedSignatureId);
  }

  _replaceSignature(signatureId) {
    if (!this.editor || this.editor.isDestroyed) {
      return;
    }
    const editable = this.editor.editable;
    if (!editable) {
      return;
    }
    // Remove the previously inserted block…
    editable
      .querySelectorAll(`[${SIGNATURE_ATTR}]`)
      .forEach((el) => el.remove());
    // …and append the newly picked one (or none) at the end.  The
    // signature HTML comes from the server STORED-SANITIZED
    // (html_sanitize on write) — the same trust level as the reading
    // pane's t-out of body_html; parsed via DOMParser, never innerHTML.
    const signature = this.state.signatures.find(
      (sig) => sig.id === signatureId,
    );
    if (signature && (signature.html_body || "").trim()) {
      const parsed = new DOMParser().parseFromString(
        `<div>${this._signatureHtml(signature)}</div>`,
        "text/html",
      ).body.firstElementChild;
      if (parsed) {
        if (this.signaturePosition === "above") {
          // PR-2e 'above': the signature sits BEFORE the quote.
          const quote = editable.querySelector("blockquote");
          if (quote && quote.parentNode) {
            quote.parentNode.insertBefore(parsed, quote);
          } else {
            editable.appendChild(parsed);
          }
        } else {
          editable.appendChild(parsed);
        }
      }
    }
    this.editor.shared.history.addStep();
    this.state.bodyHtml = this.editor.getContent();
  }

  // ------------------------------------------------------------------
  // Input handlers (t-att-value + explicit writes keep OWL reactive)
  // ------------------------------------------------------------------

  setTo(ev) {
    this.state.to = ev.target.value;
  }

  setCc(ev) {
    this.state.cc = ev.target.value;
  }

  setBcc(ev) {
    this.state.bcc = ev.target.value;
  }

  setSubject(ev) {
    this.state.subject = ev.target.value;
  }

  /** PR-2g: plain-editor textarea input. */
  setBodyText(ev) {
    this.state.bodyText = ev.target.value;
  }

  onFileChange(ev) {
    this.state.newFiles = Array.from(ev.target.files || []);
  }

  removeNewFile(index) {
    this.state.newFiles.splice(index, 1);
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

  // ------------------------------------------------------------------
  // Send
  // ------------------------------------------------------------------

  async _readFile(file) {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    return {
      name: file.name,
      datas: String(dataUrl).split(",")[1] || "",
      mimetype: file.type || "application/octet-stream",
    };
  }

  async send() {
    if (this.state.sending) {
      return;
    }
    if (!this.state.to.trim()) {
      this.notification.add(_t("At least one recipient (To) is required."), {
        type: "warning",
      });
      return;
    }
    this.state.sending = true;
    try {
      const attachments = [];
      for (const file of this.state.newFiles) {
        attachments.push(await this._readFile(file));
      }
      // Read the CURRENT editor content (the editor owns the truth).
      // PR-2g plain mode: single-part TEXT send — the textarea is
      // the body; body_html stays empty (no HTML signature insert).
      const bodyHtml = this.isPlainEditor
        ? ""
        : this.editor
          ? this.editor.getContent()
          : this.state.bodyHtml;
      const bodyText = this.isPlainEditor ? this.state.bodyText : "";
      const result = await rpc("/mail_management/send", {
        mailbox_id: this.props.mailboxId,
        to: this.state.to,
        cc: this.state.cc,
        bcc: this.state.bcc,
        subject: this.state.subject,
        body_text: bodyText,
        body_html: bodyHtml,
        mode: this.props.mode,
        reply_to_message_id: this.props.message && this.props.message.id,
        attachments,
      });
      if (result && result.warning) {
        // Sent, but the Sent-copy APPEND failed — surfaced, never
        // claimed as a send failure (spec: Sent-folder APPEND).
        this.notification.add(result.warning, {
          type: "warning",
          title: _t("Webmail"),
        });
      } else {
        this.notification.add(_t("Message sent."), { type: "success" });
      }
      await this.props.onSent?.();
      this.props.close?.();
    } catch (error) {
      const message =
        (error && error.data && error.data.message) ||
        (error && error.message) ||
        _t("Something went wrong. Check the server logs.");
      this.notification.add(message, {
        type: "danger",
        title: _t("Webmail"),
      });
    } finally {
      this.state.sending = false;
    }
  }
}

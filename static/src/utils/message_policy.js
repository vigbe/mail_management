/** @odoo-module **/

/* PR-2f — PURE, DOM-free display-policy helpers for the reading pipeline.
 *
 * Every function here is a plain string-in/string-out (or boolean/number)
 * transform with ZERO imports and no DOM access — the module runs under
 * bare node (see tests/test_message_policy.mjs, executed in-container) and
 * inside the webclient alike.  WebmailClient._buildBodyMarkup composes them
 * with the existing prepareBody image/cid policy:
 *
 *   chooseBodyHtml (prefer_html) → stripMessageStyles → collapseQuotes
 *   → prepareBody (remote-image policy + cid resolution)
 *
 * SnappyMail-parity settings consumed: prefer_html, strip_message_styles,
 * collapse_quoted_text + max_quote_levels, the image whitelist, the
 * mark-read delay semantics and show-next-on-move/delete.
 */

export function stripMessageStyles(html) {
    /** Remove inline styling from a message body: style attributes (incl.
     * background-color/color declarations inside them), class attributes
     * (email color classes have no stylesheet here anyway), bgcolor and
     * font color attributes.  Elements and text are kept untouched. */
    if (!html) {
        return "";
    }
    return html
        .replace(/\sstyle\s*=\s*"[^"]*"/gi, "")
        .replace(/\sstyle\s*=\s*'[^']*'/gi, "")
        .replace(/\sclass\s*=\s*"[^"]*"/gi, "")
        .replace(/\sclass\s*=\s*'[^']*'/gi, "")
        .replace(/\sbgcolor\s*=\s*"[^"]*"/gi, "")
        .replace(/\sbgcolor\s*=\s*'[^']*'/gi, "")
        .replace(/\scolor\s*=\s*"[^"]*"/gi, "")
        .replace(/\scolor\s*=\s*'[^']*'/gi, "");
}

export function plainTextToHtml(text) {
    /** Escape a plain-text body and turn newlines into <br/> so it renders
     * as readable HTML in the reading pane. */
    if (!text) {
        return "";
    }
    const escaped = String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    return `<div class="o_webmail_body_plain">${escaped.replace(/\n/g, "<br/>")}</div>`;
}

export function chooseBodyHtml(bodyHtml, bodyText, preferHtml) {
    /** prefer_html=False renders the PLAIN body instead of the HTML one;
     *  whichever choice has no content falls back to the other. */
    if (preferHtml === false && bodyText) {
        return plainTextToHtml(bodyText);
    }
    return bodyHtml || plainTextToHtml(bodyText || "");
}

export function extractEmailAddress(value) {
    /** 'Name <a@b.c>' → 'a@b.c'; a bare address passes through; anything
     *  without an address yields ''. */
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }
    const bracket = raw.match(/<([^<>\s]+@[^<>\s]+)>/);
    if (bracket) {
        return bracket[1];
    }
    const bare = raw.match(/[^\s<>]+@[^\s<>]+\.[^\s<>]+/);
    return bare ? bare[0] : "";
}

export function senderInWhitelist(sender, whitelist) {
    /** Exact, case-insensitive address match against the whitelist entries
     *  (never a domain/suffix match). */
    const address = extractEmailAddress(sender).toLowerCase();
    if (!address) {
        return false;
    }
    return (whitelist || []).some(
        (entry) =>
            String(entry || "")
                .trim()
                .toLowerCase() === address,
    );
}

export function collapseQuotes(html, options = {}) {
    /** Wrap quoted text in native collapsible <details> toggles:
     *
     *  - each TOP-LEVEL <blockquote> is wrapped in a collapsed
     *    <details class="o_webmail_quote"> (the native toggle — no JS
     *    handlers needed for expand/collapse);
     *  - the first blockquote level BEYOND maxLevels hides everything
     *    deeper behind ONE <details class="o_webmail_quote_more">.
     *
     *  options: {collapse: false → identity, maxLevels (default 3),
     *  quoteLabel, moreLabel} — the labels arrive pre-translated from the
     *  caller (_t) because they are injected into markup. */
    if (!html || options.collapse === false) {
        return html;
    }
    const maxLevels = Math.max(1, options.maxLevels || 3);
    const quoteLabel = options.quoteLabel || "Show quoted text";
    const moreLabel = options.moreLabel || "Show more quote";
    const parts = html.split(/(<\/?blockquote\b[^>]*>)/i);
    let depth = 0;
    let out = "";
    for (const part of parts) {
        if (/^<blockquote\b/i.test(part)) {
            depth += 1;
            if (depth === 1) {
                out += `<details class="o_webmail_quote"><summary>${quoteLabel}</summary>`;
            } else if (depth === maxLevels + 1) {
                out += `<details class="o_webmail_quote_more"><summary>${moreLabel}</summary>`;
            }
            out += part;
        } else if (/^<\/blockquote/i.test(part)) {
            out += part;
            if (depth === maxLevels + 1 || depth === 1) {
                out += "</details>";
            }
            depth = Math.max(0, depth - 1);
        } else {
            out += part;
        }
    }
    return out;
}

export function nextMessageId(ids, removedId) {
    /** After removing removedId from the id list, the message to select
     *  next: the FOLLOWING one, falling back to the PREVIOUS one when the
     *  removed message was last.  False when there is nothing to select. */
    const list = ids || [];
    const index = list.indexOf(removedId);
    if (index < 0) {
        return false;
    }
    return list[index + 1] ?? list[index - 1] ?? false;
}

export function markReadDelayMs(value) {
    /** mark_read_after_seconds semantics: 0 → 0 ms (mark instantly on
     *  open), N > 0 → N seconds (deferred timer), negative → null (NEVER
     *  auto-mark as read). */
    const seconds = Number(value);
    if (Number.isNaN(seconds)) {
        return 0;
    }
    if (seconds < 0) {
        return null;
    }
    return seconds * 1000;
}

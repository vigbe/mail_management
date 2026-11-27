/** @odoo-module **/

/* PR-1b-ii — body preparation for the reading pane (defense in depth).
 *
 * The stored body_html was already html_sanitize'd at sync time AND the
 * Html field re-sanitizes on write.  This helper additionally:
 *
 * - applies the PR-2e REMOTE-IMAGE POLICY on the client:
 *   - allowRemoteImages=false (the default): remote images are BLOCKED
 *     (privacy: no tracking pixels / web beacons) by stripping their src;
 *   - allowRemoteImages=true: remote images render directly (this only
 *     skips the client-side strip — the stored bodies stay sanitized);
 * - RESOLVES cid: inline images to the authenticated download route
 *   /mail_management/attachment/<id> (mail.management.attachment.
 *   content_id mapping — attachment payload carries content_id) REGARDLESS
 *   of the remote-image policy.
 *
 * The returned string is wrapped in markup() by the caller and rendered
 * with t-out (never t-raw).
 */

import { _t } from "@web/core/l10n/translation";

const REMOTE_SRC_RE = /^(https?:)?\/\//i;

export function containsRemoteImages(html) {
    /** True when the (already-sanitized) body embeds at least one remote
     * http(s) image — drives the per-message "Images blocked" bar. */
    if (!html) {
        return false;
    }
    const doc = new DOMParser().parseFromString(html, "text/html");
    return [...doc.querySelectorAll("img[src]")].some((img) =>
        REMOTE_SRC_RE.test(img.getAttribute("src") || ""),
    );
}

export function prepareBody(html, attachments, options = {}) {
    if (!html) {
        return "";
    }
    const allowRemoteImages = Boolean(options.allowRemoteImages);
    const doc = new DOMParser().parseFromString(html, "text/html");
    const cidMap = new Map(
        (attachments || [])
            .filter((a) => a.content_id)
            .map((a) => [a.content_id, `/mail_management/attachment/${a.id}`]),
    );
    for (const img of doc.querySelectorAll("img[src]")) {
        const src = img.getAttribute("src") || "";
        if (src.startsWith("cid:")) {
            const url = cidMap.get(src.slice(4));
            if (url) {
                img.setAttribute("src", url);
            } else {
                img.removeAttribute("src");
            }
        } else if (REMOTE_SRC_RE.test(src)) {
            if (allowRemoteImages) {
                // PR-2e: the user opted in — remote images render directly.
                continue;
            }
            // Remote image → blocked (tracking-pixel protection).
            img.removeAttribute("src");
            img.classList.add("o_webmail_img_blocked");
            img.setAttribute("alt", _t("Remote image blocked"));
        }
    }
    return doc.body.innerHTML;
}

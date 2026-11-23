// PR-2f — REAL executable tests for the pure display-policy helpers
// (static/src/utils/message_policy.js). Run IN-CONTAINER:
//   docker compose run --rm -T --no-deps --entrypoint sh web \
//     -c 'node /mnt/extra-addons/mail_management/tests/test_message_policy.mjs'
//
// The module is deliberately import-free/DOM-free so plain node can run it;
// this harness imports it through a data: URL (node < 20 does not detect ESM
// syntax in extensionless-package .js files). Exit code != 0 on any failure.

import { readFileSync } from "node:fs";

const MODULE_PATH =
    "/mnt/extra-addons/mail_management/static/src/utils/message_policy.js";

const source = readFileSync(MODULE_PATH, "utf8");
const policy = await import(
    "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);

let failures = 0;
let count = 0;

function check(name, fn) {
    count += 1;
    try {
        fn();
        console.log(`ok ${count} - ${name}`);
    } catch (error) {
        failures += 1;
        console.log(`NOT OK ${count} - ${name}`);
        console.log(`  ${error && error.message}`);
    }
}

function eq(actual, expected, label) {
    if (actual !== expected) {
        throw new Error(
            `${label || "value"}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
        );
    }
}

function ok(value, label) {
    if (!value) {
        throw new Error(
            `${label || "expected truthy"} (got ${JSON.stringify(value)})`,
        );
    }
}

// -- stripMessageStyles -----------------------------------------------------

check("strip ON removes style/class/bgcolor/color attributes", () => {
    const html =
        '<p style="color:red" class="bg-dark">Hi</p>' +
        '<table bgcolor="#fff"><font color="blue">x</font></table>';
    const out = policy.stripMessageStyles(html);
    ok(!out.includes("style="), "style attr gone");
    ok(!out.includes("class="), "class attr gone");
    ok(!out.includes("bgcolor="), "bgcolor attr gone");
    ok(!out.includes("color="), "color attr gone");
    ok(out.includes("<p>Hi</p>"), "elements and text kept");
    ok(out.includes("<table>"), "table kept");
    ok(out.includes("<font>x</font>"), "font kept");
});

check("strip handles single-quoted attributes", () => {
    const out = policy.stripMessageStyles(
        "<p style='color:red' class='c'>A</p>",
    );
    ok(!out.includes("style="), "single-quote style gone");
    ok(!out.includes("class="), "single-quote class gone");
    ok(out.includes("A"), "text kept");
});

check("strip handles mixed-case attribute names", () => {
    const out = policy.stripMessageStyles(
        '<p STYLE="color:red" CLASS="c">B</p>',
    );
    ok(!out.includes("STYLE="), "uppercase style gone");
    ok(!out.includes("CLASS="), "uppercase class gone");
});

// -- chooseBodyHtml (prefer_html) -------------------------------------------

check("prefer_html ON renders the html body", () => {
    eq(
        policy.chooseBodyHtml("<p>Rich</p>", "plain fallback", true),
        "<p>Rich</p>",
    );
});

check("prefer_html OFF renders the PLAIN body (escaped, line-broken)", () => {
    const out = policy.chooseBodyHtml("<p>Rich</p>", "a < b & c\nd", false);
    ok(!out.includes("<p>Rich</p>"), "html body not used");
    ok(out.includes("a &lt; b &amp; c"), "plain text escaped");
    ok(out.includes("c<br/>d"), "newline becomes a line break");
});

check("prefer_html OFF with NO plain body falls back to html", () => {
    eq(policy.chooseBodyHtml("<p>Rich</p>", "", false), "<p>Rich</p>");
});

// -- senderInWhitelist / extractEmailAddress --------------------------------

check("extractEmailAddress handles raw + display-name forms", () => {
    eq(policy.extractEmailAddress("alice@x.com"), "alice@x.com");
    eq(policy.extractEmailAddress("Bob Sender <Alice@X.com>"), "Alice@X.com");
    eq(policy.extractEmailAddress("no address here"), "");
});

check(
    "whitelist allows the sender case-insensitively, exact match only",
    () => {
        const whitelist = ["alice@x.com", "carol@y.cl"];
        ok(
            policy.senderInWhitelist("Alice@X.COM", whitelist),
            "case-insensitive",
        );
        ok(
            policy.senderInWhitelist("Bob <alice@x.com>", whitelist),
            "display-name form",
        );
        ok(
            !policy.senderInWhitelist("mallory@evil.com", whitelist),
            "no match",
        );
        ok(!policy.senderInWhitelist("ce@x.com", whitelist), "no suffix match");
        ok(!policy.senderInWhitelist("", whitelist), "empty sender");
        ok(!policy.senderInWhitelist("alice@x.com", []), "empty whitelist");
    },
);

// -- collapseQuotes -----------------------------------------------------------

function bq(level, inner) {
    let html = inner;
    for (let i = 0; i < level; i += 1) {
        html = `<blockquote>${html}</blockquote>`;
    }
    return `<p>top</p>${html}`;
}

check("collapse OFF returns the html unchanged", () => {
    const html = bq(3, "<p>deep</p>");
    eq(policy.collapseQuotes(html, { collapse: false, maxLevels: 3 }), html);
});

check("collapse wraps the top-level quote in a toggle block", () => {
    const out = policy.collapseQuotes(bq(1, "<p>q1</p>"), {
        collapse: true,
        maxLevels: 3,
        quoteLabel: "Show quoted text",
        moreLabel: "Show more quote",
    });
    ok(out.includes('<details class="o_webmail_quote">'), "toggle wrapper");
    ok(out.includes("<summary>Show quoted text</summary>"), "summary label");
    ok(out.includes("<blockquote><p>q1</p></blockquote>"), "quote kept");
    ok(out.endsWith("</details>"), "wrapper closed");
});

check("levels beyond max hide behind one 'Show more quote' block", () => {
    const out = policy.collapseQuotes(bq(4, "<p>deep</p>"), {
        collapse: true,
        maxLevels: 3,
        quoteLabel: "Q",
        moreLabel: "Show more quote",
    });
    eq(
        out.split('class="o_webmail_quote"').length - 1,
        1,
        "exactly one top toggle",
    );
    eq(
        out.split('class="o_webmail_quote_more"').length - 1,
        1,
        "exactly one more-block",
    );
    ok(out.includes("<summary>Show more quote</summary>"), "more label");
    ok(out.includes("<p>deep</p>"), "deep content kept inside");
});

check("nesting within max produces no 'more' block", () => {
    const out = policy.collapseQuotes(bq(3, "<p>q</p>"), {
        collapse: true,
        maxLevels: 3,
        quoteLabel: "Q",
        moreLabel: "More",
    });
    ok(!out.includes("o_webmail_quote_more"), "no more-block within max");
});

check("two sibling top-level quotes each get a toggle", () => {
    const html =
        "<p>t</p><blockquote><p>a</p></blockquote><p>m</p><blockquote><p>b</p></blockquote>";
    const out = policy.collapseQuotes(html, {
        collapse: true,
        maxLevels: 3,
        quoteLabel: "Q",
        moreLabel: "More",
    });
    eq(out.split('class="o_webmail_quote"').length - 1, 2, "two toggles");
});

// -- nextMessageId -----------------------------------------------------------

check("next message after removal is the FOLLOWING one", () => {
    eq(policy.nextMessageId([10, 11, 12, 13], 11), 12);
});

check("removal at list END falls back to the PREVIOUS message", () => {
    eq(policy.nextMessageId([10, 11, 12, 13], 13), 12);
});

check("removal of the only message selects nothing", () => {
    eq(policy.nextMessageId([10], 10), false);
});

check("unknown id selects nothing", () => {
    eq(policy.nextMessageId([10, 11], 99), false);
});

// -- markReadDelayMs -----------------------------------------------------------

check(
    "mark-read delay: 0 = instant (0 ms), -1 = NEVER (null), N = seconds",
    () => {
        eq(policy.markReadDelayMs(0), 0, "instant");
        eq(policy.markReadDelayMs(-1), null, "never");
        eq(policy.markReadDelayMs(5), 5000, "5 seconds");
    },
);

// -- TRIANGULATE: combined policy (strip + prefer_html + whitelist) ----------

check(
    "combined: prefer_html OFF picks plain; strip drops styling AND the wrapper class",
    () => {
        // Documented interaction: stripMessageStyles removes EVERY class
        // attribute — including the plain wrapper's o_webmail_body_plain
        // marker.  The CONTENT (escaped text + line breaks) is what matters
        // and the RICH html body is never used.
        const chosen = policy.chooseBodyHtml(
            '<p style="color:red">Rich</p>',
            "plain\ntext",
            false,
        );
        ok(chosen.includes("plain<br/>text"), "plain content rendered");
        ok(
            chosen.includes('class="o_webmail_body_plain"'),
            "wrapper class pre-strip",
        );
        const stripped = policy.stripMessageStyles(chosen);
        ok(stripped.includes("plain<br/>text"), "content survives the strip");
        ok(!stripped.includes("class="), "strip removes every class attr");
        ok(
            !stripped.includes('<p style="color:red">Rich</p>'),
            "rich body unused",
        );
    },
);

check("combined: strip runs before quote collapse and both survive", () => {
    const html =
        '<p style="color:blue">hi</p>' +
        "<blockquote><blockquote><blockquote><blockquote><p>deep</p></blockquote></blockquote></blockquote></blockquote>";
    const stripped = policy.stripMessageStyles(html);
    ok(!stripped.includes("style="), "style stripped first");
    const collapsed = policy.collapseQuotes(stripped, {
        collapse: true,
        maxLevels: 3,
        quoteLabel: "Q",
        moreLabel: "M",
    });
    ok(
        collapsed.includes("o_webmail_quote_more"),
        "beyond-max still collapsed",
    );
    ok(!collapsed.includes("style="), "strip not undone");
});

// -----------------------------------------------------------------------------

console.log(`\ntest_message_policy: ${count - failures}/${count} passed`);
process.exit(failures ? 1 : 0);

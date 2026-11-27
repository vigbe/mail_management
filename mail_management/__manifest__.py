{  # noqa: B018 — Odoo manifests are a top-level dict literal (ast.literal_eval)
    "name": "Mail Management",
    "version": "17.0.17.0.0",
    "category": "Tools",
    "summary": "AI-powered mail management module for Odoo",
    "description": """
            Mail Management
        """,
    "author": "Victor Bastías Escobar",
    "website": "https://vicbas.com",
    "maintainer": "Victor Bastías Escobar",
    "support": "contacto@vicbas.com",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        # PR-2c: the Wysiwyg editor component (@html_editor/wysiwyg) —
        # already transitively present via mail, declared explicitly.
        "web_editor",
    ],
    "data": [
        "security/security_groups.xml",
        "security/chat_security.xml",
        "security/ir.model.access.csv",
        "data/mail_management_cron.xml",
        "views/mail_management_mailbox_views.xml",
        "views/mail_management_signature_views.xml",
        "views/mail_management_connection_wizard_views.xml",
        "views/mail_management_webmail_action.xml",
        "views/mail_management_ai_views.xml",
    ],
    "demo": [],
    "assets": {
        "web.assets_backend": [
            # Odoo 17: the Wysiwyg component lives in a separate bundle
            # (web_editor.backend_assets_wysiwyg); include it so the
            # @web_editor/js/wysiwyg/wysiwyg import resolves in backend.
            ("include", "web_editor.backend_assets_wysiwyg"),
            "mail_management/static/src/scss/webmail.scss",
            "mail_management/static/src/utils/prepare_body.js",
            "mail_management/static/src/utils/message_policy.js",
            "mail_management/static/src/components/folder_tree.js",
            "mail_management/static/src/components/assistant_panel.js",
            "mail_management/static/src/components/message_list.js",
            "mail_management/static/src/components/reading_pane.js",
            "mail_management/static/src/components/compose_dialog.js",
            "mail_management/static/src/components/folder_dialog.js",
            "mail_management/static/src/components/settings_dialog.js",
            "mail_management/static/src/webmail_client.js",
            "mail_management/static/src/xml/webmail_client.xml",
            "mail_management/static/src/xml/folder_tree.xml",
            "mail_management/static/src/xml/assistant_panel.xml",
            "mail_management/static/src/xml/message_list.xml",
            "mail_management/static/src/xml/reading_pane.xml",
            "mail_management/static/src/xml/compose_dialog.xml",
            "mail_management/static/src/xml/folder_dialog.xml",
            "mail_management/static/src/xml/settings_dialog.xml",
        ],
        "web.assets_tests": [
            "mail_management/static/tests/tours/webmail_tour.js",
        ],
    },
    "images": [
        "static/description/thumbnail.png",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "external_dependencies": {
        "python": ["cryptography", "imap_tools"],
        "bin": [],
    },
}

"""Model-free library package for the mail_management addon.

Contains helpers that are independent of the Odoo ORM so they can be
unit-tested without a database connection.
"""

from .mail_crypto import FernetCrypto, MailCryptoError
from .mail_imap_client import ImapClient
from .mail_smtp_client import SmtpClient

__all__ = [
    "FernetCrypto",
    "ImapClient",
    "MailCryptoError",
    "SmtpClient",
]
from .gemini_client import GeminiClient as GeminiClient
from .gemini_client import GeminiError as GeminiError

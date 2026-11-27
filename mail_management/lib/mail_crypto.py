"""Model-free Fernet credential-encryption helper.

This module is intentionally independent of the Odoo ORM so it can be
unit-tested without a database.  The mailbox model injects
``ir.config_parameter`` accessors via the ``get_master_key`` call.

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §B.1
"""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

_logger = logging.getLogger(__name__)

#: Environment variable that overrides the database-stored master key.
MASTER_KEY_ENV = "MAIL_MGMT_MASTER_KEY"
#: ``ir.config_parameter`` key for the persisted master key.
MASTER_KEY_ICP = "mail_management.master_key"

_REDACTED = "***REDACTED***"


class MailCryptoError(Exception):
    """Raised when a credential crypto operation fails.

    The message describes the *cause* (e.g. key mismatch, corrupt token)
    and **never** includes a credential value or the key itself.

    .. note::

        **Key rotation** is not implemented in Slice 1.  If the master key
        changes (e.g. ``MAIL_MGMT_MASTER_KEY`` is rotated), all previously
        encrypted ``credential_encrypted`` values become undecryptable and
        ``decrypt`` will raise ``MailCryptoError``.  A future migration
        could re-encrypt ciphertexts by decrypting with the old key and
        re-encrypting with the new one during a controlled rotation window.
    """


class FernetCrypto:
    """Fernet encrypt/decrypt + master-key resolution + redaction.

    All public methods are static so the class can be used without
    instantiation and is trivially mockable in tests.
    """

    # ------------------------------------------------------------------
    # Master-key resolution  (env → icp → auto-generate)
    # ------------------------------------------------------------------
    @staticmethod
    def get_master_key(get_param=None, set_param=None):
        """Resolve the Fernet master key.

        Priority:

        1. Environment variable ``MAIL_MGMT_MASTER_KEY``
           (preferred — keeps the key off a database dump).
        2. ``ir.config_parameter`` ``mail_management.master_key``
           (via the injected *get_param* callable).
        3. Auto-generate a fresh key and persist it
           (via the injected *set_param* callable).

        ``get_param`` / ``set_param`` are injected callables so the
        method is fully testable without a database.
        """
        # 1. Environment variable (highest priority).
        key = os.environ.get(MASTER_KEY_ENV)
        if key:
            return key

        # 2. ir.config_parameter fallback.
        if get_param:
            key = get_param(MASTER_KEY_ICP)
        if key:
            return key

        # 3. Bootstrap: generate + persist (warning, not error).
        key = Fernet.generate_key().decode()
        if set_param:
            set_param(MASTER_KEY_ICP, key)
        _logger.warning(
            "mail_management: a Fernet master key was auto-generated and "
            "stored in ir.config_parameter.  For production set the %s "
            "environment variable so the key is not co-located with the "
            "ciphertext on a database dump.",
            MASTER_KEY_ENV,
        )
        return key

    # ------------------------------------------------------------------
    # Encrypt / decrypt
    # ------------------------------------------------------------------
    @staticmethod
    def encrypt(plaintext, key=None):
        """Encrypt *plaintext* → Fernet token (``str``).

        ``key`` must be a base64-urlsafe 32-byte Fernet key.
        Raises :class:`MailCryptoError` on bad input.
        """
        if key is None:
            raise MailCryptoError("An encryption key is required.")
        text = "" if not plaintext else str(plaintext)
        try:
            fernet = Fernet(FernetCrypto._coerce_key(key))
        except (ValueError, TypeError) as exc:
            raise MailCryptoError("Invalid Fernet key.") from exc
        return fernet.encrypt(text.encode("utf-8")).decode("ascii")

    @staticmethod
    def decrypt(token, key=None):
        """Decrypt a Fernet *token* → plaintext (``str``).

        Raises :class:`MailCryptoError` if the token is corrupt or was
        encrypted with a different master key.  The message names the
        cause but never a credential value.
        """
        if key is None:
            raise MailCryptoError("A decryption key is required.")
        text = "" if not token else str(token)
        try:
            fernet = Fernet(FernetCrypto._coerce_key(key))
        except (ValueError, TypeError) as exc:
            raise MailCryptoError("Invalid Fernet key.") from exc
        try:
            return fernet.decrypt(text.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise MailCryptoError(
                "Failed to decrypt credential: the token is corrupt or "
                "was encrypted with a different master key."
            ) from exc

    # ------------------------------------------------------------------
    # Convenience Fernet accessor
    # ------------------------------------------------------------------
    @staticmethod
    def fernet(get_param=None, set_param=None):
        """Return a :class:`~cryptography.fernet.Fernet` instance using the
        resolved master key.

        The Odoo model injects ``ir.config_parameter`` accessors::

            f = FernetCrypto.fernet(
                lambda k: icp.get_param(k),
                lambda k, v: icp.set_param(k, v))

        This keeps key resolution in one place and lets callers re-use the
        Fernet object for multiple encrypt/decrypt operations.
        """
        key = FernetCrypto.get_master_key(get_param, set_param)
        return Fernet(FernetCrypto._coerce_key(key))

    # ------------------------------------------------------------------
    # Redaction (defence-in-depth)
    # ------------------------------------------------------------------
    @staticmethod
    def redact(value, *secrets):
        """Return *value* with each *secret* replaced by ``***REDACTED***``.

        Used on error messages, audit logs, and any text that might
        transit a credential so plaintext never appears in persisted
        diagnostics.
        """
        result = "" if value is None else str(value)
        for secret in secrets:
            if secret:
                result = result.replace(str(secret), _REDACTED)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_key(key):
        """Accept a Fernet key as ``str`` or ``bytes`` → ``bytes``."""
        if isinstance(key, str):
            return key.encode("ascii")
        return key

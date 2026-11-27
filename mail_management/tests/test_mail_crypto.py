"""Tests for the Fernet credential-encryption helper (lib/mail_crypto.py).

Strict-TDD RED target: this file is written BEFORE the implementation
exists so the first harness run fails with an ImportError.
"""

import os
from unittest import mock

from cryptography.fernet import Fernet
from odoo.addons.mail_management.lib.mail_crypto import (
    MASTER_KEY_ENV,
    MASTER_KEY_ICP,
    FernetCrypto,
    MailCryptoError,
)
from odoo.tests.common import TransactionCase


class TestMailCrypto(TransactionCase):
    """Round-trip, ciphertext, master-key resolution, redaction, and errors."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _key():
        """Return a fresh base64-urlsafe Fernet key as ``str``."""
        return Fernet.generate_key().decode()

    @staticmethod
    def _env_without_master_key():
        """Context manager that removes ``MAIL_MGMT_MASTER_KEY`` from ``os.environ``."""
        env = dict(os.environ)
        env.pop(MASTER_KEY_ENV, None)
        return mock.patch.dict(os.environ, env, clear=True)

    # ------------------------------------------------------------------
    # Round-trip
    # ------------------------------------------------------------------
    def test_encrypt_decrypt_roundtrip(self):
        """decrypt(encrypt(plaintext)) returns the original for many inputs."""
        key = self._key()
        samples = [
            "",
            "x",
            "short",
            "a" * 5000,
            "ünïcödé password 🔑",
            "fake-imap-P@ssw0rd!#$%",
        ]
        for plaintext in samples:
            with self.subTest(plaintext=plaintext[:20]):
                token = FernetCrypto.encrypt(plaintext, key=key)
                self.assertEqual(FernetCrypto.decrypt(token, key=key), plaintext)

    def test_empty_string_roundtrip(self):
        """Encrypting and decrypting an empty string works."""
        key = self._key()
        token = FernetCrypto.encrypt("", key=key)
        self.assertIsInstance(token, str)
        self.assertTrue(token)
        self.assertEqual(FernetCrypto.decrypt(token, key=key), "")

    # ------------------------------------------------------------------
    # Ciphertext properties
    # ------------------------------------------------------------------
    def test_different_plaintexts_different_ciphertexts(self):
        """Two distinct plaintexts produce distinct ciphertexts."""
        key = self._key()
        token_a = FernetCrypto.encrypt("alpha", key=key)
        token_b = FernetCrypto.encrypt("beta", key=key)
        self.assertNotEqual(token_a, token_b)

    def test_same_plaintext_different_ciphertexts(self):
        """Fernet uses a random nonce, so identical plaintexts differ."""
        key = self._key()
        token_1 = FernetCrypto.encrypt("repeated", key=key)
        token_2 = FernetCrypto.encrypt("repeated", key=key)
        self.assertNotEqual(token_1, token_2)

    def test_ciphertext_does_not_contain_plaintext(self):
        """The ciphertext string must not contain the plaintext substring."""
        key = self._key()
        plaintext = "super-secret-imap-password"
        token = FernetCrypto.encrypt(plaintext, key=key)
        self.assertNotIn(plaintext, token)

    # ------------------------------------------------------------------
    # Master-key resolution (env > icp > autogen)
    # ------------------------------------------------------------------
    def test_master_key_env_var_wins(self):
        """When the env var is set it takes priority over icp."""
        env_key = self._key()
        with mock.patch.dict(os.environ, {MASTER_KEY_ENV: env_key}):
            result = FernetCrypto.get_master_key(
                get_param=lambda _k: "icp-fallback-should-not-be-used",
                set_param=lambda _k, _v: None,
            )
        self.assertEqual(result, env_key)

    def test_master_key_icp_fallback(self):
        """Without the env var, the icp value is used."""
        icp_key = self._key()
        with self._env_without_master_key():
            result = FernetCrypto.get_master_key(
                get_param=lambda k: icp_key if k == MASTER_KEY_ICP else None,
                set_param=lambda _k, _v: None,
            )
        self.assertEqual(result, icp_key)

    def test_master_key_autogen_persists(self):
        """With env+icp empty a key is generated, persisted, and stable."""
        with self._env_without_master_key():
            stored: dict = {}

            def fake_get(k):
                return stored.get(k)

            def fake_set(k, v):
                stored[k] = v

            key_1 = FernetCrypto.get_master_key(get_param=fake_get, set_param=fake_set)
            self.assertIn(MASTER_KEY_ICP, stored)
            self.assertEqual(stored[MASTER_KEY_ICP], key_1)

            key_2 = FernetCrypto.get_master_key(get_param=fake_get, set_param=fake_set)
            self.assertEqual(key_1, key_2)

    # ------------------------------------------------------------------
    # Error handling (TRIANGULATE)
    # ------------------------------------------------------------------
    def test_decrypt_bad_token_raises_crypto_error(self):
        """A garbled token raises MailCryptoError, not a raw exception."""
        key = self._key()
        with self.assertRaises(MailCryptoError):
            FernetCrypto.decrypt("not-a-valid-fernet-token", key=key)

    def test_decrypt_wrong_key_raises_crypto_error(self):
        """A token encrypted with key A cannot be decrypted with key B."""
        key_a = self._key()
        key_b = self._key()
        token = FernetCrypto.encrypt("secret", key=key_a)
        with self.assertRaises(MailCryptoError):
            FernetCrypto.decrypt(token, key=key_b)

    def test_encrypt_without_key_raises(self):
        with self.assertRaises(MailCryptoError):
            FernetCrypto.encrypt("data")

    def test_decrypt_without_key_raises(self):
        with self.assertRaises(MailCryptoError):
            FernetCrypto.decrypt("data")

    def test_error_message_does_not_leak_key(self):
        """The error text must never contain a key value."""
        key = self._key()
        token = FernetCrypto.encrypt("secret-data", key=key)
        wrong_key = self._key()
        try:
            FernetCrypto.decrypt(token, key=wrong_key)
        except MailCryptoError as exc:
            msg = str(exc)
            self.assertNotIn(key, msg)
            self.assertNotIn(wrong_key, msg)
        else:
            self.fail("MailCryptoError was not raised")

    def test_cross_key_encryption_is_isolated(self):
        """Data encrypted with key A cannot be decrypted with key B."""
        key_a = self._key()
        key_b = self._key()
        token = FernetCrypto.encrypt("rotation-test", key=key_a)
        # Decrypt with the correct key works.
        self.assertEqual(FernetCrypto.decrypt(token, key=key_a), "rotation-test")
        # Decrypt with a different key fails cleanly.
        with self.assertRaises(MailCryptoError):
            FernetCrypto.decrypt(token, key=key_b)

    # ------------------------------------------------------------------
    # Redaction
    # ------------------------------------------------------------------
    def test_redact_replaces_secret(self):
        result = FernetCrypto.redact("error: password=hunter2 failed", "hunter2")
        self.assertNotIn("hunter2", result)
        self.assertIn("***REDACTED***", result)

    def test_redact_multiple_secrets(self):
        result = FernetCrypto.redact(
            "user=admin pass=s3cr3t token=abc", "s3cr3t", "abc"
        )
        self.assertNotIn("s3cr3t", result)
        self.assertNotIn("abc", result)
        self.assertEqual(result.count("***REDACTED***"), 2)

    def test_redact_no_secret_unchanged(self):
        result = FernetCrypto.redact("no secrets here")
        self.assertEqual(result, "no secrets here")

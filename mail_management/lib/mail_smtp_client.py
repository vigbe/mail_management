"""SMTP client wrapper over stdlib *smtplib*.

Model-free and mockable.  Tests monkeypatch ``smtplib.SMTP`` at this
module level — **no real network calls**.

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §D
"""

import logging
import smtplib

_logger = logging.getLogger(__name__)


class SmtpClient:
    """SMTP client supporting STARTTLS and plain AUTH.

    Usage::

        with SmtpClient(host, port, use_tls, user, credential) as smtp:
            smtp.send_message(msg)
    """

    def __init__(
        self,
        host,
        port=587,
        use_tls=True,
        user="",
        credential="",
        *,
        timeout=30,
    ):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.user = user
        self.credential = credential
        self.timeout = timeout
        self._conn = None

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect(self):
        """Connect, optionally STARTTLS, and authenticate."""
        self._conn = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        self._conn.ehlo()
        if self.use_tls:
            self._conn.starttls()
            self._conn.ehlo()
        if self.user and self.credential:
            self._conn.login(self.user, self.credential)
        _logger.info("SMTP connected to %s:%s as %s", self.host, self.port, self.user)
        return self

    def close(self):
        """Quit and release the connection (idempotent)."""
        if self._conn:
            try:
                self._conn.quit()
            except Exception:
                _logger.debug("SMTP quit error (ignored)", exc_info=True)
            self._conn = None

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    def send_message(self, message):
        """Send an :class:`email.message.EmailMessage`."""
        if self._conn is None:
            raise RuntimeError("SmtpClient is not connected.")
        self._conn.send_message(message)

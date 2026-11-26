"""IMAP client wrapper over *imap_tools*.

Model-free and mockable.  Tests monkeypatch ``MailBox`` /
``MailBoxUnencrypted`` at this module level — **no real network calls**.

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §C/D
"""

import logging
from typing import Any

from imap_tools import (  # type: ignore[import-not-found]
    MailBox,
    MailBoxUnencrypted,
    MailMessageFlags,
)

_logger = logging.getLogger(__name__)


class ImapClient:
    """Context-managed IMAP client over :mod:`imap_tools`.

    Usage::

        with ImapClient(host, port, use_ssl, user, credential) as imap:
            ...
    """

    def __init__(
        self,
        host,
        port=993,
        use_ssl=True,
        user="",
        credential="",
        *,
        timeout=30,
    ):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.user = user
        self.credential = credential
        self.timeout = timeout
        self._conn: Any = None  # imap_tools MailBox instance (set in connect)

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
        """Connect to the IMAP server and log in.

        Raises an exception on failure (caller decides how to surface it).
        """
        cls = MailBox if self.use_ssl else MailBoxUnencrypted
        self._conn = cls(self.host, port=self.port)
        self._conn.login(self.user, self.credential)
        _logger.info("IMAP connected to %s:%s as %s", self.host, self.port, self.user)
        return self

    def close(self):
        """Log out and release the connection (idempotent)."""
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                _logger.debug("IMAP logout error (ignored)", exc_info=True)
            self._conn = None

    # ------------------------------------------------------------------
    # Operations (used by later PRs; kept here for seam completeness)
    # ------------------------------------------------------------------
    def folder_list(self):
        """Return the server folder list."""
        return self._conn.folder.list()

    def fetch(self, *, limit=50, reverse=True, mark_seen=False):
        """Yield messages from the currently selected folder."""
        return self._conn.fetch(
            limit=limit, reverse=reverse, mark_seen=mark_seen, bulk=True
        )

    def append(self, message_bytes, folder="INBOX", flag_set=None, date=None):
        """Append a raw message to *folder*.

        *date* is forwarded to imap_tools as ``dt`` (the upstream kwarg
        name — imap_tools 1.x: ``MailBox.append(message, folder, dt,``
        ``flag_set)``).
        """
        return self._conn.append(
            message_bytes, folder=folder, flag_set=flag_set or [], dt=date
        )

    def store(self, uids, flag_set, value):
        """STORE flags on the given UIDs."""
        return self._conn.flag(uids, flag_set, value)

    def set_flags(self, folder_imap_name, uids, seen=None, flagged=None):
        """STORE ``\\Seen``/``\\Flagged`` on *uids* in *folder_imap_name* (PR-2a).

        ``seen``/``flagged`` are booleans or ``None`` (leave unchanged).
        imap_tools applies one value per ``flag()`` call, so a mixed
        request (e.g. ``seen=True`` + ``flagged=False``) issues one call
        per ``(flag_set, value)`` pair.  Mockable like every seam method.
        """
        uid_list = [int(u) for u in uids or []]
        if not uid_list:
            return
        to_set, to_unset = set(), set()
        for value, flag in (
            (seen, MailMessageFlags.SEEN),
            (flagged, MailMessageFlags.FLAGGED),
        ):
            if value is None:
                continue
            (to_set if value else to_unset).add(flag)
        if not (to_set or to_unset):
            return
        self._conn.folder.set(folder_imap_name)
        if to_set:
            self._conn.flag(uid_list, to_set, True)
        if to_unset:
            self._conn.flag(uid_list, to_unset, False)

    # ------------------------------------------------------------------
    # Folder-aware operations (PR-1b-i sync — design §C.3)
    # These are the seam that tests mock via ``FakeImapClient``.
    # ------------------------------------------------------------------
    def list_folders(self):
        """Return a list of server folder names."""
        return [f.name for f in self._conn.folder.list()]

    def uidvalidity(self, folder_name):
        """Return the UIDVALIDITY value (as ``str``) for *folder_name*.

        Uses the IMAP STATUS command; returns ``'1'`` as a safe default
        if the server does not report one.
        """
        import re

        typ, data = self._conn.client.status(f'"{folder_name}"', "(UIDVALIDITY)")
        if typ == "OK" and data:
            text = data[0]
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            match = re.search(r"UIDVALIDITY\s+(\d+)", str(text))
            if match:
                return match.group(1)
        return "1"

    def search_uids(self, folder_name, since_uid=0):
        """Return a sorted list of UIDs ``> since_uid`` in *folder_name*."""
        self._conn.folder.set(folder_name)
        if since_uid:
            raw = self._conn.uids(f"UID {since_uid + 1}:*")
        else:
            raw = self._conn.uids("ALL")
        return sorted(int(u) for u in raw)

    def fetch_messages(self, folder_name, uids, mark_seen=False):
        """Yield ``(uid, raw_bytes, flags_set)`` for each message in *uids*.

        *flags_set* is a ``set`` of IMAP flag strings (e.g. ``{'\\Seen'}``).
        """
        self._conn.folder.set(folder_name)
        uid_criteria = ",".join(str(u) for u in uids)
        for msg in self._conn.fetch(
            f"UID {uid_criteria}", mark_seen=mark_seen, bulk=True
        ):
            yield int(msg.uid), msg.obj.as_bytes(), set(msg.flags)

    # ------------------------------------------------------------------
    # Folder CRUD + message movement (PR-2b — IMAP-first policy).
    # Folder-aware signatures: imap_tools operates on the SELECTED
    # folder, so every method selects its source folder first.  Mockable
    # like every seam method.
    # ------------------------------------------------------------------
    def create_folder(self, name):
        """IMAP CREATE *name* (server is the source of truth)."""
        return self._conn.folder.create(name)

    def rename_folder(self, old_name, new_name):
        """IMAP RENAME."""
        return self._conn.folder.rename(old_name, new_name)

    def delete_folder(self, name):
        """IMAP DELETE."""
        return self._conn.folder.delete(name)

    def copy_messages(self, folder_imap_name, uids, dest_imap_name):
        """COPY *uids* from *folder_imap_name* to *dest_imap_name*."""
        uid_list = [int(u) for u in uids or []]
        if not uid_list:
            return
        self._conn.folder.set(folder_imap_name)
        return self._conn.copy(uid_list, dest_imap_name)

    def delete_messages(self, folder_imap_name, uids):
        """Flag ``\\Deleted`` on *uids* (no EXPUNGE — see :meth:`expunge`)."""
        uid_list = [int(u) for u in uids or []]
        if not uid_list:
            return
        self._conn.folder.set(folder_imap_name)
        return self._conn.flag(uid_list, {MailMessageFlags.DELETED}, True)

    def expunge(self, folder_imap_name):
        """EXPUNGE *folder_imap_name* (permanently removes ``\\Deleted``)."""
        self._conn.folder.set(folder_imap_name)
        return self._conn.expunge()

    def move_messages(self, folder_imap_name, uids, dest_imap_name):
        """Move *uids* → *dest_imap_name*: COPY + ``\\Deleted`` + EXPUNGE.

        Portable on purpose: RFC 3501 has no MOVE (it is an extension and
        imap_tools 1.15 does not wrap it), so the copy/flag/expunge triple
        is used instead of a raw MOVE.  No uids → complete no-op (an EXPUNGE
        with nothing moved could purge unrelated pending ``\\Deleted``
        marks from other clients).
        """
        if not list(uids or []):
            return
        self.copy_messages(folder_imap_name, uids, dest_imap_name)
        self.delete_messages(folder_imap_name, uids)
        self.expunge(folder_imap_name)

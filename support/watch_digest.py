"""Watch-folder digest: one summary notification instead of toast spam.

Framework-free buffer. Watcher/pipeline handlers call add() per event;
a GUI timer calls flush() every ~20s and shows the one-line summary
(snackbar + status line) only when something actually happened.
"""
from __future__ import annotations

from support.text_utils import format_bytes


class DigestBuffer:
    def __init__(self):
        self._queued = 0
        self._done = 0
        self._failed = 0
        self._saved_bytes = 0

    def add(self, event_type: str, saved_bytes: int = 0):
        ev = str(event_type or "").lower()
        if ev == "queued":
            self._queued += 1
        elif ev == "done":
            self._done += 1
            try:
                self._saved_bytes += max(0, int(saved_bytes or 0))
            except Exception:
                pass
        elif ev == "failed":
            self._failed += 1

    @property
    def empty(self) -> bool:
        return not (self._queued or self._done or self._failed)

    def flush(self) -> str | None:
        """One-line ASCII summary, or None when nothing happened. Resets."""
        if self.empty:
            return None
        parts = []
        if self._queued:
            parts.append(f"{self._queued} queued")
        if self._done:
            parts.append(f"{self._done} done")
        if self._failed:
            parts.append(f"{self._failed} failed")
        msg = "Watcher: " + ", ".join(parts)
        if self._saved_bytes > 0:
            msg += f", saved {format_bytes(self._saved_bytes)}"
        self.__init__()
        return msg

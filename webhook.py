from __future__ import annotations

import os
import time
import random

import requests

from text_utils import _normalize_text, format_bytes


class DiscordWebhookClient:
    """
    Robust Discord webhook client with:
      • session reuse + timeouts
      • jittered exponential backoff with 429 'Retry-After' honor
      • message auto-chunking (2k chars)
      • optional username, avatar_url, tts, thread_id, silent flag
      • basic embed/file support (≤25MB)
    """
    def __init__(self, url: str, *, username: str|None=None, avatar_url: str|None=None,
                 tts: bool=False, thread_id: str|None=None, timeout: float=15.0,
                 max_retries: int=5, silent: bool=False):
        import requests, time, random
        self.url = (url or "").strip()
        self.username = username
        self.avatar_url = avatar_url
        self.tts = bool(tts)
        self.thread_id = thread_id
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.silent = bool(silent)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "BitCrusher/9 Webhook"})
        self._alive = bool(self.url)

    def set_options(self, **kw):
        for k,v in kw.items():
            if hasattr(self, k): setattr(self, k, v)

    def set_url(self, url: str):
        # Was called by load_profile / settings-apply but never defined, so
        # changing the webhook URL silently never took effect. Update + re-arm.
        self.url = (url or "").strip()
        self._alive = bool(self.url)

    def _post(self, json=None, files=None):
        import time, random
        if not self._alive: return False
        url = self.url
        params = {}
        if self.thread_id: params["thread_id"] = str(self.thread_id)
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self._session.post(url, params=params, json=json, files=files, timeout=self.timeout)
                if r.status_code == 204 or (200 <= r.status_code < 300):
                    return True
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "1"))
                    time.sleep(max(0.5, retry_after))
                else:
                    # jittered backoff on transient 5xx
                    if 500 <= r.status_code < 600 and attempt < self.max_retries:
                        time.sleep(min(10.0, (2 ** attempt) + random.random()))
                    else:
                        return False
            except Exception:
                if attempt >= self.max_retries: return False
                time.sleep(min(10.0, (2 ** attempt) + random.random()))

    def send_text(self, text: str, *, allow_everyone: bool=False):
        text = _normalize_text(text)
        if not self._alive or not text: return False
        # Split into ≤2000 char chunks
        CHUNK = 1900
        chunks = [text[i:i+CHUNK] for i in range(0, len(text), CHUNK)] or [text]
        ok = True
        for c in chunks:
            payload = {
                "content": c,
                "tts": self.tts,
                "allowed_mentions": ({"parse": ["everyone","roles","users"]} if allow_everyone else {"parse": []}),
            }
            if self.username:   payload["username"] = self.username
            if self.avatar_url: payload["avatar_url"] = self.avatar_url
            if self.silent:     payload["flags"] = 4096  # suppress embeds
            ok = self._post(json=payload) and ok
        return ok

    def send_file(self, file_path: str, *, caption: str|None=None):
        import os
        if not self._alive or not os.path.exists(file_path): return False
        try:
            size = os.path.getsize(file_path)
            if size > 25*1024*1024:  # cannot upload; send text fallback
                name = os.path.basename(file_path)
                return self.send_text(f"{caption or ''}\n`{name}` ({size//1024} KB) too large for upload.")
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
                payload = {}
                if caption:
                    payload["content"] = caption
                    if self.username:   payload["username"] = self.username
                    if self.avatar_url: payload["avatar_url"] = self.avatar_url
                return self._post(json=payload or None, files=files)
        except Exception:
            return False


def _format_webhook_summary(stats: dict) -> str:
    """Turn an encode-result stats dict into a readable Discord message."""
    try:
        name = os.path.basename(str(stats.get("output_path") or stats.get("filename") or "output"))
        o = stats.get("original_size")
        c = stats.get("compressed_size")
        parts = [f"**{name}**"]
        if o and c:
            pct = (c / o * 100.0) if o else 0.0
            parts.append(f"{format_bytes(int(o))} -> {format_bytes(int(c))} ({pct:.1f}% of original)")
        if stats.get("vmaf") is not None:
            parts.append(f"VMAF {float(stats['vmaf']):.1f}")
        if stats.get("encoder"):
            parts.append(str(stats["encoder"]))
        return _normalize_text(" | ".join(parts))[:1900] or "BitCrusher: done."
    except Exception:
        return "BitCrusher: compression complete."


def _post_webhook_hardened(url: str, *, file_path: str | None = None,
                           json_payload: dict | None = None, max_mb: int = 25) -> bool:
    _ok = False
    try:
        import time as _t
        if json_payload:
            # Discord rejects a raw stats dict with HTTP 400 "Cannot send an empty
            # message" (it needs content/embeds/file) — so this summary post used to
            # silently no-op. Format the stats into a readable message instead.
            if isinstance(json_payload, dict) and not ({"content", "embeds"} & set(json_payload)):
                _msg = _format_webhook_summary(json_payload)
                post_body = {"content": _msg, "allowed_mentions": {"parse": []}}
            else:
                post_body = json_payload
            for delay in (0, 2, 4):
                try:
                    _r = requests.post(url, json=post_body, timeout=15)
                    if _r is not None and _r.status_code < 500:
                        _ok = True
                        break
                except Exception:
                    pass
                _t.sleep(delay)
        if file_path and os.path.exists(file_path):
            sz = os.path.getsize(file_path)
            if sz <= max_mb * 1024 * 1024:
                for delay in (0, 2, 4):
                    try:
                        with open(file_path, "rb") as f:
                            _r2 = requests.post(url, files={"file": f}, timeout=60)
                        if _r2 is not None and _r2.status_code < 500:
                            _ok = True
                        break
                    except Exception:
                        _t.sleep(delay)
    except Exception:
        pass
    return _ok

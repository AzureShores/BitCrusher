from __future__ import annotations

import os
import time
import random

import requests

from support.text_utils import _normalize_text, format_bytes


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


def detect_service(url: str) -> str:
    """Classify a webhook URL: 'discord' | 'slack' | 'telegram' | 'generic'."""
    u = str(url or "").lower()
    if ("discord.com" in u or "discordapp.com" in u) and "/api/webhooks" in u:
        return "discord"
    if "hooks.slack.com" in u:
        return "slack"
    if "api.telegram.org" in u and "/bot" in u:
        return "telegram"
    return "generic"


def redact_webhook_url(url: str) -> str:
    """Mask secrets for logs: Telegram bot token, Discord webhook token."""
    try:
        import re
        u = str(url or "")
        u = re.sub(r"/bot[^/]+/", "/bot***/", u)
        u = re.sub(r"(/api/webhooks/\d+)/[\w-]+", r"\1/***", u)
        return u
    except Exception:
        return "<webhook>"


def _format_webhook_summary(stats: dict, style: str = "discord") -> str:
    """Turn an encode-result stats dict into a readable message.

    style: 'discord' (**bold**) | 'slack' (*bold*) | 'plain'.
    """
    try:
        name = os.path.basename(str(stats.get("output_path") or stats.get("filename") or "output"))
        if style == "discord":
            head = f"**{name}**"
        elif style == "slack":
            head = f"*{name}*"
        else:
            head = name
        o = stats.get("original_size")
        c = stats.get("compressed_size")
        parts = [head]
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


def _telegram_endpoints(url: str) -> tuple[str, str, str | None]:
    """(sendMessage_url, sendDocument_url, chat_id) from a user-configured
    Telegram URL like https://api.telegram.org/bot<token>/sendMessage?chat_id=N."""
    from urllib.parse import urlparse, parse_qs
    p = urlparse(str(url or ""))
    chat_id = (parse_qs(p.query).get("chat_id") or [None])[0]
    base = p.path
    for m in ("/sendMessage", "/sendDocument", "/sendPhoto", "/sendVideo"):
        if base.endswith(m):
            base = base[: -len(m)]
            break
    root = f"{p.scheme}://{p.netloc}{base}"
    return f"{root}/sendMessage", f"{root}/sendDocument", chat_id


def build_webhook_requests(url: str, *, json_payload: dict | None = None,
                           file_path: str | None = None,
                           max_mb: int = 25) -> tuple[list[dict], list[str]]:
    """Pure request planner: (request specs, log notes).

    Spec keys: url, and either 'json' (JSON post) or 'file_path' +
    'file_field' (+ optional 'data' form fields) for multipart uploads.
    The runner supplies retries/timeouts.
    """
    service = detect_service(url)
    specs: list[dict] = []
    notes: list[str] = []

    summary = None
    if json_payload:
        if isinstance(json_payload, dict) and not ({"content", "embeds"} & set(json_payload)):
            style = {"discord": "discord", "slack": "slack"}.get(service, "plain")
            summary = _format_webhook_summary(json_payload, style)
        else:
            # Pre-shaped payload: pass through untouched (Discord-shaped).
            specs.append({"url": url, "json": json_payload})

    file_ok = bool(file_path and os.path.exists(file_path)
                   and os.path.getsize(file_path) <= max_mb * 1024 * 1024)
    # Telegram bot API caps uploads at 50 MB regardless of caller max_mb.
    if service == "telegram" and file_ok and os.path.getsize(file_path) > 50 * 1024 * 1024:
        file_ok = False
        notes.append("[Webhook] File exceeds Telegram's 50 MB bot upload cap; sending summary only.")

    if service == "slack":
        if summary:
            specs.append({"url": url, "json": {"text": summary}})
        if file_path:
            notes.append("[Webhook] Slack incoming webhooks do not accept file uploads; sending summary only.")
    elif service == "telegram":
        msg_url, doc_url, chat_id = _telegram_endpoints(url)
        if not chat_id:
            notes.append("[Webhook] Telegram URL is missing ?chat_id=; cannot post.")
        else:
            if summary:
                specs.append({"url": msg_url, "json": {"chat_id": chat_id, "text": summary}})
            if file_ok:
                specs.append({"url": doc_url, "data": {"chat_id": chat_id},
                              "file_field": "document", "file_path": file_path})
    else:  # discord + generic keep the existing Discord-shaped behavior
        if summary:
            specs.append({"url": url, "json": {"content": summary,
                                               "allowed_mentions": {"parse": []}}})
        if file_ok:
            specs.append({"url": url, "file_field": "file", "file_path": file_path})
    return specs, notes


def _post_webhook_hardened(url: str, *, file_path: str | None = None,
                           json_payload: dict | None = None, max_mb: int = 25) -> bool:
    """Post an encode result to Discord/Slack/Telegram/generic webhooks.

    Same call-site contract as always; the service is detected from the
    URL and each request keeps the hardened (0, 2, 4)s retry loop.
    """
    _ok = False
    try:
        import time as _t
        specs, notes = build_webhook_requests(
            url, json_payload=json_payload, file_path=file_path, max_mb=max_mb)
        for n in notes:
            try:
                import logging
                logging.getLogger("BitCrusher").info(n)
            except Exception:
                pass
        for spec in specs:
            for delay in (0, 2, 4):
                try:
                    if spec.get("file_path"):
                        with open(spec["file_path"], "rb") as f:
                            _r = requests.post(spec["url"],
                                               data=spec.get("data"),
                                               files={spec.get("file_field", "file"): f},
                                               timeout=60)
                    else:
                        _r = requests.post(spec["url"], json=spec.get("json"), timeout=15)
                    if _r is not None and _r.status_code < 500:
                        _ok = True
                        break
                except Exception:
                    pass
                _t.sleep(delay)
    except Exception:
        pass
    return _ok

"""Webhook adapter tests: service detection, per-service request
planning, secret redaction (support/webhook.py)."""
import support.webhook as wh


DISCORD = "https://discord.com/api/webhooks/123456/AbCdEf-token"
SLACK = "https://hooks.slack.com/services/T000/B000/XXXX"
TELEGRAM = "https://api.telegram.org/bot99:SECRET/sendMessage?chat_id=42"


def test_detect_service_matrix():
    assert wh.detect_service(DISCORD) == "discord"
    assert wh.detect_service("https://discordapp.com/api/webhooks/1/t") == "discord"
    assert wh.detect_service(SLACK) == "slack"
    assert wh.detect_service(TELEGRAM) == "telegram"
    assert wh.detect_service("https://example.com/hook") == "generic"
    assert wh.detect_service("") == "generic"


def test_discord_requests_unchanged_shape(tmp_path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"x" * 100)
    specs, notes = wh.build_webhook_requests(
        DISCORD, json_payload={"compressed_size": 100, "original_size": 200},
        file_path=str(f))
    assert len(specs) == 2 and not notes
    assert "content" in specs[0]["json"]
    assert specs[0]["json"]["allowed_mentions"] == {"parse": []}
    assert specs[1]["file_field"] == "file" and specs[1]["file_path"] == str(f)


def test_slack_text_only_and_file_note(tmp_path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")
    specs, notes = wh.build_webhook_requests(
        SLACK, json_payload={"compressed_size": 1}, file_path=str(f))
    assert len(specs) == 1
    assert "text" in specs[0]["json"]
    assert any("Slack" in n for n in notes)


def test_telegram_message_and_document(tmp_path):
    f = tmp_path / "out.mp4"
    f.write_bytes(b"x" * 10)
    specs, notes = wh.build_webhook_requests(
        TELEGRAM, json_payload={"compressed_size": 10}, file_path=str(f))
    assert len(specs) == 2 and not notes
    assert specs[0]["url"].endswith("/sendMessage")
    assert specs[0]["json"]["chat_id"] == "42"
    assert specs[1]["url"].endswith("/sendDocument")
    assert specs[1]["data"] == {"chat_id": "42"}
    assert specs[1]["file_field"] == "document"


def test_telegram_missing_chat_id_no_specs():
    specs, notes = wh.build_webhook_requests(
        "https://api.telegram.org/bot99:SECRET/sendMessage",
        json_payload={"compressed_size": 1})
    assert specs == []
    assert any("chat_id" in n for n in notes)


def test_preshaped_payload_passthrough():
    payload = {"content": "hi"}
    specs, _ = wh.build_webhook_requests(DISCORD, json_payload=payload)
    assert specs[0]["json"] is payload


def test_oversize_file_skipped(tmp_path):
    f = tmp_path / "big.mp4"
    f.write_bytes(b"x" * 2048)
    specs, _ = wh.build_webhook_requests(
        DISCORD, json_payload={"compressed_size": 1}, file_path=str(f),
        max_mb=0)
    assert all("file_path" not in s for s in specs)


def test_summary_styles():
    stats = {"filename": "clip.mp4", "vmaf": 95.0}
    assert wh._format_webhook_summary(stats, "discord").startswith("**clip.mp4**")
    assert wh._format_webhook_summary(stats, "slack").startswith("*clip.mp4*")
    assert wh._format_webhook_summary(stats, "plain").startswith("clip.mp4")


def test_redaction_hides_tokens():
    r = wh.redact_webhook_url(TELEGRAM)
    assert "SECRET" not in r and "bot***" in r
    r2 = wh.redact_webhook_url(DISCORD)
    assert "AbCdEf-token" not in r2


def test_hardened_post_uses_specs(monkeypatch, tmp_path):
    calls = []

    class _R:
        status_code = 200

    def fake_post(url, **kw):
        calls.append((url, sorted(k for k in kw if kw[k] is not None)))
        return _R()

    monkeypatch.setattr(wh.requests, "post", fake_post)
    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")
    ok = wh._post_webhook_hardened(TELEGRAM,
                                   json_payload={"compressed_size": 1},
                                   file_path=str(f))
    assert ok is True
    assert len(calls) == 2
    assert calls[0][0].endswith("/sendMessage")
    assert calls[1][0].endswith("/sendDocument")

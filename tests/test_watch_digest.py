"""Watch-folder digest buffer tests (support/watch_digest.py)."""
from support.watch_digest import DigestBuffer


def test_empty_flush_returns_none():
    d = DigestBuffer()
    assert d.empty is True
    assert d.flush() is None


def test_aggregates_and_resets():
    d = DigestBuffer()
    d.add("queued")
    d.add("queued")
    d.add("done", 1024 * 1024)
    d.add("done", 1024 * 1024)
    d.add("failed")
    msg = d.flush()
    assert "2 queued" in msg
    assert "2 done" in msg
    assert "1 failed" in msg
    assert "saved" in msg and "MB" in msg
    # Flush resets.
    assert d.empty is True
    assert d.flush() is None


def test_saved_bytes_omitted_when_zero():
    d = DigestBuffer()
    d.add("queued")
    msg = d.flush()
    assert "saved" not in msg


def test_unknown_event_ignored():
    d = DigestBuffer()
    d.add("nonsense")
    assert d.empty is True


def test_negative_saved_clamped():
    d = DigestBuffer()
    d.add("done", -500)
    msg = d.flush()
    assert "1 done" in msg
    assert "saved" not in msg

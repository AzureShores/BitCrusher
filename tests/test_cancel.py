"""Cancellation system tests.

The Stop button was a silent no-op for the app's whole life: nothing ever
set a cancel flag True, stop_event was never read, and no encoder process
was ever killed. These tests pin the rebuilt single-Event design.
"""
import subprocess
import sys
import time

import BitCrusherV9 as bc
from encode import ffmpeg_exec as fx


def _spawn_sleeper():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_registry_register_unregister():
    p = _spawn_sleeper()
    try:
        fx._register_proc(p)
        assert p in fx._ACTIVE_PROCS
        fx._unregister_proc(p)
        assert p not in fx._ACTIVE_PROCS
        fx._unregister_proc(p)  # double-unregister is harmless
    finally:
        p.kill()


def test_kill_active_processes_tree_kills_within_2s():
    p = _spawn_sleeper()
    fx._register_proc(p)
    try:
        t0 = time.time()
        n = fx.kill_active_processes()
        assert n == 1
        p.wait(timeout=2.0)
        assert p.poll() is not None
        assert time.time() - t0 < 2.5
    finally:
        fx._unregister_proc(p)
        if p.poll() is None:
            p.kill()


def test_kill_active_skips_finished_processes():
    p = subprocess.Popen([sys.executable, "-c", "pass"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait(timeout=10)
    fx._register_proc(p)
    try:
        assert fx.kill_active_processes() == 0
    finally:
        fx._unregister_proc(p)


class _CancelStub:
    """Grafts the real property + methods onto a Tk-free object."""
    compression_cancelled = bc.CompressorGUI.compression_cancelled
    stop_compression = bc.CompressorGUI.stop_compression
    cancel_queue = bc.CompressorGUI.cancel_queue

    def __init__(self, running=True):
        import threading
        self.cancel_event = threading.Event()
        self.compression_running = running
        self.statuses = []

    def update_status(self, msg, level="INFO"):
        self.statuses.append((level, msg))


def test_stop_compression_sets_event_and_property_reads_it():
    g = _CancelStub(running=True)
    assert g.compression_cancelled is False
    g.stop_compression()
    assert g.cancel_event.is_set()
    assert g.compression_cancelled is True
    assert any("[Cancel]" in m for _, m in g.statuses)


def test_stop_compression_noop_when_idle():
    g = _CancelStub(running=False)
    g.stop_compression()
    assert not g.cancel_event.is_set()
    assert any("Nothing to stop" in m for _, m in g.statuses)


def test_cancel_queue_delegates():
    g = _CancelStub(running=True)
    g.cancel_queue()
    assert g.cancel_event.is_set()


def test_cli_cancel_reads_event():
    bc._CLI_CANCEL_EVENT.clear()
    assert bc._cli_cancel() is False
    bc._CLI_CANCEL_EVENT.set()
    try:
        assert bc._cli_cancel() is True
    finally:
        bc._CLI_CANCEL_EVENT.clear()

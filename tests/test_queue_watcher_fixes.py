"""Regression tests for the queue reorder buttons and the watcher toast.

Both bugs were silent no-ops: the toolbar arrows called a method that did
not exist, and the polling watcher called notify_cb with one argument
while the GUI wired a two-argument lambda (TypeError swallowed).
"""
import os
import time

import BitCrusherV9 as bc


class _StubQueueBox:
    def __init__(self, selection):
        self._sel = list(selection)
        self.selected_iids = None

    def curselection(self):
        return list(self._sel)

    def selection_set(self, iids):
        self.selected_iids = list(iids)


class _StubGui:
    """Bare object exercising CompressorGUI.move_* unbound (no Tk needed)."""
    move_selection = bc.CompressorGUI.move_selection
    move_up = bc.CompressorGUI.move_up
    move_down = bc.CompressorGUI.move_down

    def __init__(self, files, selection):
        self.file_list = list(files)
        self.queue_box = _StubQueueBox(selection)
        self.refreshed = 0
        self.saved = 0

    def refresh_queue_box(self):
        self.refreshed += 1

    def _save_queue(self):
        self.saved += 1


def test_move_selection_up_swaps_and_reselects():
    g = _StubGui(["a.mp4", "b.mp4", "c.mp4"], selection=[1])
    g.move_selection(-1)
    assert g.file_list == ["b.mp4", "a.mp4", "c.mp4"]
    assert g.refreshed == 1 and g.saved == 1
    # Moved row stays selected so a second click keeps moving it.
    assert g.queue_box.selected_iids == [bc._normalize_drop_path("b.mp4")]


def test_move_selection_down_swaps_and_reselects():
    g = _StubGui(["a.mp4", "b.mp4", "c.mp4"], selection=[1])
    g.move_selection(+1)
    assert g.file_list == ["a.mp4", "c.mp4", "b.mp4"]
    assert g.queue_box.selected_iids == [bc._normalize_drop_path("b.mp4")]


def test_move_selection_boundaries_no_change():
    g = _StubGui(["a.mp4", "b.mp4"], selection=[0])
    g.move_selection(-1)          # already at top
    assert g.file_list == ["a.mp4", "b.mp4"]
    g2 = _StubGui(["a.mp4", "b.mp4"], selection=[1])
    g2.move_selection(+1)         # already at bottom
    assert g2.file_list == ["a.mp4", "b.mp4"]


def test_move_selection_empty_selection_is_noop():
    g = _StubGui(["a.mp4"], selection=[])
    g.move_selection(-1)
    assert g.refreshed == 0 and g.saved == 0


def test_watcher_flush_ready_notifies_with_title_and_message(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 2048)
    old = time.time() - 60
    os.utime(f, (old, old))

    ready, notified = [], []
    w = bc.FolderWatcher(
        on_file_ready=lambda p: ready.append(p),
        notify_cb=lambda *a: notified.append(a),
        stable_secs=1.0,
    )
    w._seen[str(f)] = old
    w._flush_ready()

    assert ready == [str(f)]
    assert len(notified) == 1
    assert len(notified[0]) == 2          # (title, msg) — the bug was one arg
    title, msg = notified[0]
    assert str(f) in msg

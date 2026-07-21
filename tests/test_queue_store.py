"""queue.json v2 persistence tests (support/queue_store.py).

v1 forgot job status entirely - a crash mid-batch restored done files as
pending and they re-encoded on the next Start. v2 persists status and
these tests pin the migration, round-trip, skip and demotion rules.
"""
from support.queue_store import dump_queue, load_queue, make_job, pending_jobs


def _touch(tmp_path, name, data=b"x"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_v1_migration_all_pending(tmp_path):
    a = _touch(tmp_path, "a.mp4")
    b = _touch(tmp_path, "b.mp4")
    v1 = {"version": 1, "files": [a, b],
          "per_file_opts": {a: {"encoder": "x265"}}}
    jobs = load_queue(v1)
    assert [j["path"] for j in jobs] == [a, b]
    assert all(j["status"] == "pending" for j in jobs)
    assert jobs[0]["opts"] == {"encoder": "x265"}
    assert jobs[1]["opts"] == {}


def test_v2_round_trip(tmp_path):
    a = _touch(tmp_path, "a.mp4")
    out = _touch(tmp_path, "a_out.mp4")
    payload = dump_queue(
        [a], {a: {"target_mb": 8}},
        {a: {"status": "done", "output": out, "finished_at": 123.0}})
    assert payload["version"] == 2
    jobs = load_queue(payload)
    assert len(jobs) == 1
    j = jobs[0]
    assert j["status"] == "done" and j["output"] == out
    assert j["finished_at"] == 123.0
    assert j["opts"] == {"target_mb": 8}


def test_done_with_missing_output_demoted(tmp_path):
    a = _touch(tmp_path, "a.mp4")
    payload = dump_queue([a], None,
                         {a: {"status": "done",
                              "output": str(tmp_path / "gone.mp4"),
                              "finished_at": 1.0}})
    jobs = load_queue(payload)
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["output"] is None


def test_missing_source_dropped(tmp_path):
    a = _touch(tmp_path, "a.mp4")
    payload = dump_queue([a, str(tmp_path / "ghost.mp4")], None, None)
    jobs = load_queue(payload)
    assert [j["path"] for j in jobs] == [a]


def test_pending_jobs_filter(tmp_path):
    jobs = [make_job("a", status="done"), make_job("b", status="failed"),
            make_job("c", status="cancelled"), make_job("d")]
    keep = pending_jobs(jobs)
    assert [j["path"] for j in keep] == ["b", "c", "d"]


def test_corrupt_input_tolerated():
    assert load_queue(None) == []
    assert load_queue("nonsense") == []
    assert load_queue({"version": 2, "jobs": "not-a-list"}) == []
    assert load_queue({"version": 2,
                       "jobs": [42, {"no": "path"}, {"path": ""}]}) == []


def test_invalid_status_normalized(tmp_path):
    a = _touch(tmp_path, "a.mp4")
    jobs = load_queue({"version": 2,
                       "jobs": [{"path": a, "status": "exploded"}]})
    assert jobs[0]["status"] == "pending"

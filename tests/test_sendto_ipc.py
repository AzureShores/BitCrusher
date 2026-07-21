"""Send-To / single-instance IPC protocol v2 tests.

Covers the endpoint file (port + session token), the OK-ack handshake
that stops a foreign port-squatter from silently swallowing hand-offs,
the TARGET line (context-menu preset groundwork), retry/fallback, and
the port-collision bind ladder.
"""
import json
import os
import socket
import time

from support import sendto_ipc as ipc


def _free_ports(n=3):
    """Grab n distinct ephemeral ports (freed immediately; race-tolerant)."""
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


def _start_server(tmp_path, ports, received):
    srv = ipc.IpcServer(
        on_paths=lambda paths, target: received.append((paths, target)),
        settings_dir=str(tmp_path),
        ports=ports,
    )
    assert srv.start()
    return srv


def test_endpoint_file_written_and_removed(tmp_path):
    ports = _free_ports(1)
    srv = _start_server(tmp_path, ports, [])
    try:
        ep = ipc.read_endpoint(str(tmp_path))
        assert ep is not None
        assert ep["port"] == srv.port == ports[0]
        assert ep["token"] == srv.token and len(ep["token"]) == 32
        assert ep["pid"] == os.getpid()
    finally:
        srv.stop()
    assert ipc.read_endpoint(str(tmp_path)) is None


def test_send_receives_ack_and_paths(tmp_path):
    received = []
    srv = _start_server(tmp_path, _free_ports(1), received)
    try:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        # Point the client at the server's port via the endpoint file.
        old_port = ipc._BC_IPC_PORT
        ipc._BC_IPC_PORT = srv.port
        try:
            ok = ipc._bc_ipc_send([str(f)], settings_dir=str(tmp_path))
        finally:
            ipc._BC_IPC_PORT = old_port
        assert ok is True
        deadline = time.time() + 2
        while not received and time.time() < deadline:
            time.sleep(0.05)
        assert received and received[0][0] == [str(f)]
        assert received[0][1] is None
    finally:
        srv.stop()


def test_target_line_parsed(tmp_path):
    received = []
    srv = _start_server(tmp_path, _free_ports(1), received)
    try:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        ok = ipc._bc_ipc_send([str(f)], settings_dir=str(tmp_path), target_mb=10)
        assert ok is True
        deadline = time.time() + 2
        while not received and time.time() < deadline:
            time.sleep(0.05)
        assert received and received[0][1] == 10.0
    finally:
        srv.stop()


def test_bad_token_gets_no_ack(tmp_path):
    received = []
    srv = _start_server(tmp_path, _free_ports(1), received)
    try:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        # Corrupt the endpoint token so the client presents a wrong one.
        ep_path = os.path.join(str(tmp_path), ipc._ENDPOINT_NAME)
        with open(ep_path, "r", encoding="utf-8") as fh:
            ep = json.load(fh)
        ep["token"] = "0" * 32
        with open(ep_path, "w", encoding="utf-8") as fh:
            json.dump(ep, fh)
        ok = ipc._bc_ipc_send([str(f)], settings_dir=str(tmp_path),
                              attempts=1, timeout=1.0)
        assert ok is False
        assert received == []
    finally:
        srv.stop()


def test_nothing_listening_falls_back_fast(tmp_path):
    port = _free_ports(1)[0]
    with open(os.path.join(str(tmp_path), ipc._ENDPOINT_NAME), "w",
              encoding="utf-8") as fh:
        json.dump({"port": port, "token": "dead" * 8, "pid": 1}, fh)
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    t0 = time.time()
    # Windows firewalls sometimes silently drop instead of refusing, so the
    # bound is attempts*timeout, not "instant": 1 attempt at 1.0s here.
    ok = ipc._bc_ipc_send([str(f)], settings_dir=str(tmp_path),
                          attempts=1, timeout=1.0)
    assert ok is False
    assert time.time() - t0 < 3.0


def test_port_collision_falls_through_ladder(tmp_path):
    p1, p2 = _free_ports(2)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", p1))
    blocker.listen(1)
    try:
        srv = _start_server(tmp_path, [p1, p2], [])
        try:
            assert srv.port == p2
            assert ipc.read_endpoint(str(tmp_path))["port"] == p2
        finally:
            srv.stop()
    finally:
        blocker.close()


def test_all_ports_busy_start_returns_false(tmp_path):
    p1 = _free_ports(1)[0]
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", p1))
    blocker.listen(1)
    try:
        srv = ipc.IpcServer(on_paths=lambda *a: None,
                            settings_dir=str(tmp_path), ports=[p1])
        assert srv.start() is False
        assert ipc.read_endpoint(str(tmp_path)) is None
    finally:
        blocker.close()


def test_missing_files_send_false(tmp_path):
    assert ipc._bc_ipc_send([str(tmp_path / "nope.mp4")],
                            settings_dir=str(tmp_path)) is False
    assert ipc._bc_ipc_send([], settings_dir=str(tmp_path)) is False

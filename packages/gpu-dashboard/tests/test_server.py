"""End-to-end HTTP server tests — spins build_server on an ephemeral
port, exercises every shell behavior the routes module can't reach
(static serving, OPTIONS/CORS, SPA fallback gating, path traversal)."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.client import HTTPConnection

import pytest

from gpuwatchlib import Node, NodeRegistry, NullAuditLogger
from gpu_dashboard.server import _is_api_path, build_server


@pytest.fixture
def live_server(tmp_path):
    registry = NodeRegistry(tmp_path / "hosts.json")
    registry.add(Node(name="g1", host="h", user="u", key_path="/k"))
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><div id=root></div>")
    (static / "app.js").write_text("// js")
    server = build_server(
        host="127.0.0.1", port=0, registry=registry,
        audit=NullAuditLogger(), static_dir=static,
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(30):
        try:
            c = HTTPConnection("127.0.0.1", port, timeout=1)
            c.request("GET", "/healthz")
            c.getresponse().read()
            c.close()
            break
        except OSError:
            time.sleep(0.05)
    try:
        yield port, static
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, r.read().decode("utf-8"), r.headers


def _post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


# ---- Smoke ----


def test_healthz_over_http(live_server):
    port, _ = live_server
    status, body, _ = _get(port, "/healthz")
    assert status == 200 and json.loads(body)["ok"] is True


def test_nodes_listed_over_http(live_server):
    port, _ = live_server
    status, body, _ = _get(port, "/nodes")
    assert status == 200
    parsed = json.loads(body)
    assert parsed["nodes"][0]["name"] == "g1"


def test_post_then_delete_node(live_server):
    port, _ = live_server
    status, body = _post(port, "/nodes", {
        "name": "new", "host": "h", "user": "u", "key_path": "/k",
    })
    assert status == 201
    req = urllib.request.Request(f"http://127.0.0.1:{port}/nodes/new", method="DELETE")
    with urllib.request.urlopen(req) as r:
        assert r.status == 200


def test_options_cors(live_server):
    port, _ = live_server
    req = urllib.request.Request(f"http://127.0.0.1:{port}/nodes", method="OPTIONS")
    with urllib.request.urlopen(req) as r:
        assert r.status == 204
        assert r.headers.get("Access-Control-Allow-Origin") == "*"
        assert "POST" in r.headers.get("Access-Control-Allow-Methods", "")


# ---- SPA fallback + path-traversal ----


def test_static_index_at_root(live_server):
    port, _ = live_server
    status, body, headers = _get(port, "/")
    assert status == 200 and "doctype" in body.lower()
    assert "html" in headers.get("Content-Type", "")


def test_static_asset_with_mime(live_server):
    port, _ = live_server
    _, _, headers = _get(port, "/app.js")
    assert "javascript" in headers.get("Content-Type", "").lower()


def test_unknown_spa_route_falls_back_to_index(live_server):
    port, _ = live_server
    status, body, _ = _get(port, "/somewhere/in/the/spa")
    assert status == 200 and "doctype" in body.lower()


def test_unknown_api_route_returns_json_404(live_server):
    """SPA fallback shouldn't mask API 404s."""
    port, _ = live_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nodes/missing/status")
    assert exc.value.code == 404


def test_path_traversal_blocked(live_server):
    """Try to read /etc/passwd via the static fallback. urllib
    normalizes "..", so we need a raw HTTPConnection to actually send it."""
    port, _ = live_server
    c = HTTPConnection("127.0.0.1", port, timeout=2)
    c.request("GET", "/../../../etc/passwd")
    resp = c.getresponse()
    body = resp.read().decode("utf-8", errors="replace")
    c.close()
    assert "root:" not in body and "x:0:0:" not in body


def test_post_empty_body_is_400(live_server):
    port, _ = live_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/nodes", data=b"",
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_malformed_json_body_treated_as_empty(live_server):
    port, _ = live_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/nodes", data=b"{not json",
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


# ---- _is_api_path classifier ----


def test_is_api_path_recognises_known_prefixes():
    for p in ("/healthz", "/audit", "/nodes", "/nodes/g1/status", "/fleet/summary"):
        assert _is_api_path(p) is True
    for p in ("/", "/anywhere", "/index.html", "/static/app.js"):
        assert _is_api_path(p) is False

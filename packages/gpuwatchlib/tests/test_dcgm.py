"""Tests for the dcgm-exporter HTTP client + Prometheus text parser."""
from __future__ import annotations

import http.server
import threading
import time
from http.client import HTTPConnection

import pytest

from gpuwatchlib import DcgmSample, parse_prometheus_text
from gpuwatchlib.probe_dcgm import (
    DcgmExporterClient, DcgmExporterError, KEY_METRICS,
)


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


SAMPLE_METRICS = """\
# HELP DCGM_FI_DEV_GPU_TEMP GPU temperature.
# TYPE DCGM_FI_DEV_GPU_TEMP gauge
DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-aaa",device="nvidia0",modelName="H100"} 35
DCGM_FI_DEV_GPU_TEMP{gpu="1",UUID="GPU-bbb",device="nvidia1",modelName="H100"} 42

# HELP DCGM_FI_DEV_XID_ERRORS Last Xid code.
# TYPE DCGM_FI_DEV_XID_ERRORS gauge
DCGM_FI_DEV_XID_ERRORS{gpu="0"} 0
DCGM_FI_DEV_XID_ERRORS{gpu="1"} 79

# A line without labels — uncommon but legal.
SOME_OTHER_METRIC 1.5
"""


def test_parser_extracts_labels_and_values():
    out = parse_prometheus_text(SAMPLE_METRICS)
    assert "DCGM_FI_DEV_GPU_TEMP" in out
    temps = {s.gpu: s.value for s in out["DCGM_FI_DEV_GPU_TEMP"]}
    assert temps == {0: 35.0, 1: 42.0}


def test_parser_keeps_full_label_dict():
    [first] = parse_prometheus_text(
        'DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-x"} 35\n'
    )["DCGM_FI_DEV_GPU_TEMP"]
    assert first.labels == {"gpu": "0", "UUID": "GPU-x"}


def test_parser_handles_unlabelled_metric():
    out = parse_prometheus_text("SOME_OTHER_METRIC 1.5\n")
    assert out["SOME_OTHER_METRIC"][0].value == 1.5
    assert out["SOME_OTHER_METRIC"][0].labels == {}


def test_parser_skips_comments_and_blanks():
    text = "# header\n\n# more\nDCGM_FI_DEV_GPU_TEMP{gpu=\"0\"} 1\n"
    out = parse_prometheus_text(text)
    assert len(out["DCGM_FI_DEV_GPU_TEMP"]) == 1


def test_parser_skips_malformed_lines_silently():
    text = "not a metric line at all\nDCGM_FI_DEV_GPU_TEMP{gpu=\"0\"} 1\n"
    out = parse_prometheus_text(text)
    assert "DCGM_FI_DEV_GPU_TEMP" in out


def test_dcgm_sample_gpu_property_handles_non_integer():
    s = DcgmSample(name="x", labels={"gpu": "not-a-number"}, value=1)
    assert s.gpu is None
    s2 = DcgmSample(name="x", labels={}, value=1)
    assert s2.gpu is None


def test_parser_handles_trailing_timestamp():
    """The Prometheus format allows an optional millisecond timestamp
    after the value. The exporter doesn't emit them, but our regex
    tolerates them anyway."""
    out = parse_prometheus_text("X{a=\"b\"} 1 12345678901\n")
    assert out["X"][0].value == 1.0


def test_parser_handles_escaped_quotes_in_labels():
    text = 'X{a="he said \\"hi\\""} 1\n'
    out = parse_prometheus_text(text)
    assert out["X"][0].labels["a"] == 'he said "hi"'


def test_key_metrics_set_is_immutable_tuple():
    """If someone refactors KEY_METRICS to a list, fetch_selected
    drift becomes a per-call mutation hazard. Lock it as a tuple."""
    assert isinstance(KEY_METRICS, tuple)


# ----------------------------------------------------------------------
# HTTP client — runs a tiny local server so we exercise the full path
# ----------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    response_body: bytes = SAMPLE_METRICS.encode("utf-8")
    response_code: int = 200

    def do_GET(self):
        self.send_response(self.response_code)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *_a, **_kw):  # quiet
        pass


@pytest.fixture
def exporter_server():
    """One-shot in-process metrics server. Yields (port, set_response)
    where set_response(body, code) lets the test mutate the next reply."""
    handler_cls = type("MutableHandler", (_Handler,), {})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Drain a no-op connect attempt to make sure socket is bound.
    for _ in range(10):
        try:
            c = HTTPConnection("127.0.0.1", port, timeout=1)
            c.request("GET", "/")
            c.getresponse().read()
            c.close()
            break
        except OSError:
            time.sleep(0.02)

    def setter(body: bytes, code: int = 200):
        handler_cls.response_body = body
        handler_cls.response_code = code

    try:
        yield port, setter
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_client_fetches_text(exporter_server):
    port, _ = exporter_server
    client = DcgmExporterClient(f"http://127.0.0.1:{port}")
    text = client.fetch_text()
    assert "DCGM_FI_DEV_GPU_TEMP" in text


def test_client_fetch_parses_into_dict(exporter_server):
    port, _ = exporter_server
    client = DcgmExporterClient(f"http://127.0.0.1:{port}")
    out = client.fetch()
    assert "DCGM_FI_DEV_GPU_TEMP" in out and "DCGM_FI_DEV_XID_ERRORS" in out


def test_client_fetch_selected_strips_non_curated_metrics(exporter_server):
    port, _ = exporter_server
    client = DcgmExporterClient(f"http://127.0.0.1:{port}")
    out = client.fetch_selected()
    assert "SOME_OTHER_METRIC" not in out
    assert "DCGM_FI_DEV_GPU_TEMP" in out


def test_client_raises_on_5xx(exporter_server):
    port, setter = exporter_server
    setter(b"oops", code=500)
    client = DcgmExporterClient(f"http://127.0.0.1:{port}", timeout=2.0)
    with pytest.raises(DcgmExporterError, match="HTTP 500"):
        client.fetch_text()


def test_client_raises_on_unreachable_host():
    # Use a port that's almost certainly closed.
    client = DcgmExporterClient("http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(DcgmExporterError):
        client.fetch_text()


def test_client_strips_trailing_slash_on_base_url():
    client = DcgmExporterClient("http://host/")
    assert client.base_url == "http://host"

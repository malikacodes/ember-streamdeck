import importlib.util
import json
from pathlib import Path
import struct
from unittest.mock import Mock

import pytest


PLUGIN_PATH = Path(__file__).parents[1] / "com.embermug.dial.sdPlugin" / "plugin.py"
SPEC = importlib.util.spec_from_file_location("ember_streamdeck_plugin", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class FakeSocket:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.sent = []

    def recv(self, count):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > count:
            self.chunks.insert(0, chunk[count:])
            return chunk[:count]
        return chunk

    def sendall(self, data):
        self.sent.append(bytes(data))


class FeedbackSocket:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def test_plugin_entrypoint_is_executable():
    assert PLUGIN_PATH.stat().st_mode & 0o111


def test_atomic_json_creates_parent_and_replaces_file(tmp_path):
    destination = tmp_path / "nested" / "command.json"

    plugin.atomic_json(destination, {"cmd": "set_temp", "temp_c": 42})

    assert json.loads(destination.read_text()) == {"cmd": "set_temp", "temp_c": 42}
    assert not destination.with_suffix(".tmp").exists()


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ('{"connected": true}', {"connected": True}),
        ("not-json", {}),
        ("[]", {}),
        ("null", {}),
        ('"connected"', {}),
    ],
)
def test_status_accepts_only_valid_json_objects(tmp_path, monkeypatch, contents, expected):
    status_path = tmp_path / "status.json"
    status_path.write_text(contents)
    monkeypatch.setattr(plugin, "STATUS", status_path)

    assert plugin.status() == expected


def test_status_returns_empty_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "STATUS", tmp_path / "missing.json")

    assert plugin.status() == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (145, 145.0),
        ("145.5", 145.5),
        (None, None),
        (True, None),
        ("hot", None),
        (float("nan"), None),
        (float("inf"), None),
        ([], None),
    ],
)
def test_finite_number_rejects_bad_or_non_finite_values(value, expected):
    assert plugin.finite_number(value) == expected


@pytest.mark.parametrize("bad_target", [None, True, "hot", float("nan"), float("inf"), []])
def test_target_f_falls_back_to_off_for_bad_values(monkeypatch, bad_target):
    monkeypatch.setattr(plugin, "status", lambda: {"target_f": bad_target})

    assert plugin.target_f() == 0.0


@pytest.mark.parametrize(
    ("state", "now", "expected"),
    [
        ({"connected": False}, 1_000, False),
        ({"connected": True, "updated_ts": 700}, 1_000, False),
        ({"connected": True, "updated_ts": 699.9}, 1_000, True),
        ({"connected": True, "updated_ts": 1_001}, 1_000, False),
        ({"connected": True}, 1_000, True),
        ({"connected": True, "updated_ts": "invalid"}, 1_000, True),
    ],
)
def test_status_staleness_uses_five_minute_threshold(state, now, expected):
    assert plugin.status_is_stale(state, now=now) is expected


@pytest.mark.parametrize(
    ("fahrenheit", "expected_celsius"),
    [(0, 0), (32, 0), (120, pytest.approx(48.8888889)), (145, pytest.approx(62.7777778))],
)
def test_set_temp_writes_celsius_command(monkeypatch, fahrenheit, expected_celsius):
    atomic_json = Mock()
    monkeypatch.setattr(plugin, "atomic_json", atomic_json)

    plugin.set_temp(fahrenheit)

    path, command = atomic_json.call_args.args
    assert path == plugin.COMMAND
    assert command == {"cmd": "set_temp", "temp_c": expected_celsius}


def test_send_temp_command_reports_write_failure(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin, "set_temp", Mock(side_effect=PermissionError("read only")))

    assert plugin.send_temp_command(ws, "dial-1", 145) is False
    assert ws.messages[-1]["payload"] == {"title": "COMMAND FAILED", "value": "TRY AGAIN"}


def test_send_temp_command_reports_success(monkeypatch):
    ws = FeedbackSocket()
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)

    assert plugin.send_temp_command(ws, "dial-1", 145) is True
    set_temp.assert_called_once_with(145)
    assert ws.messages == []


def test_request_reconnect_opens_existing_app(tmp_path, monkeypatch):
    ember_app = tmp_path / "Ember Mug.app"
    ember_app.mkdir()
    popen = Mock()
    monkeypatch.setattr(plugin, "EMBER_APP", ember_app)
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)

    plugin.request_reconnect()

    popen.assert_called_once_with(["open", str(ember_app)])


def test_request_reconnect_reports_missing_app(tmp_path, monkeypatch):
    popen = Mock()
    monkeypatch.setattr(plugin, "EMBER_APP", tmp_path / "Missing.app")
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)

    with pytest.raises(FileNotFoundError, match="Ember app not found"):
        plugin.request_reconnect()

    popen.assert_not_called()


def test_feedback_treats_old_connected_status_as_offline(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "updated_ts": plugin.time.time() - plugin.STATUS_STALE_SECONDS - 1,
            "battery_percent": 90,
        },
    )

    plugin.feedback(ws, "dial-1", override=145)

    assert ws.messages[-1]["payload"]["title"] == "EMBER · OFFLINE"


@pytest.mark.parametrize("bad_connected", ["true", 1, [], {}])
def test_feedback_requires_real_boolean_connection(monkeypatch, bad_connected):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {"connected": bad_connected, "updated_ts": plugin.time.time(), "battery_percent": 90},
    )

    plugin.feedback(ws, "dial-1", override=145)

    assert ws.messages[-1]["payload"]["title"] == "EMBER · OFFLINE"


@pytest.mark.parametrize("bad_battery", ["unknown", True, float("nan"), float("inf"), []])
def test_feedback_ignores_bad_battery_without_crashing(monkeypatch, bad_battery):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "updated_ts": plugin.time.time(),
            "battery_percent": bad_battery,
        },
    )

    plugin.feedback(ws, "dial-1", override=145)

    assert ws.messages[-1]["payload"]["title"] == "EMBER"


def make_websocket(chunks):
    websocket = object.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket(chunks)
    return websocket


def test_websocket_reads_header_split_across_tcp_chunks():
    websocket = make_websocket([b"\x81", b"\x02", b"{}"])

    assert websocket.receive() == {}


def test_websocket_reads_extended_payload_length():
    payload = json.dumps({"message": "x" * 200}).encode()
    frame = b"\x81\x7e" + struct.pack("!H", len(payload)) + payload
    websocket = make_websocket([frame[:1], frame[1:3], frame[3:25], frame[25:]])

    assert websocket.receive() == {"message": "x" * 200}


def test_websocket_raises_eof_when_frame_ends_early():
    websocket = make_websocket([b"\x81\x05", b"ab", b""])

    with pytest.raises(EOFError):
        websocket.receive()


def test_websocket_raises_eof_when_header_is_missing():
    websocket = make_websocket([b""])

    with pytest.raises(EOFError):
        websocket.receive()

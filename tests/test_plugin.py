import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
from unittest.mock import Mock

import pytest


PLUGIN_PATH = (
    Path(__file__).parents[1]
    / "com.embermug.dial.sdPlugin"
    / "plugin.py"
)
PLUGIN_ROOT = PLUGIN_PATH.parent
MANIFEST_PATH = PLUGIN_ROOT / "manifest.json"
SPEC = importlib.util.spec_from_file_location("ember_streamdeck_plugin", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class FakeSocket:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.sent = []
        self.timeout = None

    def recv(self, count):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        if len(chunk) > count:
            self.chunks.insert(0, chunk[count:])
            return chunk[:count]
        return chunk

    def sendall(self, data):
        self.sent.append(bytes(data))

    def settimeout(self, timeout):
        self.timeout = timeout


class FeedbackSocket:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


@pytest.fixture(autouse=True)
def reset_plugin_state():
    plugin.selected = None
    plugin.last_action = 0.0


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def dial_actions():
    # Only the dial has a strip layout. The keys draw whole pictures.
    return [action for action in load_manifest()["Actions"] if "Encoder" in action]


def resolve_extensionless_asset(relative_path):
    path = PLUGIN_ROOT / relative_path
    return [candidate for suffix in (".svg", ".png") if (candidate := path.with_suffix(suffix)).exists()]


def rectangles_overlap(first, second):
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return not (
        first_x + first_width <= second_x
        or second_x + second_width <= first_x
        or first_y + first_height <= second_y
        or second_y + second_height <= first_y
    )


def test_manifest_references_files_that_exist():
    manifest = load_manifest()

    assert (PLUGIN_ROOT / manifest["CodePath"]).is_file()
    assert resolve_extensionless_asset(manifest["Icon"])
    for action in manifest["Actions"]:
        assert resolve_extensionless_asset(action["Icon"])
        if "Encoder" in action:
            assert (PLUGIN_ROOT / action["Encoder"]["layout"]).is_file()
        if "PropertyInspectorPath" in action:
            assert (PLUGIN_ROOT / action["PropertyInspectorPath"]).is_file()
        for state in action["States"]:
            assert resolve_extensionless_asset(state["Image"])


def test_manifest_identity_and_version_are_well_formed():
    manifest = load_manifest()

    assert manifest["UUID"] == "com.embermug.dial"
    assert manifest["Category"] == manifest["Name"]
    assert all(action["UUID"].startswith(f'{manifest["UUID"]}.') for action in manifest["Actions"])
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", manifest["Version"])


@pytest.mark.parametrize(
    ("suffix", "expected_size"),
    [(".png", (256, 256)), ("@2x.png", (512, 512))],
)
def test_manifest_plugin_icon_uses_required_png_sizes(suffix, expected_size):
    icon_path = PLUGIN_ROOT / f'{load_manifest()["Icon"]}{suffix}'
    header = icon_path.read_bytes()[:24]

    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", header[16:24]) == expected_size


@pytest.mark.parametrize(
    ("suffix", "expected_size"),
    [(".png", (28, 28)), ("@2x.png", (56, 56))],
)
def test_manifest_category_has_required_icon_sizes(suffix, expected_size):
    manifest = load_manifest()
    icon_path = PLUGIN_ROOT / f'{manifest["CategoryIcon"]}{suffix}'
    header = icon_path.read_bytes()[:24]

    assert manifest["Category"] == manifest["Name"]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", header[16:24]) == expected_size


def test_manifest_documents_macos_12_or_newer():
    manifest = load_manifest()
    macos = next(entry for entry in manifest["OS"] if entry["Platform"] == "mac")

    assert int(macos["MinimumVersion"].split(".")[0]) >= 12


def test_plugin_entrypoint_has_python_shebang():
    first_line = PLUGIN_PATH.read_text(encoding="utf-8").splitlines()[0]

    assert first_line == "#!/usr/bin/env python3"


def test_plugin_entrypoint_is_executable_for_fresh_installs():
    assert PLUGIN_PATH.stat().st_mode & 0o111


def test_presets_are_unique_sorted_and_inside_ember_range():
    assert plugin.PRESETS == sorted(set(plugin.PRESETS))
    assert all(120 <= preset <= 145 for preset in plugin.PRESETS)


def test_layout_keys_are_unique_and_items_stay_inside_dial_canvas():
    for action in dial_actions():
        layout_path = PLUGIN_ROOT / action["Encoder"]["layout"]
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        items = layout["items"]
        keys = [item["key"] for item in items]
        assert len(keys) == len(set(keys))
        for item in items:
            x, y, width, height = item["rect"]
            assert x >= 0 and y >= 0
            assert width > 0 and height > 0
            assert x + width <= 200
            assert y + height <= 100


def test_layout_items_do_not_overlap_on_the_same_visual_layer():
    for action in dial_actions():
        layout_path = PLUGIN_ROOT / action["Encoder"]["layout"]
        items = json.loads(layout_path.read_text(encoding="utf-8"))["items"]
        for index, first in enumerate(items):
            for second in items[index + 1 :]:
                if first.get("zOrder", 0) == second.get("zOrder", 0):
                    assert not rectangles_overlap(first["rect"], second["rect"]), (
                        f'{first["key"]} overlaps {second["key"]} on zOrder '
                        f'{first.get("zOrder", 0)}'
                    )


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
def test_status_reads_valid_json_and_ignores_invalid_json(tmp_path, monkeypatch, contents, expected):
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
        ({"temperature": 145}, None),
    ],
)
def test_finite_number_rejects_malformed_and_non_finite_values(value, expected):
    result = plugin.finite_number(value)
    if expected is None:
        assert result is None
    else:
        assert result == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [({"target_f": 145}, 145.0), ({"target_f": 130.5}, 130.5), ({}, 0.0)],
)
def test_target_f(monkeypatch, state, expected):
    monkeypatch.setattr(plugin, "status", lambda: state)

    assert plugin.target_f() == expected


@pytest.mark.parametrize("bad_target", [None, True, "hot", float("nan"), float("inf"), []])
def test_target_f_falls_back_to_off_for_malformed_values(monkeypatch, bad_target):
    monkeypatch.setattr(plugin, "status", lambda: {"target_f": bad_target})

    assert plugin.target_f() == 0.0


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
    assert command["cmd"] == "set_temp"
    assert command["temp_c"] == expected_celsius


def test_request_reconnect_opens_the_ember_app(tmp_path, monkeypatch):
    ember_app = tmp_path / "Ember Mug.app"
    ember_app.mkdir()
    popen = Mock()
    monkeypatch.setattr(plugin, "EMBER_APP", ember_app)
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)

    plugin.request_reconnect()

    popen.assert_called_once_with(["open", str(plugin.EMBER_APP)])


def test_request_reconnect_reports_missing_app_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "EMBER_APP", tmp_path / "Missing Ember Mug.app")
    popen = Mock()
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)

    with pytest.raises(FileNotFoundError, match="Ember app not found"):
        plugin.request_reconnect()

    popen.assert_not_called()


def test_arg_reads_stream_deck_command_line_flag(monkeypatch):
    monkeypatch.setattr(plugin.sys, "argv", ["plugin.py", "-port", "1234"])

    assert plugin.arg("-port") == "1234"


@pytest.mark.parametrize(
    ("state", "now", "expected"),
    [
        ({"connected": False}, 1_000, False),
        ({"connected": True, "updated_ts": 701}, 1_000, False),
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
    ("state", "target", "expected"),
    [
        ({"current_f": 145}, 145, True),
        ({"current_f": 144}, 145, True),
        ({"current_f": 146}, 145, True),
        ({"current_f": 143.9}, 145, False),
        ({"current_f": 145}, 0, False),
        ({}, 145, False),
    ],
)
def test_is_at_target_uses_one_degree_range(state, target, expected):
    assert plugin.is_at_target(state, target) is expected


def test_off_display_hides_temperature_and_arrow():
    display = plugin.temperature_display({"current_f": 116}, 0)

    assert display["value"] == {"value": "OFF", "enabled": True}
    assert display["current"]["enabled"] is False
    assert display["trend"]["enabled"] is False
    assert display["target"]["enabled"] is False


def test_target_preview_shows_only_selected_temperature():
    display = plugin.temperature_display({"current_f": 116}, 145, target_only=True)

    assert display["value"] == {"value": "145°", "enabled": True}
    assert display["current"]["enabled"] is False
    assert display["trend"]["enabled"] is False


@pytest.mark.parametrize("bad_current", [None, True, "warm", float("nan"), float("inf"), []])
def test_temperature_display_ignores_malformed_current_temperature(bad_current):
    display = plugin.temperature_display({"current_f": bad_current}, 145)

    assert display["value"] == {"value": "145°", "enabled": True}
    assert display["current"]["enabled"] is False
    assert display["trend"]["enabled"] is False


def test_heating_display_uses_red_up_arrow():
    display = plugin.temperature_display({"current_f": 120}, 145)

    assert display["current"]["value"] == "120°"
    assert display["trend"] == {"value": "▲", "color": "#FF5A5F", "enabled": True}
    assert display["target"]["value"] == "145°"


def test_cooling_display_uses_blue_down_arrow():
    display = plugin.temperature_display({"current_f": 145}, 135)

    assert display["trend"] == {"value": "▼", "color": "#3B9CFF", "enabled": True}


def test_at_target_checkmark_can_be_shown_then_removed():
    with_check = plugin.temperature_display({"current_f": 145}, 145, show_check=True)
    without_check = plugin.temperature_display({"current_f": 145}, 145, show_check=False)

    assert with_check["value"]["value"] == "145°  ✓"
    assert without_check["value"]["value"] == "145°"
    assert with_check["trend"]["enabled"] is False


@pytest.mark.parametrize(
    ("state", "target", "expected"),
    [
        ({"current_f": 145}, 145, "145°  ✓"),
        ({"current_f": 130}, 145, "130°  145°"),
        ({}, 145, "—  145°"),
        ({"current_f": 116}, 0, "116°  OFF"),
    ],
)
def test_temperature_value(state, target, expected):
    assert plugin.temperature_value(state, target) == expected


def test_feedback_builds_connected_heating_payload(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "battery_percent": 91,
            "charging": True,
            "liquid_state_label": "Heating",
            "current_f": 130,
            "target_f": 145,
            "updated_ts": plugin.time.time(),
        },
    )

    plugin.feedback(ws, "dial-1", override=145)

    message = ws.messages[-1]
    assert message["event"] == "setFeedback"
    assert message["context"] == "dial-1"
    assert message["payload"]["title"] == "HEATING · 91% ⚡"
    assert message["payload"]["trend"]["value"] == "▲"


def test_feedback_hides_transient_unknown_liquid_state(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "battery_percent": 100,
            "charging": True,
            "liquid_state_label": "Unknown",
            "current_f": 76,
            "updated_ts": plugin.time.time(),
        },
    )

    plugin.feedback(ws, "dial-1", override=120)

    assert ws.messages[-1]["payload"]["title"] == "CONNECTED · 100% ⚡"


@pytest.mark.parametrize(
    ("current_f", "target_f", "expected_state"),
    [(119, 145, "HEATING"), (145, 120, "COOLING"), (145, 145, "PERFECT")],
)
def test_feedback_replaces_transitional_no_control_state(
    monkeypatch, current_f, target_f, expected_state
):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "battery_percent": 100,
            "liquid_state_label": "Warm (No Control)",
            "current_f": current_f,
            "updated_ts": plugin.time.time(),
        },
    )

    plugin.feedback(ws, "dial-1", override=target_f)

    assert ws.messages[-1]["payload"]["title"] == f"{expected_state} · 100%"


def test_feedback_builds_offline_payload(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin, "status", lambda: {"connected": False})

    plugin.feedback(ws, "dial-1", override=145)

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "OFFLINE"
    assert payload["value"]["value"] == "145°"


# Off is off: heating off shows the sleeping mug whether the mug is
# connected or not, so I never watch the drink cool degree by degree.
# Every text field is switched off underneath it.
@pytest.mark.parametrize(
    "state",
    [
        {},
        {"connected": False, "battery_percent": 90, "current_f": 130},
        {"connected": True, "updated_ts": 699.0, "battery_percent": 90},
        {"connected": True, "updated_ts": 1_000.0, "battery_percent": 80, "liquid_state_label": "Warm (No Control)", "current_f": 116},
    ],
)
def test_feedback_shows_sleeping_mug_whenever_heating_is_off(monkeypatch, state):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(plugin, "status", lambda: state)

    plugin.feedback(ws, "dial-1", override=0)

    payload = ws.messages[-1]["payload"]
    assert payload["mug"] == {"value": plugin.SLEEPING_MUG, "enabled": True}
    assert payload["title"] == ""
    assert all(payload[key]["enabled"] is False for key in ("value", "current", "trend", "target"))


# Offline isn't off: an offline mug that's still set to a temp keeps its
# number, because heating comes back on by itself when it reconnects.
def test_feedback_keeps_the_target_when_offline_but_heating_is_on(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin, "status", lambda: {"connected": False})

    plugin.feedback(ws, "dial-1", override=145)

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "OFFLINE"
    assert payload["value"]["enabled"] is True
    assert payload["mug"] == {"enabled": False}


# The picture sits on top of the text, so every text screen has to switch it
# off again or the mug would stay stuck over the temperatures.
def test_text_screens_switch_the_sleeping_mug_off():
    ws = FeedbackSocket()

    plugin.message_feedback(ws, "dial-1", "RECONNECTING…", "…")

    assert ws.messages[-1]["payload"]["mug"] == {"enabled": False}
    assert plugin.temperature_display({"current_f": 120}, 145)["mug"] == {"enabled": False}
    assert plugin.temperature_display({}, 0)["mug"] == {"enabled": False}


# The deck is pure black, so the picture's background has to be #000000 or it
# shows up as a gray box. It also has to fill the layout slot made for it.
def test_sleeping_mug_picture_fills_its_slot_on_pure_black():
    picture = (PLUGIN_ROOT / plugin.SLEEPING_MUG).read_text(encoding="utf-8")
    layout = json.loads((PLUGIN_ROOT / "layouts" / "temperature.json").read_text(encoding="utf-8"))
    slot = next(item for item in layout["items"] if item["key"] == "mug")

    assert '<rect width="200" height="100" fill="#000000"/>' in picture
    assert slot["type"] == "pixmap"
    assert slot["rect"] == [0, 0, 200, 100]
    assert slot["enabled"] is False


@pytest.mark.parametrize("bad_battery", ["unknown", True, float("nan"), float("inf"), []])
def test_feedback_ignores_malformed_battery_without_crashing(monkeypatch, bad_battery):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "battery_percent": bad_battery,
            "current_f": 130,
            "updated_ts": plugin.time.time(),
        },
    )

    plugin.feedback(ws, "dial-1", override=145)

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "CONNECTED"
    assert payload["current"]["value"] == "130°"


@pytest.mark.parametrize("bad_connected", ["true", 1, [], {}])
def test_feedback_requires_a_real_boolean_connection_flag(monkeypatch, bad_connected):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": bad_connected,
            "battery_percent": 90,
            "current_f": 130,
        },
    )

    plugin.feedback(ws, "dial-1", override=145)

    assert ws.messages[-1]["payload"]["title"] == "OFFLINE"


def test_feedback_treats_old_connected_status_as_offline(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "updated_ts": 699.0,
            "battery_percent": 90,
            "current_f": 130,
        },
    )

    plugin.feedback(ws, "dial-1", override=145)

    assert ws.messages[-1]["payload"]["title"] == "OFFLINE"


def test_message_feedback_hides_comparison_fields():
    ws = FeedbackSocket()

    plugin.message_feedback(ws, "dial-1", "RECONNECTING…", "…")

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "RECONNECTING…"
    assert payload["value"] == {"value": "…", "enabled": True}
    assert payload["current"]["enabled"] is False
    assert payload["trend"]["enabled"] is False


def test_send_temp_command_returns_true_after_success(monkeypatch):
    ws = FeedbackSocket()
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)

    assert plugin.send_temp_command(ws, "dial-1", 145) is True
    set_temp.assert_called_once_with(145)
    assert ws.messages == []


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("disk full")])
def test_send_temp_command_reports_filesystem_failure_without_crashing(monkeypatch, error):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin, "set_temp", Mock(side_effect=error))

    assert plugin.send_temp_command(ws, "dial-1", 145) is False
    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "COMMAND FAILED"
    assert payload["value"]["value"] == "TRY AGAIN"


@pytest.mark.parametrize(
    ("actual", "expected"),
    [(0, 0), (119, 120), (126, 130), (138, 140), (144, 145)],
)
def test_sync_selection_chooses_nearest_preset(monkeypatch, actual, expected):
    monkeypatch.setattr(plugin, "target_f", lambda state=None: actual)

    plugin.sync_selection()

    assert plugin.selected == expected


def decode_client_frame(frame):
    length_code = frame[1] & 0x7F
    position = 2
    if length_code == 126:
        length = struct.unpack("!H", frame[position : position + 2])[0]
        position += 2
    elif length_code == 127:
        length = struct.unpack("!Q", frame[position : position + 8])[0]
        position += 8
    else:
        length = length_code
    mask = frame[position : position + 4]
    position += 4
    encoded = frame[position : position + length]
    return bytes(byte ^ mask[index % 4] for index, byte in enumerate(encoded))


@pytest.mark.parametrize("text_size", [1, 200, 70_000])
def test_websocket_send_encodes_masked_json_frames(monkeypatch, text_size):
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket()
    monkeypatch.setattr(plugin.os, "urandom", lambda count: b"\x01" * count)
    message = {"text": "x" * text_size}

    websocket.send(message)

    frame = websocket.sock.sent[-1]
    assert frame[0] == 0x81
    assert frame[1] & 0x80
    assert json.loads(decode_client_frame(frame)) == message


def server_frame(opcode, payload=b""):
    return bytes([0x80 | opcode, len(payload)]) + payload


def test_websocket_receive_decodes_text_message():
    payload = json.dumps({"event": "dialRotate"}).encode()
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([server_frame(1, payload)])

    assert websocket.receive() == {"event": "dialRotate"}


@pytest.mark.parametrize("payload_size", [200, 70_000])
def test_websocket_receive_supports_extended_payload_lengths(payload_size):
    payload = json.dumps({"text": "x" * payload_size}).encode()
    if len(payload) < 65_536:
        header = bytes([0x81, 126]) + struct.pack("!H", len(payload))
    else:
        header = bytes([0x81, 127]) + struct.pack("!Q", len(payload))
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([header[:1], header[1:], payload[:7], payload[7:]])

    assert websocket.receive() == {"text": "x" * payload_size}


def test_websocket_receive_raises_when_socket_ends_without_a_frame():
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket()

    with pytest.raises(EOFError):
        websocket.receive()


def test_websocket_receive_raises_when_socket_ends_mid_frame():
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([b"\x81", b""])

    with pytest.raises(EOFError):
        websocket.receive()


def test_websocket_receive_replies_to_ping():
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([server_frame(9, b"hello")])
    websocket.send_raw = Mock()

    assert websocket.receive() is None
    websocket.send_raw.assert_called_once_with(10, b"hello")


def test_websocket_receive_raises_when_stream_deck_closes_connection():
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([server_frame(8)])

    with pytest.raises(EOFError):
        websocket.receive()


def test_websocket_send_raw_builds_masked_control_frame(monkeypatch):
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket()
    monkeypatch.setattr(plugin.os, "urandom", lambda count: b"\x02" * count)

    websocket.send_raw(10, b"hello")

    frame = websocket.sock.sent[-1]
    assert frame[0] == 0x8A
    assert decode_client_frame(frame) == b"hello"


def test_websocket_handshake_connects_and_sets_short_timeout(monkeypatch):
    key_bytes = b"k" * 16
    key = base64.b64encode(key_bytes).decode()
    accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    )
    sock = FakeSocket([b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"])
    create_connection = Mock(return_value=sock)
    monkeypatch.setattr(plugin.socket, "create_connection", create_connection)
    monkeypatch.setattr(plugin.os, "urandom", lambda count: key_bytes)

    websocket = plugin.WebSocket(4321)

    assert websocket.sock is sock
    create_connection.assert_called_once_with(("127.0.0.1", 4321), timeout=5)
    assert sock.timeout == 0.25
    assert b"GET / HTTP/1.1" in sock.sent[0]


def test_websocket_handshake_rejects_invalid_response(monkeypatch):
    sock = FakeSocket([b"HTTP/1.1 403 Forbidden\r\n\r\n"])
    monkeypatch.setattr(plugin.socket, "create_connection", lambda *args, **kwargs: sock)

    with pytest.raises(RuntimeError, match="handshake failed"):
        plugin.WebSocket(4321)


class ScriptedWebSocket(FeedbackSocket):
    def __init__(self, events, clock=None, press_times=()):
        super().__init__()
        self.events = iter(events)
        self.clock = clock
        self.press_times = iter(press_times)

    def receive(self):
        try:
            event = next(self.events)
        except StopIteration:
            raise EOFError
        if self.clock is not None and event.get("event") in ("dialDown", "dialUp"):
            self.clock.value = next(self.press_times, self.clock.value)
        return event


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class TimedWebSocket(FeedbackSocket):
    def __init__(self, wall_clock, steady_clock, timeline):
        super().__init__()
        self.wall_clock = wall_clock
        self.steady_clock = steady_clock
        self.timeline = iter(timeline)

    def receive(self):
        try:
            item = next(self.timeline)
        except StopIteration:
            raise EOFError
        if len(item) == 2:
            wall_time, result = item
            steady_time = wall_time
        else:
            wall_time, steady_time, result = item
        self.wall_clock.value = wall_time
        self.steady_clock.value = steady_time
        if isinstance(result, BaseException):
            raise result
        return result


def run_main_until_events_end(monkeypatch, events, monotonic_values=(), info="{}"):
    steady_clock = Clock(1.0)
    websocket = ScriptedWebSocket(events, steady_clock, monotonic_values)
    monkeypatch.setattr(plugin, "WebSocket", lambda port: websocket)
    arguments = {"-port": "1234", "-pluginUUID": "plugin-id", "-registerEvent": "registerPlugin", "-info": info}
    monkeypatch.setattr(plugin, "arg", arguments.__getitem__)
    monkeypatch.setattr(plugin.time, "time", lambda: 1.0)
    monkeypatch.setattr(plugin.time, "monotonic", steady_clock)
    with pytest.raises(EOFError):
        plugin.main()
    return websocket


def run_timed_main(monkeypatch, timeline, clock=None):
    wall_clock = clock or Clock()
    steady_clock = Clock()
    websocket = TimedWebSocket(wall_clock, steady_clock, timeline)
    monkeypatch.setattr(plugin, "WebSocket", lambda port: websocket)
    arguments = {"-port": "1234", "-pluginUUID": "plugin-id", "-registerEvent": "registerPlugin", "-info": "{}"}
    monkeypatch.setattr(plugin, "arg", arguments.__getitem__)
    monkeypatch.setattr(plugin.time, "time", wall_clock)
    monkeypatch.setattr(plugin.time, "monotonic", steady_clock)
    with pytest.raises(EOFError):
        plugin.main()
    return websocket


def test_target_preview_expires_then_checkmark_disappears_after_three_seconds(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    monkeypatch.setattr(plugin, "set_temp", Mock())
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (2.6, plugin.socket.timeout()),
            (5.7, plugin.socket.timeout()),
        ],
    )

    assert any(call.kwargs.get("target_only") is True for call in feedback.call_args_list)
    assert any(call.kwargs.get("show_check") is True for call in feedback.call_args_list)
    assert feedback.call_args.kwargs.get("show_check", False) is False


def test_checkmark_does_not_repeat_while_mug_stays_at_target(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (2.1, plugin.socket.timeout()),
            (4.2, plugin.socket.timeout()),
        ],
    )

    assert not any(call.kwargs.get("show_check") is True for call in feedback.call_args_list)


def test_checkmark_reappears_after_leaving_and_reentering_target_range(monkeypatch):
    clock = Clock()
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145 if clock.value in (0.0, 4.2) else 140})
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (2.1, plugin.socket.timeout()),
            (4.2, plugin.socket.timeout()),
        ],
        clock=clock,
    )

    assert any(call.kwargs.get("show_check") is True for call in feedback.call_args_list)


def test_target_preview_expiry_updates_every_visible_dial_context(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 130})
    monkeypatch.setattr(plugin, "set_temp", Mock())
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (0.1, {"event": "willAppear", "context": "dial-2"}),
            (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (2.6, plugin.socket.timeout()),
        ],
    )

    refreshed_contexts = {
        call.args[1]
        for call in feedback.call_args_list
        if call.kwargs.get("show_check") is False
    }
    assert refreshed_contexts == {"dial-1", "dial-2"}


def test_reconnect_rejects_connected_status_older_than_request(monkeypatch):
    clock = Clock()
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {"connected": True, "updated_ts": 0.5, "current_f": 145, "target_f": 145},
    )
    monkeypatch.setattr(plugin, "feedback", Mock())
    monkeypatch.setattr(plugin, "request_reconnect", Mock())

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, 10.0, {"event": "dialDown", "context": "dial-1"}),
            (1.0, 12.0, {"event": "dialUp", "context": "dial-1"}),
            (3.1, 14.1, plugin.socket.timeout()),
        ],
        clock=clock,
    )

    reconnect_titles = [
        message["payload"]["title"]
        for message in websocket.messages
        if message.get("event") == "setFeedback"
    ]
    assert reconnect_titles.count("RECONNECTING…") >= 2
    assert "CONNECTED" not in reconnect_titles


# A junk "connected" value in status.json must not end the reconnect wait.
# Only a real True counts, the same rule the normal display uses.
@pytest.mark.parametrize("bad_connected", ["false", "yes", 1])
def test_reconnect_ignores_fresh_status_without_a_real_connection_flag(monkeypatch, bad_connected):
    clock = Clock()
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": bad_connected,
            "updated_ts": clock.value,
            "current_f": 145,
            "target_f": 145,
        },
    )
    monkeypatch.setattr(plugin, "feedback", Mock())
    monkeypatch.setattr(plugin, "request_reconnect", Mock())

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, 10.0, {"event": "dialDown", "context": "dial-1"}),
            (1.0, 12.0, {"event": "dialUp", "context": "dial-1"}),
            (3.1, 14.1, plugin.socket.timeout()),
        ],
        clock=clock,
    )

    titles = [
        message["payload"]["title"]
        for message in websocket.messages
        if message.get("event") == "setFeedback"
    ]
    assert "CONNECTED" not in titles
    assert titles.count("RECONNECTING…") >= 2


def test_reconnect_accepts_fresh_connected_status(monkeypatch):
    clock = Clock()
    plugin.selected = 145
    sync_selection = Mock()
    monkeypatch.setattr(plugin, "sync_selection", sync_selection)
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": clock.value >= 3.1,
            "updated_ts": clock.value,
            "current_f": 145,
            "target_f": 145,
        },
    )
    monkeypatch.setattr(plugin, "feedback", Mock())
    monkeypatch.setattr(plugin, "request_reconnect", Mock())

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, 10.0, {"event": "dialDown", "context": "dial-1"}),
            (1.0, 12.0, {"event": "dialUp", "context": "dial-1"}),
            (3.1, 14.1, plugin.socket.timeout()),
        ],
        clock=clock,
    )

    assert any(
        message.get("payload", {}).get("title") == "CONNECTED"
        for message in websocket.messages
    )
    assert sync_selection.call_count >= 2


def test_reconnect_message_stops_after_thirty_second_timeout(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {"connected": False, "updated_ts": 0, "current_f": 145, "target_f": 145},
    )
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)
    monkeypatch.setattr(plugin, "request_reconnect", Mock())

    run_timed_main(
        monkeypatch,
        [
            (0.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, 10.0, {"event": "dialDown", "context": "dial-1"}),
            (1.0, 12.0, {"event": "dialUp", "context": "dial-1"}),
            (31.1, 42.1, plugin.socket.timeout()),
        ],
    )

    assert feedback.call_args.args[1] == "dial-1"


def test_wall_clock_jump_forward_does_not_end_target_preview_early(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 130})
    monkeypatch.setattr(plugin, "set_temp", Mock())
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (1_000.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1_001.0, 1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (10_000.0, 1.2, plugin.socket.timeout()),
            (10_001.0, 2.6, plugin.socket.timeout()),
        ],
    )

    target_only_calls = [call for call in feedback.call_args_list if call.kwargs.get("target_only") is True]
    comparison_calls = [call for call in feedback.call_args_list if call.kwargs.get("show_check") is False]
    assert len(target_only_calls) == 1
    assert len(comparison_calls) == 1


def test_wall_clock_jump_backward_does_not_extend_checkmark(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    monkeypatch.setattr(plugin, "set_temp", Mock())
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (1_000.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1_001.0, 1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (900.0, 2.6, plugin.socket.timeout()),
            (800.0, 5.7, plugin.socket.timeout()),
        ],
    )

    assert any(call.kwargs.get("show_check") is True for call in feedback.call_args_list)
    assert feedback.call_args.kwargs.get("show_check", False) is False


def test_reconnect_timeout_uses_steady_clock_when_wall_clock_moves_backward(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {"connected": False, "updated_ts": 0, "current_f": 145, "target_f": 145},
    )
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)
    monkeypatch.setattr(plugin, "request_reconnect", Mock())

    run_timed_main(
        monkeypatch,
        [
            (1_000.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1_001.0, 10.0, {"event": "dialDown", "context": "dial-1"}),
            (1_001.0, 12.0, {"event": "dialUp", "context": "dial-1"}),
            (900.0, 42.1, plugin.socket.timeout()),
        ],
    )

    assert feedback.call_args.args[1] == "dial-1"


def test_large_sleep_wake_jump_expires_preview_and_check_without_crashing(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    monkeypatch.setattr(plugin, "set_temp", Mock())
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_timed_main(
        monkeypatch,
        [
            (1_000.0, 0.0, {"event": "willAppear", "context": "dial-1"}),
            (1_001.0, 1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (4_601.0, 3_601.0, plugin.socket.timeout()),
            (4_605.0, 3_605.0, plugin.socket.timeout()),
        ],
    )

    assert any(call.kwargs.get("show_check") is True for call in feedback.call_args_list)
    assert feedback.call_args.kwargs.get("show_check", False) is False


def test_main_rotates_clockwise_from_off_to_120(monkeypatch):
    plugin.selected = 0
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 100})
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}},
        ],
    )

    assert plugin.selected == 120
    set_temp.assert_called_once_with(120)


def test_main_rotates_counterclockwise_from_off_to_145(monkeypatch):
    plugin.selected = 0
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 100})
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": -1}},
        ],
    )

    assert plugin.selected == 145
    set_temp.assert_called_once_with(145)


@pytest.mark.parametrize(("starting", "ticks", "expected"), [(130, 999, 145), (140, -999, 120)])
def test_main_clamps_extreme_dial_rotation_to_valid_presets(monkeypatch, starting, ticks, expected):
    plugin.selected = starting
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 100})
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": ticks}},
        ],
    )

    assert plugin.selected == expected
    set_temp.assert_called_once_with(expected)


def test_main_ignores_dial_event_with_zero_ticks(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 100})
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 0}},
        ],
    )

    assert plugin.selected == 140
    set_temp.assert_not_called()


def test_main_short_press_turns_heating_off(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-1"},
        ],
        monotonic_values=[10.0, 10.5],
    )

    assert plugin.selected == 0
    set_temp.assert_called_once_with(0)


def test_main_press_just_below_hold_threshold_is_still_a_short_press(monkeypatch):
    plugin.selected = 0
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 100})
    set_temp = Mock()
    reconnect = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "request_reconnect", reconnect)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-1"},
        ],
        monotonic_values=[10.0, 10.0 + plugin.HOLD_SECONDS - 0.001],
    )

    set_temp.assert_called_once_with(145)
    reconnect.assert_not_called()


def test_main_press_at_hold_threshold_reconnects(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    reconnect = Mock()
    set_temp = Mock()
    monkeypatch.setattr(plugin, "request_reconnect", reconnect)
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-1"},
        ],
        monotonic_values=[10.0, 10.0 + plugin.HOLD_SECONDS],
    )

    reconnect.assert_called_once_with()
    set_temp.assert_not_called()


def test_main_tracks_press_duration_separately_for_each_dial(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    reconnect = Mock()
    set_temp = Mock()
    monkeypatch.setattr(plugin, "request_reconnect", reconnect)
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "willAppear", "context": "dial-2"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-2"},
            {"event": "dialUp", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-2"},
        ],
        monotonic_values=[10.0, 10.2, 10.5, 12.0],
    )

    set_temp.assert_called_once_with(0)
    reconnect.assert_called_once_with()


def test_main_ignores_dial_up_after_action_disappears(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    set_temp = Mock()
    reconnect = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    monkeypatch.setattr(plugin, "request_reconnect", reconnect)
    monkeypatch.setattr(plugin, "feedback", Mock())

    run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "willDisappear", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-1"},
        ],
        monotonic_values=[10.0],
    )

    set_temp.assert_not_called()
    reconnect.assert_not_called()


def test_main_long_press_requests_reconnect_without_changing_temperature(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    monkeypatch.setattr(plugin, "feedback", Mock())
    reconnect = Mock()
    set_temp = Mock()
    monkeypatch.setattr(plugin, "request_reconnect", reconnect)
    monkeypatch.setattr(plugin, "set_temp", set_temp)

    websocket = run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-1"},
        ],
        monotonic_values=[10.0, 12.0],
    )

    reconnect.assert_called_once_with()
    set_temp.assert_not_called()
    assert any(message.get("payload", {}).get("title") == "RECONNECTING…" for message in websocket.messages)


def test_main_keeps_previous_selection_when_command_file_write_fails(monkeypatch):
    plugin.selected = 140
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 130})
    monkeypatch.setattr(plugin, "set_temp", Mock(side_effect=PermissionError("denied")))
    monkeypatch.setattr(plugin, "feedback", Mock())

    websocket = run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}},
        ],
    )

    assert plugin.selected == 140
    assert any(
        message.get("payload", {}).get("title") == "COMMAND FAILED"
        for message in websocket.messages
    )


@pytest.mark.parametrize(
    ("error", "expected_title"),
    [
        (FileNotFoundError("missing"), "APP NOT FOUND"),
        (OSError("launch failed"), "RECONNECT FAILED"),
    ],
)
def test_main_reports_reconnect_launch_failure_without_crashing(monkeypatch, error, expected_title):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: {"current_f": 145})
    monkeypatch.setattr(plugin, "feedback", Mock())
    monkeypatch.setattr(plugin, "request_reconnect", Mock(side_effect=error))

    websocket = run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "context": "dial-1"},
            {"event": "dialDown", "context": "dial-1"},
            {"event": "dialUp", "context": "dial-1"},
        ],
        monotonic_values=[10.0, 12.0],
    )

    assert any(
        message.get("payload", {}).get("title") == expected_title
        for message in websocket.messages
    )


def setfeedback_values(websocket):
    # The big number on the dial in every setFeedback the plugin sent, in order.
    return [
        message["payload"]["value"]["value"]
        for message in websocket.messages
        if message.get("event") == "setFeedback" and "value" in message["payload"]
    ]


def test_nothing_is_drawn_until_a_dial_has_appeared(monkeypatch):
    # Protects the "no dial on screen" case: Stream Deck only gets a
    # setFeedback for a context it told us about with willAppear. Without one,
    # ticks pass and the plugin sends nothing.
    monkeypatch.setattr(plugin, "status", lambda: {"connected": True, "target_f": 130, "battery_percent": 80})

    websocket = run_timed_main(
        monkeypatch,
        [(0.0, plugin.socket.timeout()), (2.5, plugin.socket.timeout()), (5.0, plugin.socket.timeout())],
    )

    # Only the registration message goes out.
    assert [message["event"] for message in websocket.messages] == ["registerPlugin"]


def test_frame_cut_by_a_read_timeout_loses_its_first_bytes():
    # Pins what happens today: the 0.25 s timeout can hit between the two
    # halves of one frame. The bytes already read are dropped, so the next
    # receive() starts in the middle of the frame. Loopback almost never
    # splits a frame, so I left it. See the xfail test below for what I want.
    frame = server_frame(1, b'{"event":"willAppear"}')
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([frame[:2], plugin.socket.timeout(), frame[2:]])

    with pytest.raises(plugin.socket.timeout):
        websocket.receive()
    # The 2-byte header is gone: the leftover payload now reads as a header,
    # asks for more bytes than are left, and the socket looks closed.
    with pytest.raises(EOFError):
        websocket.receive()


@pytest.mark.xfail(strict=True, raises=EOFError, reason="a frame split by a read timeout is not reassembled yet")
def test_frame_cut_by_a_read_timeout_is_reassembled_on_the_next_read():
    # What I want: a timeout mid-frame keeps the bytes already read, and the
    # next receive() finishes the same frame.
    frame = server_frame(1, b'{"event":"willAppear"}')
    websocket = plugin.WebSocket.__new__(plugin.WebSocket)
    websocket.sock = FakeSocket([frame[:2], plugin.socket.timeout(), frame[2:]])

    with pytest.raises(plugin.socket.timeout):
        websocket.receive()

    assert websocket.receive() == {"event": "willAppear"}


def not_applied_titles(websocket):
    # Titles of every short message (title plus one line) the plugin drew.
    return [
        message["payload"]["title"]
        for message in websocket.messages
        if message.get("event") == "setFeedback" and message["payload"].get("title") == "NOT APPLIED"
    ]


def app_that_applies_after(monkeypatch, seconds, target_f=130):
    # Fake status.json: says 120 until `seconds` of steady-clock time have
    # passed, then says target_f. seconds=None means the app never applies it.
    def status():
        applied = seconds is not None and plugin.time.monotonic() >= seconds
        return {"target_f": target_f if applied else 120}

    monkeypatch.setattr(plugin, "status", status)
    monkeypatch.setattr(plugin, "set_temp", Mock())


ROTATE_TO_130 = [
    (0.0, {"event": "willAppear", "context": "dial-1"}),
    (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
    (2.6, plugin.socket.timeout()),
    (4.7, plugin.socket.timeout()),
    (6.8, plugin.socket.timeout()),
    (8.9, plugin.socket.timeout()),
]


def test_rotation_the_app_never_applies_says_not_applied_before_it_reverts(monkeypatch):
    # I turn the dial to 130 and the app ignores it, so status.json keeps
    # saying 120. Three seconds after the turn the dial says NOT APPLIED
    # instead of quietly going back to 120, holds that for a few seconds,
    # then shows the real 120.
    app_that_applies_after(monkeypatch, None)

    websocket = run_timed_main(monkeypatch, ROTATE_TO_130)

    assert not_applied_titles(websocket) == ["NOT APPLIED", "NOT APPLIED"]
    assert setfeedback_values(websocket) == ["120°", "130°", "130°", "TRY AGAIN", "TRY AGAIN", "120°"]


def test_rotation_the_app_applies_in_time_never_says_not_applied(monkeypatch):
    # The normal case. status.json agrees with the dial by the time the three
    # seconds are up, so there is nothing to warn about.
    app_that_applies_after(monkeypatch, 2.0)

    websocket = run_timed_main(monkeypatch, ROTATE_TO_130)

    assert not_applied_titles(websocket) == []
    assert setfeedback_values(websocket)[-1] == "130°"


def test_a_press_the_app_never_applies_says_not_applied(monkeypatch):
    # Same check for the press that switches heating off: status.json keeps
    # saying 120 (heating on) after I asked for OFF.
    app_that_applies_after(monkeypatch, None, target_f=0)
    plugin.selected = 120

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, {"event": "dialDown", "context": "dial-1"}),
            (1.1, {"event": "dialUp", "context": "dial-1"}),
            (4.5, plugin.socket.timeout()),
        ],
    )

    assert not_applied_titles(websocket) == ["NOT APPLIED"]


def test_turning_the_dial_again_clears_the_not_applied_message(monkeypatch):
    # A new turn is a new command, so the old warning goes and the dial shows
    # the new target straight away (from the 120 the mug really has).
    app_that_applies_after(monkeypatch, None)

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (2.6, plugin.socket.timeout()),
            (4.7, plugin.socket.timeout()),
            (5.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (6.5, plugin.socket.timeout()),
            (7.0, plugin.socket.timeout()),
        ],
    )

    assert not_applied_titles(websocket) == ["NOT APPLIED"]
    # The dial had gone back to the real 120, so the new turn starts there.
    assert setfeedback_values(websocket)[-2:] == ["130°", "130°"]


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    # Keeps the tests away from the real ~/Library/Logs/EmberMug, and takes
    # the file handler back off the logger afterwards.
    path = tmp_path / "logs" / "plugin.log"
    monkeypatch.setattr(plugin, "LOG_FILE", path)
    yield path
    for handler in list(plugin.log.handlers):
        plugin.log.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize("hangup", [EOFError, BrokenPipeError, ConnectionResetError])
def test_run_exits_quietly_when_stream_deck_closes_the_socket(monkeypatch, log_file, hangup):
    # Protects the Stream Deck quit case: a closed socket (politely, or
    # abruptly with a reset or broken pipe) is a normal goodbye, so exit code
    # 0 and one plain line in the log, no traceback.
    monkeypatch.setattr(plugin, "main", Mock(side_effect=hangup))

    with pytest.raises(SystemExit) as exit_info:
        plugin.run()

    assert exit_info.value.code == 0
    text = log_file.read_text(encoding="utf-8")
    assert "Stream Deck closed the connection" in text
    assert "Traceback" not in text


def test_run_logs_the_traceback_and_exits_1_on_a_real_crash(monkeypatch, log_file):
    # Protects the real-crash case: it still exits 1 so Stream Deck notices,
    # and the traceback lands in the log file instead of nowhere.
    monkeypatch.setattr(plugin, "main", Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(SystemExit) as exit_info:
        plugin.run()

    assert exit_info.value.code == 1
    text = log_file.read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "RuntimeError: boom" in text


def test_run_still_starts_when_the_log_folder_cannot_be_made(monkeypatch, tmp_path):
    # Protects the plugin from its own log: if the folder is blocked (here a
    # plain file sits where the folder should be), the plugin runs anyway.
    blocker = tmp_path / "logs"
    blocker.write_text("not a folder")
    monkeypatch.setattr(plugin, "LOG_FILE", blocker / "plugin.log")
    main = Mock(side_effect=EOFError)
    monkeypatch.setattr(plugin, "main", main)

    with pytest.raises(SystemExit) as exit_info:
        plugin.run()

    assert exit_info.value.code == 0
    main.assert_called_once()


def test_failed_command_write_is_logged_with_its_traceback(monkeypatch, log_file):
    # Protects the "COMMAND FAILED" dial message: the reason (disk full,
    # permissions) now has somewhere to go.
    plugin.setup_logging()
    monkeypatch.setattr(plugin, "set_temp", Mock(side_effect=OSError("disk full")))

    plugin.send_temp_command(FeedbackSocket(), "dial-1", 130)

    text = log_file.read_text(encoding="utf-8")
    assert "disk full" in text
    assert "Traceback" in text


def test_timings_have_names():
    # The loop reads these two names; the numbers are the ones it always used.
    assert plugin.STATUS_REFRESH_SECONDS == 2
    assert plugin.SELECTION_SETTLE_SECONDS == 3


def count_status_reads_by_time(monkeypatch, timeline, state):
    # Runs main() on a timeline and returns the steady-clock time of every
    # status.json read, so a test can count the reads inside one tick.
    reads = []

    def counting_status():
        reads.append(plugin.time.monotonic())
        return state

    monkeypatch.setattr(plugin, "status", counting_status)
    monkeypatch.setattr(plugin, "set_temp", Mock())
    run_timed_main(monkeypatch, timeline)
    return reads


STATE = {"connected": True, "battery_percent": 80, "current_f": 100, "target_f": 130, "updated_ts": 0}


def test_idle_ticks_do_not_read_status_when_no_dial_is_on_screen(monkeypatch):
    # Protects the idle case: with no dial on screen there is nothing to draw,
    # so reading and parsing status.json every 2 seconds is wasted work.
    reads = count_status_reads_by_time(
        monkeypatch,
        [(2.5, plugin.socket.timeout()), (5.0, plugin.socket.timeout()), (7.5, plugin.socket.timeout())],
        STATE,
    )

    assert reads == []


def test_a_dial_that_comes_back_is_drawn_again_straight_away(monkeypatch):
    # Protects the other half of the idle skip: while the dial is away nothing
    # is read, and willAppear draws it the moment it returns.
    reads = count_status_reads_by_time(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, {"event": "willDisappear", "context": "dial-1"}),
            (2.5, plugin.socket.timeout()),
            (5.0, plugin.socket.timeout()),
            (6.0, {"event": "willAppear", "context": "dial-1"}),
        ],
        STATE,
    )

    assert [time for time in reads if 1.0 < time < 6.0] == []
    assert any(time == 6.0 for time in reads)


@pytest.mark.parametrize("dials", [1, 2])
def test_a_refresh_tick_reads_status_once_however_many_dials_are_showing(monkeypatch, dials):
    # Protects the cost of a tick: one read, shared by the checkmark logic,
    # the selection sync and every dial. t=5.0 is past the 3 s settle time,
    # so the sync path runs too.
    appear = [(0.0, {"event": "willAppear", "context": f"dial-{n}"}) for n in range(dials)]
    reads = count_status_reads_by_time(monkeypatch, appear + [(5.0, plugin.socket.timeout())], STATE)

    assert reads.count(5.0) == 1


def test_target_preview_expiry_reads_status_once(monkeypatch):
    # Same protection for the other timer: when the target preview ends,
    # both dials redraw from one read.
    timeline = [
        (0.0, {"event": "willAppear", "context": "dial-1"}),
        (0.0, {"event": "willAppear", "context": "dial-2"}),
        (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
        (2.6, plugin.socket.timeout()),
    ]
    reads = count_status_reads_by_time(monkeypatch, timeline, STATE)

    assert reads.count(2.6) == 1


def test_a_dial_that_comes_back_does_not_get_a_verdict_on_an_old_command(monkeypatch):
    # I turn the dial, switch pages before the check, and come back later.
    # The command is old news by then, so no NOT APPLIED on return.
    app_that_applies_after(monkeypatch, None)

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (1.5, {"event": "willDisappear", "context": "dial-1"}),
            (3.0, plugin.socket.timeout()),
            (5.5, plugin.socket.timeout()),
            (6.0, {"event": "willAppear", "context": "dial-1"}),
            (9.0, plugin.socket.timeout()),
        ],
    )

    assert not_applied_titles(websocket) == []


def test_every_screen_builds_the_same_five_fields_exactly():
    # Protects the shape Stream Deck receives. The "all fields off" block is
    # written out in more than one place, and this pins each screen's exact
    # payload so folding the copies together cannot change a key or a value.
    ws = FeedbackSocket()
    off = {"value": "", "enabled": False}

    plugin.message_feedback(ws, "dial-1", "RECONNECTING…", "…")
    assert ws.messages[-1]["payload"] == {
        "title": "RECONNECTING…",
        "value": {"value": "…", "enabled": True},
        "current": off,
        "trend": off,
        "target": off,
        "mug": {"enabled": False},
    }
    assert plugin.temperature_display({}, 0) == {
        "value": {"value": "OFF", "enabled": True},
        "current": off,
        "trend": off,
        "target": off,
        "mug": {"enabled": False},
    }
    assert plugin.sleeping_mug_display() == {
        "title": "",
        "value": off,
        "current": off,
        "trend": off,
        "target": off,
        "mug": {"value": plugin.SLEEPING_MUG, "enabled": True},
    }


def test_short_messages_reach_every_dial_on_screen(monkeypatch):
    # Protects the loops that draw one message on all dials: with two dials
    # showing, NOT APPLIED has to land on both, not just the one I turned.
    app_that_applies_after(monkeypatch, None)

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (0.0, {"event": "willAppear", "context": "dial-2"}),
            (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (2.6, plugin.socket.timeout()),
            (4.7, plugin.socket.timeout()),
        ],
    )

    warned = [m["context"] for m in websocket.messages if m.get("payload", {}).get("title") == "NOT APPLIED"]
    assert sorted(warned) == ["dial-1", "dial-2"]


# ---- Keys: On/Off, Set Temp and the Ember Page key ----

def mug(target, current=130, connected=True):
    return {"connected": connected, "updated_ts": plugin.time.time(), "target_f": target, "current_f": current}


def key_down(context, action, settings=None, device="plus-1"):
    return [
        {"event": "willAppear", "action": action, "context": context, "device": device, "payload": {"settings": settings or {}}},
        {"event": "keyDown", "action": action, "context": context, "device": device, "payload": {"settings": settings or {}}},
    ]


def pictures(websocket, context):
    images = [m["payload"]["image"] for m in websocket.messages if m.get("event") == "setImage" and m["context"] == context]
    return [base64.b64decode(image.split(",", 1)[1]).decode() for image in images]


# The deck is pure black, so every key picture has to be #000000 behind
# everything, in every state, or it shows up as a gray square.
@pytest.mark.parametrize("action", plugin.KEY_ACTIONS)
@pytest.mark.parametrize("state", [mug(145), mug(0), mug(145, connected=False), {}])
def test_every_key_picture_sits_on_pure_black(action, state):
    svg = plugin.key_picture(action, {}, state, state.get("target_f"))

    assert '<rect width="144" height="144" fill="#000000"/>' in svg


# The settings page sends the temp as text, or nothing on a brand new key.
# Anything that isn't one of the dial's four has to fall back to 145, so a
# key can never send the mug a temp the dial can't show.
@pytest.mark.parametrize(
    ("settings", "expected"),
    [({"temp": "120"}, 120), ({"temp": "140"}, 140), ({}, 145), ({"temp": "135"}, 145), ({"temp": "warm"}, 145), (None, 145)],
)
def test_set_temp_key_reads_its_temp_with_a_fallback(settings, expected):
    assert plugin.key_temp(settings) == expected


# Pink on a Set Temp key means "the mug is set to me". Only one of the
# four on the XL page should be pink, and none while heating is off.
def test_set_temp_key_is_pink_only_when_it_is_the_target():
    assert plugin.PINK in plugin.key_picture(plugin.TEMP, {"temp": "140"}, mug(140), 140)
    assert plugin.PINK not in plugin.key_picture(plugin.TEMP, {"temp": "130"}, mug(140), 140)
    assert plugin.PINK not in plugin.key_picture(plugin.TEMP, {"temp": "145"}, mug(0), 0)
    offline = plugin.key_picture(plugin.TEMP, {"temp": "140"}, mug(140, connected=False), 140)
    assert "Offline" in offline and plugin.PINK not in offline


# On/Off shows the drink's temp in pink while heating. Off is off: the
# sleeping mug, connected or not, and never the drink cooling down.
def test_on_off_key_shows_the_temp_while_heating_and_the_mug_when_off():
    on = plugin.key_picture(plugin.POWER, {}, mug(145, current=145), 145)

    assert ">On<" in on and ">145°<" in on and plugin.PINK in on
    for state in (mug(0, current=118), mug(0, current=118, connected=False), {}):
        for action in (plugin.POWER, plugin.OPEN):
            off = plugin.key_picture(action, {}, state, 0)
            assert off == plugin.SLEEPING_KEY
            assert "118" not in off


# The key's sleeping mug is the dial's drawing sized for a key, on pure black.
def test_sleeping_mug_key_is_the_dials_mug_on_pure_black():
    dial = (PLUGIN_ROOT / plugin.SLEEPING_MUG).read_text(encoding="utf-8")

    assert '<rect width="144" height="144" fill="#000000"/>' in plugin.SLEEPING_KEY
    for line in dial.splitlines():
        if line.strip().startswith("<path"):
            assert line in plugin.SLEEPING_KEY


# A press on a Set Temp key sends the mug that key's temp, and the dial
# on screen follows, since the dial and the keys share one choice.
def test_set_temp_key_press_sets_the_mug_and_the_dial_follows(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: mug(145))
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    feedback = Mock()
    monkeypatch.setattr(plugin, "feedback", feedback)

    run_main_until_events_end(
        monkeypatch,
        [{"event": "willAppear", "context": "dial-1"}, *key_down("key-1", plugin.TEMP, {"temp": "130"})],
    )

    set_temp.assert_called_once_with(130)
    assert plugin.selected == 130
    assert feedback.call_args.args[:3] == (feedback.call_args.args[0], "dial-1", 130)


# On/Off works like a push of the dial: off from any temp, back on at 145.
@pytest.mark.parametrize(("starting", "expected"), [(140, 0), (0, 145)])
def test_on_off_key_toggles_like_the_dial_push(monkeypatch, starting, expected):
    plugin.selected = starting
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: mug(starting))
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)

    websocket = run_main_until_events_end(monkeypatch, key_down("key-1", plugin.POWER))

    set_temp.assert_called_once_with(expected)
    last = pictures(websocket, "key-1")[-1]
    assert (">▲<" in last) if expected else (last == plugin.SLEEPING_KEY)


# While the drink is getting to its temp, the label on On/Off and Ember
# Page is the dial's arrow in the dial's colors: red up when warming, blue
# down when cooling. Within a degree of the target the word comes back.
@pytest.mark.parametrize(
    ("action", "current", "label", "color"),
    [
        (plugin.POWER, 130, "▲", plugin.WARMING),
        (plugin.POWER, 150, "▼", plugin.COOLING),
        (plugin.POWER, 139.6, "On", plugin.DIM),
        (plugin.OPEN, 130, "▲", plugin.WARMING),
        (plugin.OPEN, 150, "▼", plugin.COOLING),
        (plugin.OPEN, 140, "Ember", plugin.DIM),
    ],
)
def test_label_is_the_arrow_until_the_drink_gets_there(action, current, label, color):
    svg = plugin.key_picture(action, {}, mug(140, current=current), 140)

    assert f'fill="{color}" font-size="20" font-weight="600" text-anchor="middle">{label}<' in svg
    assert plugin.PINK in svg


# A Set Temp press always gets an answer: "Sent" straight away, and the
# plain pink key once status.json shows the mug took the temp.
def test_set_temp_key_says_sent_until_the_mug_takes_it(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    sent = []
    monkeypatch.setattr(plugin, "set_temp", sent.append)
    # The Ember app reads command.json a moment after it's written, so the
    # mug only reports the new temp from the one second mark on.
    monkeypatch.setattr(plugin, "status", lambda: mug(sent[-1] if sent and plugin.time.monotonic() >= 1.0 else 145))

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "action": plugin.TEMP, "context": "key-1", "payload": {"settings": {"temp": "130"}}}),
            (0.5, {"event": "keyDown", "action": plugin.TEMP, "context": "key-1", "payload": {"settings": {"temp": "130"}}}),
            (1.0, plugin.socket.timeout()),
            (2.6, plugin.socket.timeout()),
        ],
    )

    labels = [re.search(r'font-size="20"[^>]*>([^<]*)<', svg).group(1) for svg in pictures(websocket, "key-1")]
    assert labels == ["Temp", "Sent", "Temp"]
    assert plugin.PINK in pictures(websocket, "key-1")[-1]


# If the mug never takes it (offline, or the app dropped command.json),
# "Sent" turns into "Not applied" when the dial gives up, then clears.
def test_set_temp_key_says_not_applied_when_the_mug_never_takes_it(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "status", lambda: mug(145))
    monkeypatch.setattr(plugin, "set_temp", Mock())

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "action": plugin.TEMP, "context": "key-1", "payload": {"settings": {"temp": "130"}}}),
            (1.0, {"event": "keyDown", "action": plugin.TEMP, "context": "key-1", "payload": {"settings": {"temp": "130"}}}),
            (4.5, plugin.socket.timeout()),
            (8.0, plugin.socket.timeout()),
        ],
    )

    labels = [re.search(r'font-size="20"[^>]*>([^<]*)<', svg).group(1) for svg in pictures(websocket, "key-1")]
    assert labels == ["Temp", "Sent", "Not applied", "Temp"]


# If command.json can't be written, a key gets Stream Deck's alert
# triangle and the plugin keeps running, instead of a key that looks
# like it worked.
def test_key_press_that_cannot_write_the_command_shows_an_alert(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: mug(145))
    monkeypatch.setattr(plugin, "set_temp", Mock(side_effect=PermissionError("blocked")))

    websocket = run_main_until_events_end(monkeypatch, key_down("key-1", plugin.TEMP, {"temp": "120"}))

    assert {"event": "showAlert", "context": "key-1"} in websocket.messages
    assert plugin.selected == 145


# The Ember Page key opens the page made for the deck it's on: the XL
# page on my XL, the + page on my +. Any other deck has no page that
# fits, so it gets the alert triangle.
@pytest.mark.parametrize(("deck_type", "page"), [(2, "profiles/ember-xl"), (7, "profiles/ember-plus"), (0, None)])
def test_ember_page_key_opens_the_page_for_its_deck(monkeypatch, deck_type, page):
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: mug(145))
    set_temp = Mock()
    monkeypatch.setattr(plugin, "set_temp", set_temp)
    info = json.dumps({"devices": [{"id": "deck-1", "type": deck_type}]})

    websocket = run_main_until_events_end(monkeypatch, key_down("key-1", plugin.OPEN, device="deck-1"), info=info)

    switches = [m for m in websocket.messages if m.get("event") == "switchToProfile"]
    if page:
        assert switches == [{"event": "switchToProfile", "context": "plugin-id", "device": "deck-1", "payload": {"profile": page}}]
    else:
        assert switches == [] and {"event": "showAlert", "context": "key-1"} in websocket.messages
    set_temp.assert_not_called()


# An XL plugged in after Stream Deck started still counts as an XL.
def test_ember_page_key_knows_a_deck_plugged_in_later(monkeypatch):
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: mug(145))

    websocket = run_main_until_events_end(
        monkeypatch,
        [{"event": "deviceDidConnect", "device": "xl-1", "deviceInfo": {"type": 2}}, *key_down("key-1", plugin.OPEN, device="xl-1")],
    )

    assert any(m.get("event") == "switchToProfile" for m in websocket.messages)


# Picking a new temp on the settings page shows on the key straight away,
# not on the next refresh.
def test_new_temp_from_the_settings_page_redraws_the_key(monkeypatch):
    plugin.selected = 145
    monkeypatch.setattr(plugin, "sync_selection", Mock())
    monkeypatch.setattr(plugin, "status", lambda: mug(145))

    websocket = run_main_until_events_end(
        monkeypatch,
        [
            {"event": "willAppear", "action": plugin.TEMP, "context": "key-1", "payload": {"settings": {"temp": "145"}}},
            {"event": "didReceiveSettings", "action": plugin.TEMP, "context": "key-1", "payload": {"settings": {"temp": "120"}}},
        ],
    )

    drawn = pictures(websocket, "key-1")
    assert ">145°<" in drawn[0] and ">120°<" in drawn[-1]


# Keys redraw on the two second refresh, but the same picture is never
# sent twice: the deck slows down when it's flooded with pictures.
def test_keys_only_get_a_picture_when_it_changes(monkeypatch):
    plugin.selected = 145
    readings = iter([mug(145, current=130)] * 3 + [mug(145, current=131)] * 10)
    monkeypatch.setattr(plugin, "status", lambda: next(readings))
    monkeypatch.setattr(plugin, "sync_selection", Mock())

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "action": plugin.POWER, "context": "key-1", "payload": {}}),
            (2.5, plugin.socket.timeout()),
            (5.0, plugin.socket.timeout()),
            (7.5, plugin.socket.timeout()),
        ],
    )

    drawn = pictures(websocket, "key-1")
    assert len(drawn) == 2
    assert ">130°<" in drawn[0] and ">131°<" in drawn[1]


# ---- The bundled Ember pages ----

SPEC_PROFILES = importlib.util.spec_from_file_location("ember_profiles", Path(__file__).parents[1] / "scripts" / "profiles.py")
profiles = importlib.util.module_from_spec(SPEC_PROFILES)
SPEC_PROFILES.loader.exec_module(profiles)


def built_page(name):
    files = profiles.profile_files(name, load_manifest(), "home-id")
    page = next(content for content in files.values() if content.get("Controllers", [{}])[0].get("Actions"))
    return page["Controllers"][0]["Actions"], page["Controllers"][1]["Actions"] or {}


# The pages are written from the manifest, so a renamed action or a temp
# the dial doesn't have would give me a page of blank keys. Home has to
# sit where it is on my other pages: top left on the XL, top right on the +.
@pytest.mark.parametrize(("name", "home"), [("ember-xl", "0,0"), ("ember-plus", "3,0")])
def test_pages_use_real_actions_the_dial_temps_and_my_home_spot(name, home):
    keys, dials = built_page(name)
    manifest_uuids = {action["UUID"]: action["Controllers"] for action in load_manifest()["Actions"]}

    assert keys[home]["UUID"] == profiles.SWITCH_PROFILE
    assert keys[home]["Settings"]["ProfileUUID"] == "home-id"
    plugin_keys = [key for key in keys.values() if key["UUID"] != profiles.SWITCH_PROFILE]
    assert all(manifest_uuids[key["UUID"]] == ["Keypad"] for key in plugin_keys)
    assert all(manifest_uuids[dial["UUID"]] == ["Encoder"] for dial in dials.values())
    temps = [int(key["Settings"]["temp"]) for key in plugin_keys if key["UUID"] == plugin.TEMP]
    assert temps == plugin.PRESETS


# Every page the Ember Page key can open is in the manifest for the right
# kind of deck, or Stream Deck has nothing to switch to.
def test_every_page_the_key_opens_is_bundled_for_that_deck():
    bundled = {profile["Name"]: profile["DeviceType"] for profile in load_manifest()["Profiles"]}

    assert {page: kind for kind, page in plugin.PAGES.items()} == bundled
    assert sorted(f"profiles/{name}" for name in profiles.PAGES) == sorted(bundled)


# Same ids every build, so a rebuilt page replaces the old one instead of
# arriving as a second "Ember XL".
@pytest.mark.parametrize("name", ["ember-xl", "ember-plus"])
def test_page_ids_are_the_same_every_build(name):
    assert profiles.profile_files(name, load_manifest(), "home-id") == profiles.profile_files(name, load_manifest(), "home-id")


# On a dial, ShowTitle false hides the strip's top line (the layout's
# "title" item): the Ember + page's dial showed 135° with nothing above it.
# Keys keep it off because they draw their own words.
def test_page_dials_show_their_title_and_keys_do_not():
    keys, dials = built_page("ember-plus")

    assert dials and all(dial["States"][0]["ShowTitle"] is True for dial in dials.values())
    assert all(key["States"][0]["ShowTitle"] is False for key in keys.values())

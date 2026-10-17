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
        assert (PLUGIN_ROOT / action["Encoder"]["layout"]).is_file()
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
    manifest = load_manifest()
    for action in manifest["Actions"]:
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
    manifest = load_manifest()
    for action in manifest["Actions"]:
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


def test_feedback_gives_off_mode_its_own_connected_title(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(
        plugin,
        "status",
        lambda: {
            "connected": True,
            "battery_percent": 80,
            "liquid_state_label": "Warm (No Control)",
            "current_f": 116,
            "updated_ts": plugin.time.time(),
        },
    )

    plugin.feedback(ws, "dial-1", override=0)

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "CONNECTED · 80%"
    assert payload["value"]["value"] == "OFF"
    assert payload["trend"]["enabled"] is False


def test_feedback_builds_offline_payload(monkeypatch):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin, "status", lambda: {"connected": False})

    plugin.feedback(ws, "dial-1", override=145)

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == "OFFLINE"
    assert payload["value"]["value"] == "145°"


# Offline with heating off used to say OFFLINE over OFF. That pair is the one
# case that shows the sleeping mug, with every text field switched off.
@pytest.mark.parametrize(
    "state",
    [
        {},
        {"connected": False, "battery_percent": 90, "current_f": 130},
        {"connected": True, "updated_ts": 699.0, "battery_percent": 90},
    ],
)
def test_feedback_shows_sleeping_mug_when_offline_and_heating_is_off(monkeypatch, state):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(plugin, "status", lambda: state)

    plugin.feedback(ws, "dial-1", override=0)

    payload = ws.messages[-1]["payload"]
    assert payload["mug"] == {"value": plugin.SLEEPING_MUG, "enabled": True}
    assert payload["title"] == ""
    assert all(payload[key]["enabled"] is False for key in ("value", "current", "trend", "target"))


# Only one "off" is not enough for the mug: a connected mug with heating off
# keeps its battery line, and an offline mug with a target keeps its number.
@pytest.mark.parametrize(
    ("state", "target", "expected_title"),
    [
        ({"connected": True, "updated_ts": 1_000.0, "battery_percent": 78}, 0, "CONNECTED · 78%"),
        ({"connected": False}, 145, "OFFLINE"),
    ],
)
def test_feedback_keeps_text_when_only_one_thing_is_off(monkeypatch, state, target, expected_title):
    ws = FeedbackSocket()
    monkeypatch.setattr(plugin.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(plugin, "status", lambda: state)

    plugin.feedback(ws, "dial-1", override=target)

    payload = ws.messages[-1]["payload"]
    assert payload["title"] == expected_title
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
    monkeypatch.setattr(plugin, "target_f", lambda: actual)

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


def run_main_until_events_end(monkeypatch, events, monotonic_values=()):
    steady_clock = Clock(1.0)
    websocket = ScriptedWebSocket(events, steady_clock, monotonic_values)
    monkeypatch.setattr(plugin, "WebSocket", lambda port: websocket)
    arguments = {"-port": "1234", "-pluginUUID": "plugin-id", "-registerEvent": "registerPlugin"}
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
    arguments = {"-port": "1234", "-pluginUUID": "plugin-id", "-registerEvent": "registerPlugin"}
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


def test_rotation_the_app_never_applies_reverts_a_few_seconds_later(monkeypatch):
    # Pins what happens today. I turn the dial to 130, the app ignores the
    # command and status.json keeps saying 120. The dial shows 130, then
    # quietly goes back to 120 a few seconds after the turn (3.7 s here, at the first refresh after 3 s), with no
    # hint that the command did not land. What I want: say so on the dial.
    # The "NOT APPLIED" change replaces this test.
    monkeypatch.setattr(plugin, "status", lambda: {"target_f": 120})
    monkeypatch.setattr(plugin, "set_temp", Mock())

    websocket = run_timed_main(
        monkeypatch,
        [
            (0.0, {"event": "willAppear", "context": "dial-1"}),
            (1.0, {"event": "dialRotate", "context": "dial-1", "payload": {"ticks": 1}}),
            (2.6, plugin.socket.timeout()),
            (4.7, plugin.socket.timeout()),
        ],
    )

    assert setfeedback_values(websocket) == ["120°", "130°", "130°", "120°"]


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

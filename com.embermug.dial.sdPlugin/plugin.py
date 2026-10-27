#!/usr/bin/env python3
import base64
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

PRESETS = [120, 130, 140, 145] # Preset temperature values in Fahrenheit
HOLD_SECONDS = 1.5
TARGET_PREVIEW_SECONDS = 1.5
CHECKMARK_SECONDS = 3
RECONNECT_TIMEOUT_SECONDS = 30
CONNECTED_MESSAGE_SECONDS = 3
NOT_APPLIED_MESSAGE_SECONDS = 3
STATUS_REFRESH_SECONDS = 2 # How often the dial redraws from status.json
SELECTION_SETTLE_SECONDS = 3 # After a dial action, wait this long before trusting status.json over my choice
STATUS_STALE_SECONDS = 300
SUPPORT = Path.home() / "Library/Application Support/EmberMug"
STATUS = SUPPORT / "status.json" # Mug data read from the Ember menu-bar app
COMMAND = SUPPORT / "command.json" # Commands to control the Ember menu-bar app
SLEEPING_MUG = "imgs/sleeping-mug.svg" # Shown on the dial whenever heating is off
# The same sleeping mug sized for a key. Keys get their pictures as text,
# so this one is read in once instead of named by path.
SLEEPING_KEY = (Path(__file__).parent / "imgs/sleeping-mug-key.svg").read_text(encoding="utf-8")
LOG_FILE = Path.home() / "Library/Logs/EmberMug/plugin.log"
EMBER_APP = Path.home() / "Applications/Ember Mug.app"
log = logging.getLogger("embermug")

# The four things I can put on the deck. The dial is the original. The
# keys are for places a dial can't go: the Action Wheel (it only takes
# key actions) and my XL, which has no dials at all.
DIAL = "com.embermug.dial.temperature"
POWER = "com.embermug.dial.power"
TEMP = "com.embermug.dial.temp"
OPEN = "com.embermug.dial.open"
KEY_ACTIONS = (POWER, TEMP, OPEN)
DEFAULT_TEMP = 145 # My favorite. On/Off turns heating back on here, same as the dial
# The page the Ember Page key opens, by Stream Deck's number for the kind
# of deck: 2 is the XL, 7 is the +. Both are bundled with the plugin.
PAGES = {2: "profiles/ember-xl", 7: "profiles/ember-plus"}

# My colors and type for keys, the same as my other plugins (the
# Pomodoro key is the picture of it). Gray means off or offline, pink
# means heating, or "this is the temp the mug is set to".
BLACK = "#000000"
WHITE = "#ffffff"
PINK = "#f2a7c3"
DIM = "#5c5c62"
FONT = "-apple-system, Helvetica, sans-serif"
# The arrow colors on the dial and the On/Off key: here the color is the
# information, red heading up to a warmer target, blue cooling down.
WARMING = "#FF5A5F"
COOLING = "#3B9CFF"
selected = None # Current dial preset; none until the plugin initializes
last_action = 0.0  # Time of the most recent dial action


def setup_logging():
    # Stream Deck throws away everything a plugin prints, so the log file is
    # the only place a crash can leave a note. Small and rotating, so it never
    # grows into a surprise. If the folder is blocked the plugin still runs.
    log.setLevel(logging.INFO)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=200_000, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


def atomic_json(path, value):
    # Make sure the destination folder exists before writing
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path) # Atomically replace the official file with the completed temporary file


# Read the latest mug status written by the Ember menu-bar app
def status():
    try:
        value = json.loads(STATUS.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def finite_number(value):
    """Return a usable finite number, or None for malformed status data."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def target_f(state=None):
    state = status() if state is None else state
    value = finite_number(state.get("target_f"))
    return value if value is not None else 0.0


def status_is_stale(state, now=None):
    """Return whether a claimed connection is backed by a recent update."""
    if state.get("connected") is not True:
        return False
    updated_at = finite_number(state.get("updated_ts"))
    if updated_at is None:
        return True
    now = time.time() if now is None else now
    return now - updated_at > STATUS_STALE_SECONDS


def set_temp(fahrenheit):
    celsius = (fahrenheit - 32) * 5 / 9 if fahrenheit else 0
    atomic_json(COMMAND, {"cmd": "set_temp", "temp_c": celsius})


def request_reconnect():
    # The menu-bar app only reads command.json while connected, so its
    # "takeback" command cannot wake an offline app. Opening the app runs its
    # launcher, which replaces the stuck process and starts a fresh connection.
    if not EMBER_APP.is_dir():
        raise FileNotFoundError(f"Ember app not found: {EMBER_APP}")
    subprocess.Popen(["open", str(EMBER_APP)])


class WebSocket:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ) % (port, key)
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if b"101" not in response.split(b"\r\n", 1)[0] or expected not in response:
            raise RuntimeError("Stream Deck WebSocket handshake failed")
        # A short timeout lets the display leave target-preview mode close to
        # 1.5 seconds after the last dial turn, even when no new event arrives.
        self.sock.settimeout(0.25)

    def send(self, value):
        payload = json.dumps(value, separators=(",", ":")).encode()
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        body = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + body)

    def receive(self):
        # TCP may split even this two-byte header across multiple reads. Use
        # the same exact-length reader as the rest of the frame instead of
        # assuming recv(2) always returns both bytes together.
        first = self._read(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read(8))[0]
        payload = self._read(length)
        if opcode == 9:
            self.send_raw(10, payload)
            return None
        if opcode == 8:
            raise EOFError
        return json.loads(payload.decode()) if opcode == 1 else None

    def _read(self, count):
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                # The connection ended before the complete frame arrived.
                # Treat it as a normal disconnect rather than looping forever.
                raise EOFError
            data += chunk
        return data

    def send_raw(self, opcode, payload):
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
        body = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + body)


def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1]


def is_at_target(state, target):
    current = finite_number(state.get("current_f"))
    target = finite_number(target)
    return current is not None and target is not None and target >= 1 and abs(current - target) <= 1


def hidden_fields():
    """Every field of the dial layout switched off; screens turn on what they need."""
    return {
        "value": {"value": "", "enabled": False},
        "current": {"value": "", "enabled": False},
        "trend": {"value": "", "enabled": False},
        "target": {"value": "", "enabled": False},
        "mug": {"enabled": False},
    }


def temperature_display(state, target, target_only=False, show_check=False):
    """Build the separate display fields used on the Stream Deck dial."""
    current = finite_number(state.get("current_f"))
    target = finite_number(target)
    target = target if target is not None else 0.0
    target_text = "OFF" if target < 1 else f"{round(target):d}°"
    fields = hidden_fields()
    fields["value"] = {"value": target_text, "enabled": True}

    # Heating off has its own uncluttered interface. OFF is a mode, not a
    # temperature target, so do not show the current temperature or an arrow.
    if target < 1:
        return fields

    # While the dial is turning, keep the chosen target large and uncluttered.
    if target_only or current is None:
        return fields

    # Once the drink is at its target, the direction marker is no longer useful.
    if is_at_target(state, target):
        fields["value"]["value"] = f"{round(target):d}°  ✓" if show_check else target_text
        return fields

    fields["value"]["enabled"] = False
    fields["current"] = {"value": f"{round(current):d}°", "enabled": True}
    fields["target"] = {"value": target_text, "enabled": True}

    # Red points up toward a warmer target; blue points down toward a cooler
    # target (or toward OFF). Keeping this symbol small prevents truncation.
    warming = current < target
    fields["trend"] = {
        "value": "▲" if warming else "▼",
        "color": WARMING if warming else COOLING,
        "enabled": True,
    }
    return fields


def temperature_value(state, target):
    """Plain-text fallback used by short reconnect status messages."""
    current = finite_number(state.get("current_f"))
    target = finite_number(target)
    target = target if target is not None else 0.0
    if is_at_target(state, target):
        return f"{round(target):d}°  ✓"
    current_text = "—" if current is None else f"{round(current):d}°"
    target_text = "OFF" if target < 1 else f"{round(target):d}°"
    return f"{current_text}  {target_text}"


def sleeping_mug_display():
    """Swap every text field for the sleeping mug picture."""
    # The layout stacks the picture on top of the text. Each text field is
    # switched off here so nothing from the last screen shows through.
    return {"title": "", **hidden_fields(), "mug": {"value": SLEEPING_MUG, "enabled": True}}


def feedback(ws, context, override=None, target_only=False, show_check=False, state=None):
    # A caller redrawing several dials passes the status it already read, so
    # one tick is one read of status.json instead of one per dial.
    state = status() if state is None else state
    connected = state.get("connected") is True and not status_is_stale(state)
    target = target_f(state) if override is None else override
    if target < 1:
        # Off is off. Connected or not, the dial shows one sleeping mug
        # instead of a drink cooling down degree by degree.
        ws.send({"event": "setFeedback", "context": context, "payload": sleeping_mug_display()})
        return
    battery = finite_number(state.get("battery_percent"))
    if connected and battery is not None:
        charging = " ⚡" if state.get("charging") is True else ""
        label = state.get("liquid_state_label")
        mug_state = label.strip().upper() if isinstance(label, str) and label.strip() else "CONNECTED"
        if mug_state == "UNKNOWN":
            # The Ember app can briefly report UNKNOWN before its first full
            # status update. The Bluetooth connection is already real, so use
            # the useful connection label until a meaningful state arrives.
            mug_state = "CONNECTED"
        elif mug_state == "WARM (NO CONTROL)":
            # Immediately after heating is enabled, the app can briefly keep
            # its previous no-control label. The temperatures already tell us
            # the useful direction while the app catches up.
            current = finite_number(state.get("current_f"))
            if current is None:
                mug_state = "CONNECTED"
            elif current < target - 1:
                mug_state = "HEATING"
            elif current > target + 1:
                mug_state = "COOLING"
            else:
                mug_state = "PERFECT"
        title = f"{mug_state} · {round(float(battery))}%{charging}"
        display = temperature_display(state, target, target_only, show_check)
    else:
        title = "CONNECTED" if connected else "OFFLINE"
        display = temperature_display(state, target) if connected else temperature_display({}, target)
    ws.send({"event": "setFeedback", "context": context, "payload": {"title": title, **display}})


def message_feedback(ws, context, title, value):
    display = {**hidden_fields(), "value": {"value": value, "enabled": True}}
    ws.send({"event": "setFeedback", "context": context, "payload": {"title": title, **display}})


def redraw_all(ws, contexts, *args, message=None, **kwargs):
    """Draw the same screen on every dial on screen.

    With message=(title, value) it draws that short text screen; otherwise it
    passes everything on to feedback().
    """
    # list() because a willDisappear could change the set while we loop.
    for context in list(contexts):
        if message:
            message_feedback(ws, context, *message)
        else:
            feedback(ws, context, *args, **kwargs)


def key_svg(label, value, color, label_color=DIM):
    """One 144px key: a small label on top, gray unless said otherwise, the big value under it."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144" font-family="{FONT}">'
        f'<rect width="144" height="144" fill="{BLACK}"/>'
        f'<text x="72" y="38" fill="{label_color}" font-size="20" font-weight="600" text-anchor="middle">{label}</text>'
        f'<text x="72" y="88" fill="{color}" font-size="38" font-weight="600" text-anchor="middle">{value}</text>'
        "</svg>"
    )


def key_temp(settings):
    # The temp comes from the key's settings page as text ("140"), or not
    # at all on a key I just dragged on. Anything that isn't one of the
    # dial's presets means my default, so a key can never ask the mug for
    # a temp the dial can't show.
    try:
        value = int(settings.get("temp"))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_TEMP
    return value if value in PRESETS else DEFAULT_TEMP


def key_picture(action, settings, state, target, notice=None):
    """What a key shows, from the mug's status and the temp I've picked."""
    connected = state.get("connected") is True and not status_is_stale(state)
    target = target or 0
    heating = target >= 1
    current = finite_number(state.get("current_f"))
    now_text = "—" if current is None else f"{round(current):d}°"
    if action == TEMP:
        temp = key_temp(settings)
        if notice:
            # "Sent" until the mug says it's set, or "Not applied" when
            # it never does, so a press always gets an answer.
            return key_svg(notice, f"{temp}°", WHITE)
        if not connected:
            return key_svg("Offline", f"{temp}°", DIM)
        return key_svg("Temp", f"{temp}°", PINK if heating and round(target) == temp else WHITE)
    if not heating:
        # Off is off, same as the dial: the sleeping mug, connected or
        # not, instead of the drink cooling down degree by degree.
        return SLEEPING_KEY
    if not connected:
        return key_svg("Offline" if action == POWER else "Ember", "—", DIM)
    if current is not None and not is_at_target(state, target):
        # While the drink is still getting to its temp, the label on On/Off
        # and Ember Page is the dial's arrow: which way it's going says more
        # than "On" or "Ember" does. Once it's there, the word comes back.
        warming = current < target
        return key_svg("▲" if warming else "▼", now_text, PINK, WARMING if warming else COOLING)
    return key_svg("On" if action == POWER else "Ember", now_text, PINK)


def draw_keys(ws, keys, state=None, now=None):
    # Only a picture that changed gets sent. These redraw every couple of
    # seconds, and the deck chokes when it's sent the same picture over
    # and over. With no keys showing there's nothing to read status for.
    if not keys:
        return
    state = status() if state is None else state
    now = time.monotonic() if now is None else now
    mug_target = target_f(state)
    for context, key in list(keys.items()):
        notice = key.get("notice")
        if notice == "Sent" and mug_target >= 1 and round(mug_target) == key_temp(key["settings"]):
            # The mug reports the temp this key sent: that's the yes.
            notice = key["notice"] = None
        elif notice == "Not applied" and now >= key.get("notice_until", 0):
            notice = key["notice"] = None
        svg = key_picture(key["action"], key["settings"], state, selected, notice)
        if key.get("drawn") == svg:
            continue
        key["drawn"] = svg
        image = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
        ws.send({"event": "setImage", "context": context, "payload": {"image": image, "target": 0}})


def deck_kinds(info):
    """Every deck in Stream Deck's -info, as {id: kind of deck}."""
    try:
        devices = json.loads(info).get("devices", [])
        return {device["id"]: device.get("type") for device in devices}
    except (AttributeError, KeyError, TypeError, ValueError):
        return {}


def send_temp_command(ws, context, fahrenheit):
    """Write a mug command without allowing a filesystem error to kill the plugin."""
    try:
        set_temp(fahrenheit)
    except OSError:
        log.exception("Could not write command.json")
        message_feedback(ws, context, "COMMAND FAILED", "TRY AGAIN")
        return False
    return True


def sync_selection(state=None):
    global selected
    actual = target_f(state)
    selected = 0 if actual < 1 else PRESETS[min(range(len(PRESETS)), key=lambda i: abs(PRESETS[i] - actual))]


def main():
    global selected, last_action
    port = int(arg("-port"))
    plugin_uuid = arg("-pluginUUID")
    register_event = arg("-registerEvent")
    ws = WebSocket(port)
    ws.send({"event": register_event, "uuid": plugin_uuid})
    # The Ember Page key opens a page made for the kind of deck it's on, so
    # I keep track of which kind each one is. Stream Deck lists them at startup and says
    # when one is plugged in or pulled out.
    decks = deck_kinds(arg("-info"))
    contexts = set()
    # Keys on screen: {context: {"action", "settings", "drawn"}}. Dials stay
    # in contexts, because everything the dial does is built on that set.
    keys = {}
    # Stream Deck gives each placed action its own context. Keeping one start
    # time per context prevents presses on different dials from interfering.
    press_started = {}
    # Keep the wall-clock request time only for comparing status.json's epoch
    # timestamp. All elapsed-time decisions use the monotonic clock so macOS
    # clock corrections cannot stretch or shorten UI timers.
    reconnect_requested_at = None
    reconnect_started_at = None
    connected_message_until = 0.0
    # The app reads command.json about once a second and drops it if the mug
    # is offline, so a command can get lost without any error. pending_selection
    # remembers what I asked for until status.json can be checked against it.
    pending_selection = None
    not_applied_until = 0.0
    target_preview_until = 0.0
    checkmark_until = 0.0
    was_at_target = None
    last_update = 0.0

    def choose(new_selection, now, sent_from=None):
        # Every way of picking a temp, a turn, a push or a key, leaves the
        # same trail: what I asked for and when, so "not applied" can check
        # it later, and a clean slate for the check mark. A new choice
        # replaces any old "Sent" on the keys, and sent_from is the Set Temp
        # key that made this one. The keys redraw right away so a turn of
        # the dial shows on them too.
        global selected, last_action
        nonlocal pending_selection, not_applied_until, checkmark_until, was_at_target
        selected = new_selection
        pending_selection = new_selection
        not_applied_until = 0.0
        checkmark_until = 0.0
        was_at_target = False
        last_action = now
        for key in keys.values():
            key["notice"] = None
        if sent_from in keys:
            keys[sent_from]["notice"] = "Sent"
        draw_keys(ws, keys, now=now)

    while True:
        try:
            event = ws.receive()
        except socket.timeout:
            event = None
        now = time.monotonic()
        if event:
            name = event.get("event")
            context = event.get("context")
            action = event.get("action")
            settings = event.get("payload", {}).get("settings") or {}
            if name == "willAppear" and context and action in KEY_ACTIONS:
                keys[context] = {"action": action, "settings": settings}
                sync_selection()
                draw_keys(ws, {context: keys[context]})
            elif name == "willAppear" and context:
                contexts.add(context)
                sync_selection()
                was_at_target = is_at_target(status(), selected)
                feedback(ws, context, selected)
            elif name == "willDisappear" and context:
                contexts.discard(context)
                keys.pop(context, None)
                press_started.pop(context, None)
            elif name == "didReceiveSettings" and context in keys:
                # A new temp picked on the settings page shows straight away.
                keys[context]["settings"] = settings
                draw_keys(ws, {context: keys[context]})
            elif name == "deviceDidConnect":
                decks[event.get("device")] = event.get("deviceInfo", {}).get("type")
            elif name == "deviceDidDisconnect":
                decks.pop(event.get("device"), None)
            elif name == "keyDown" and context in keys:
                key = keys[context]
                if key["action"] == OPEN:
                    # A plugin can only open pages it ships with, and I only
                    # made them for my XL and my +, so any other deck gets the
                    # alert triangle instead of a page that doesn't fit.
                    device = event.get("device")
                    page = PAGES.get(decks.get(device))
                    if page:
                        ws.send({"event": "switchToProfile", "context": plugin_uuid, "device": device, "payload": {"profile": page}})
                    else:
                        ws.send({"event": "showAlert", "context": context})
                    continue
                if key["action"] == TEMP:
                    new_selection = key_temp(key["settings"])
                else:
                    new_selection = 0 if selected and selected >= 1 else DEFAULT_TEMP
                try:
                    set_temp(new_selection)
                except OSError:
                    # A key has no room for "COMMAND FAILED", so it gets
                    # Stream Deck's own alert triangle and the log keeps why.
                    log.exception("Could not write command.json")
                    ws.send({"event": "showAlert", "context": context})
                    last_update = now
                    continue
                choose(new_selection, now, sent_from=context if key["action"] == TEMP else None)
                redraw_all(ws, contexts, selected)
            elif name == "dialRotate" and context:
                ticks = int(event.get("payload", {}).get("ticks", 0))
                if ticks:
                    if selected in PRESETS:
                        index = PRESETS.index(selected) + ticks
                    else:
                        # Heating off is outside the preset list. Clockwise
                        # enters at 120°F; counterclockwise enters at 145°F.
                        index = (-1 if ticks > 0 else len(PRESETS)) + ticks
                    index = max(0, min(len(PRESETS) - 1, index))
                    new_selection = PRESETS[index]
                    if not send_temp_command(ws, context, new_selection):
                        last_update = now
                        continue
                    choose(new_selection, now)
                    target_preview_until = now + TARGET_PREVIEW_SECONDS
                    feedback(ws, context, selected, target_only=True)
            elif name == "dialDown" and context:
                # Do not toggle heating yet. We must wait for dial-up so a
                # quick press can be distinguished from a reconnect hold.
                press_started[context] = now
            elif name == "dialUp" and context:
                started = press_started.pop(context, None)
                if started is None:
                    continue
                held_for = now - started
                last_action = now
                if held_for >= HOLD_SECONDS:
                    try:
                        request_reconnect()
                    except FileNotFoundError:
                        log.exception("Ember app not found")
                        message_feedback(ws, context, "APP NOT FOUND", "REINSTALL APP")
                        last_update = now
                        continue
                    except OSError:
                        log.exception("Could not open the Ember app")
                        message_feedback(ws, context, "RECONNECT FAILED", "TRY AGAIN")
                        last_update = now
                        continue
                    # Wait for a status update newer than this request. That
                    # prevents stale "connected" data from confirming too soon.
                    reconnect_requested_at = time.time()
                    reconnect_started_at = now
                    message_feedback(ws, context, "RECONNECTING…", "…")
                else:
                    # A short press keeps the original behavior: turn heating
                    # off, or turn it back on at the default of 145°F.
                    new_selection = 0 if selected and selected >= 1 else DEFAULT_TEMP
                    if not send_temp_command(ws, context, new_selection):
                        last_update = now
                        continue
                    choose(new_selection, now)
                    feedback(ws, context, selected)
        if checkmark_until and now >= checkmark_until:
            # The target-reached check is a brief confirmation, not a
            # permanent part of the temperature display.
            checkmark_until = 0.0
            current_status = status()
            redraw_all(ws, contexts, selected, state=current_status)
            last_update = now
            continue
        if target_preview_until and now >= target_preview_until:
            # The user has stopped turning the dial. Replace the large target
            # preview with the live current-temperature-to-target comparison.
            target_preview_until = 0.0
            current_status = status()
            at_target = is_at_target(current_status, selected)
            if at_target and not was_at_target:
                checkmark_until = now + CHECKMARK_SECONDS
            was_at_target = at_target
            redraw_all(ws, contexts, selected, show_check=now < checkmark_until, state=current_status)
            last_update = now
            continue
        if now - last_update >= STATUS_REFRESH_SECONDS:
            # With no dial or key on screen there is nothing to draw, so
            # skip the status.json read. willAppear syncs everything again
            # when one comes back.
            if not contexts and not keys:
                # Nobody is watching, so nobody should get a verdict on a
                # command from before they left.
                pending_selection = None
                last_update = now
                continue
            # Do not let the normal refresh overwrite the target preview while
            # the user is still choosing a preset.
            if target_preview_until:
                continue
            current_status = status()
            # Keys only show the plain truth, so they skip the dial's
            # reconnecting and "not applied" screens below.
            draw_keys(ws, keys, current_status)
            if reconnect_requested_at is not None:
                updated_at = finite_number(current_status.get("updated_ts")) or 0.0
                status_is_fresh = updated_at >= reconnect_requested_at
                if current_status.get("connected") is True and status_is_fresh:
                    reconnect_requested_at = None
                    reconnect_started_at = None
                    connected_message_until = now + CONNECTED_MESSAGE_SECONDS
                    sync_selection(current_status)
                elif now - reconnect_started_at >= RECONNECT_TIMEOUT_SECONDS:
                    # Stop showing an endless reconnect message. The normal
                    # feedback will explain that the mug is still offline.
                    reconnect_requested_at = None
                    reconnect_started_at = None
                else:
                    redraw_all(ws, contexts, message=("RECONNECTING…", "…"))
                    last_update = now
                    continue
            if now < connected_message_until:
                value = temperature_value(current_status, selected or 0)
                redraw_all(ws, contexts, message=("CONNECTED", value))
                last_update = now
                continue
            if now < not_applied_until:
                redraw_all(ws, contexts, message=("NOT APPLIED", "TRY AGAIN"))
                last_update = now
                continue
            if now - last_action > SELECTION_SETTLE_SECONDS:
                sync_selection(current_status)
                if pending_selection is not None:
                    # Time is up and status.json is the truth. If it still
                    # disagrees, the app never took the command, so say so
                    # before the dial falls back to what the mug is doing.
                    if selected != pending_selection:
                        not_applied_until = now + NOT_APPLIED_MESSAGE_SECONDS
                        redraw_all(ws, contexts, message=("NOT APPLIED", "TRY AGAIN"))
                        # The Set Temp key still saying "Sent" gets the same news.
                        for key in keys.values():
                            if key.get("notice") == "Sent":
                                key["notice"] = "Not applied"
                                key["notice_until"] = not_applied_until
                        draw_keys(ws, keys, current_status, now)
                        pending_selection = None
                        last_update = now
                        continue
                    pending_selection = None
            at_target = is_at_target(current_status, selected)
            if at_target and not was_at_target:
                checkmark_until = now + CHECKMARK_SECONDS
            elif not at_target:
                checkmark_until = 0.0
            was_at_target = at_target
            redraw_all(ws, contexts, selected, show_check=now < checkmark_until, state=current_status)
            last_update = now


def run():
    setup_logging()
    try:
        main()
    except (EOFError, BrokenPipeError, ConnectionResetError):
        # Stream Deck closing the socket is how it says goodbye when it quits.
        # Sometimes it hangs up abruptly (reset, broken pipe) instead of
        # politely. None of that is a crash, so it exits 0 and leaves one line, not an alarm.
        log.info("Stream Deck closed the connection, exiting")
        sys.exit(0)
    except Exception:
        log.exception("Plugin crashed")
        sys.exit(1)


if __name__ == "__main__":
    run()

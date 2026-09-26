#!/usr/bin/env python3
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

PRESETS = [120, 130, 140, 145] # Preset temperature values in Fahrenheit
HOLD_SECONDS = 1.5
RECONNECT_TIMEOUT_SECONDS = 30
CONNECTED_MESSAGE_SECONDS = 3
SUPPORT = Path.home() / "Library/Application Support/EmberMug"
STATUS = SUPPORT / "status.json" # Mug data read from the Ember menu-bar app
COMMAND = SUPPORT / "command.json" # Commands to control the Ember menu-bar app
EMBER_APP = Path.home() / "Applications/Ember Mug.app"
selected = None # Current dial preset; none until the plugin initializes
last_action = 0.0  # Time of the most recent dial action


def atomic_json(path, value):
    # Make sure the destination folder exists before writing
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path) # Atomically replace the official file with the completed temporary file


# Read the latest mug status written by the Ember menu-bar app
def status():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def target_f():
    value = status().get("target_f")
    return float(value) if value is not None else 0.0


def set_temp(fahrenheit):
    celsius = (fahrenheit - 32) * 5 / 9 if fahrenheit else 0
    atomic_json(COMMAND, {"cmd": "set_temp", "temp_c": celsius})


def request_reconnect():
    # The menu-bar app only reads command.json while connected, so its
    # "takeback" command cannot wake an offline app. Opening the app runs its
    # launcher, which replaces the stuck process and starts a fresh connection.
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
        self.sock.settimeout(1)

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
        first = self.sock.recv(2)
        if not first:
            raise EOFError
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
            data += self.sock.recv(count - len(data))
        return data

    def send_raw(self, opcode, payload):
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
        body = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + body)


def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1]


def feedback(ws, context, override=None):
    state = status()
    connected = bool(state.get("connected"))
    target = target_f() if override is None else override
    value = "OFF" if target < 1 else f"{round(target):d}°F"
    battery = state.get("battery_percent")
    if connected and battery is not None:
        charging = " ⚡" if state.get("charging") else ""
        title = f"EMBER · {round(float(battery))}%{charging}"
    else:
        title = "EMBER" if connected else "EMBER · OFFLINE"
    ws.send({"event": "setFeedback", "context": context, "payload": {"title": title, "value": value}})


def message_feedback(ws, context, title, value):
    ws.send({"event": "setFeedback", "context": context, "payload": {"title": title, "value": value}})


def sync_selection():
    global selected
    actual = target_f()
    selected = 0 if actual < 1 else PRESETS[min(range(len(PRESETS)), key=lambda i: abs(PRESETS[i] - actual))]


def main():
    global selected, last_action
    port = int(arg("-port"))
    plugin_uuid = arg("-pluginUUID")
    register_event = arg("-registerEvent")
    ws = WebSocket(port)
    ws.send({"event": register_event, "uuid": plugin_uuid})
    contexts = set()
    # Stream Deck gives each placed action its own context. Keeping one start
    # time per context prevents presses on different dials from interfering.
    press_started = {}
    reconnect_requested_at = None
    connected_message_until = 0.0
    last_update = 0.0
    while True:
        try:
            event = ws.receive()
        except socket.timeout:
            event = None
        if event:
            name = event.get("event")
            context = event.get("context")
            if name == "willAppear" and context:
                contexts.add(context)
                sync_selection()
                feedback(ws, context, selected)
            elif name == "willDisappear" and context:
                contexts.discard(context)
                press_started.pop(context, None)
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
                    selected = PRESETS[index]
                    last_action = time.time()
                    set_temp(selected)
                    feedback(ws, context, selected)
            elif name == "dialDown" and context:
                # Do not toggle heating yet. We must wait for dial-up so a
                # quick press can be distinguished from a reconnect hold.
                press_started[context] = time.monotonic()
            elif name == "dialUp" and context:
                started = press_started.pop(context, None)
                if started is None:
                    continue
                held_for = time.monotonic() - started
                last_action = time.time()
                if held_for >= HOLD_SECONDS:
                    request_reconnect()
                    # Wait for a status update newer than this request. That
                    # prevents stale "connected" data from confirming too soon.
                    reconnect_requested_at = time.time()
                    message_feedback(ws, context, "RECONNECTING…", "…")
                else:
                    # A short press keeps the original behavior: turn heating
                    # off, or turn it back on at the default of 145°F.
                    selected = 0 if selected and selected >= 1 else 145
                    set_temp(selected)
                    feedback(ws, context, selected)
        if time.time() - last_update >= 2:
            now = time.time()
            current_status = status()
            if reconnect_requested_at is not None:
                status_is_fresh = float(current_status.get("updated_ts", 0)) >= reconnect_requested_at
                if current_status.get("connected") and status_is_fresh:
                    reconnect_requested_at = None
                    connected_message_until = now + CONNECTED_MESSAGE_SECONDS
                    sync_selection()
                elif now - reconnect_requested_at >= RECONNECT_TIMEOUT_SECONDS:
                    # Stop showing an endless reconnect message. The normal
                    # feedback will explain that the mug is still offline.
                    reconnect_requested_at = None
                else:
                    for context in list(contexts):
                        message_feedback(ws, context, "RECONNECTING…", "…")
                    last_update = now
                    continue
            if now < connected_message_until:
                value = "OFF" if not selected else f"{selected}°F"
                for context in list(contexts):
                    message_feedback(ws, context, "CONNECTED", value)
                last_update = now
                continue
            if now - last_action > 3:
                sync_selection()
            for context in list(contexts):
                feedback(ws, context, selected)
            last_update = now


if __name__ == "__main__":
    main()

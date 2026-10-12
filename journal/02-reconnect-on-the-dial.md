# 02 · Making reconnect feel like it belongs on the dial

September 26, 2026

Today, I made a running list instead of changing everything at once.

I wanted to update the author name, add reconnect behavior, improve the visuals, show the mug's real liquid level, verify the macOS requirements, and eventually increase the plugin version.

The important rule was: one thing at a time.

## Could reconnect be a long press?

At first, reconnect was going to be a separate Stream Deck button. Then I thought:

> Could it be a long press feature on the same dial?

Yes, and that design felt much better.

The plan became:

```text
quick press       → heating on/off
hold 1.5 seconds  → reconnect mug
turn              → choose temperature
```

This required changing how pressing worked. The old plugin toggled heating as soon as the dial went down. That would fire before it knew whether I was trying to hold the dial.

The new version records the time on `dialDown`, waits for `dialUp`, then measures the difference. A short press toggles heating. A long press reconnects.

The Ember app already had a command for this, with a weird internal name: `takeback`. The Ember app uses “handoff” when it temporarily releases the mug to the phone and “takeback” when the Mac wants it again. Internally, `takeback` also calls reconnect.

That sounded perfect, until I tested it.

## The command worked, but reconnect did not

The Stream Deck showed `RECONNECTING…`, which proved the long press was working. But the mug stayed offline.

The command file contained exactly what it was supposed to contain:

```json
{"cmd": "takeback"}
```

The bug was in the interaction between the two programs. The menu-bar app only checked `command.json` while it was already connected or intentionally handed off. While fully offline, the reconnect command just sat there waiting to be read.

That was a very useful failure. The plugin was doing its job, but the receiving app was not listening in that state.

At the same time, the Ember log was repeating another error:

```text
Bluetooth is not authorized for an unknown reason
```

This one had an unexpected cause. Installing GitHub CLI through Anaconda replaced the Python executable with a different Python 3.12 build. macOS still displayed Bluetooth permission as enabled, but the executable behind that permission had changed.

Turning the `python3.12` Bluetooth permission off and back on refreshed it. Then the Ember app's normal **Reconnect** menu item worked again.

That was frustrating, but also kind of fascinating. The Bluetooth permission looked correct in Settings, yet the application was still denied because the actual Python binary had changed underneath it.

## Reconnect needed a different approach

Because an offline app could not read `takeback`, the dial's long press now reopens `Ember Mug.app`. Its launcher replaces the stuck background process and starts a fresh connection attempt.

The first reconnect message was too large. `RECONNECTING…` filled the big temperature area and looked clumsy. I moved it to the smaller top line and used a simple `…` underneath while the app reconnects.

Then another bug appeared.

After reconnecting, the mug was actually connected but heating was off. The plugin tried to rotate from a target of `0`. Its preset list only contained:

```text
120, 130, 140, 145
```

Trying to find `0` in that list crashed the plugin. Stream Deck restarted it, but the dial felt frozen.

The fix made the off state intentional:

```text
clockwise from OFF        → 120°F
counterclockwise from OFF → 145°F
```

I considered automatically setting the mug to 120°F after reconnecting because seeing `OFF` did not feel intuitive. But reconnecting and turning on heat are two different actions. Automatically heating could be surprising, and 120°F was not even my chosen default.

The better design was to explain what happened:

```text
RECONNECTING…
      …
```

Then, after a fresh status update proves the mug is back:

```text
CONNECTED
    OFF
```

The confirmation stays for three seconds, then the dial returns to its normal battery and temperature display. If the mug does not reconnect within 30 seconds, the plugin stops pretending and returns to the normal offline status.

When that finally worked, it felt awesome. The dial was no longer just sending commands. It was communicating what the system was actually doing.

# Ember Stream Deck Dev Log

This is the part where I keep the real story of building this project. Not just the clean instructions after everything works, but the questions, wrong turns, weird errors, small wins, and the things I actually learned along the way.

## September 25, 2026 — Can I control my mug with a Stream Deck?

This started with a pretty simple idea: I wanted to turn a Stream Deck dial and change the temperature on my Ember Mug.

My favorite temperature is 145°F. I use my Stream Deck all the time. It felt like those two things should be able to talk to each other.

At first, I assumed Ember would have an API. I thought I would find some official command that said, “set the mug to 145,” connect that to Stream Deck, and be done.

That is not what happened.

Ember does not have the kind of public API I expected. What I found instead was `python-ember-mug`, an unofficial Python library that talks directly to the mug over Bluetooth Low Energy.

This was my first real question:

> So Ember doesn't have an API we can use?

The answer was basically no—not an official public one. The Python library was acting as the missing API layer.

The setup became:

```text
Python command → Bluetooth → Ember Mug
```

I started learning how Terminal commands are structured. I used commands like:

```bash
ember-mug discover
ember-mug info --imperial
ember-mug set --imperial --target-temp 135
```

I asked why temperature was called “imperial.” That turned into a useful little lesson: in this program, imperial means Fahrenheit and metric means Celsius.

I also learned that a Terminal command reads almost like a sentence:

```text
program → action → options → value
```

The Mac found my Ember Mug 2 and gave me a long Bluetooth identifier. On macOS, it looked like a UUID instead of the kind of MAC address I expected. I thought passing that identifier meant “connect to this exact thing.” That was close, but not completely right. It meant “only accept this device while scanning.” It could filter for the mug, but it could not force a successful connection.

That difference became very important.

The Mac could **find** the mug and still fail while trying to **connect** to it.

When the mug was flashing blue in pairing mode, the temperature command worked. Seeing this line was a huge win:

```text
Setting target_temp to 135.0
```

The Mac had actually changed the mug.

Then came the frustrating part. Once the command finished, the Bluetooth connection closed. The next command would find the mug, try to connect, and time out. I kept seeing versions of:

```text
Failed to connect after 4 attempt(s): TimeoutError
```

I did not want a Stream Deck control that required me to flip the mug over and put it into blue pairing mode every time I touched the dial. That would defeat the whole point.

### Why do I need an app if the command already worked?

This was probably the question that unlocked the whole project.

I kept asking why I needed to build an app when I had already changed the temperature from Terminal. The difference was not really “Terminal versus app.” The difference was a temporary Bluetooth connection versus a persistent controller.

Without something running in the background, every dial movement would need to do this:

```text
find → connect → send command → disconnect
```

The reconnect step was exactly where things kept breaking.

I found an open-source macOS Ember menu-bar app that stays running, keeps the Bluetooth connection alive, reconnects when needed, and accepts local commands.

That changed the design to:

```text
Stream Deck → menu-bar app → Bluetooth → Ember Mug
```

I compared it to Home Assistant because I wanted to understand whether Home Assistant does the same thing. Conceptually, yes: it is always there managing device connections and reconnections. Home Assistant is the large smart-home version. This menu-bar app is the small, focused version for my mug.

### Learning `uv` and `.venv`

Then I ran into `uv`.

At first Terminal said:

```text
zsh: command not found: uv
```

I installed it, but I did not just want to paste commands without understanding them. I wanted to know what `uv` was doing and why I needed it when Python and Anaconda were already installed.

I learned that `uv` manages Python projects and their packages. More importantly, it helped create `.venv`, a private Python environment just for the Ember app.

My question was:

> What exactly is `.venv`?

The answer made Python projects make a lot more sense. `.venv` is not a virtual computer. It is an isolated folder containing the Python setup and packages that this project needs. It keeps the Ember app from fighting with packages used by other projects.

I cloned the menu-bar project, found the mug, saved its Bluetooth address, and launched the source version with:

```bash
uv run ember_mug_app.py
```

It connected.

The menu bar showed the mug temperature, target, battery, liquid level, and charging state. I clicked 130°F and watched the Terminal log confirm:

```text
Panel action: set-temp/130
Target set to 54.4C
```

That was the first time the whole idea felt real instead of theoretical.

### Building the Mac app was not perfectly smooth

The source version still depended on Terminal staying open, so I built it into a normal `Ember Mug.app`.

The first build failed:

```text
No Python found with rumps + python-ember-mug installed.
```

I created `.venv`, installed those packages, and tried again.

It still failed.

That was annoying because I had followed the error message exactly. The build script was secretly checking for a third dependency—WebKit—but its suggested fix only listed two. Once I installed the missing WebKit bridge, the build finally worked.

Now the app could live in my Applications folder, start when I signed in, and run without a Terminal window sitting open.

### From a macro to a real Stream Deck plugin

I originally thought this would be a macro. But a macro is usually a fixed sequence. My dial needed to understand clockwise turns, counterclockwise turns, presses, live battery changes, charging state, and whether the mug was connected.

That meant building a small Stream Deck plugin.

The first working behavior was:

```text
rotate → 120°F, 130°F, 140°F, or 145°F
press  → heating off or back on at 145°F
screen → target temperature and battery
```

I learned that Stream Deck calls the dial and its section of the touchscreen an **encoder**. The plugin receives separate events for rotation, pressing down, and releasing.

The first temperature logic had a timing bug. The dial was reading the mug's previous target before Bluetooth finished reporting the new one. If I turned quickly, the presets jumped around instead of moving cleanly in order.

The fix was to let the plugin remember its own selected preset immediately, then use the mug's Bluetooth status as confirmation afterward.

The first display also had a mug icon. I did not like it. It was ugly and took up space, so I replaced the built-in layout with a custom text-only design:

```text
EMBER · 78% ⚡
      145°F
```

By the end of the night, the dial was controlling the mug, the battery was updating, the app was maintaining Bluetooth, and the whole thing finally felt like one system.

That was a lot more than the “simple macro” I thought I was starting with, but I understood every layer much better because it did not work perfectly the first time.

## September 26, 2026 — Making reconnect feel like it belongs on the dial

Today, I made a running list instead of changing everything at once.

I wanted to update the author name, add reconnect behavior, improve the visuals, show the mug's real liquid level, verify the macOS requirements, and eventually increase the plugin version.

The important rule was: one thing at a time.

### Could reconnect be a long press?

At first, reconnect was going to be a separate Stream Deck button. Then I asked:

> Could it be a long press feature on the same dial?

Yes—and that design felt much better.

The plan became:

```text
quick press       → heating on/off
hold 1.5 seconds  → reconnect mug
turn              → choose temperature
```

This required changing how pressing worked. The old plugin toggled heating as soon as the dial went down. That would fire before it knew whether I was trying to hold the dial.

The new version records the time on `dialDown`, waits for `dialUp`, then measures the difference. A short press toggles heating. A long press reconnects.

I asked about the weird internal command name `takeback`. The Ember app uses “handoff” when it temporarily releases the mug to the phone and “takeback” when the Mac wants it again. Internally, `takeback` also calls reconnect.

That sounded perfect—until we tested it.

### The command worked, but reconnect did not

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

### Reconnect needed a different approach

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

## September 28, 2026 — The things I did not know to worry about

Today was about making the dial more reliable, and wow, the “unknown unknowns” showed up fast. I knew a mug could go offline. I had not really thought through all the ways the information about that mug could be wrong before it even reached my screen.

What if `status.json` contains malformed JSON? Or a list instead of an object? What if the temperature says “hot,” the battery is not a real number, or an old status file still insists the mug is connected? That last one was especially frustrating. A saved “connected” message is not proof that anything is connected right now! I added checks for usable numbers and real boolean values, and a five-minute freshness check so stale status does not keep pretending the mug is online.

Then I learned that a WebSocket message can arrive in pieces. Even its tiny two-byte header can be split across reads. And if the connection ends halfway through a message, waiting for bytes that will never arrive can leave the plugin stuck. Now it reads the bytes it needs and detects when the connection ends early.

The command file had its own surprises. Writing a temperature command can fail because of file permissions, and I do not want one failed write to take down the whole dial or make it look like the temperature changed. I added “COMMAND FAILED” feedback and kept the previous selection when the write fails. I also had to consider something embarrassingly easy to overlook: what if `Ember Mug.app` is missing? A long press now checks for the app, with feedback for a missing app or a launch failure.

I learned another timing detail too: checking the age of saved status needs the clock, but measuring a hold or a reconnect timeout needs a timer that keeps moving steadily even if the system clock changes. Those are different jobs!

Honestly, this part was frustrating. Every time I thought I had covered the weird cases, another “but what happens if…” appeared. But turning those questions into tests felt so good. I reached **50 focused reliability tests**! Malformed and stale status, interrupted messages, permission failures, and a missing app all became things I could deliberately check instead of surprises I had to discover while trying to enjoy my coffee.

The exciting part is that I am starting to see reliability as part of the experience. I want the dial to tell me what actually happened, even when something goes wrong. Getting this little mug controller to handle the messy parts feels like a real win!

## What is next

The running list still has some fun visual work ahead:

- remove the remaining mug artwork from the action;
- create a GIF-style coffee cup with animated steam;
- make the cup's fill level match the mug's reported liquid level;
- replace the reconnect `…` with a small image or animation;
- verify the documented macOS requirement;
- increase the plugin version when the new feature set is ready.

The project keeps getting more interesting. I started by asking whether I could change a mug temperature from Stream Deck. Now I am thinking about state machines, background processes, Bluetooth permissions, event timing, interface feedback, and how to make a tiny screen feel clear and alive.

That is exactly the kind of learning I wanted from this!

# 01 · Can I control my mug with a Stream Deck?

September 25, 2026

This started with a pretty simple idea: I wanted to turn a Stream Deck dial and change the temperature on my Ember Mug.

My favorite temperature is 145°F. I use my Stream Deck all the time. It felt like those two things should be able to talk to each other.

At first, I assumed Ember would have an API. I thought I would find some official command that said, “set the mug to 145,” connect that to Stream Deck, and be done.

That is not what happened.

Ember does not have the kind of public API I expected. What I found instead was `python-ember-mug`, an unofficial Python library that talks directly to the mug over Bluetooth Low Energy.

This was my first real question:

> So Ember doesn't have an API I can use?

The answer was basically no. Not an official public one. The Python library was acting as the missing API layer.

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

I wondered why temperature was called “imperial.” That turned into a useful little lesson: in this program, imperial means Fahrenheit and metric means Celsius.

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

## Why do I need an app if the command already worked?

This was probably the question that unlocked the whole project.

I kept getting stuck on why I needed to build an app when I had already changed the temperature from Terminal. The difference was not really “Terminal versus app.” The difference was a temporary Bluetooth connection versus a persistent controller.

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

## Learning `uv` and `.venv`

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

## Building the Mac app was not perfectly smooth

The source version still depended on Terminal staying open, so I built it into a normal `Ember Mug.app`.

The first build failed:

```text
No Python found with rumps + python-ember-mug installed.
```

I created `.venv`, installed those packages, and tried again.

It still failed.

That was annoying because I had followed the error message exactly. The build script was secretly checking for a third dependency, WebKit, but its suggested fix only listed two. Once I installed the missing WebKit bridge, the build finally worked.

Now the app could live in my Applications folder, start when I signed in, and run without a Terminal window sitting open.

## From a macro to a real Stream Deck plugin

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

# I Put My Ember Mug on a Stream Deck Dial

I started with what felt like a simple idea: I wanted to control my Ember Mug from my Stream Deck instead of reaching for my phone every time I wanted to change the temperature.

My favorite temperature is **145°F**, so I wanted the dial to feel natural:

- turn it to move between **120°F, 130°F, 140°F, and 145°F**;
- press it to turn heating off;
- press it again to turn heating back on at **145°F**;
- show the mug's battery percentage and charging status on the Stream Deck screen.

It sounded like a macro. It turned into a small lesson in Python, Bluetooth, background apps, virtual environments, and how Stream Deck plugins actually work.

## The first thing I tried

I started with [`python-ember-mug`](https://github.com/sopelj/python-ember-mug), an unofficial Python library that talks directly to Ember mugs over Bluetooth Low Energy.

That was my first important discovery: Ember does not provide the kind of public API I expected to use. The Python library is basically our API layer. Instead of sending a request to an Ember web service, the computer talks straight to the mug over Bluetooth.

I used commands like:

```bash
ember-mug discover
ember-mug info --imperial
ember-mug set --imperial --target-temp 135
```

I learned to read a Terminal command like a sentence:

```text
program       action     option          value
ember-mug     set        --imperial      --target-temp 135
```

I also learned a few details that were small but useful:

- `--imperial` means use Fahrenheit.
- On macOS, the mug's long Bluetooth identifier looks like a UUID, not a traditional Bluetooth MAC address.
- The library's `--mac` option filters the scan for that exact mug. It does not force the mug to connect.
- A command can find a Bluetooth device and still fail while opening the actual connection.

The temperature command worked. My Mac found the mug, connected, and changed the target temperature. But it worked most reliably while the mug was flashing blue in pairing mode. After the command ended, the Bluetooth connection closed. The next command often found the mug but timed out while reconnecting.

That was the real problem. A Stream Deck control would be annoying if I had to put the mug into pairing mode every time I turned a dial.

## Why I ended up needing an app

At first I kept asking some version of: if the Terminal command already changed the temperature, why do I need an app?

The answer is that the app is not there just to give me another screen. Its important job is to stay running and manage the Bluetooth connection.

Without it, every dial movement would need to do this:

```text
find the mug → connect → change temperature → disconnect
```

The unreliable part was connecting again.

With a background app, the flow becomes:

```text
connect once → stay available → receive commands → control the mug
```

I used the open-source [`ember-mug-app`](https://github.com/gwhillhouse/ember-mug-app), which runs in the macOS menu bar. It connects to the mug, reconnects when needed, shows temperature and battery information, and accepts local commands from other programs.

This also helped me understand the difference between this little app and Home Assistant. Home Assistant can manage Bluetooth connections and automations for an entire smart home. The menu-bar app is the smaller version of that idea: one Mac application dedicated to one kind of device.

## What `uv` and `.venv` were doing

The menu-bar project uses `uv`, which manages Python environments and packages.

I had already installed Python packages in my Anaconda `base` environment, so I wanted to understand why we needed another environment. The answer was isolation.

The `.venv` folder is a private Python environment belonging to this project:

```text
ember-mug-app/
└── .venv/
    ├── Python
    ├── rumps
    ├── python-ember-mug
    ├── WebKit bridge
    └── Bluetooth dependencies
```

It does not replace Anaconda or install another virtual computer. It keeps this app's Python and package versions separate from everything else on the Mac.

I first tested the app from Terminal with:

```bash
uv run ember_mug_app.py
```

That gave me the menu-bar interface and proved the app could keep the mug connected. I could click **130°F**, and the Terminal log showed the target changing.

Then I built it as a normal macOS application. The source-code version required Terminal to remain open. The built version lives in `~/Applications`, runs independently, and can start automatically when I sign in.

So I was not building a second menu-bar app. I was packaging the same tested program into an app I could use every day.

## The connection that made the dial possible

The running menu-bar app keeps two local files updated:

```text
~/Library/Application Support/EmberMug/status.json
~/Library/Application Support/EmberMug/command.json
```

`status.json` contains information such as the current target, battery percentage, charging state, and whether the mug is connected.

`command.json` is how another local program asks the app to change something.

That gives the project this shape:

```text
Stream Deck dial
      ↓
this plugin
      ↓
command.json
      ↓
Ember menu-bar app
      ↓
existing Bluetooth connection
      ↓
Ember Mug
```

The Stream Deck plugin does not open its own Bluetooth connection. It asks the already-running app to do the Bluetooth work.

## Why this is a plugin instead of a basic macro

A normal macro is good at performing a fixed list of steps. This dial has to respond to several live events:

- clockwise rotation;
- counterclockwise rotation;
- pressing the dial;
- battery changes;
- charging changes;
- the mug going online or offline.

Stream Deck calls the dial and its section of the touch screen an **encoder**. The plugin listens for encoder events, keeps track of the selected preset, writes commands for the Ember app, and sends updated text back to the touch screen.

One early version used the mug's reported target as the starting point for every dial tick. Bluetooth status updates arrive a little later than the physical turn, so fast turns could make the preset sequence jump backward. I fixed that by letting the plugin remember the selected preset immediately and use the mug's status as confirmation afterward.

## What the dial does now

Rotation moves through:

```text
120°F ↔ 130°F ↔ 140°F ↔ 145°F
```

Pressing toggles:

```text
heating on → off
heating off → 145°F
```

The display is intentionally simple:

```text
EMBER · 78% ⚡
      145°F
```

The lightning bolt appears while the mug is charging. I originally had a mug icon on the display, but I did not like how it looked, so I replaced the built-in icon layout with a clean text-only layout.

## Project files

```text
com.embermug.dial.sdPlugin/
├── manifest.json
├── plugin.py
├── layouts/
│   └── temperature.json
└── imgs/
    └── ember.svg
```

- `manifest.json` tells Stream Deck the plugin's name, ID, supported controls, entry point, and display layout.
- `plugin.py` listens for dial events, reads mug status, writes commands, and updates the display.
- `layouts/temperature.json` defines the text-only touch-screen layout.
- `imgs/ember.svg` is used in Stream Deck's action list, not on the physical dial display.

## Requirements

- macOS 12 or newer
- Elgato Stream Deck software 6.5 or newer
- A Stream Deck model with a dial and touch strip
- The Ember Mug menu-bar app installed and running
- The mug paired with the Mac and available over Bluetooth

## Installing the plugin

Copy the plugin bundle into Stream Deck's local plugin directory:

```bash
cp -R com.embermug.dial.sdPlugin \
  "$HOME/Library/Application Support/com.elgato.StreamDeck/Plugins/"
```

Then fully quit and reopen Stream Deck. Find **Ember Mug** in the action list and drag **Ember Temperature** onto a dial.

The plugin expects the Ember menu-bar app to keep its support files in:

```text
~/Library/Application Support/EmberMug/
```

## What I learned

The biggest thing I learned is that “control my mug with a dial” is not one problem. It is a chain of smaller problems:

1. Can Python see the mug?
2. Can it connect and change the temperature?
3. Can something keep that connection reliable?
4. Can another program send commands to it?
5. Can Stream Deck turn dial movements into those commands?
6. Can the screen show useful live information without looking cluttered?

I also learned that asking basic questions was useful. Questions like “what exactly is `.venv`?”, “why do we need to build an app?”, and “is this what Home Assistant does?” were not side trips. They helped me understand why every layer exists.

The finished control feels simple because all of those layers are doing their jobs underneath it. I turn the dial, the number changes, and the mug follows.

## A note about Ember

This is an unofficial personal project. It is not affiliated with or endorsed by Ember. It relies on the reverse-engineered Bluetooth work in `python-ember-mug` and the local control interface provided by `ember-mug-app`.

# 04 · The dial shows where my drink is going

October 12, 2026

The dial could set a temperature, but it could not tell me how close my coffee was to it. I wanted both numbers on the screen: where the drink is now, and where it is headed.

I built most of this at the end of September, right after the reconnect work. Then it sat on my Mac for two weeks, finished and unpublished. But exam week is behind me now!! and I'm ready to play around again!

## Two numbers and an arrow

The old screen only knew the target:

```text
EMBER · 78% ⚡
      145°F
```

The new one shows the trip:

```text
HEATING · 78% ⚡
  120°  ▲  145°
```

To make room, the word EMBER left the top line. I kind of liked seeing it there, but that line is more useful when it says what the mug is doing right now.

The bottom row looks like one line of text. It is really three separate text boxes in `layouts/temperature.json`: the current temperature, the arrow, and the target. A Stream Deck text box can only be one color, and I wanted the arrow to have its own.

The big single number from before is still there too, sitting underneath on its own layer. The plugin switches between the two views by turning boxes on and off:

```text
big number  → OFF, the target while I am turning, or "at target"
three boxes → current, arrow, target
```

## The arrow took a few tries

The first arrow was way too big. It crowded the numbers, so it got its own smaller font size.

Then the colors. I started with green for up and red for down. Then cooling became blue, which I liked right away. Then heating became red. Red means getting warmer, blue means getting cooler, and I should have started there.

## Taking things off the screen

Most of the work after that was removing things.

- **At the target.** If the drink is already at the number I want, an arrow has nothing to say. The screen shows the one number with a check mark for three seconds, then just the number.
- **While I turn the dial.** The arrow and the current temperature were distracting while I was still choosing. Now the screen shows only the target, big, and goes back to the comparison 1.5 seconds after the last tick.
- **When heating is off.** The first version showed a blue arrow pointing down at OFF. But OFF is a mode, not a temperature. Now it just says OFF.
- **The background.** I tried a deep espresso brown with a warm glow behind the numbers. It did not look right on the strip, so the background stayed black.

The 1.5 second preview needed one change I did not expect. The plugin only woke up once a second if nothing was happening, so the preview could hang around late. It now checks four times a second.

## The labels that flickered

Right after I turned heating on, the top line would flash something like this before settling:

```text
WARM (NO CONTROL) · 78%
```

Sometimes it said `UNKNOWN` instead. Neither one is wrong, exactly. The Ember app just needs a moment to catch up after a change, and the dial was repeating whatever it said.

So the plugin fills in that gap itself. `UNKNOWN` shows as CONNECTED, because the Bluetooth connection is real by then. And if heating is on but the label still says no control, the plugin compares the two temperatures and shows HEATING, COOLING or PERFECT.

## Icons, because the validator said so

I ran Elgato's command line validator on the plugin for the first time. It wanted PNG icons in exact sizes, and all I had was one SVG:

```text
plugin icon    256 × 256 and 512 × 512
category icon   28 × 28  and  56 × 56
```

The category icon is what gives Ember Mug its own group in Stream Deck's action list. With those in, the validator passed, and the version went up to 1.1.

## Did it work?

I tested it on the real mug:

1. Turned to 120°F. The screen showed the big 120°, then the comparison with a red arrow.
2. Pressed for OFF, then pressed again for 145°F.
3. Held the dial to reconnect and watched RECONNECTING turn into CONNECTED.
4. Waited for the check mark at the target.
5. Turned back down to 120°F and got the blue arrow.

The tests grew from 50 to 138. Three of those cases are from today. Reading the code back before publishing, I found that the reconnect wait would accept any "connected" value that was not empty, including the text `"false"`. I had already made the normal display require a real `true`, and then missed the same check further down the same file.

## What I learned

A screen this small gets better every time I take something off it. Every fix in this entry, except the icons, was about showing less.

## What is next

- remove the remaining mug artwork from the action;
- create a GIF-style coffee cup with animated steam;
- make the cup's fill level match the mug's reported liquid level;
- replace the reconnect `…` with a small image or animation.

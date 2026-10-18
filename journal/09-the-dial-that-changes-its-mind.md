# 09 · The dial that changes its mind

October 17, 2026

I turn the dial to 130°. A few seconds later it says 120° again. No message, no hint. It looked like the dial had second thoughts.

## Is the app slow, or is it ignoring me?

Before changing anything I wanted to know which one it was, so I read how my Ember app picks up `command.json`:

```text
every second, while connected: read command.json → delete it → send it to the mug
mug not connected:             read it, delete it, log "Ignoring set_temp: mug not connected"
app closed or in setup:        never reads it
```

So when it works, the command is applied within about a second and `status.json` follows soon after. Three seconds is a fair wait. The dial changing its mind only happens when the command really didn't land: the mug is offline, the app is closed, or the setup window has Bluetooth. (An offline mug throws the command away, it does not save it for later. I had worried about that one.)

That settles it. The wait is fine, the silence isn't.

## What the dial does now

```text
turn or press → remember what I asked for
at the first refresh after 3 seconds (so 3 to 5), status.json agrees → nothing, as before
at the first refresh after 3 seconds, status.json disagrees → NOT APPLIED / TRY AGAIN for a few seconds, then the real number
```

The same check runs for the press that switches heating off. A new turn clears the message, and starts from the real number, because that's what the mug is actually set to.

I changed the test from entry 06. It used to pin `120° → 130° → 130° → 120°` and say "this will be replaced". It got replaced. 🎉

## Two catches

If the app is slower than five seconds, I'd see NOT APPLIED and then the dial would catch up and flip to the new number. I haven't seen the app take that long. Also, switching pages before the check cancels it, so coming back never shows a verdict on an old command.

The other one: if the app is closed, `command.json` just sits there. When I open the app later it will find the old command and run it. I haven't touched that, because the plugin can't see it from its side. Keeping it in mind for later.

164 tests pass, plus the one expected failure from entry 06. I didn't run this one on the real dial, since turning it would really write a command to my mug.

## What I learned

When a thing looks flaky, check whether it's slow or ignored before adding a wait. Here the answer was "ignored, and silently", so the fix is to say so.

## What's next

- Tidy the repeated bits of the plugin (hidden fields, redraws), if it still bothers me
- Copy the new version into Stream Deck

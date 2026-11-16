# 12 · A key for leaving it alone

October 27, 2026

Every key I'd made so far was about using the mug. This one is for when I'm not. When my coffee's done, I want the Ember app gone and the mug left alone, without digging through the menu bar for it.

> How do you turn off an app that has no off switch?

## The app doesn't take orders to quit

My plugin talks to the Ember menu-bar app through `command.json`: write a temperature in, the app sends it to the mug. There's no "quit" in there. The app only quits from its own menu.

But its launcher already knows how to stop it. Before it starts a fresh copy, it finds the running script by name and kills it. So the App key does the same thing:

```text
press ─→ pkill -f ember_mug_app.py ─→ wait for it to go ─→ delete status.json
```

That last step matters. The app's own Quit deletes `status.json` on the way out. If I skipped it, the dial would keep reading the last thing the app wrote and act like nothing happened. A ghost mug.

Press it again and the key runs the launcher, same as holding the dial to reconnect.

## Off is not Offline

Once quitting was a thing I did on purpose, the keys needed two different words:

- **Off**: no `status.json` at all. I quit the app. Nothing's wrong.
- **Offline**: the app is running but lost the mug.

The App key itself says **On** when the app is running (pink once it's connected to the mug, white until then), and gray **Off** when it's quit.

## The mug that went quiet

This one surprised me. The plugin used to call the mug Offline if `status.json` hadn't changed in five minutes. Except the app only rewrites that file when something changes. A mug sitting happily at 140° doesn't change. So a perfectly good mug, doing exactly its job, got called Offline for it. Rude.

Now an old file only means trouble if the app that wrote it is gone. The plugin asks `pgrep` whether the app is still running, and keeps that answer for 30 seconds so it isn't asking every two seconds. If `pgrep` itself fails, it guesses "not running", which is the safe wrong answer: a press then opens the app, and the launcher replaces any copy that was secretly running anyway.

## Press or hold

On/Off and App both do two things now. A press does the main job. A hold reconnects. The catch is you can't know which one it is when the key goes down, only when it comes back up. So these two keys wait for me to let go before doing anything, same as the dial already did.

And holding never quits. Holding On/Off only ever opens or reconnects. The App key is the one and only way the deck turns the app off, so I can't do it by accident while reaching for something else.

## Where it lives

- XL page: the end of the top row, after the four temps.
- + page: right next to On/Off.

11 new tests cover it, including quitting, opening, the 30 second check and "a hold never quits". The whole suite is 251 passing.

## What I learned

"Hasn't changed" and "isn't there" are not the same thing. A quiet file can mean everything is fine.

## What's next

- Live with it for a while and see whether I actually reach for the App key, or just leave the mug on like I always have. (Probably the second one.)

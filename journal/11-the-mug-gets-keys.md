# 11 · The mug gets keys

October 27, 2026

I wanted my mug in Stream Deck's Action Wheel: one dial that scrolls through a list of actions, push to run the one showing. My Ember dial wouldn't go in. Stream Deck just wouldn't take it.

> Why can't I drop my mug into the Action Wheel?

## The wheel is a key thing

My first guess was the Dial Stack, which I already use for my Audio Dial. Wrong feature. The Dial Stack stacks dials. The Action Wheel stacks *keys*, and Stream Deck says so right in its own files:

```text
"Name": "Action Wheel",
"Tooltip": "Assign multiple actions to a dial, then rotate to cycle through them",
"Rotate": "Navigate actions",
"Push": "Execute action"
```

My manifest only ever said `"Controllers": ["Encoder"]`, which is Elgato for "this goes on a dial". So the mug was never allowed in. Not a bug, just me never telling Stream Deck the mug could be a key.

## Two keys for the wheel, one more for the XL

- **Ember On/Off** shows the drink's temperature right now, pink while heating. Press works like pushing the dial: off, or back on at 145°F.
- **Ember Set Temp** shows one temperature and sets the mug to it. Which one comes from its settings page, picked from the same four the dial uses. It turns pink when the mug is set to it.
- **Ember Page** goes on my XL's Home page. It shows the temperature, and pressing it opens a whole Ember page on the XL, the same trick my Apple Music art key does.

The part I like most: the dial and the keys share one choice. Press 130° on the XL and the dial on my + jumps to 130°F. Turn the dial to 140 and the 140° key lights up pink. It's one mug, so it should be one answer.

```text
dial turn ─┐
key press ─┼─→ the temp I picked ─→ command.json ─→ Ember app ─→ mug
           └─→ every dial and key redraws from it
```

## The XL page

A plugin can ship its own profile (a page of keys), so I wrote `scripts/profiles.py` to build one: Home top left, On/Off, then 120, 130, 140, 145 along the top. My Mac Vitals and Apple Music plugins already do this in JavaScript. This plugin is Python, so the script is too.

Stream Deck's checker didn't like my first try:

```text
Profiles[0] must not contain property: AutoInstall
```

I'd copied that line from my newer plugins, which are on a newer version of Elgato's kit. This one's on the old version, which has no such setting. Out it went. Then I opened the profile file by hand, Stream Deck imported it, and my XL flipped straight to the new page. Every key said **Offline**, which was the truth: the Ember app wasn't running. Honest keys. 🎉

## Testing without touching my coffee

My pretend Stream Deck runs the real plugin, and a press in it is a real press. That's fine for lights. It's less fine when the thing on the other end is a hot drink. So I gave the test run a fake home folder with a made-up `status.json`, and the plugin wrote its commands into the fake folder instead of sending them to my mug.

## Then I actually used them

Three things bugged me within minutes.

**Off wasn't off.** With heating off, On/Off showed the drink's temperature going down, 118°, 117°, 116°, like a sad little countdown. I don't need that. Off is off. So both On/Off and Ember Page now show the sleeping mug from the dial whenever heating is off. The dial changed too: it used to show the mug only when the mug was offline *and* off. Now it's any time heating is off, connected or not. The battery line goes with it, which I'm fine with. I can see the battery when I'm actually drinking.

**Set Temp didn't say anything.** I pressed 140° and nothing changed, because the mug was offline and the command went nowhere. Now the label says **Sent** right away, and when the mug reports the new temperature, the number turns pink. If the mug never takes it, the label says **Not applied** for three seconds, at the same moment the dial gives up (that's the check I built in entry 09, finally useful twice).

**Ember Page on the + showed a warning triangle.** That was on purpose, since there was no page for the +. So I made one: On/Off top left, Home top right, the four temps underneath, and the Ember dial on the first dial. The script builds both pages now, and the key opens whichever one fits the deck it's on.

## The dial had a trap in it

Stream Deck tells the plugin when the dial turns while it's held down (a `pressed` flag on the turn). My plugin never looked at it. So a push-and-turn counted as a normal turn, and then letting go counted as a press, which turns heating off. Hold it a little too long while turning and letting go started a reconnect instead. I could set 140° and switch the mug off in the same move. Very efficient. Very wrong.

Now push-and-turn is its own thing: one degree per click, anywhere from 120° to 145°. Letting go after it doesn't count as anything.

That broke something I didn't expect. The plugin used to round whatever the mug reported to the nearest preset, so 137° would quietly turn into 140° three seconds later, and the dial would call my perfectly good command "not applied". Now the dial keeps the mug's own degree, and a plain turn from 137° goes to the next preset that way.

## Too many pictures

Then my decks started feeling sluggish. I counted instead of guessing: a fast push-and-turn, 20 clicks in a second, sent 43 pictures. Stream Deck takes about ten a second for a whole plugin. I'd already learned this on my Apple Music Volume dial (it's in my wiki), and then built the exact same flood again. (Yes, again.)

Same fix as last time. The turned dial draws at most ten times a second and always ends on the newest number. The keys sit the spin out and catch up once when it stops. A strip that hasn't changed isn't sent again. Same spin now: 7 pictures. Every click still reaches the mug.

## What I learned

Before building a workaround, read what the app says about itself. The answer was sitting in a JSON file inside Stream Deck the whole time.

And a key that changes something has to say so. Silence reads as broken, even when it's working.

## What's next

- Put Ember Page on my XL Home page

Update, same night: On/Off and Set Temp went into an Action Wheel on my +, right after my Apple Music keys, and they work. Set Temp said **Sent**, and pushing the dial on On/Off turned the mug off, which is exactly what On/Off is for. The wheel remembers which slot it's on, so a push does whatever key is showing.

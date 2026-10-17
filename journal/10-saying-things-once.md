# 10 · Saying things once

October 17, 2026

A small tidy-up, and I stopped on purpose where the next one would have started a bigger job.

## Two things written out over and over

**The "everything off" block.** Every screen on the dial starts from the same five fields (big number, current temperature, arrow, target, sleeping mug) all switched off, then turns on what it needs. That block was typed out three times: in the temperature screen, in the short message screen and in the sleeping mug. Entry 05 already taught me what happens when one copy gets forgotten (the mug naps over my temperatures).

Now it's `hidden_fields()` and each screen starts from it. Before touching anything I wrote one test that spells out the exact payload of each of the three screens, so I'd know if a key or a value moved by even a letter.

**The "draw this on every dial" loop.** This one was seven copies of:

```python
for context in list(contexts):
    feedback(ws, context, ...)
```

Seven, not the five I'd counted, because entries 08 and 09 added a couple. It's `redraw_all()` now. It takes the same arguments `feedback()` takes, or `message=(title, value)` for the short text screens. A second test checks that NOT APPLIED reaches both dials when two are showing.

## Where I stopped

`main()` is still about 190 lines with a pile of timers in it, and the obvious next move is splitting it into one function per event. I'm not doing that now. It works, 166 tests are watching it, and it is the kind of change that is fun to start and annoying to finish. If I ever have to change how two timers interact, I'll split it then, with the tests already there to catch me.

## What I learned

Counting the copies before fixing them was worth it: the number was already wrong by the time I got to them.

166 tests pass, plus the one expected failure from entry 06.

## What's next

- Copy the new version into Stream Deck and restart the plugin
- Fix the half-read message (the expected failure in the tests) only if it ever really bites

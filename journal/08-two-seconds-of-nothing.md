# 08 · Two seconds of nothing

October 17, 2026

The plugin does not spin. It sleeps on the socket and wakes up four times a second to see if anything needs doing. But when I looked at what it did on every second wake-up, it was reading `status.json` even when there was no dial on screen to draw on.

## Slice 1: nobody's watching

Every two seconds the plugin reads the mug's status file and redraws. If I'm on another Stream Deck page, there is no dial to redraw, so that read was for no one. Now the refresh checks first:

```text
no dial on screen → skip the read, try again in 2 seconds
dial comes back   → willAppear reads and draws straight away
```

The test runs a few ticks with no dial and counts the reads: zero. (Before the change it counted one per tick. The computer, diligently reading to an empty room.)

## Slice 2: one read, not four

With a dial on screen, a single tick read the file more than once: once for the checkmark logic, once for the selection sync, and once more inside every dial's redraw. With two dials that was four reads of a file that cannot change between them.

Now the tick reads once and hands the same snapshot to each piece. `feedback`, `sync_selection` and `target_f` each got an optional `state` argument. If nobody passes one they read the file themselves, like before, so everything that called them the old way still works.

The tests count reads inside one tick, for one dial and for two, and for the other timer too (the one that ends the target preview).

## The honest part

This is a file about the size of a text message, read once every two seconds. It was never slow. I did it because the work was easy to see, and because it made the loop easier to follow, which is the part I'll actually feel later.

One side effect to remember: with no dial on screen the reconnect bookkeeping also pauses. If I hold a dial to reconnect and then switch pages, it picks up again when the dial returns. The 30 second limit still counts on the steady clock, so it can't get stuck. If the reconnect finished while I was away, the dial says CONNECTED for a few seconds when I come back. Cosmetic.

160 tests pass (plus the one expected failure from entry 06).

## What I learned

Changing a function's inputs is safest when the new input is optional. Old calls keep working, and only the new call sites need to know.

## What's next

- The dial that changes its mind

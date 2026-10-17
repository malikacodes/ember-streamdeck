# 07 · The plugin gets a diary

October 17, 2026

Stream Deck's own log kept saying this about my plugin, on and off for four days:

```text
Process stopped (unexpected) code=0x1
```

I couldn't tell if that was a crash or nothing, because my plugin never wrote anything down. Printed text just disappears when Stream Deck launches it.

## Where the exits came from

The ones I could match to a time were all when Stream Deck itself quit. When it quits it closes the socket my plugin listens on, and my plugin treated that as an error (`EOFError`) and died with exit code 1. So the plugin did the right thing and made it look like a disaster. Very dramatic for a goodbye.

## What I changed

- A log file at `~/Library/Logs/EmberMug/plugin.log`. It rotates at about 200 KB and keeps one old copy, so it can't quietly eat my disk.
- A real crash writes its full traceback there and still exits 1.
- A closed socket (polite, or an abrupt reset or broken pipe) writes one line (`Stream Deck closed the connection, exiting`) and exits 0.
- A failed write to `command.json` and a failed reconnect also log why, next to the dial message that says "TRY AGAIN".
- The two timings that were bare numbers in the loop, `2` and `3`, are now `STATUS_REFRESH_SECONDS` and `SELECTION_SETTLE_SECONDS`. Same numbers, so I can read the loop out loud.

If the log folder can't be made, the plugin runs without a log. A diary shouldn't be able to stop the show.

## Did it work?

I made a little stand-in for Stream Deck, put one dial on it, then closed the socket the way a quit would.

```text
plugin exit code 0
2026-10-17 15:37:10 INFO Stream Deck closed the connection, exiting
```

The stand-in is mine because my pretend deck starts plugins with Node and this one is Python. 155 tests pass, and the new ones check the exit codes, the traceback, a blocked log folder, a failed command write and the two names.

## What I learned

An error that fires every time I quit an app is not an error, it's a goodbye. I only found that out because the dates lined up with my quits, and I'd rather not depend on that. Now the log says it for me.

## What's next

- Stop doing the two second work when no dial is on screen
- The dial that changes its mind

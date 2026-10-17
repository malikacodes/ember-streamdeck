# 06 · Three things I was not sure about

October 17, 2026

Before touching the plugin again I went looking for what it does that I never actually checked. I found three, and wrote a test for each before changing anything.

## 1. No dial, no drawing

Stream Deck only lets me draw on a dial it has told me about (it sends `willAppear` when one shows up). I believed that, but nothing proved it. Now a test runs a few quiet ticks with no dial on screen and checks that the only thing the plugin ever sends is its own registration. It is a light test: with no dial there is nobody to send to. Boring, and exactly what I want.

## 2. A frame cut in half

The plugin waits a quarter of a second for each read from Stream Deck. If that quarter second ends in the middle of a message, the bytes I already read get thrown away and the next read starts from the middle of the message. Think of reading a sentence, getting interrupted, and picking the book back up at the next word.

```text
header (2 bytes) → read fine
payload          → timeout
next read        → starts inside the payload, so it looks like a header
```

It lives on my own computer, over a loopback connection, so a message almost never gets split. I did not fix it. One test pins the lossy behaviour as it is today, and a second one describes what I want (finish the same message on the next read). That second one is marked as an expected failure, so it will tell me loudly the day I fix it.

## 3. The dial that changes its mind

I turn the dial to 130°. The Ember app is supposed to apply that. If it doesn't, `status.json` keeps saying 120°, and a few seconds later (the first refresh after three seconds) the dial snaps back to 120° with no explanation. The test walks through it:

```text
120° → 130° → 130° → 120°
```

That last step is the problem. It looks like the dial changed its mind. This test pins today's behaviour and will be replaced when the dial learns to say the command did not land.

## What I learned

Writing a test for something I think I know is a good way to find out I don't. Two of the three were things I assumed. 148 tests pass, plus the one expected failure.

## What's next

- A log file, so errors have somewhere to go
- Skipping the two second work when no dial is on screen
- Dealing with the dial that changes its mind

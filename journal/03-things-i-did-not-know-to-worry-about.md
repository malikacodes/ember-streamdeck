# 03 · The things I did not know to worry about

September 28, 2026

Today was about making the dial more reliable, and wow, the “unknown unknowns” showed up fast. I knew a mug could go offline. I had not really thought through all the ways the information about that mug could be wrong before it even reached my screen.

What if `status.json` contains malformed JSON? Or a list instead of an object? What if the temperature says “hot,” the battery is not a real number, or an old status file still insists the mug is connected? That last one was especially frustrating. A saved “connected” message is not proof that anything is connected right now! I added checks for usable numbers and real boolean values, and a five-minute freshness check so stale status does not keep pretending the mug is online.

Then I learned that a WebSocket message can arrive in pieces. Even its tiny two-byte header can be split across reads. And if the connection ends halfway through a message, waiting for bytes that will never arrive can leave the plugin stuck. Now it reads the bytes it needs and detects when the connection ends early.

The command file had its own surprises. Writing a temperature command can fail because of file permissions, and I do not want one failed write to take down the whole dial or make it look like the temperature changed. I added “COMMAND FAILED” feedback and kept the previous selection when the write fails. I also had to consider something embarrassingly easy to overlook: what if `Ember Mug.app` is missing? A long press now checks for the app, with feedback for a missing app or a launch failure.

I learned another timing detail too: checking the age of saved status needs the clock, but measuring a hold or a reconnect timeout needs a timer that keeps moving steadily even if the system clock changes. Those are different jobs!

Honestly, this part was frustrating. Every time I thought I had covered the weird cases, another “but what happens if…” appeared. But turning those questions into tests felt so good. I reached **50 focused reliability tests**! Malformed and stale status, interrupted messages, permission failures, and a missing app all became things I could deliberately check instead of surprises I had to discover while trying to enjoy my coffee.

The exciting part is that I am starting to see reliability as part of the experience. I want the dial to tell me what actually happened, even when something goes wrong. Getting this little mug controller to handle the messy parts feels like a real win!

## What is next

The running list still has some fun visual work ahead:

- remove the remaining mug artwork from the action;
- create a GIF-style coffee cup with animated steam;
- make the cup's fill level match the mug's reported liquid level;
- replace the reconnect `…` with a small image or animation;
- verify the documented macOS requirement;
- increase the plugin version when the new feature set is ready.

The project keeps getting more interesting. I started by wondering whether I could change a mug temperature from Stream Deck. Now I am thinking about state machines, background processes, Bluetooth permissions, event timing, interface feedback, and how to make a tiny screen feel clear and alive.

That is exactly the kind of learning I wanted from this!

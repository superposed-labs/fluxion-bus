# Hero animation

Source for the README / website "hero" video: a 28-second loop
staged as one continuous camera move, with short reading holds where the
playback slows to a near-stop. Kinetic type opens it ("Stay in your agent." / "Delegate to
Codex"), then a 3D scene shows Claude Code splitting a task between a Codex and an
Antigravity sub-agent that run in parallel over MCP, and the changes come back
reviewable and revertible. The
macOS notch then walks through its states as the app draws them: the collapsed
ring-gauge strip, the peek callout pointing at Codex and then Antigravity, quota rings
filling one by one, a Codex window running dry and resetting (with the app's
quota-reset notification and Auto-Ping), and tokens used today. The reset banner then
flies off the Mac into a phone chat, and a task is sent from that chat (Slack, Telegram, WeChat, LINE, QQ, Feishu) and the Mac
replies. The bot's avatar becomes the logo of the closing card, which shows the
install command. The video opens on that same card before pushing into the
opening title, so the first frame works as the poster GitHub shows before play,
and the loop has no seam.

It is a single self-contained file with **no dependencies and no build step**.

> **Illustrative, not a live product.** Everything on screen is fake, staged
> data for explanation. No real tokens, accounts, emails, paths, or user data
> appear, and it does not claim to be a live recording of the product.

## Preview

Open [`hero-demo.html`](hero-demo.html) directly in a browser.

- **Space** pauses, **R** restarts, **H** hides the on-screen controls.
- `?t=6.5` freezes the animation at that time, which is handy for checking a frame.
- Honors `prefers-reduced-motion` by rendering one static frame.

The animation is a pure function of time: `window.__render(t)` sets every
element from `t` alone (no CSS transitions, no timers). Camera moves are
keyframes in the `CAMB` (3D scene) and `CAMC` (notch) tables. Scene timings are
story time; the `HOLDS` table lists where playback slows down and for how long (and
`SKIPS` a still beat it jumps over),
and `?t=` takes playback time. The hand-off from
the 3D scene to the desktop is exact: `lockB` derives scene B's camera from scene
C's, so the chat window lands on the window parked on the desktop. The scene
timings are listed in the comment at the top of the file.

## Export (optional)

Rendering is **optional tooling**. You only need it to regenerate the published
asset. It is intentionally *not* wired into the project's build or CI, and
Playwright is **not** a root dev dependency.

```bash
# one-time, only if you're regenerating the asset
brew install ffmpeg node
npm i -D playwright && npx playwright install chromium

./export.sh          # → docs/hero/out/hero.mp4 (1920×1080, 60fps, motion blur)
./export.sh 60 1     # fast draft without motion blur
```

The script steps headless Chromium through every frame and pipes the
screenshots into ffmpeg, so there are no dropped frames and every export is
identical. Motion blur comes from rendering three sub-frames per frame and
averaging them. The timeline is split across parallel workers (one headless
Chromium each, `JOBS=n` to override) and the parts are concatenated, so a full
render takes about 9 minutes on an 8-core Mac.

## What to commit

- **Commit** the source: `hero-demo.html`, `export.sh`, and this `README.md`.
- **Do not commit** generated renders. `out/` is gitignored. Publish the
  `mp4` as a GitHub asset (drag-drop it into a README edit on github.com to get
  a `user-attachments` URL) and put that URL in `README.md`, `README.zh-CN.md`,
  and `README.ja.md`.

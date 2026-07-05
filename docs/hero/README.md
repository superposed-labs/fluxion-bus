# Hero animation

Source for the README / website "hero" demo — a short, looping animation that
shows Fluxion's core story in one glance: you stay in your own agent, it
delegates a scoped task to another provider over MCP, changed files come back
reviewable, and cross-provider quota is monitored in the macOS notch.

It is a single self-contained file with **no dependencies and no build step**.

> **Illustrative, not a live product.** Everything on screen is fake, staged
> data for explanation. No real tokens, accounts, emails, paths, or user data
> appear, and it does not claim to be a live recording of the product.

## Preview

Open [`hero-demo.html`](hero-demo.html) directly in a browser.

- **Full** (website hero, ~20s loop): default.
- **Compact** (README-sized, ~10s loop): append `?variant=compact`.
- Controls: **R** replays from a clean start; **H** hides the chrome for a
  clean recording (hover the bottom edge to bring controls back).
- Honors `prefers-reduced-motion`: renders a single static frame, no loop.

## Export (optional)

Rendering to video/GIF is **optional tooling** — only needed when you want to
regenerate the published asset. It is intentionally *not* wired into the
project's build or CI, and Playwright is **not** a root dev dependency.

```bash
# one-time, only if you're regenerating the asset
brew install ffmpeg node
npm i -D playwright && npx playwright install chromium

./export.sh          # → docs/hero/out/{hero.mp4,hero.webm,hero.gif,…}
```

The script records both variants headlessly and transcodes them, printing each
file's size plus ready-to-paste embed snippets.

## What to commit

- **Commit** the source: `hero-demo.html`, `export.sh`, this `README.md`.
- **Do not commit** generated renders — `out/` is gitignored. Publish the final
  `mp4`/`gif` as a GitHub asset (drag-drop into the README on github.com for a
  `user-attachments` URL, or attach to a Release) and reference that URL, e.g.:

  ```html
  <video src="…/hero.mp4" autoplay muted loop playsinline width="760"></video>
  ```

Keeping the source in-repo lets contributors preview, tweak, and re-export the
demo as the product evolves; keeping the binaries out keeps git history lean.

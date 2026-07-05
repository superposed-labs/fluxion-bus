#!/usr/bin/env bash
# export.sh — render hero-demo.html to shareable video/gif.
#
#   Website hero : out/hero.mp4  +  out/hero.webm   (full variant, ~21s loop)
#   README       : out/hero.gif                     (compact variant, ~11s loop)
#
# Records the animation with headless Chromium (Playwright), then transcodes
# with ffmpeg. Deterministic: each variant is restarted (R) and captured for
# exactly one loop with the on-screen controls hidden.
#
# One-time setup:
#   brew install ffmpeg node          # or your platform's equivalent
#   npm i -D playwright && npx playwright install chromium
#
# Usage:  ./export.sh        (run from anywhere; paths are resolved to this dir)
set -euo pipefail
cd "$(dirname "$0")"

SRC="file://$PWD/hero-demo.html"
OUT="$PWD/out"
mkdir -p "$OUT"

# ── dependency checks ────────────────────────────────────────────────
command -v ffmpeg >/dev/null || { echo "✗ ffmpeg not found → brew install ffmpeg"; exit 1; }
command -v node   >/dev/null || { echo "✗ node not found → install Node.js"; exit 1; }
node -e "require('playwright')" 2>/dev/null || {
  echo "✗ playwright not found → npm i -D playwright && npx playwright install chromium"; exit 1; }

# ── 1) record both variants to webm ──────────────────────────────────
# Bump W/H (and the ffmpeg scale below) for a higher-res master.
node - "$SRC" "$OUT" <<'JS'
const { chromium } = require('playwright');
const [src, out] = process.argv.slice(2);
const jobs = [
  { name: 'full',    url: src,                     w: 1200, h: 640, ms: 17500 },
  { name: 'compact', url: src + '?variant=compact', w: 1200, h: 500, ms: 9000 },
];
(async () => {
  const browser = await chromium.launch();
  for (const j of jobs) {
    const ctx = await browser.newContext({
      viewport: { width: j.w * 2, height: j.h * 2 },
      deviceScaleFactor: 2,
      recordVideo: { dir: out, size: { width: j.w * 2, height: j.h * 2 } },
    });
    const page = await ctx.newPage();
    await page.goto(j.url, { waitUntil: 'load' });
    await page.addStyleTag({ content: '.controls{display:none !important} .stage{transform: scale(2); transform-origin: center;}' });
    await page.evaluate(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'r' }));
    });
    const video = page.video();
    await page.waitForTimeout(j.ms);
    await ctx.close();                 // finalizes the .webm
    require('fs').renameSync(await video.path(), `${out}/hero-${j.name}.webm`);
    console.log('  recorded', j.name);
  }
  await browser.close();
})();
JS

# ── 2) transcode ─────────────────────────────────────────────────────
echo "→ mp4 (website hero)"
ffmpeg -y -loglevel error -i "$OUT/hero-full.webm" \
  -an -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart "$OUT/hero.mp4"

echo "→ webm (smaller alt for the site)"
ffmpeg -y -loglevel error -i "$OUT/hero-full.webm" \
  -an -c:v libvpx-vp9 -b:v 0 -crf 30 "$OUT/hero.webm"

# 2-pass palette gif (best size/quality for a mostly-dark UI)
mkgif() { # <src.webm> <fps> <width> <out.gif>
  ffmpeg -y -loglevel error -i "$1" \
    -vf "fps=$2,scale=$3:-1:flags=lanczos,palettegen=stats_mode=diff" "$OUT/palette.png"
  ffmpeg -y -loglevel error -i "$1" -i "$OUT/palette.png" \
    -lavfi "fps=$2,scale=$3:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3" "$4"
  rm -f "$OUT/palette.png"
}
echo "→ gif (README, FULL story, 12fps @ 720w — size-capped)"
mkgif "$OUT/hero-full.webm"    12 720 "$OUT/hero.gif"
echo "→ gif (short alt, compact, 15fps @ 960w)"
mkgif "$OUT/hero-compact.webm" 15 960 "$OUT/hero-compact.gif"

echo
echo "✓ done → $OUT"
ls -lh "$OUT"/hero.mp4 "$OUT"/hero.webm "$OUT"/hero.gif "$OUT"/hero-compact.gif 2>/dev/null | awk '{print "  "$9"  "$5}'
echo
echo "Pick a README embed by the sizes above:"
echo "  • video (recommended, tiny even for the full 21s — upload as a GitHub asset):"
echo "        <video src=\"…/hero.mp4\" autoplay muted loop playsinline width=\"760\"></video>"
echo "  • full gif (works via relative path everywhere): docs/hero/out/hero.gif"
echo "  • if hero.gif is too big: use hero-compact.gif, or drop 12→10fps / 720→640w above."

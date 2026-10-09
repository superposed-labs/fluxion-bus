#!/usr/bin/env bash
# export.sh — render hero-demo.html to out/hero.mp4 (1920×1080, 60fps, motion blur).
#
# hero-demo.html is a pure function of time (window.__render(t)), so this steps
# headless Chromium through every frame and pipes screenshots into ffmpeg.
# Nothing is recorded in real time, so there are no dropped frames and every
# export is identical. Motion blur comes from rendering SUB sub-frames across
# a 180° shutter (half a frame) and averaging them.
#
# One-time setup (kept out of the root project; node_modules/ is gitignored):
#   brew install ffmpeg node
#   npm i -D playwright && npx playwright install chromium
#
# Usage:  ./export.sh [fps] [sub]  (defaults 60 and 3; sub=1 is a fast draft)
#         JOBS=4 ./export.sh       (parallel workers; default = performance cores − 2)
set -euo pipefail
cd "$(dirname "$0")"

FPS="${1:-60}"
SUB="${2:-3}"
SRC="file://$PWD/hero-demo.html?export=1"
OUT="$PWD/out"
mkdir -p "$OUT"

command -v ffmpeg >/dev/null || { echo "✗ ffmpeg not found → brew install ffmpeg"; exit 1; }
command -v node   >/dev/null || { echo "✗ node not found → install Node.js"; exit 1; }
node -e "require('playwright')" 2>/dev/null || {
  echo "✗ playwright not found → npm i -D playwright && npx playwright install chromium"; exit 1; }

# Split the timeline into JOBS contiguous chunks and render them in parallel,
# one headless Chromium + ffmpeg per chunk, then concatenate the encoded parts.
# Each output frame depends only on its own sub-frames, so chunk seams are exact.
JOBS="${JOBS:-$(( $(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4) - 2 ))}"
(( JOBS < 1 )) && JOBS=1
TMP="$OUT/.parts"
rm -rf "$TMP"; mkdir -p "$TMP"
trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$TMP"' EXIT

# playback length (story time plus the reading holds), as the page reports it
TOTAL="$(node - "$SRC" "$FPS" <<'JS'
const { chromium } = require('playwright');
const [src, fps] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(src, { waitUntil: 'load' });
  await page.evaluate(() => window.__ready);
  console.log(Math.round(await page.evaluate(() => window.__DUR) * fps));
  await browser.close();
})();
JS
)"
echo "→ rendering ${TOTAL} frames at ${FPS}fps × ${SUB} sub-frames on ${JOBS} workers"

render_chunk() {  # $1 = first frame, $2 = end frame (exclusive), $3 = output file
  node - "$SRC" "$FPS" "$SUB" "$1" "$2" <<'JS' | ffmpeg -y -loglevel error -f image2pipe -framerate "$((FPS * SUB))" -c:v png -i - \
    -vf "tmix=frames=${SUB},select='eq(mod(n\\,${SUB})\\,$((SUB - 1)))',setpts=N/(${FPS}*TB)" -r "$FPS" \
    -an -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p -tune animation "$3"
const { chromium } = require('playwright');
const [src, fps, sub, a, b] = process.argv.slice(2).map((v, i) => i ? +v : v);
(async () => {
  const browser = await chromium.launch();
  // 1280×720 stage at 1.5× device scale → 1920×1080 frames
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5 });
  await page.goto(src, { waitUntil: 'load' });
  await page.evaluate(() => window.__ready);
  for (let i = a; i < b; i++) {
    for (let j = 0; j < sub; j++) {       // sub-frames spread over half a frame
      await page.evaluate(t => window.__render(t), (i + .5 * j / sub) / fps);
      process.stdout.write(await page.screenshot({ type: 'png' }));
    }
  }
  await browser.close();
})();
JS
}

START=$SECONDS
: > "$TMP/list.txt"
for ((k = 0; k < JOBS; k++)); do
  a=$(( TOTAL * k / JOBS )); b=$(( TOTAL * (k + 1) / JOBS ))
  part="$TMP/part$k.mp4"
  echo "file '$part'" >> "$TMP/list.txt"
  render_chunk "$a" "$b" "$part" &
done
fail=0
for pid in $(jobs -p); do wait "$pid" || fail=1; done
(( fail )) && { echo "✗ a render worker failed"; exit 1; }

ffmpeg -y -loglevel error -f concat -safe 0 -i "$TMP/list.txt" -c copy -movflags +faststart "$OUT/hero.mp4"
echo "  rendered in $(( (SECONDS - START) / 60 ))m $(( (SECONDS - START) % 60 ))s"

echo "✓ $(ls -lh "$OUT/hero.mp4" | awk '{print $5}')  →  $OUT/hero.mp4"
echo "Publish it as a GitHub asset (drag-drop into a README edit on github.com)"
echo "and paste the resulting user-attachments URL into README.md / README.*.md."

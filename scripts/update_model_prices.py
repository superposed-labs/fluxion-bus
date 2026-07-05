#!/usr/bin/env python3
"""Prototype: refresh src/fluxion/usage/model_prices.json from official pricing.

This is the *human-in-the-loop* updater described in the design discussion — NOT
a daily auto-scraper. You run it occasionally; it fetches the official pricing
pages, pulls candidate prices, diffs them against the committed JSON, and prints
a report. It never writes the JSON: you eyeball the report and edit + commit by
hand (git history is the change log).

Why this shape
--------------
- Anthropic and Google publish prices in *server-rendered* docs pages, so a
  plain fetch gets the numbers. OpenAI's pricing page is a JS shell, so it needs
  a headless browser — left as a TODO below.
- The extraction step here is a rough heuristic. The robust version is to feed
  the cleaned page text to an LLM and have it return structured rows — see the
  "AI EXTRACTION HOOK" section. An LLM reads semantically, so it survives the
  page redesigns that break CSS-selector scrapers.
- A human reviews before merge, which is the safeguard against a bad extraction
  silently corrupting every cost number.

Usage:
  python scripts/update_model_prices.py            # fetch + report
  python scripts/update_model_prices.py --offline  # just audit the JSON (no network)
  python scripts/update_model_prices.py --snapshot-dir scratch/prices
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICES_PATH = REPO_ROOT / "src" / "fluxion" / "usage" / "model_prices.json"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Prefer the *docs* pricing pages — they're server-rendered, so prices are in
# the raw HTML. mode="browser" sources need rendering we don't do yet.
SOURCES = [
    {
        "provider": "claude",
        "name": "Anthropic docs",
        "url": "https://docs.claude.com/en/docs/about-claude/pricing",
        "mode": "fetch",
    },
    {
        "provider": "antigravity",
        "name": "Gemini docs",
        "url": "https://ai.google.dev/gemini-api/docs/pricing",
        "mode": "fetch",
    },
    {
        "provider": "codex",
        "name": "OpenAI API pricing",
        "url": "https://openai.com/api/pricing/",
        "mode": "browser",
    },
]

STALE_AFTER_DAYS = 180
MODEL_KEYWORDS = (
    "opus",
    "sonnet",
    "haiku",
    "gpt",
    "o1",
    "o3",
    "o4",
    "codex",
    "gemini",
    "flash",
    "pro",
    "nano",
    "mini",
    "fable",
)


def _fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


# ── AI EXTRACTION HOOK ───────────────────────────────────────────────
# Replace _extract_candidates with an LLM call for robust, layout-proof
# extraction. Sketch:
#
#   def _extract_candidates(text, provider):
#       prompt = ("Extract every model's API price from this pricing page as "
#                 "JSON rows {model, input_per_mtok, output_per_mtok, "
#                 "cache_write_per_mtok, cache_read_per_mtok}. Page:\n" + text)
#       return anthropic_or_openai_complete(prompt)   # -> list[dict]
#
# Keep the human review + manual merge regardless of how good extraction gets.
def _extract_candidates(text: str, provider: str) -> list[dict]:
    """Rough heuristic: pair each '$<num>' with the nearest model keyword in a
    small text window. Good enough to *flag* changes for a human; not good
    enough to trust blindly. This is the piece to swap for the AI hook above."""
    candidates: list[dict] = []
    for m in re.finditer(r"\$\s?(\d+(?:\.\d+)?)", text):
        price = float(m.group(1))
        if price <= 0 or price > 1000:
            continue  # skip plan prices / noise
        window = text[max(0, m.start() - 60) : m.end() + 20].lower()
        hit = next((k for k in MODEL_KEYWORDS if k in window), None)
        if hit:
            candidates.append(
                {
                    "keyword": hit,
                    "price": price,
                    "context": text[max(0, m.start() - 40) : m.end() + 12].strip(),
                }
            )
    # De-dup by (keyword, price), keep first context.
    seen: set = set()
    uniq = []
    for c in candidates:
        key = (c["keyword"], c["price"])
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _load_prices() -> dict:
    return json.loads(PRICES_PATH.read_text(encoding="utf-8"))


def _latest_effective(rate_list: list) -> str:
    return max((str(r.get("effective_date", "")) for r in rate_list), default="")


def _age_days(iso: str) -> int | None:
    try:
        return (date.today() - date.fromisoformat(iso)).days
    except ValueError:
        return None


def _audit_current(prices: dict) -> None:
    print("\n=== Committed rates (src/fluxion/usage/model_prices.json) ===")
    print(f"file updated_at: {prices.get('updated_at', '?')}\n")
    rows: list[tuple[str, str, dict, str]] = []
    for model, body in prices.get("models", {}).items():
        rows.append(
            ("model", model, body.get("rates", []), _latest_effective(body.get("rates", [])))
        )
    for fam, rl in prices.get("families", {}).items():
        rows.append(("family", fam, rl, _latest_effective(rl)))
    for prov, rl in prices.get("providers", {}).items():
        rows.append(("provider", prov, rl, _latest_effective(rl)))
    for kind, name, rl, eff in rows:
        latest = sorted(rl, key=lambda r: str(r.get("effective_date", "")))[-1] if rl else {}
        age = _age_days(eff)
        flag = "  ⚠ STALE" if (age is not None and age > STALE_AFTER_DAYS) else ""
        in_, out = latest.get("in", "?"), latest.get("out", "?")
        print(f"  [{kind:8}] {name:22} in ${in_:<7} out ${out:<7} eff {eff} ({age}d){flag}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--offline", action="store_true", help="Audit the JSON only; no network.")
    ap.add_argument(
        "--snapshot-dir",
        default="scratch/price-snapshots",
        help="Where to save fetched HTML/text snapshots.",
    )
    args = ap.parse_args()

    prices = _load_prices()
    _audit_current(prices)

    if args.offline:
        print("\n(offline) skipped fetching. Re-run without --offline to pull live prices.")
        return 0

    snap_dir = REPO_ROOT / args.snapshot_dir
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    print("\n=== Live pricing pages ===")
    for src in SOURCES:
        if src["mode"] == "browser":
            print(f"\n• {src['name']} ({src['url']})")
            print("  JS-rendered shell — needs a headless browser (Playwright). TODO.")
            continue
        print(f"\n• {src['name']} ({src['url']})")
        try:
            html = _fetch(src["url"])
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  fetch failed: {exc}")
            continue
        text = _to_text(html)
        (snap_dir / f"{src['provider']}-{stamp}.txt").write_text(text, encoding="utf-8")
        cands = _extract_candidates(text, src["provider"])
        print(
            f"  fetched {len(html):,} bytes · {len(cands)} price candidates "
            f"(snapshot: {args.snapshot_dir}/{src['provider']}-{stamp}.txt)"
        )
        for c in cands[:12]:
            print(f"      ${c['price']:<7} ~{c['keyword']:8} … {c['context'][:60]}")

    print("\n--- next steps -------------------------------------------------")
    print("  Candidates above are heuristic hints. Verify against the page,")
    print("  then hand-edit src/fluxion/usage/model_prices.json (bump effective_date")
    print("  + updated_at) and commit. Swap _extract_candidates for the AI hook")
    print("  for reliable extraction; add Playwright for the OpenAI page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

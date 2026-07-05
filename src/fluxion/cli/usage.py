"""`fluxion-usage` — print the model-quota usage snapshot as JSON.

The single data source for any usage UI (menu-bar widget, statusline, etc.):
it runs the same probes as the web console's /api/usage and emits the same
JSON shape, but without needing the web server running.
"""

from __future__ import annotations

import argparse
import json

from fluxion.config.settings import Settings
from fluxion.usage.collector import collect_usage_payload


def build_payload(*, force: bool = False, force_providers: list[str] | None = None) -> dict:
    settings = Settings.load()
    return collect_usage_payload(settings, force=force, force_providers=force_providers)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print Fluxion model-quota usage as JSON (same shape as /api/usage)."
    )
    parser.add_argument(
        "--force", action="store_true", help="Bypass the TTL cache and re-probe now."
    )
    parser.add_argument(
        "--force-provider", action="append", help="Bypass the TTL cache for a specific provider."
    )
    parser.add_argument("--indent", type=int, default=None, help="Pretty-print with this indent.")
    parser.add_argument(
        "--refresh-prices",
        action="store_true",
        help="Fetch the latest price tables from the shared repo into the local "
        "cache, then exit. The only command that touches the network for prices.",
    )
    args = parser.parse_args()
    if args.refresh_prices:
        from fluxion.usage import price_data

        results = price_data.refresh()
        print(json.dumps(results, indent=args.indent))
        raise SystemExit(0 if all(r["ok"] for r in results) else 1)
    print(
        json.dumps(
            build_payload(force=args.force, force_providers=args.force_provider), indent=args.indent
        )
    )


if __name__ == "__main__":
    main()

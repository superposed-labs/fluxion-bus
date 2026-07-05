from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from fluxion.availability import detect_all, initialize_env, write_snapshot
from fluxion.config.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect Fluxion executor and usage-provider availability."
    )
    parser.add_argument(
        "--initialize", action="store_true", help="Initialize missing first-launch settings."
    )
    args = parser.parse_args()

    settings = Settings.load()
    snapshot = detect_all(settings)
    write_snapshot(snapshot, settings.data_dir / "availability.json")
    if args.initialize:
        env_path = Path(os.environ.get("FLUXION_ENV_FILE", ".env")).expanduser()
        initialize_env(snapshot, env_path)
    print(json.dumps(snapshot, ensure_ascii=False))


if __name__ == "__main__":
    main()

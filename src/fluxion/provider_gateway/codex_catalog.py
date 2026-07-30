"""Staleness of a local Codex model catalog override.

Codex decides the sub-agent protocol from the model's own `multi_agent_version`.
A model declaring `v2` has its spawn payload encrypted for OpenAI, so a local
agent serving that sub-agent receives ciphertext instead of the delegated task —
see `codex_config.check_feature_conflicts` for why that fails silently. The only
local lever Codex offers is `model_catalog_json`, and it is a blunt one:

- it **replaces** the model list rather than merging per entry, so an override
  file that omits a model removes it from the effective catalog entirely;
- it **rejects entries missing any field**, so entries cannot be partial.

An override is therefore a full snapshot of the server catalog, and a snapshot
goes stale in both directions: models added upstream never appear, and models
retired upstream linger. Neither announces itself — the user finds out when a
model is missing from the picker, or when a route starts failing every turn.

`~/.codex/models_cache.json` keeps refreshing from the server regardless (it is
Codex's own cache, not ours), so it stays the fresh side of the comparison.

Which entries were deliberately overridden is read off the snapshot itself: a
`multi_agent_version` that differs from the cache *is* the override. Nothing here
carries a list of model ids that would need to stay in sync with the user's file.
Differences in any other field are treated as upstream drift, not intent —
`refresh` takes the upstream value and says how many it discarded.
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The one field an override is allowed to pin. Everything else follows upstream.
PROTOCOL_FIELD = "multi_agent_version"

CONFIG_KEY = "model_catalog_json"


class CatalogError(Exception):
    """A catalog file that exists but cannot be used."""


@dataclass(frozen=True)
class CatalogDrift:
    """How a snapshot differs from the catalog the server is serving today."""

    catalog_path: Path
    cache_path: Path
    # Upstream lists these, the snapshot does not: invisible to Codex.
    added: tuple[str, ...]
    # The snapshot lists these, upstream no longer does: offered but retired.
    dropped: tuple[str, ...]
    # slug -> (upstream value, snapshot value) for the protocol field.
    overrides: Mapping[str, tuple[object, object]]
    # Slugs whose other fields have moved upstream. Quiet on purpose: a changed
    # `base_instructions` or `context_window` is worth re-deriving for, but it
    # does not break a route, and a daily notification for it would train the
    # user to dismiss the one that does.
    field_drift: tuple[str, ...]

    @property
    def blocking(self) -> tuple[str, ...]:
        """Findings that justify interrupting the user."""
        problems = []
        for slug in self.added:
            problems.append(f"{slug} exists upstream but is missing from the snapshot")
        for slug in self.dropped:
            problems.append(f"{slug} is in the snapshot but retired upstream")
        return tuple(problems)

    @property
    def quiet(self) -> tuple[str, ...]:
        """Findings worth logging without raising an alarm."""
        notes = []
        if self.field_drift:
            notes.append(
                f"{len(self.field_drift)} model(s) have non-protocol fields changed "
                f"upstream: {', '.join(self.field_drift)}"
            )
        if not self.overrides:
            notes.append(
                "the snapshot pins nothing upstream does not already say — "
                f"remove `{CONFIG_KEY}` and the override becomes one less thing to maintain"
            )
        for slug, (upstream, pinned) in sorted(self.overrides.items()):
            notes.append(
                f"{slug}: {PROTOCOL_FIELD} pinned to {pinned!r} (upstream says {upstream!r})"
            )
        return tuple(notes)


def codex_home(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("CODEX_HOME", "~/.codex")).expanduser()


def find_override(home: Path) -> Path | None:
    """The catalog file `config.toml` points at, or None when there is none.

    A parse failure is not this module's business to report: `doctor` and Codex
    itself both surface a broken config.toml, and raising here would turn an
    unrelated typo into a catalog complaint.
    """
    config = home / "config.toml"
    if not config.exists():
        return None
    try:
        parsed = tomllib.loads(config.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    raw = parsed.get(CONFIG_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def _models(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        raise CatalogError(f"{path}: {err}") from err
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise CatalogError(f"{path}: no usable 'models' array")
    by_slug = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("slug"), str):
            by_slug[entry["slug"]] = entry
    if not by_slug:
        raise CatalogError(f"{path}: no entries with a 'slug'")
    return by_slug


def inspect(home: Path | None = None) -> CatalogDrift | None:
    """Compare the installed snapshot against Codex's own fresh cache.

    Returns None when there is nothing to compare — no override installed, or no
    cache yet. Both are ordinary states, not problems: most installs never touch
    `model_catalog_json`, and a Codex that has not run yet has no cache.
    """
    home = codex_home() if home is None else home
    catalog_path = find_override(home)
    if catalog_path is None or not catalog_path.exists():
        return None
    cache_path = home / "models_cache.json"
    if not cache_path.exists():
        return None

    snapshot = _models(catalog_path)
    upstream = _models(cache_path)

    overrides: dict[str, tuple[object, object]] = {}
    field_drift = []
    for slug in sorted(set(snapshot) & set(upstream)):
        ours, theirs = snapshot[slug], upstream[slug]
        if ours.get(PROTOCOL_FIELD) != theirs.get(PROTOCOL_FIELD):
            overrides[slug] = (theirs.get(PROTOCOL_FIELD), ours.get(PROTOCOL_FIELD))
        if any(
            key != PROTOCOL_FIELD and ours.get(key) != theirs.get(key)
            for key in set(ours) | set(theirs)
        ):
            field_drift.append(slug)

    return CatalogDrift(
        catalog_path=catalog_path,
        cache_path=cache_path,
        added=tuple(sorted(set(upstream) - set(snapshot))),
        dropped=tuple(sorted(set(snapshot) - set(upstream))),
        overrides=overrides,
        field_drift=tuple(field_drift),
    )


def refresh(drift: CatalogDrift) -> tuple[str, ...]:
    """Re-derive the snapshot from the cache, re-applying the pins.

    Backs the old snapshot up first: it is the only record of what was pinned,
    so overwriting it without a copy would make a wrong pin unrecoverable.
    """
    upstream = _models(drift.cache_path)
    lost = [slug for slug in drift.overrides if slug not in upstream]

    reapplied = []
    for slug, (_, pinned) in drift.overrides.items():
        if slug in upstream:
            upstream[slug][PROTOCOL_FIELD] = pinned
            reapplied.append(slug)

    backup = drift.catalog_path.with_suffix(drift.catalog_path.suffix + ".bak")
    shutil.copy2(drift.catalog_path, backup)
    payload = {"models": list(upstream.values())}
    drift.catalog_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    messages = [
        f"rewrote {drift.catalog_path} from {drift.cache_path}: "
        f"{len(upstream)} model(s), backup at {backup.name}"
    ]
    if reapplied:
        messages.append(f"re-applied {PROTOCOL_FIELD} pin on {', '.join(sorted(reapplied))}")
    if lost:
        messages.append(
            f"dropped pin for {', '.join(sorted(lost))}: retired upstream, "
            "so pinning it would resurrect a model Codex cannot use"
        )
    if drift.field_drift:
        messages.append(
            f"took upstream values for other fields on {len(drift.field_drift)} model(s)"
        )
    return tuple(messages)


def models_needing_pin(home: Path) -> tuple[str, ...]:
    """Slugs whose declared protocol keeps a local sub-agent from reading its task.

    Read from the cache rather than hardcoded: the declaration is the server's to
    change, and a list compiled here would quietly stop matching it.
    """
    try:
        upstream = _models(home / "models_cache.json")
    except CatalogError:
        return ()
    return tuple(
        slug for slug, entry in sorted(upstream.items()) if entry.get(PROTOCOL_FIELD) == "v2"
    )


def build_snapshot(home: Path, slugs: Sequence[str]) -> str:
    """The catalog file's contents: today's server catalog with `slugs` pinned.

    A full copy because the override replaces the list — writing only the pinned
    entries would leave Codex with only those models.
    """
    upstream = _models(home / "models_cache.json")
    missing = [slug for slug in slugs if slug not in upstream]
    if missing:
        raise CatalogError(f"not in {home / 'models_cache.json'}: {', '.join(missing)}")
    for slug in slugs:
        upstream[slug][PROTOCOL_FIELD] = "v1"
    return json.dumps({"models": list(upstream.values())}, indent=2, ensure_ascii=False) + "\n"


def plan_config_line(config_text: str, catalog_path: Path) -> str:
    """`config.toml` with `model_catalog_json` pointing at `catalog_path`.

    Inserted at the top: it is a root-level key, and TOML assigns any key that
    follows a `[table]` header to that table. Appending it — the obvious thing —
    would silently make it `[projects."…"].model_catalog_json`, which Codex would
    ignore while the file still looked correct.
    """
    line = f'model_catalog_json = "{catalog_path}"'
    return "\n".join([line, *_without_config_line(config_text)]).rstrip("\n") + "\n"


def plan_config_removal(config_text: str) -> str | None:
    """`config.toml` with the `model_catalog_json` key dropped, or None if absent.

    Only the key: any comment the user wrote around it is theirs, and guessing at
    which lines belong to us would delete someone's note about their own config.
    The caller shows the diff, which is where an orphaned comment becomes visible.
    """
    kept = _without_config_line(config_text)
    if len(kept) == len(config_text.splitlines()):
        return None
    return "\n".join(kept).rstrip("\n") + "\n"


def _without_config_line(config_text: str) -> list[str]:
    return [
        line
        for line in config_text.splitlines()
        if not line.strip().startswith(f"{CONFIG_KEY} ")
        and not line.strip().startswith(f"{CONFIG_KEY}=")
    ]


def report(mode: str, home: Path | None = None) -> tuple[list[str], list[str]]:
    """Catalog findings as `(problems, notes)`, matching the check-models shape.

    `mode` is the user's answer to "and then what": `warn` reports and changes
    nothing, `refresh` re-derives the snapshot, `off` skips the check. Rewriting
    a file under `~/.codex` is the user's call, not a default — the snapshot
    carries `base_instructions` and other fields that shape how their models
    behave, and an unattended job changing those is hard to trace back later.
    """
    if mode == "off":
        return [], []
    try:
        drift = inspect(home)
    except CatalogError as err:
        return [f"codex model catalog: {err}"], []
    if drift is None:
        return [], []

    label = f"codex model catalog {drift.catalog_path}"
    problems = [f"{label}: {finding}" for finding in drift.blocking]
    notes = [f"  {label}: {note}" for note in drift.quiet]

    if problems and mode == "refresh":
        try:
            messages = refresh(drift)
        except (CatalogError, OSError) as err:
            problems.append(f"{label}: auto-refresh failed: {err}")
            return problems, notes
        # Reported, not silenced: the refresh is the answer to the findings, and
        # the user should still see what went stale and what was done about it.
        notes.extend(f"  {label}: {message}" for message in messages)
        return [], notes

    if problems:
        problems.append(
            "Re-derive the snapshot with `fluxion-provider refresh-codex-catalog`, "
            "or set FLUXION_PROVIDER_CODEX_CATALOG_DRIFT=refresh to have the daily "
            "check do it."
        )
    if not problems and not notes:
        notes.append(f"  {label}: current with {drift.cache_path.name}")
    return problems, notes


def modes() -> Sequence[str]:
    return ("off", "warn", "refresh")

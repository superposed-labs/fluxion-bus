from __future__ import annotations

from fluxion.web.services.aggregator import _parse_diffstat_totals


def test_parses_both_insertions_and_deletions():
    summary = (
        "Changed files:\n M a.swift\n\n"
        "Diff stat:\n a.swift | 286 ++++\n b.swift | 51 +\n"
        " 2 files changed, 338 insertions(+), 1 deletion(-)"
    )
    assert _parse_diffstat_totals(summary) == (338, 1)


def test_parses_insertions_or_deletions_alone():
    assert _parse_diffstat_totals("1 file changed, 5 insertions(+)") == (5, 0)
    assert _parse_diffstat_totals("1 file changed, 2 deletions(-)") == (0, 2)


def test_returns_zero_for_non_string_or_no_counts():
    # Untracked-only changes produce no `git diff --stat` counts, and a missing
    # or non-string summary must not raise.
    assert _parse_diffstat_totals(None) == (0, 0)
    assert _parse_diffstat_totals("No git diff.") == (0, 0)
    assert _parse_diffstat_totals({"files": 1}) == (0, 0)

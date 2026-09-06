from __future__ import annotations

from fluxion.usage.model_identity import billing_model_id, identify_model, parse_model_name


def test_gemini_display_and_slug_share_one_billing_identity():
    display = identify_model("antigravity", "Gemini 3.5 Flash (High)")
    slug = identify_model("antigravity", "gemini-3.5-flash-low")

    assert display.billing_id == slug.billing_id == "gemini-3.5-flash"
    assert display.effort == "high"
    assert slug.effort == "low"
    assert display.quota_pool == slug.quota_pool == "gemini"
    assert display.version == slug.version == (3, 5)


def test_unknown_gemini_display_effort_still_keeps_billing_identity():
    identity = identify_model("antigravity", "Gemini 3.5 Flash (Experimental)")

    assert identity.billing_id == "gemini-3.5-flash"
    assert identity.effort == ""
    assert identity.quota_pool == "gemini"


def test_antigravity_external_model_metadata():
    identity = identify_model("antigravity", "GPT-OSS 120B (Medium)")

    assert identity.billing_id == "gpt-oss 120b (medium)"
    assert identity.effort == "medium"
    assert identity.quota_pool == "external"
    assert identity.version == ()


def test_billing_model_id_leaves_normal_ids_unchanged():
    assert billing_model_id("codex", "GPT-5.4-Mini") == "gpt-5.4-mini"


def test_only_backend_slugs_abbreviate_gemini_3_5_to_3():
    """agy's backend slugs write 3.5 as plain `3`; its display names do not.

    Applying the remap to display names too would silently merge a real
    `Gemini 3 Flash` into the 3.5 usage row.
    """
    assert parse_model_name("antigravity", "gemini-3-flash").label == "Gemini 3.5 Flash"
    assert parse_model_name("antigravity", "Gemini 3 Flash").label == "Gemini 3 Flash"


def test_every_published_effort_token_folds_onto_the_product():
    """low/medium/high are all agy ships today; the higher tiers are recognized
    so a future `-max` id lands on its product instead of its own usage row."""
    for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
        parsed = parse_model_name("antigravity", f"gemini-3.7-flash-{effort}")
        assert parsed.family == "gemini-3.7-flash"
        assert parsed.label == "Gemini 3.7 Flash"
        assert parsed.effort == effort


def test_thinking_is_not_an_effort():
    parsed = parse_model_name("antigravity", "claude-opus-4-6-thinking")

    assert parsed.family == "claude-opus-4-6"
    assert parsed.effort == ""


def test_codex_colon_effort_resolves_to_the_bare_product():
    """Codex writes a per-request effort override as `gpt-5.6-luna:high`.

    A colon never appears inside a published model id, so the suffix is always
    effort. Leaving it on made the id miss its exact `models` key and fall
    through to the coarse provider fallback — Luna priced at the $5/$30
    flagship tier instead of its real $0.20/$1.20.
    """
    identity = identify_model("codex", "gpt-5.6-luna:high")

    assert identity.billing_id == "gpt-5.6-luna"
    assert identity.effort == "high"
    assert parse_model_name("codex", "gpt-5.6-luna:high").family == "gpt-5.6-luna"


def test_codex_dash_effort_is_not_stripped_from_the_product_id():
    """The dash form is ambiguous outside Antigravity, so it stays on the id.

    `gpt-5.1-codex-max` is a product in its own right, distinct from
    `gpt-5.1-codex`; stripping `-max` the way Antigravity does would merge two
    different models onto one usage row. `gpt-5.6-luna-max` really is an effort
    variant, but nothing in the id distinguishes the two cases, so both keep
    their full id and the price table covers them with a family key instead.
    """
    product = identify_model("codex", "gpt-5.1-codex-max")
    assert product.billing_id == "gpt-5.1-codex-max"
    assert billing_model_id("codex", "gpt-5.1-codex") == "gpt-5.1-codex"

    variant = identify_model("codex", "gpt-5.6-luna-max")
    assert variant.billing_id == "gpt-5.6-luna-max"

    # Effort is still reported on both; only the id is left alone.
    assert product.effort == variant.effort == "max"


def test_colon_suffix_that_is_not_an_effort_is_left_alone():
    """Ollama-style tags (`qwen3:32b`) are part of the id, not an effort."""
    assert billing_model_id("codex", "qwen3:32b") == "qwen3:32b"

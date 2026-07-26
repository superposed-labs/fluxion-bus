from __future__ import annotations

from fluxion.usage.model_identity import billing_model_id, identify_model


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

"""Tests for LLM provider resolution."""

from __future__ import annotations

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    """Build Settings without reading any .env file."""
    base = {
        "openai_api_key": "",
        "openai_base_url": None,
        "openai_model": "gpt-5",
        "azure_ai_foundry_base_url": None,
        "azure_ai_foundry_api_key": None,
        "azure_ai_foundry_deployment": None,
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})


def test_openai_is_used_when_azure_is_absent():
    s = _settings(openai_api_key="sk-test")

    assert not s.is_azure_foundry
    assert s.llm_api_key == "sk-test"
    assert s.llm_model == "gpt-5"
    assert s.llm_base_url is None


def test_azure_takes_precedence_over_openai():
    """A filled-in Azure block wins, so a Foundry .env drops in unchanged."""
    s = _settings(
        openai_api_key="sk-should-be-ignored",
        openai_model="gpt-5",
        azure_ai_foundry_base_url="https://r.services.ai.azure.com/openai/v1",
        azure_ai_foundry_api_key="azure-key",
        azure_ai_foundry_deployment="gpt-4.1-mini",
    )

    assert s.is_azure_foundry
    assert s.llm_api_key == "azure-key"
    assert s.llm_base_url == "https://r.services.ai.azure.com/openai/v1"
    # The deployment name is what Foundry routes on, not the model id.
    assert s.llm_model == "gpt-4.1-mini"


def test_no_provider_configured_reports_empty_key():
    s = _settings()
    assert s.llm_api_key == ""
    assert not s.is_azure_foundry

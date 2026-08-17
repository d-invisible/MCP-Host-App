"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # pydantic-settings applies these left to right, and later files win, so
    # the repo-root .env is listed first and the app-local .env overrides it.
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- app ---------------------------------------------------------------
    app_name: str = "MCP Host"
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = True

    # Public base URL of this backend. Used to build OAuth issuer URLs,
    # the CIMD document URL, and MCP OAuth redirect URIs.
    backend_base_url: str = "http://localhost:8000"
    # Public base URL of the Next.js frontend.
    frontend_base_url: str = "http://localhost:3000"

    # ---- database ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://mcphost:mcphost@localhost:5432/mcphost"
    db_echo: bool = False

    # ---- crypto ------------------------------------------------------------
    # Master key used by pgcrypto to encrypt third-party OAuth tokens at rest.
    # MUST be overridden in any non-local environment.
    token_encryption_key: str = Field(
        default="dev-only-insecure-token-encryption-key-change-me",
        min_length=16,
    )
    # Signing key for this app's own JWT access tokens.
    jwt_secret_key: str = Field(
        default="dev-only-insecure-jwt-secret-change-me",
        min_length=16,
    )
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 30  # 30 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    # Authorization codes issued by our own auth server are short-lived.
    auth_code_ttl_seconds: int = 60

    # ---- LLM ---------------------------------------------------------------
    # Works with both api.openai.com and Azure AI Foundry (set the base URL).
    openai_api_key: str = ""
    openai_base_url: str | None = None
    openai_model: str = "gpt-5"
    # Some OpenAI-compatible gateways want an api-version query parameter.
    # Azure Foundry's /openai/v1 surface does not, so this stays unset there.
    openai_api_version: str | None = None
    llm_request_timeout_seconds: float = 120.0
    llm_max_tool_iterations: int = 12

    # Azure AI Foundry, under its own variable names. When these are present
    # they win over the OPENAI_* trio, so an existing Foundry .env can be
    # dropped in unchanged.
    azure_ai_foundry_base_url: str | None = None
    azure_ai_foundry_api_key: str | None = None
    azure_ai_foundry_deployment: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_api_key(self) -> str:
        return self.azure_ai_foundry_api_key or self.openai_api_key

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_base_url(self) -> str | None:
        return self.azure_ai_foundry_base_url or self.openai_base_url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_model(self) -> str:
        """The model to call.

        On Azure this is the *deployment* name, which is what the Foundry
        endpoint routes on rather than the underlying model id.
        """
        return self.azure_ai_foundry_deployment or self.openai_model

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_azure_foundry(self) -> bool:
        return bool(self.azure_ai_foundry_base_url)

    # ---- CORS --------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:3000"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def issuer_url(self) -> str:
        """OAuth 2.0 issuer identifier for this app's built-in auth server."""
        return self.backend_base_url.rstrip("/")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cimd_url(self) -> str:
        """Client ID Metadata Document URL.

        Under the CIMD model the client_id *is* an HTTPS URL that resolves to
        this document, so MCP authorization servers can identify this host app
        without any pre-registration.
        """
        return f"{self.backend_base_url.rstrip('/')}/.well-known/oauth-client"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mcp_oauth_redirect_uri(self) -> str:
        """Single redirect URI used for every outbound MCP OAuth connection."""
        return f"{self.backend_base_url.rstrip('/')}/api/connectors/oauth/callback"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_local(self) -> bool:
        return self.environment == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

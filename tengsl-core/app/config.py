"""Настройки backend'а, читаются из переменных окружения (см. docker-compose.yml)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORION_", env_file=".env", extra="ignore")

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://tengsl:tengsl@postgres:5432/tengsl"

    # MQTT
    mqtt_host: str = "mqtt-broker"
    mqtt_port: int = 1883
    mqtt_use_tls: bool = False
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "tengsl"
    mqtt_client_id: str = "backend"
    mqtt_reconnect_delay_seconds: float = 5.0

    # API
    cors_allow_origins: str = "http://localhost:8080"  # список через запятую
    auth_cookie_secure: bool = False
    auth_session_ttl_hours: int = 12
    # Shared secret used by Edge Agent for internal read-only API access.
    agent_api_token: str = ""

    # Public URL of the TENGSL installation. Change these values when deploying
    # to another domain. ntfy is deliberately exposed on a host/port, not a path,
    # because ntfy does not support hosting under a URL sub-path.
    public_base_url: str = "https://morianas.ru"

    # ntfy notifications
    ntfy_enabled: bool = False
    ntfy_base_url: str = "http://ntfy:80"
    ntfy_public_url: str = "https://ntfy.morianas.ru"
    ntfy_publisher_token: str = ""
    ntfy_topic_secret: str = "change-me-ntfy-topic-secret"
    ntfy_timeout_seconds: float = 5.0
    test_results_path: str = ""
    environment: str = "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


settings = Settings()

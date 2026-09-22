"""Application settings loaded from the environment (and .env)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLite database file, relative paths resolved against the app root.
    database_path: str = "data/gym.db"
    # Secret used to sign NiceGUI browser-tab storage cookies.
    storage_secret: str = "dev-storage-secret-change-me"
    # Seed admin email; on first start an invite-style setup link is printed to the log.
    first_admin_email: str | None = None
    # Public base URL of the app, used for invite links.
    base_url: str = "http://localhost:8080"
    log_level: str = "INFO"
    # uvicorn bind address (Caddy proxies to this).
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080

    @property
    def database_url(self) -> str:
        path = Path(self.database_path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.resolve()}"


settings = Settings()

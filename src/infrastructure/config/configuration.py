from typing import List
from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Application-wide configuration loaded from environment variables or .env file.
    """
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "development"
    db_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/nomadx_db"
    jwt_secret: str = "nomadx-secret-key-123456789-super-long-signing-key"
    internal_secret: str = "nomadx-internal-secret"
    
    # Primary & Fallback Blockchain RPC Node URLs (Base Mainnet)
    base_rpc_url: str = "https://mainnet.base.org"
    base_rpc_fallback_urls: List[str] = [
        "https://developer-access-mainnet.base.org",
        "https://base.gateway.tenderly.co"
    ]

    # PostgreSQL connection pool & SSL parameters
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_pre_ping: bool = True
    db_ssl_mode: str = "prefer"

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.env == "production":
            if self.jwt_secret in ["nomadx-secret-key-123456789-super-long-signing-key", "secret"]:
                raise ValueError("JWT_SECRET must be overridden in production with a secure random key.")
            if self.internal_secret in ["nomadx-internal-secret", "secret"]:
                raise ValueError("INTERNAL_SECRET must be overridden in production with a secure token.")
            if len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters long in production.")
            if len(self.internal_secret) < 16:
                raise ValueError("INTERNAL_SECRET must be at least 16 characters long in production.")
            if "secret" in self.jwt_secret.lower() or "password" in self.jwt_secret.lower():
                raise ValueError("JWT_SECRET contains insecure default keywords.")
            if "secret" in self.internal_secret.lower() or "password" in self.internal_secret.lower():
                raise ValueError("INTERNAL_SECRET contains insecure default keywords.")
        return self

# Instantiate global settings
settings = Settings()

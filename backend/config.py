from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_mode: Literal['demo', 'production'] = 'production'
    base_url: str = 'http://localhost:8000'
    database_path: str = 'data/switch.db'
    session_secret: str = ''
    cookie_secure: bool = True
    entra_tenant_id: str = ''
    entra_client_id: str = ''
    entra_client_secret: str = ''
    bootstrap_admin_oid: str = ''
    collector_id: str = 'campus'
    collector_token: str = ''
    frontend_path: str = 'frontend/dist'
    stale_seconds: int = 120

    @property
    def sso_ready(self):
        return all((self.entra_tenant_id, self.entra_client_id, self.entra_client_secret))

    def validate_runtime(self):
        if len(self.session_secret) < 32:
            raise ValueError('SESSION_SECRET must contain at least 32 characters.')
        if self.app_mode == 'production':
            if not self.cookie_secure or urlparse(self.base_url).scheme != 'https':
                raise ValueError('Production requires HTTPS and secure cookies.')
            if len(self.collector_token) < 32:
                raise ValueError('COLLECTOR_TOKEN must contain at least 32 characters.')
        if self.entra_tenant_id:
            UUID(self.entra_tenant_id)
        if self.bootstrap_admin_oid:
            UUID(self.bootstrap_admin_oid)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings():
    return Settings()

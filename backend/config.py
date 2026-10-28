from functools import lru_cache
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_mode: Literal['production'] = 'production'
    environment: Literal['production', 'development'] = 'production'
    auth_mode: Literal['local', 'entra'] = 'local'
    power_mode: Literal['fiap', 'aws', 'azure'] = 'fiap'
    aws_region: str = 'us-east-2'
    aws_vpc_id: str = ''
    azure_subscription_id: str = ''
    azure_resource_group: str = ''
    base_url: str = 'http://localhost:8000'
    database_path: str = 'data/switch.db'
    session_secret: str = Field(default='', repr=False)
    cookie_secure: bool = True
    entra_tenant_id: str = ''
    entra_client_id: str = ''
    entra_client_secret: str = Field(default='', repr=False)
    bootstrap_admin_oid: str = ''
    collector_id: str = 'campus'
    collector_token: str = Field(default='', repr=False)
    frontend_path: str = 'frontend/dist'
    stale_seconds: int = 120
    bootstrap_admin_email: str = ''
    bootstrap_admin_password_hash: str = Field(default='', repr=False)
    bootstrap_admin_name: str = 'Administrador'

    @property
    def sso_ready(self):
        return all((self.entra_tenant_id, self.entra_client_id, self.entra_client_secret))

    def validate_runtime(self):
        if self.power_mode == 'aws' and (not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', self.aws_region) or not re.fullmatch(r'vpc-[0-9a-f]{8,17}', self.aws_vpc_id)):
            raise ValueError('O modo AWS exige AWS_REGION e AWS_VPC_ID válidos.')
        if self.power_mode == 'azure':
            UUID(self.azure_subscription_id)
            if not re.fullmatch(r'[A-Za-z0-9_.()-]{1,90}', self.azure_resource_group):
                raise ValueError('O modo Azure exige AZURE_RESOURCE_GROUP válido.')
        if len(self.session_secret) < 32 or self.session_secret.startswith(('replace-', 'substitua-')):
            raise ValueError('SESSION_SECRET deve ter pelo menos 32 caracteres.')
        if self.environment == 'production':
            if not self.cookie_secure or urlparse(self.base_url).scheme != 'https':
                raise ValueError('Produção exige HTTPS e cookies seguros.')
            if len(self.collector_token) < 32 or self.collector_token.startswith(('replace-', 'substitua-')):
                raise ValueError('COLLECTOR_TOKEN deve ter pelo menos 32 caracteres.')
        if self.entra_tenant_id:
            UUID(self.entra_tenant_id)
        if self.bootstrap_admin_oid:
            UUID(self.bootstrap_admin_oid)
        if self.auth_mode == 'entra' and not self.sso_ready:
            raise ValueError('A autenticação Entra exige tenant ID, client ID e client secret.')
        if self.bootstrap_admin_email and not self.bootstrap_admin_email.lower().endswith('@fiap.com.br'):
            raise ValueError('BOOTSTRAP_ADMIN_EMAIL deve usar o domínio da FIAP.')
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

    def power_config(self):
        return {'mode': self.power_mode, 'aws_region': self.aws_region, 'aws_vpc_id': self.aws_vpc_id,
                'azure_subscription_id': self.azure_subscription_id, 'azure_resource_group': self.azure_resource_group}


@lru_cache
def get_settings():
    return Settings()

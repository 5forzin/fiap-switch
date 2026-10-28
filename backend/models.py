import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.db import FLOORS


class Grants(BaseModel):
    floors: list[int] = Field(default_factory=list, max_length=9)
    labs: list[str] = Field(default_factory=list, max_length=100)
    computers: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator('floors')
    @classmethod
    def floors_exist(cls, values):
        if any(v not in FLOORS for v in values):
            raise ValueError('Andar não permitido.')
        return sorted(set(values))


class UserInput(BaseModel):
    id: str | None = Field(default=None, max_length=100)
    oid: str | None = Field(default=None, max_length=100)
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=100)
    role: Literal['admin', 'support', 'monitor', 'viewer']
    enabled: bool = True
    grants: Grants = Field(default_factory=Grants)
    password: str | None = Field(default=None, max_length=128, repr=False)

    @field_validator('password')
    @classmethod
    def strong_password(cls, value):
        if value is not None and (len(value) < 8 or value.isspace()):
            raise ValueError('A senha deve ter pelo menos 8 caracteres.')
        return value

    @field_validator('name')
    @classmethod
    def meaningful_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Informe o nome do usuário.')
        return value

    @field_validator('email')
    @classmethod
    def domain(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[^\s@]+@fiap\.com\.br', value):
            raise ValueError('Use um e-mail corporativo da FIAP.')
        return value

    @model_validator(mode='after')
    def monitor_floor(self):
        if self.role == 'monitor' and (len(self.grants.floors) != 1 or self.grants.labs or self.grants.computers):
            raise ValueError('Selecione um único andar para o monitor.')
        return self


class LabInput(BaseModel):
    floor: int
    number: int = Field(ge=1, le=9)
    label: str = Field(min_length=1, max_length=80)
    students: int | None = Field(ge=0, le=99)
    include_professor: bool = True

    @field_validator('floor')
    @classmethod
    def floor_exists(cls, value):
        if value not in FLOORS:
            raise ValueError('Andar não permitido.')
        return value


class ComputerInput(BaseModel):
    hostname: str = Field(default='', max_length=253)
    mac: str = ''

    @field_validator('hostname')
    @classmethod
    def valid_hostname(cls, value):
        if value and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,251}[a-zA-Z0-9]|[a-zA-Z0-9]', value):
            raise ValueError('Nome de rede inválido.')
        return value

    @field_validator('mac')
    @classmethod
    def valid_mac(cls, value):
        if not value:
            return ''
        raw = re.sub('[:-]', '', value).upper()
        if not re.fullmatch('[0-9A-F]{12}', raw) or int(raw[:2], 16) & 1 or int(raw, 16) == 0:
            raise ValueError('Informe um endereço MAC unicast.')
        return ':'.join(raw[i:i+2] for i in range(0, 12, 2))


class LabQuantityInput(BaseModel):
    students: int | None = Field(ge=0, le=99)


class ActionInput(BaseModel):
    action: Literal['wake', 'shutdown', 'restart']
    computer_ids: list[str] = Field(min_length=1, max_length=250)


class Hardware(BaseModel):
    cpu_model: str | None = Field(default=None, max_length=200)
    logical_cpus: int | None = Field(default=None, ge=1, le=4096)
    memory_gib: float | None = Field(default=None, ge=0, le=65536, allow_inf_nan=False)
    storage_gib: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    disk_count: int | None = Field(default=None, ge=0, le=100)
    gpu_models: list[str] | None = Field(default=None, max_length=16)

    @field_validator('gpu_models')
    @classmethod
    def gpu_names(cls, value):
        if value and any(len(name) > 200 for name in value):
            raise ValueError('O nome do modelo de GPU é muito longo.')
        return value


class HardwareStandard(BaseModel):
    enabled: bool = True
    match_mode: Literal['exact', 'minimum'] = 'exact'
    cpu_model: str | None = Field(default=None, max_length=200)
    logical_cpus: int | None = Field(default=None, ge=1, le=4096)
    memory_gib: float | None = Field(default=None, ge=0.5, le=65536, allow_inf_nan=False)
    storage_gib: float | None = Field(default=None, ge=1, le=1000000, allow_inf_nan=False)
    disk_count: int | None = Field(default=None, ge=1, le=100)
    gpu_model: str | None = Field(default=None, max_length=200)

    @field_validator('cpu_model','gpu_model')
    @classmethod
    def nonblank_models(cls, value):
        return value.strip() or None if value is not None else None

    @model_validator(mode='after')
    def configured(self):
        if self.enabled and not any(getattr(self, key) is not None and getattr(self, key) != '' for key in ('cpu_model','logical_cpus','memory_gib','storage_gib','disk_count','gpu_model')):
            raise ValueError('Configure pelo menos um requisito de hardware.')
        return self


class CheckInput(BaseModel):
    floor: int | None = None
    lab_id: str | None = Field(default=None, max_length=100)

    @field_validator('floor')
    @classmethod
    def valid_floor(cls, value):
        if value is not None and value not in FLOORS:
            raise ValueError('Andar não permitido.')
        return value


class Report(BaseModel):
    computer_id: str
    status: Literal['online', 'unreachable', 'unknown']
    metrics: dict = Field(default_factory=dict)
    mac: str = ''
    issue: str = Field(default='', max_length=250)
    hardware: Hardware | None = None

    @field_validator('metrics')
    @classmethod
    def metrics_valid(cls, value):
        allowed = {'cpu', 'memory', 'disk_free', 'uptime_hours', 'os'}
        if set(value) - allowed:
            raise ValueError('Métrica não permitida.')
        for key in ('cpu', 'memory', 'disk_free'):
            if key in value and (not isinstance(value[key], (int, float)) or not 0 <= value[key] <= 100):
                raise ValueError('Métrica fora do intervalo permitido.')
        if 'uptime_hours' in value and (not isinstance(value['uptime_hours'], (int, float)) or not 0 <= value['uptime_hours'] <= 100000):
            raise ValueError('Tempo de atividade inválido.')
        if 'os' in value and (not isinstance(value['os'], str) or len(value['os']) > 120):
            raise ValueError('Sistema operacional inválido.')
        return value

    @field_validator('mac')
    @classmethod
    def mac_valid(cls, value):
        return ComputerInput.valid_mac(value)


class Reports(BaseModel):
    reports: list[Report] = Field(max_length=250)


class JobResult(BaseModel):
    lease_token: str
    state: Literal['completed', 'failed']
    message: str = Field(max_length=250)


class PasswordLogin(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator('username')
    @classmethod
    def normalize(cls, value):
        return value.strip().lower()


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=8, max_length=128, repr=False)

import ipaddress
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
            raise ValueError('Unsupported floor.')
        return sorted(set(values))


class UserInput(BaseModel):
    oid: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=100)
    role: Literal['admin', 'monitor', 'viewer']
    enabled: bool = True
    grants: Grants = Field(default_factory=Grants)

    @field_validator('email')
    @classmethod
    def domain(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[^\s@]+@fiap\.com\.br', value):
            raise ValueError('Use a FIAP corporate address.')
        return value


class LabInput(BaseModel):
    floor: int
    number: int = Field(ge=1, le=9)
    label: str = Field(min_length=1, max_length=80)
    students: int = Field(ge=0, le=99)
    include_professor: bool = True

    @field_validator('floor')
    @classmethod
    def floor_exists(cls, value):
        if value not in FLOORS:
            raise ValueError('Unsupported floor.')
        return value


class ComputerInput(BaseModel):
    hostname: str = Field(default='', max_length=253)
    mac: str = ''

    @field_validator('hostname')
    @classmethod
    def valid_hostname(cls, value):
        if value and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,251}[a-zA-Z0-9]|[a-zA-Z0-9]', value):
            raise ValueError('Invalid hostname.')
        return value

    @field_validator('mac')
    @classmethod
    def valid_mac(cls, value):
        if not value:
            return ''
        raw = re.sub('[:-]', '', value).upper()
        if not re.fullmatch('[0-9A-F]{12}', raw) or int(raw[:2], 16) & 1 or int(raw, 16) == 0:
            raise ValueError('Enter a unicast MAC address.')
        return ':'.join(raw[i:i+2] for i in range(0, 12, 2))


class ActionInput(BaseModel):
    action: Literal['wake', 'shutdown', 'restart']
    computer_ids: list[str] = Field(min_length=1, max_length=250)


class Report(BaseModel):
    computer_id: str
    status: Literal['online', 'unreachable', 'unknown']
    metrics: dict = Field(default_factory=dict)
    mac: str = ''
    issue: str = Field(default='', max_length=250)

    @field_validator('metrics')
    @classmethod
    def metrics_valid(cls, value):
        allowed = {'cpu', 'memory', 'disk_free', 'uptime_hours', 'os'}
        if set(value) - allowed:
            raise ValueError('Unsupported metric.')
        for key in ('cpu', 'memory', 'disk_free'):
            if key in value and (not isinstance(value[key], (int, float)) or not 0 <= value[key] <= 100):
                raise ValueError('Metric out of range.')
        if 'uptime_hours' in value and (not isinstance(value['uptime_hours'], (int, float)) or not 0 <= value['uptime_hours'] <= 100000):
            raise ValueError('Invalid uptime.')
        if 'os' in value and (not isinstance(value['os'], str) or len(value['os']) > 120):
            raise ValueError('Invalid OS.')
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

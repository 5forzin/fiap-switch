"""Pareamento explícito e autenticação de controladores de produção."""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from backend.audit import audit
from backend.db import connect
from collector.signing import canonical


class Enrollment(BaseModel):
    id: str = Field(max_length=36)
    token: str = Field(min_length=32, max_length=100, repr=False)
    public_key: str = Field(max_length=44)
    hostname: str = Field(min_length=1, max_length=100)
    local_ips: list[str] = Field(default_factory=list, max_length=16)

    @field_validator('local_ips')
    @classmethod
    def valid_ips(cls, values):
        import ipaddress
        return [str(ipaddress.ip_address(ip)) for ip in values]


class PackageInput(BaseModel):
    platform: Literal['windows', 'linux']


async def verify_device(request, cfg, pending=False):
    try:
        device_id = request.headers['X-Switch-Device']
        stamp, nonce = request.headers['X-Switch-Time'], request.headers['X-Switch-Nonce']
        if abs(time.time() - int(stamp)) > 60 or not re.fullmatch('[0-9a-f]{32}', nonce):
            raise ValueError()
        signature = base64.b64decode(request.headers['X-Switch-Signature'], validate=True)
        body = await request.body()
        if len(body) > 1024 * 1024:
            raise ValueError()
        path = request.scope.get('raw_path', request.url.path.encode()).decode('ascii')
        if request.scope.get('query_string'):
            path += '?' + request.scope['query_string'].decode('ascii')
        with connect(cfg.database_path) as db:
            device = db.execute('SELECT * FROM controller_devices WHERE id=?', (device_id,)).fetchone()
            if not device or not device['public_key'] or (not pending and (device['state'] != 'approved' or cfg.power_mode != 'fiap')):
                raise ValueError()
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(device['public_key'], validate=True))
            key.verify(signature, canonical(request.method, path, stamp, nonce, body))
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT * FROM controller_devices WHERE id=?', (device_id,)).fetchone()
            if not latest or latest['public_key'] != device['public_key'] or (not pending and latest['state'] != 'approved'):
                raise ValueError()
            db.execute('DELETE FROM controller_nonces WHERE created<?', (time.time() - 120,))
            db.execute('INSERT INTO controller_nonces VALUES (?,?,?)', (device_id, nonce, time.time()))
            db.execute('UPDATE controller_devices SET last_seen=? WHERE id=?', (time.time(), device_id))
            return dict(latest)
    except (KeyError, ValueError, InvalidSignature, sqlite3.IntegrityError):
        raise HTTPException(401, 'O controlador precisa de uma chave válida e aprovação do administrador.')


def register_controllers(app, cfg, admin):
    from backend.packages import build_package, issuer_public

    @app.get('/api/controllers')
    def devices(user=Depends(admin)):
        public = issuer_public(cfg)
        with connect(cfg.database_path) as db:
            rows = [{key: row[key] for key in ('id', 'platform', 'state', 'hostname', 'source_ip', 'local_ips', 'created', 'last_seen', 'expires')}
                    | {'fingerprint': hashlib.sha256(base64.b64decode(row['public_key'])).hexdigest() if row['public_key'] else ''}
                    for row in db.execute('SELECT * FROM controller_devices ORDER BY created DESC LIMIT 100')]
        return {'devices': rows, 'issuer_fingerprint': hashlib.sha256(public).hexdigest(), 'mode': cfg.power_mode}

    @app.post('/api/controllers/packages')
    def package(payload: PackageInput, user=Depends(admin)):
        if cfg.power_mode != 'fiap':
            raise HTTPException(409, 'O modo de nuvem opera sozinho. Selecione FIAP produção na instalação para parear um servidor.')
        archive, identifier = build_package(cfg, payload.platform, user['id'])
        return Response(archive, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="fiap-controlador-{payload.platform}-{identifier[:8]}.zip"'})

    @app.post('/api/controllers/{device_id}/{operation}')
    def decide(device_id: str, operation: Literal['approve', 'reject', 'revoke'], user=Depends(admin)):
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            device = db.execute('SELECT * FROM controller_devices WHERE id=?', (device_id,)).fetchone()
            expected = 'approved' if operation == 'revoke' else 'pending'
            if not device or device['state'] != expected:
                raise HTTPException(409, 'A solicitação não está neste estado. Atualize a lista.')
            if operation == 'approve' and db.execute("SELECT 1 FROM controller_devices WHERE state='approved' AND collector_id=?", (device['collector_id'],)).fetchone():
                raise HTTPException(409, 'Revogue o controlador atual antes de aprovar outro para este campus.')
            state = {'approve': 'approved', 'reject': 'rejected', 'revoke': 'revoked'}[operation]
            db.execute('UPDATE controller_devices SET state=?,approved_by=? WHERE id=?', (state, user['id'], device_id))
            audit(db, user['id'], 'controller_' + state, device_id)
        return {'ok': True}

    @app.post('/api/enrollment')
    def enroll(payload: Enrollment, request: Request):
        if cfg.power_mode != 'fiap':
            raise HTTPException(409, 'O pareamento externo pertence ao modo FIAP produção.')
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(payload.public_key, validate=True))
        except ValueError:
            raise HTTPException(422, 'Chave pública inválida.')
        origin = request.client.host if request.client else 'unknown'
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            db.execute('DELETE FROM enrollment_attempts WHERE created<?', (now - 300,))
            attempts = db.execute('SELECT COUNT(*) FROM enrollment_attempts WHERE ip=?', (origin,)).fetchone()[0]
            if attempts >= 10:
                raise HTTPException(429, 'Aguarde antes de repetir o pareamento.')
            device = db.execute('SELECT * FROM controller_devices WHERE id=?', (payload.id,)).fetchone()
            valid = device and device['state'] == 'issued' and device['expires'] > now and secrets.compare_digest(device['enrollment_hash'], hashlib.sha256(payload.token.encode()).hexdigest())
            if not valid:
                db.execute('INSERT INTO enrollment_attempts VALUES (?,?)', (origin, now))
            else:
                db.execute("UPDATE controller_devices SET state='pending',enrollment_hash='',public_key=?,hostname=?,source_ip=?,local_ips=?,last_seen=? WHERE id=?",
                           (payload.public_key, payload.hostname, origin, json.dumps(payload.local_ips), now, payload.id))
        if not valid:
            raise HTTPException(401, 'O convite expirou, já foi usado ou é inválido.')
        return {'state': 'pending'}

    @app.get('/api/collector/enrollment/status')
    async def enrollment_status(request: Request):
        device = await verify_device(request, cfg, pending=True)
        return {'state': device['state'], 'active': device['state'] == 'approved' and cfg.power_mode == 'fiap'}

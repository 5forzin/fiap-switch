"""Pacotes por convite, sem distribuir a chave privada de assinatura do painel."""

import base64
import hashlib
import io
import json
import os
import secrets
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.audit import audit
from backend.db import connect


def issuer_key(cfg):
    path = Path(cfg.database_path).parent / 'controller-issuer.key'
    with connect(cfg.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return Ed25519PrivateKey.from_private_bytes(path.read_bytes())
        key = Ed25519PrivateKey.generate()
        with os.fdopen(descriptor, 'wb') as output:
            output.write(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
        return key


def issuer_public(cfg):
    return issuer_key(cfg).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def build_package(cfg, platform, actor_id):
    identifier, token, expires = str(uuid4()), secrets.token_urlsafe(32), time.time() + 1800
    names = ['collector/service.py', 'collector/signing.py', 'collector/identity.py', 'requirements-controller.txt']
    files = {name: Path(name).read_bytes() for name in names}
    files['collector/__init__.py'] = b''
    for name in ('verify.py', 'configure.py', 'instalar.ps1' if platform == 'windows' else 'instalar.sh'):
        files[name] = Path('installers/controller', name).read_bytes()
    files['enrollment.json'] = json.dumps({'id': identifier, 'url': cfg.base_url, 'token': token, 'expires': expires}).encode()
    key = issuer_key(cfg)
    manifest = json.dumps({'platform': platform, 'expires': expires, 'issuer': base64.b64encode(issuer_public(cfg)).decode(),
                           'files': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}}, sort_keys=True, separators=(',', ':')).encode()
    files['manifest.json'], files['manifest.sig'] = manifest, base64.b64encode(key.sign(manifest))
    with connect(cfg.database_path) as db:
        db.execute('INSERT INTO controller_devices(id,platform,enrollment_hash,expires,created,collector_id) VALUES (?,?,?,?,?,?)',
                   (identifier, platform, hashlib.sha256(token.encode()).hexdigest(), expires, time.time(), cfg.collector_id))
        audit(db, actor_id, 'controller_package_created', identifier)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, body in files.items():
            output.writestr(name, body)
    return archive.getvalue(), identifier

"""Identidade local do controlador e espera pela aprovação no painel."""

import base64
import hashlib
import ipaddress
import json
import os
import socket
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from collector.signing import DeviceAuth


def paired_client(base_url, enrollment_path, device_path):
    enrollment = json.loads(Path(enrollment_path).read_text(encoding='utf-8'))
    if enrollment['url'].rstrip('/') != base_url or not base_url.startswith('https://'):
        raise ValueError('A URL do painel deve corresponder ao pacote assinado e usar HTTPS.')
    path = Path(device_path)
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        identity = {'id': enrollment['id'], 'private_key': base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode(),
                    'public_key': base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(identity, output)
    identity = json.loads(path.read_text(encoding='utf-8'))
    client = httpx.Client(base_url=base_url, auth=DeviceAuth(identity['id'], identity['private_key']), timeout=20, follow_redirects=False)
    status = client.get('/api/collector/enrollment/status')
    if status.status_code == 401:
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
                                if not ipaddress.ip_address(item[4][0]).is_loopback})[:16]
        except socket.gaierror:
            addresses = []
        with httpx.Client(base_url=base_url, timeout=20, follow_redirects=False) as registration:
            response = registration.post('/api/enrollment', json={'id': enrollment['id'], 'token': enrollment.get('token', ''),
                'public_key': identity['public_key'], 'hostname': socket.gethostname(), 'local_ips': addresses})
            response.raise_for_status()
        enrollment['token'] = ''
        Path(enrollment_path).write_text(json.dumps(enrollment), encoding='utf-8')
    print('Impressão digital do controlador: ' + hashlib.sha256(base64.b64decode(identity['public_key'])).hexdigest(), flush=True)
    while True:
        response = client.get('/api/collector/enrollment/status')
        response.raise_for_status()
        state = response.json()
        if state['active']:
            return client
        if state['state'] in ('rejected', 'revoked'):
            raise RuntimeError('O administrador rejeitou ou revogou este controlador.')
        time.sleep(5)

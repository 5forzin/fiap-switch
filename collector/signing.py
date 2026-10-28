"""Assinaturas de requisições com chave própria e proteção contra repetição."""

import base64
import hashlib
import secrets
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(method, path, timestamp, nonce, body):
    return '\n'.join((method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest())).encode()


class DeviceAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, device_id, private_key):
        self.device_id = device_id
        self.key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key, validate=True))

    def auth_flow(self, request):
        stamp, nonce = str(int(time.time())), secrets.token_hex(16)
        signature = self.key.sign(canonical(request.method, request.url.raw_path.decode('ascii'), stamp, nonce, request.content))
        request.headers.update({'X-Switch-Device': self.device_id, 'X-Switch-Time': stamp,
                                'X-Switch-Nonce': nonce, 'X-Switch-Signature': base64.b64encode(signature).decode()})
        yield request

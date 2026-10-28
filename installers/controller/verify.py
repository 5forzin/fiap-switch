"""Confere a assinatura do pacote usando a impressão digital obtida no painel."""

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def verify(root, expected):
    manifest = (root / 'manifest.json').read_bytes()
    data = json.loads(manifest)
    public = base64.b64decode(data['issuer'], validate=True)
    if hashlib.sha256(public).hexdigest() != expected.lower().replace(':', ''):
        raise ValueError('A assinatura não pertence ao painel esperado.')
    Ed25519PublicKey.from_public_bytes(public).verify(base64.b64decode((root / 'manifest.sig').read_bytes(), validate=True), manifest)
    if data['expires'] < time.time():
        raise ValueError('O convite expirou. Gere outro pacote no painel.')
    for name, expected_hash in data['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError('A integridade do pacote não pôde ser confirmada.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fingerprint', required=True)
    args = parser.parse_args()
    verify(Path(__file__).parent, args.fingerprint)
    print('Assinatura e integridade conferidas.')

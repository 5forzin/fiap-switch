import base64
import hashlib
import importlib.util
import io
import json
import zipfile

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_access import login

from backend.app import create_app
from backend.db import connect
from collector.signing import DeviceAuth


def package(client, platform='windows'):
    login(client)
    response = client.post('/api/controllers/packages', json={'platform': platform})
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return archive, json.loads(archive.read('enrollment.json'))


def identity():
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return private, public


def enroll(client, ticket, public):
    return client.post('/api/enrollment', json={'id': ticket['id'], 'token': ticket['token'], 'public_key': public, 'hostname': 'servidor-fiap', 'local_ips': ['10.3.31.10']})


def signed(client, private, device_id, path='/api/collector/inventory', method='GET', payload=None, repeat=None):
    request = httpx.Request(method, 'https://testserver' + path, json=payload) if payload is not None else httpx.Request(method, 'https://testserver' + path)
    outgoing = next(DeviceAuth(device_id, private).auth_flow(request))
    headers = repeat or dict(outgoing.headers)
    return client.request(method, path, content=outgoing.content, headers=headers), headers


@pytest.mark.parametrize('platform', ['windows', 'linux'])
def test_pacote_assinado_tem_apenas_controlador_e_fingerprint_independente(client, tmp_path, platform):
    archive, ticket = package(client, platform)
    assert 'backend/app.py' not in archive.namelist()
    assert not any(name.endswith(('.key', '.env', '.pem')) for name in archive.namelist())
    assert ticket['url'] == 'https://testserver' and len(ticket['token']) >= 32
    for name in archive.namelist():
        assert client.app.state.settings.collector_token.encode() not in archive.read(name)
        assert client.app.state.settings.session_secret.encode() not in archive.read(name)
    archive.extractall(tmp_path)
    spec = importlib.util.spec_from_file_location('package_verify', tmp_path / 'verify.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fingerprint = client.get('/api/controllers').json()['issuer_fingerprint']
    module.verify(tmp_path, fingerprint)
    (tmp_path / 'collector/service.py').write_text('alterado')
    with pytest.raises(ValueError):
        module.verify(tmp_path, fingerprint)
    with pytest.raises(ValueError):
        module.verify(tmp_path, '0' * 64)


def test_pareamento_exige_admin_uso_unico_e_aprovacao_antes_de_operar(client):
    _, ticket = package(client)
    private, public = identity()
    assert enroll(client, ticket, public).json()['state'] == 'pending'
    assert enroll(client, ticket, public).status_code == 401
    status, _ = signed(client, private, ticket['id'], '/api/collector/enrollment/status')
    assert status.json() == {'state': 'pending', 'active': False}
    assert signed(client, private, ticket['id'])[0].status_code == 401
    dashboard = client.get('/api/dashboard').json()
    assert dashboard['controller_requests'] == 1
    row = client.get('/api/controllers').json()['devices'][0]
    assert row['source_ip'] == 'testclient' and row['hostname'] == 'servidor-fiap'
    assert row['fingerprint'] == hashlib.sha256(base64.b64decode(public)).hexdigest()
    login(client, 'support')
    assert client.post(f"/api/controllers/{ticket['id']}/approve", json={}).status_code == 403
    assert client.get('/api/controllers').status_code == 403
    login(client)
    assert client.post(f"/api/controllers/{ticket['id']}/approve", json={}).status_code == 200
    assert signed(client, private, ticket['id'])[0].status_code == 200
    payload = {'reports': [{'computer_id': '301-1', 'status': 'online', 'source': 'aws'}]}
    assert signed(client, private, ticket['id'], '/api/collector/reports', 'POST', payload)[0].status_code == 200
    assert client.post(f"/api/controllers/{ticket['id']}/revoke", json={}).status_code == 200
    assert signed(client, private, ticket['id'])[0].status_code == 401


def test_assinatura_rejeita_repeticao_corpo_alterado_e_chave_errada(client):
    _, ticket = package(client)
    private, public = identity()
    enroll(client, ticket, public).raise_for_status()
    client.post(f"/api/controllers/{ticket['id']}/approve", json={}).raise_for_status()
    response, headers = signed(client, private, ticket['id'], '/api/collector/inventory?probe=1')
    assert response.status_code == 200
    assert signed(client, private, ticket['id'], '/api/collector/inventory?probe=1', repeat=headers)[0].status_code == 401
    wrong, _ = identity()
    assert signed(client, wrong, ticket['id'])[0].status_code == 401
    body = {'reports': [{'computer_id': '301-1', 'status': 'online'}]}
    request = httpx.Request('POST', 'https://testserver/api/collector/reports', json=body)
    outgoing = next(DeviceAuth(ticket['id'], private).auth_flow(request))
    assert client.post('/api/collector/reports', content=b'{"reports":[]}', headers=dict(outgoing.headers)).status_code == 401


def test_convite_expirado_e_duas_aprovacoes_nao_abrem_acesso(client):
    _, first = package(client)
    private, public = identity()
    with connect(client.app.state.settings.database_path) as db:
        db.execute('UPDATE controller_devices SET expires=0 WHERE id=?', (first['id'],))
    assert enroll(client, first, public).status_code == 401
    _, second = package(client)
    enroll(client, second, public).raise_for_status()
    client.post(f"/api/controllers/{second['id']}/approve", json={}).raise_for_status()
    _, third = package(client)
    _, another = identity()
    enroll(client, third, another).raise_for_status()
    assert client.post(f"/api/controllers/{third['id']}/approve", json={}).status_code == 409
    assert client.post(f"/api/controllers/{third['id']}/reject", json={}).status_code == 200
    assert signed(client, private, first['id'])[0].status_code == 401


def test_token_compartilhado_nao_autentica_requisicao_http_externa(client):
    cfg = client.app.state.settings
    with TestClient(create_app(cfg), base_url=cfg.base_url) as external:
        response = external.get('/api/collector/inventory', headers={'Authorization': f'Bearer {cfg.collector_token}', 'X-Internal-Collector': 'true'})
        assert response.status_code == 401

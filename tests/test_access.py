import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app, digest
from backend.config import Settings
from backend.db import connect


PASSWORD = 'test-account-password-123'



def login(client, role='admin', password=PASSWORD):
    assert client.post('/auth/login', json={'username': f'{role}@fiap.com.br', 'password': password}).status_code == 200
    client.headers['x-csrf-token'] = client.get('/api/session').json()['user']['csrf']


def action(client, ids, operation='shutdown', key=None):
    return client.post('/api/actions', json={'action': operation, 'computer_ids': ids},
                       headers={'idempotency-key': key or str(uuid4())})


def test_anonymous_cannot_read_or_control(client):
    assert client.get('/api/dashboard').status_code == 401
    assert action(client, ['301-1']).status_code == 401


def test_monitor_sees_only_assigned_floor(client):
    login(client, 'monitor')
    data = client.get('/api/dashboard').json()
    assert len(data['computers']) == 26
    assert {c['floor'] for c in data['computers']} == {3}
    assert {l['floor'] for l in data['labs']} == {3}
    assert client.get('/api/users').status_code == 403


def test_cross_floor_batch_is_atomic(client):
    login(client, 'monitor')
    assert action(client, ['301-1', '201-1']).status_code == 403
    assert client.get('/api/dashboard').json()['jobs'] == []
    assert action(client, ['301-1']).status_code == 200


def test_viewer_cannot_control(client):
    login(client, 'viewer')
    assert action(client, ['301-1']).status_code == 403
    assert all(not c['can_control'] for c in client.get('/api/dashboard').json()['computers'])


def test_csrf_and_origin_are_required(client):
    login(client)
    client.headers.pop('x-csrf-token')
    assert action(client, ['301-1']).status_code == 403
    login(client)
    client.headers['origin'] = 'https://other.example'
    assert action(client, ['301-1']).status_code == 403


def test_idempotency_and_conflicting_command(client):
    login(client)
    key = str(uuid4())
    first = action(client, ['301-1'], key=key)
    assert first.status_code == 200
    assert action(client, ['301-1'], key=key).json() == first.json()
    assert action(client, ['301-2'], key=key).status_code == 409
    assert action(client, ['301-1'], 'restart').status_code == 409


def test_domain_and_self_lockout(client):
    login(client)
    payload = {'email': 'user@example.com', 'name': 'Test', 'role': 'monitor'}
    assert client.put('/api/users', json=payload).status_code == 422
    payload.update(id='test-admin', email='support@fiap.com.br', role='viewer')
    assert client.put('/api/users', json=payload).status_code == 409


def test_monitor_floor_reassignment(client):
    login(client)
    response = client.put('/api/users', json={'id': 'test-monitor', 'email': 'monitor@fiap.com.br', 'name': 'Monitor', 'role': 'monitor', 'grants': {'floors': [2]}})
    assert response.status_code == 200
    login(client, 'monitor')
    assert {c['floor'] for c in client.get('/api/dashboard').json()['computers']} == {2}
    assert action(client, ['301-1']).status_code == 403
    assert action(client, ['201-100']).status_code == 200


def test_correct_ip_mapping_and_professor(client):
    login(client)
    pcs = {c['id']: c for c in client.get('/api/dashboard').json()['computers']}
    assert pcs['301-1']['ip'] == '10.3.31.1'
    assert pcs['202-100']['ip'] == '10.3.22.100'
    assert pcs['1001-1']['ip'] == '10.3.101.1'
    assert client.post('/api/labs', json={'floor': 11, 'number': 3, 'label': 'Lab 1103', 'students': 2}).status_code == 200
    pcs = {c['id']: c for c in client.get('/api/dashboard').json()['computers']}
    assert pcs['1103-100']['ip'] == '10.3.113.100'


def test_mac_validation_and_collector_authentication(client):
    login(client)
    assert client.put('/api/computers/301-1', json={'mac': 'FF:FF:FF:FF:FF:FF'}).status_code == 422
    assert client.put('/api/computers/301-1', json={'mac': ''}).status_code == 200
    assert action(client, ['301-1'], 'wake').status_code == 409
    assert client.post('/api/collector/claim').status_code == 401


@pytest.fixture
def production(tmp_path, encoded_password):
    cfg = Settings(app_mode='production', base_url='https://testserver', cookie_secure=True,
                   collector_token='test-collector-token-that-is-long-enough',
                   session_secret='test-session-secret-that-is-long-enough', database_path=str(tmp_path/'production.db'))
    app = create_app(cfg)
    @app.middleware('http')
    async def internal_collector_fixture(request, call_next):
        if request.headers.get('Authorization') == f'Bearer {cfg.collector_token}':
            request.scope['fiap_internal_collector'] = True
        return await call_next(request)
    with TestClient(app, base_url=cfg.base_url) as client:
        with connect(cfg.database_path) as db:
            db.execute("INSERT INTO users(id,email,name,role) VALUES ('test-admin','test@fiap.com.br','Admin','admin')")
            db.execute('INSERT INTO credentials VALUES (?,?,?,?)', ('test-admin', 'test@fiap.com.br', encoded_password, time.time()))
            db.execute("INSERT INTO labs VALUES ('301',3,1,'Lab 301','10.3.31.0/24','10.3.31.255','campus')")
            db.execute("INSERT INTO labs VALUES ('302',3,2,'Lab 302','10.3.32.0/24','10.3.32.255','another-controller')")
            db.execute("INSERT INTO computers(id,lab_id,number,ip,mac) VALUES ('301-1','301',1,'10.3.31.1','00:11:22:33:44:55')")
            db.execute("INSERT INTO computers(id,lab_id,number,ip) VALUES ('302-1','302',1,'10.3.32.1')")
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest('fixture-session'), 'test-admin', time.time()+600, 'fixture-csrf'))
        from itsdangerous import TimestampSigner
        import base64
        cookie = TimestampSigner(cfg.session_secret).sign(base64.b64encode(json.dumps({'sid': 'fixture-session'}).encode())).decode()
        client.cookies.set('switch_session', cookie)
        client.headers.update({'origin': cfg.base_url, 'x-csrf-token': 'fixture-csrf'})
        yield client, cfg


def test_production_has_no_demo_login(production):
    client, cfg = production
    assert client.post('/auth/demo').status_code in (404, 405)
    assert client.get('/auth/login').status_code == 503
    assert client.get('/api/session').json()['auth_mode'] == 'local'
    assert client.post('/api/collector/claim').status_code == 401


def test_real_collector_protocol_lease_and_tenant_scope(production):
    client, cfg = production
    response = action(client, ['301-1'])
    assert response.status_code == 200
    headers = {'authorization': f'Bearer {cfg.collector_token}'}
    claimed = client.post('/api/collector/claim', headers=headers).json()['job']
    assert claimed['computer_id'] == '301-1'
    assert client.post('/api/collector/claim', headers=headers).json()['job'] is None
    result = {'state': 'completed', 'message': 'Accepted by Windows.', 'lease_token': 'wrong'}
    assert client.post(f"/api/collector/jobs/{claimed['id']}/result", json=result, headers=headers).status_code == 409
    result['lease_token'] = claimed['lease_token']
    assert client.post(f"/api/collector/jobs/{claimed['id']}/result", json=result, headers=headers).status_code == 200
    assert client.post(f"/api/collector/jobs/{claimed['id']}/result", json=result, headers=headers).status_code == 409
    assert client.post('/api/collector/reports', json={'reports': [{'computer_id': '302-1', 'status': 'online'}]}, headers=headers).status_code == 403
    assert client.post('/api/collector/reports', json={'reports': [{'computer_id': '301-1', 'status': 'offline'}]}, headers=headers).status_code == 422
    assert client.post('/api/collector/reports', json={'reports': [{'computer_id': '301-1', 'status': 'online', 'metrics': {'cpu': 12}}]}, headers=headers).status_code == 200
    client.post('/api/collector/reports', json={'reports': [{'computer_id': '301-1', 'status': 'online', 'mac': '02:11:22:33:44:55'}]}, headers=headers)
    assert client.get('/api/dashboard').json()['computers'][0]['mac'] == '00:11:22:33:44:55'
    assert client.get('/api/dashboard').json()['computers'][0]['status'] == 'online'
    with connect(cfg.database_path) as db:
        db.execute('UPDATE computers SET last_seen=?', (time.time()-300,))
    assert client.get('/api/dashboard').json()['computers'][0]['status'] == 'unknown'


def test_legacy_demo_database_cannot_be_reused_in_production(client):
    cfg = client.app.state.settings
    with connect(cfg.database_path) as db:
        db.execute("UPDATE metadata SET value='demo' WHERE key='mode'")
    with pytest.raises(ValueError, match='bancos de dados separados'):
        with TestClient(create_app(cfg)):
            pass


def test_queued_command_is_rejected_after_access_revocation(production):
    client, cfg = production
    assert action(client, ['301-1']).status_code == 200
    with connect(cfg.database_path) as db:
        db.execute("UPDATE users SET enabled=0 WHERE id='test-admin'")
    response = client.post('/api/collector/claim', headers={'authorization': f'Bearer {cfg.collector_token}'})
    assert response.json()['job'] is None
    with connect(cfg.database_path) as db:
        assert db.execute('SELECT state FROM jobs').fetchone()['state'] == 'failed'

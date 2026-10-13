import base64
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import connect, initialize
from backend.passwords import hash_password, needs_rehash, verify_password

PASSWORD = 'initial-account-password-123'


@pytest.fixture(scope='module')
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def config(tmp_path, encoded_password):
    return Settings(base_url='https://testserver',
        session_secret='a-test-session-secret-long-enough', collector_token='a-test-collector-secret-long-enough',
        database_path=str(tmp_path/'switch.db'), bootstrap_admin_email='admin@fiap.com.br',
        bootstrap_admin_password_hash=encoded_password, bootstrap_admin_name='Administrator')


def sign_in(client, email='admin@fiap.com.br', password=PASSWORD):
    client.headers['origin'] = 'https://testserver'
    response = client.post('/auth/login', json={'username': email, 'password': password})
    if response.status_code == 200:
        client.headers['x-csrf-token'] = client.get('/api/session').json()['user']['csrf']
    return response


def test_secure_session_and_private_credentials(config, encoded_password):
    assert verify_password(PASSWORD, encoded_password)
    assert not verify_password('wrong', encoded_password)
    assert not verify_password(PASSWORD, 'invalid')
    assert not needs_rehash(encoded_password)
    with TestClient(create_app(config), base_url=config.base_url) as client:
        result = sign_in(client, ' ADMIN@FIAP.COM.BR ')
        assert result.status_code == 200
        cookie = result.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
        user = client.get('/api/session').json()['user']
        assert user['auth_type'] == 'local' and user['role'] == 'admin'
        accounts = client.get('/api/users')
        assert encoded_password not in accounts.text and PASSWORD not in accounts.text
        assert set(accounts.json()[0]) == {'id', 'name', 'email', 'role', 'enabled', 'grants', 'auth_type'}
        assert client.get('/api/dashboard').json()['computers'] == []
        assert client.post('/auth/demo').status_code in (404, 405)
        assert client.post('/auth/temporary').status_code in (404, 405)
        assert client.post('/auth/logout').status_code == 200
        assert client.get('/api/dashboard').status_code == 401


def test_rate_limit_persists_across_restarts(config):
    with TestClient(create_app(config), base_url=config.base_url) as client:
        for _ in range(5):
            assert sign_in(client, password='wrong').status_code == 401
        assert sign_in(client).status_code == 429
    with TestClient(create_app(config), base_url=config.base_url) as client:
        assert sign_in(client).status_code == 429
    with TestClient(create_app(config), base_url=config.base_url, client=('another-ip', 50000)) as client:
        assert sign_in(client).status_code == 200


def test_account_limit_applies_across_ips(config):
    app = create_app(config)
    for index in range(10):
        with TestClient(app, base_url=config.base_url, client=(f'ip-{index}', 50000)) as client:
            assert sign_in(client, password='wrong').status_code == 401
    with TestClient(app, base_url=config.base_url, client=('new-ip', 50000)) as client:
        assert sign_in(client).status_code == 429


def test_account_creation_reset_and_disable(config):
    app = create_app(config)
    with TestClient(app, base_url=config.base_url) as admin:
        assert sign_in(admin).status_code == 200
        payload = {'email': 'monitor@fiap.com.br', 'name': 'Monitor', 'role': 'monitor', 'grants': {'floors': [3]}}
        assert admin.put('/api/users', json=payload).status_code == 422
        payload['password'] = 'new-account-password-123'
        created = admin.put('/api/users', json=payload)
        assert created.status_code == 200
        uid = created.json()['id']
        assert admin.put('/api/users', json=payload).status_code == 409
        with TestClient(app, base_url=config.base_url, client=('monitor-ip', 50000)) as monitor:
            assert sign_in(monitor, payload['email'], payload['password']).status_code == 200
            assert monitor.get('/api/users').status_code == 403
            payload.update(id=uid, password='reset-account-password-456')
            assert admin.put('/api/users', json=payload).status_code == 200
            assert monitor.get('/api/dashboard').status_code == 401
            assert sign_in(monitor, payload['email'], 'new-account-password-123').status_code == 401
            assert sign_in(monitor, payload['email'], payload['password']).status_code == 200
            payload['enabled'] = False
            assert admin.put('/api/users', json=payload).status_code == 200
            assert monitor.get('/api/dashboard').status_code == 401
            assert sign_in(monitor, payload['email'], payload['password']).status_code == 401


def test_password_change_revokes_other_sessions(config):
    app = create_app(config)
    with TestClient(app, base_url=config.base_url) as first, TestClient(app, base_url=config.base_url, client=('second-ip', 50000)) as second:
        assert sign_in(first).status_code == 200
        assert sign_in(second).status_code == 200
        old_csrf = first.headers['x-csrf-token']
        payload = {'current_password': PASSWORD, 'new_password': 'replacement-password-456'}
        assert first.post('/auth/password', json={**payload, 'current_password': 'wrong'}).status_code == 400
        assert first.post('/auth/password', json=payload, headers={'x-csrf-token': 'wrong'}).status_code == 403
        assert first.post('/auth/password', json=payload).status_code == 200
        assert second.get('/api/dashboard').status_code == 401
        assert first.get('/api/session').json()['user']['csrf'] != old_csrf
        assert sign_in(second, password=PASSWORD).status_code == 401
        assert sign_in(second, password=payload['new_password']).status_code == 200
        with connect(config.database_path) as db:
            assert db.execute("SELECT 1 FROM audit WHERE action='password_changed'").fetchone()
            assert verify_password(payload['new_password'], db.execute('SELECT password_hash FROM credentials').fetchone()[0])


def test_validation_does_not_echo_passwords(config):
    with TestClient(create_app(config), base_url=config.base_url) as client:
        assert sign_in(client).status_code == 200
        payload = {'email': 'new@fiap.com.br', 'name': 'New', 'role': 'monitor', 'password': 'private-short'}
        response = client.put('/api/users', json=payload)
        assert response.status_code == 422
        assert payload['password'] not in response.text
        assert client.put('/api/users', json={**payload, 'password': PASSWORD, 'grants': {'labs': ['999']}}).status_code == 422
        uid = client.get('/api/session').json()['user']['id']
        assert client.put('/api/users', json={'id': uid, 'email': 'admin@fiap.com.br', 'name': 'Admin', 'role': 'admin', 'enabled': False}).status_code == 409


def test_legacy_login_migrates_without_overwriting_password(config):
    config.bootstrap_admin_email = ''
    initialize(config)
    salt = b'legacy-test-salt'
    key = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    legacy = 'scrypt$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(key).decode()
    with connect(config.database_path) as db:
        db.execute("INSERT INTO users VALUES ('temporary-admin','admin@fiap.com.br','Admin','admin',1,'{}')")
        db.execute("INSERT INTO metadata VALUES ('temporary_password_hash',?)", (legacy,))
    with TestClient(create_app(config), base_url=config.base_url) as client:
        assert sign_in(client).status_code == 200
        assert client.get('/api/session').json()['user']['id'] == 'temporary-admin'
        with connect(config.database_path) as db:
            assert not db.execute("SELECT 1 FROM metadata WHERE key='temporary_password_hash'").fetchone()
            upgraded = db.execute('SELECT password_hash FROM credentials').fetchone()[0]
            assert not needs_rehash(upgraded) and verify_password(PASSWORD, upgraded)
    config.bootstrap_admin_email = 'different@fiap.com.br'
    with TestClient(create_app(config), base_url=config.base_url) as client:
        assert sign_in(client).status_code == 200
        with connect(config.database_path) as db:
            assert db.execute('SELECT COUNT(*) FROM credentials').fetchone()[0] == 1


def test_explicit_entra_mode_rejects_local_sessions(config):
    with TestClient(create_app(config), base_url=config.base_url) as client:
        assert sign_in(client).status_code == 200
        config.auth_mode = 'entra'
        config.entra_tenant_id = '11111111-1111-1111-1111-111111111111'
        config.entra_client_id, config.entra_client_secret = 'test-client', 'test-secret'
        assert sign_in(client).status_code == 404
        assert client.get('/api/dashboard').status_code == 401


def test_login_rechecks_revocation_before_creating_session(config, monkeypatch):
    app = create_app(config)
    def revoke_during_verification(password, encoded):
        valid = verify_password(password, encoded)
        with connect(config.database_path) as db:
            db.execute('UPDATE users SET enabled=0')
        return valid
    monkeypatch.setattr('backend.app.verify_password', revoke_during_verification)
    with TestClient(app, base_url=config.base_url) as client:
        assert sign_in(client).status_code == 401
        with connect(config.database_path) as db:
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0


def test_password_change_rechecks_revoked_session(config, monkeypatch):
    app = create_app(config)
    with TestClient(app, base_url=config.base_url) as client:
        assert sign_in(client).status_code == 200
        def revoke_during_hash(password):
            with connect(config.database_path) as db:
                db.execute('DELETE FROM sessions')
            return hash_password(password)
        monkeypatch.setattr('backend.app.hash_password', revoke_during_hash)
        assert client.post('/auth/password', json={'current_password': PASSWORD, 'new_password': 'replacement-password-456'}).status_code == 401
        with connect(config.database_path) as db:
            assert verify_password(PASSWORD, db.execute('SELECT password_hash FROM credentials').fetchone()[0])


@pytest.mark.parametrize('changes', [
    {'session_secret': ''}, {'collector_token': 'short'}, {'base_url': 'http://testserver'},
    {'cookie_secure': False}, {'auth_mode': 'entra'}, {'bootstrap_admin_email': 'admin@example.com'},
])
def test_invalid_production_configuration_is_rejected(config, changes):
    for key, value in changes.items():
        setattr(config, key, value)
    with pytest.raises(ValueError):
        create_app(config)

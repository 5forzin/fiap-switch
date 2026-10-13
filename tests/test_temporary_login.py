import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.passwords import hash_password, verify_password


def test_temporary_login_is_hashed_rate_limited_and_disabled_by_sso(tmp_path):
    password = 'local-test-password-123'
    hashed = hash_password(password)
    assert password not in hashed
    assert verify_password(password, hashed)
    assert not verify_password('wrong', hashed)
    cfg = Settings(app_mode='production', base_url='https://testserver',
        session_secret='a-test-session-secret-long-enough', collector_token='a-test-collector-secret-long-enough',
        database_path=str(tmp_path/'production.db'), temporary_login_enabled=True,
        temporary_username='admin', temporary_password_hash=hashed)
    with TestClient(create_app(cfg), base_url=cfg.base_url) as client:
        client.headers['origin'] = cfg.base_url
        assert client.get('/api/session').json()['temporary_login_ready']
        for _ in range(5):
            assert client.post('/auth/temporary', json={'username': 'admin', 'password': 'wrong'}).status_code == 401
        assert client.post('/auth/temporary', json={'username': 'admin', 'password': password}).status_code == 429
    with TestClient(create_app(cfg), base_url=cfg.base_url, client=('other-ip', 50000)) as client:
        client.headers['origin'] = cfg.base_url
        result = client.post('/auth/temporary', json={'username': 'admin', 'password': password})
        assert result.status_code == 200
        assert 'httponly' in result.headers['set-cookie'].lower()
        assert 'secure' in result.headers['set-cookie'].lower()
        assert client.get('/api/session').json()['user']['id'] == 'temporary-admin'
        assert client.get('/api/dashboard').status_code == 200
        # Activation of corporate SSO also rejects existing temporary sessions.
        cfg.entra_tenant_id = '11111111-1111-1111-1111-111111111111'
        cfg.entra_client_id = 'test-client'
        cfg.entra_client_secret = 'test-secret'
        assert client.post('/auth/temporary', json={'username': 'admin', 'password': password}).status_code == 404
        assert client.get('/api/dashboard').status_code == 401

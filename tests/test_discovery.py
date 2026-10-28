from unittest.mock import MagicMock, patch

import pytest
from test_access import login
from test_checks_hardware import HARDWARE, STANDARD

from backend.db import connect
from collector.service import Controller


def collector_headers(client):
    return {'Authorization': f'Bearer {client.app.state.settings.collector_token}'}


def create_room(client):
    login(client)
    response = client.post('/api/labs', json={'floor': 3, 'number': 9, 'label': 'Sala 309', 'students': None, 'include_professor': False})
    assert response.status_code == 200
    assert client.put('/api/labs/309/hardware-standard', json=STANDARD).status_code == 200


def found(number=4):
    return {'number': number, 'hardware': HARDWARE, 'mac': '00:11:22:33:44:55', 'metrics': {'cpu': 10}}


def test_descoberta_de_sala_vazia_historico_hardware_e_repeticao_sem_duplicar(client):
    create_room(client)
    login(client, 'monitor')
    assert any(l['id'] == '309' for l in client.get('/api/dashboard').json()['labs'])
    check_id = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    assert client.post('/api/checks', json={'lab_id': '309'}).json()['id'] == check_id
    detail = client.get('/api/checks/' + check_id).json()
    assert detail['summary']['total'] == 0 and detail['state'] == 'queued'
    assert any(c['id'] == check_id for c in client.get('/api/checks').json())
    headers = collector_headers(client)
    task = next(t for t in client.get('/api/collector/discovery', headers=headers).json() if t['check_id'] == check_id)
    assert task['known_ips'] == [] and task['subnet'] == '10.3.39.0/24'
    url = f'/api/collector/discovery/{check_id}/309'
    result = client.post(url, json={'computers': [found(), found()]}, headers=headers)
    assert result.status_code == 200 and result.json()['added'] == 1
    assert client.post(url, json={'computers': [found()]}, headers=headers).json()['added'] == 0
    detail = client.get('/api/checks/' + check_id).json()
    assert detail['state'] == 'completed' and detail['summary']['online'] == 1
    assert detail['computers'][0]['hardware_state'] == 'match'
    assert detail['computers'][0]['ip'] == '10.3.39.4'
    assert client.get('/api/history').json()[0]['summary']['online'] == 1
    next_check = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    task = next(t for t in client.get('/api/collector/discovery', headers=headers).json() if t['check_id'] == next_check)
    assert task['known_ips'] == ['10.3.39.4']
    assert client.post(f'/api/collector/discovery/{next_check}/309', json={'computers': [found(), found(100)]}, headers=headers).json()['added'] == 1
    pcs = [p for p in client.get('/api/dashboard').json()['computers'] if p['lab_id'] == '309']
    assert len(pcs) == 2 and {p['number'] for p in pcs} == {4, 100}
    assert next(l for l in client.get('/api/dashboard').json()['labs'] if l['id'] == '309')['student_count'] is None


def test_descoberta_vazia_conclui_sem_inventar_maquinas(client):
    create_room(client)
    check_id = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    assert client.post(f'/api/collector/discovery/{check_id}/309', json={'computers': []}, headers=collector_headers(client)).status_code == 200
    detail = client.get('/api/checks/' + check_id)
    assert detail.status_code == 200 and detail.json()['state'] == 'completed'
    assert detail.json()['summary']['total'] == 0


def test_descoberta_respeita_controlador_andar_e_mudanca_de_quantidade(client):
    create_room(client)
    login(client, 'monitor')
    assert client.post('/api/checks', json={'floor': 2}).status_code == 404
    check_id = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    url = f'/api/collector/discovery/{check_id}/309'
    assert client.post(url, json={'computers': [found()]}).status_code == 401
    with connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE labs SET collector_id='outro' WHERE id='309'")
    assert client.post(url, json={'computers': [found()]}, headers=collector_headers(client)).status_code == 404
    with connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE labs SET collector_id='campus' WHERE id='309'")
    login(client)
    assert client.put('/api/labs/309/quantity', json={'students': 0}).status_code == 200
    assert client.post(url, json={'computers': [found()]}, headers=collector_headers(client)).json()['added'] == 0
    with connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM computers WHERE lab_id='309'").fetchone()[0] == 0


def test_descoberta_administrativa_vazia_continua_privada(client):
    create_room(client)
    check_id = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    login(client, 'monitor')
    assert client.get('/api/checks/' + check_id).status_code == 404
    assert all(c['id'] != check_id for c in client.get('/api/checks').json())
    assert client.post(f'/api/collector/discovery/{check_id}/309', json={'computers': [found()]}, headers=collector_headers(client)).status_code == 200
    assert all(a['actor_role'] != 'admin' for a in client.get('/api/audit').json())
    assert client.get('/api/checks/' + check_id).status_code == 404


def test_resultados_conhecidos_aguardam_descoberta_e_varredura_seguinte_nao_duplica(client):
    from test_checks_hardware import report

    create_room(client)
    headers = collector_headers(client)
    first = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    client.post(f'/api/collector/discovery/{first}/309', json={'computers': [found()]}, headers=headers).raise_for_status()
    second = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    report(client, '309-4')
    assert client.get('/api/checks/' + second).json()['state'] == 'queued'
    assert client.post(f'/api/collector/discovery/{second}/309', json={'computers': [found()]}, headers=headers).json()['added'] == 0
    detail = client.get('/api/checks/' + second).json()
    assert detail['state'] == 'completed' and detail['summary']['total'] == 1


def test_descoberta_invalida_e_conflito_nao_sobrescrevem_inventario(client):
    create_room(client)
    check_id = client.post('/api/checks', json={'lab_id': '309'}).json()['id']
    url = f'/api/collector/discovery/{check_id}/309'
    headers = collector_headers(client)
    assert client.post(url, json={'computers': [found(101)]}, headers=headers).status_code == 422
    assert client.post(url, json={'computers': [{**found(), 'status': 'unreachable'}]}, headers=headers).status_code == 422
    with connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE computers SET ip='10.3.39.4' WHERE id='301-1'")
    assert client.post(url, json={'computers': [found()]}, headers=headers).status_code == 409
    with connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM computers WHERE lab_id='309'").fetchone()[0] == 0


@pytest.fixture
def controller(monkeypatch):
    for key, value in {'SWITCH_URL': 'https://switch.example.com', 'COLLECTOR_TOKEN': 'test-token', 'WINRM_USERNAME': 'svc', 'WINRM_PASSWORD': 'test-password'}.items():
        monkeypatch.setenv(key, value)
    return Controller()


def test_controlador_varre_somente_ips_novos_e_cadastra_so_winrm_autenticado(controller):
    task = {'check_id': 'scan', 'lab_id': '301', 'subnet': '10.3.31.0/24', 'known_ips': ['10.3.31.1', '10.3.31.100']}
    controller.client = MagicMock()
    controller.client.get.return_value.json.return_value = [task]
    def connect(address, timeout):
        if address[0] not in ('10.3.31.4', '10.3.31.5'):
            raise TimeoutError
        return MagicMock()
    def probe(pc):
        return {'computer_id': pc['id'], 'status': 'unreachable'} if pc['ip'].endswith('.4') else {'computer_id': pc['id'], 'status': 'online', 'hardware': HARDWARE, 'metrics': {}, 'mac': ''}
    with patch('collector.service.socket.create_connection', side_effect=connect) as tcp, patch.object(controller, 'probe', side_effect=probe), patch.object(controller, 'post', return_value={'added': 1}) as post:
        controller.discover()
        attempted = {call.args[0][0] for call in tcp.call_args_list}
        assert '10.3.31.1' not in attempted and '10.3.31.100' not in attempted
        assert attempted == {f'10.3.31.{n}' for n in range(2, 100)}
        assert [c['number'] for c in post.call_args.args[1]['computers']] == [5]


def test_controlador_rejeita_descoberta_fora_da_lan(controller):
    controller.client = MagicMock()
    controller.client.get.return_value.json.return_value = [{'check_id': 'scan', 'lab_id': 'fora', 'subnet': '8.8.8.0/24', 'known_ips': []}]
    with patch('collector.service.socket.create_connection') as tcp, pytest.raises(ValueError):
        controller.discover()
    assert not tcp.called

from argparse import Namespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from test_access import action, login

from backend.app import create_app
from backend.config import Settings
from backend.db import connect, initialize
from backend.passwords import verify_password
from collector.power import execute_cloud
from collector.service import Controller
from scripts.setup import configure


AWS = {'mode': 'aws', 'aws_region': 'us-east-2', 'aws_vpc_id': 'vpc-0123456789abcdef0'}
AZURE = {'mode': 'azure', 'azure_subscription_id': '11111111-1111-4111-8111-111111111111', 'azure_resource_group': 'fiap-labs'}


@pytest.mark.parametrize('mode', ['fiap', 'aws', 'azure'])
def test_setup_cria_admin_automatico_sem_resetar_e_preserva_dolares_no_hash(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)
    args = Namespace(mode=mode, url='http://localhost:8000', port=8000, admin_email='admin@fiap.com.br',
                     aws_region=AWS['aws_region'], aws_vpc_id=AWS['aws_vpc_id'],
                     azure_subscription_id=AZURE['azure_subscription_id'], azure_resource_group=AZURE['azure_resource_group'])
    values, password = configure(args)
    saved = dotenv_values('.env')
    assert saved['BOOTSTRAP_ADMIN_PASSWORD_HASH'] == values['BOOTSTRAP_ADMIN_PASSWORD_HASH']
    assert verify_password(password, saved['BOOTSTRAP_ADMIN_PASSWORD_HASH'])
    assert password not in (tmp_path / '.env').read_text()
    assert dotenv_values('.env.collector')['COLLECTOR_TOKEN'] == saved['COLLECTOR_TOKEN']
    cfg = Settings()
    assert cfg.power_mode == mode
    with TestClient(create_app(cfg), base_url=cfg.base_url, headers={'Origin': cfg.base_url}) as client:
        assert client.post('/auth/login', json={'username': args.admin_email, 'password': password}).status_code == 200
        assert client.get('/api/session').json()['user']['role'] == 'admin'
    existing, new_password = configure(args)
    assert existing == saved and new_password is None


def test_fila_usa_api_em_nuvem_sem_exigir_mac_e_mudanca_de_modo_invalida_fila(client):
    cfg = client.app.state.settings
    cfg.power_mode, cfg.aws_region, cfg.aws_vpc_id = 'aws', AWS['aws_region'], AWS['aws_vpc_id']
    initialize(cfg)
    login(client, 'monitor')
    with connect(cfg.database_path) as db:
        db.execute("UPDATE computers SET mac='' WHERE id='301-1'")
    assert action(client, ['301-1'], 'wake').status_code == 200
    claim = client.post('/api/collector/claim', headers={'Authorization': f'Bearer {cfg.collector_token}'}).json()['job']
    assert claim['power']['mode'] == 'aws' and claim['power']['aws_vpc_id'] == AWS['aws_vpc_id']
    assert action(client, ['201-1'], 'wake').status_code == 403
    assert action(client, ['301-2'], 'wake').status_code == 200
    cfg.power_mode = 'fiap'
    initialize(cfg)
    with connect(cfg.database_path) as db:
        assert db.execute("SELECT state FROM jobs WHERE computer_id='301-2'").fetchone()['state'] == 'failed'


@pytest.mark.parametrize('action_name,operation', [('wake', 'start_instances'), ('shutdown', 'stop_instances'), ('restart', 'reboot_instances')])
def test_aws_mapeia_ip_na_vpc_e_envia_somente_a_operacao_permitida(action_name, operation):
    ec2 = MagicMock()
    ec2.get_paginator.return_value.paginate.return_value = [{'Reservations': [{'Instances': [{'InstanceId': 'i-0123456789abcdef0', 'PrivateIpAddress': '10.3.22.4', 'VpcId': AWS['aws_vpc_id']}]}]}]
    with patch('boto3.client', return_value=ec2) as factory:
        assert 'AWS' in execute_cloud({'power': AWS, 'ip': '10.3.22.4', 'action': action_name})
    factory.assert_called_once_with('ec2', region_name='us-east-2')
    getattr(ec2, operation).assert_called_once_with(InstanceIds=['i-0123456789abcdef0'])
    filters = ec2.get_paginator.return_value.paginate.call_args.kwargs['Filters']
    assert {'Name': 'vpc-id', 'Values': [AWS['aws_vpc_id']]} in filters
    assert {'Name': 'private-ip-address', 'Values': ['10.3.22.4']} in filters


def test_aws_ip_ambiguo_nao_envia_comando():
    ec2 = MagicMock()
    ec2.get_paginator.return_value.paginate.return_value = [{'Reservations': [{'Instances': [{}, {}]}]}]
    with patch('boto3.client', return_value=ec2), pytest.raises(ValueError):
        execute_cloud({'power': AWS, 'ip': '10.3.22.4', 'action': 'wake'})
    assert not ec2.start_instances.called


@pytest.mark.parametrize('action_name,operation', [('wake', 'start'), ('shutdown', 'deallocate'), ('restart', 'restart')])
def test_azure_resolve_nic_pelo_ip_e_aciona_api_do_grupo(action_name, operation):
    prefix = f"/subscriptions/{AZURE['azure_subscription_id']}/resourceGroups/{AZURE['azure_resource_group']}"
    vm = prefix + '/providers/Microsoft.Compute/virtualMachines/pc202-4'
    requests = []
    def handle(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'value': [{'properties': {'virtualMachine': {'id': vm}, 'ipConfigurations': [{'properties': {'privateIPAddress': '10.3.22.4'}}]}}]})
        return httpx.Response(202)
    credential = MagicMock()
    credential.get_token.return_value.token = 'token-de-teste'
    original = httpx.Client
    with patch('azure.identity.DefaultAzureCredential', return_value=credential), patch('collector.power.httpx.Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw)):
        assert 'Azure' in execute_cloud({'power': AZURE, 'ip': '10.3.22.4', 'action': action_name})
    assert requests[-1].method == 'POST' and requests[-1].url.path == vm + '/' + operation
    assert requests[-1].url.params['api-version'] == '2025-04-01'
    assert requests[-1].headers['Authorization'] == 'Bearer token-de-teste'
    credential.close.assert_called_once()


def test_azure_nao_transmite_token_para_paginacao_externa():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'value': [], 'nextLink': 'https://outro.example/roubar-token'})
    original = httpx.Client
    with patch('azure.identity.DefaultAzureCredential'), patch('collector.power.httpx.Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handle), **kw)), pytest.raises(ValueError):
        execute_cloud({'power': AZURE, 'ip': '10.3.22.4', 'action': 'wake'})
    assert len(requests) == 1 and requests[0].url.host == 'management.azure.com'


def test_controlador_nunca_recai_em_wol_quando_api_falha(monkeypatch):
    for key, value in {'SWITCH_URL': 'https://switch.example.com', 'COLLECTOR_TOKEN': 'test-token', 'WINRM_USERNAME': 'svc', 'WINRM_PASSWORD': 'test-password'}.items():
        monkeypatch.setenv(key, value)
    controller = Controller()
    with patch('collector.service.execute_cloud', side_effect=RuntimeError), patch.object(controller, 'wake') as wake, patch.object(controller, 'session') as winrm, pytest.raises(RuntimeError):
        controller.execute({'power': AWS, 'ip': '10.3.22.4', 'action': 'wake'})
    assert not wake.called and not winrm.called

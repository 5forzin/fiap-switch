import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.native_cloud import AwsInventory, machine_report


def test_inventario_aws_usa_configuracao_real_da_vm_e_volumes_sem_comandos_no_so():
    ec2 = MagicMock()
    ec2.get_paginator.return_value.paginate.return_value = [{'Reservations': [{'Instances': [{
        'InstanceType': 't3.small', 'PrivateIpAddress': '10.3.22.4', 'State': {'Name': 'running'},
        'CpuOptions': {'CoreCount': 1, 'ThreadsPerCore': 2},
        'BlockDeviceMappings': [{'Ebs': {'VolumeId': 'vol-root'}}, {'Ebs': {'VolumeId': 'vol-extra'}}],
    }]}]}]
    ec2.describe_instance_types.return_value = {'InstanceTypes': [{'InstanceType': 't3.small', 'VCpuInfo': {'DefaultVCpus': 2}, 'MemoryInfo': {'SizeInMiB': 2048}, 'ProcessorInfo': {'Manufacturer': 'Intel'}}]}
    ec2.describe_volumes.return_value = {'Volumes': [{'VolumeId': 'vol-root', 'Size': 30}, {'VolumeId': 'vol-extra', 'Size': 8}]}
    with patch('boto3.client', return_value=ec2):
        native = AwsInventory({'aws_region': 'us-east-2', 'aws_vpc_id': 'vpc-0123456789abcdef0'})
        machines = native.inventory()
    pc = machine_report({'id': '202-4', 'ip': '10.3.22.4'}, machines, 'aws')
    assert pc['status'] == 'online' and pc['source'] == 'aws'
    assert pc['hardware']['memory_gib'] == 2 and pc['hardware']['storage_gib'] == 38 and pc['hardware']['disk_count'] == 2
    filters = ec2.get_paginator.return_value.paginate.call_args.kwargs['Filters']
    assert {'Name': 'tag:FIAPSwitchManaged', 'Values': ['true']} in filters
    assert set(name for name, _, _ in ec2.method_calls) == {'get_paginator', 'describe_instance_types', 'describe_volumes'}


def test_api_nao_inventa_saude_do_windows_ou_inventario_de_vm_desligada():
    pc = {'id': '202-4', 'ip': '10.3.22.4'}
    assert machine_report(pc, {}, 'aws')['status'] == 'unknown'
    stopped = machine_report(pc, {pc['ip']: {'running': False, 'hardware': {}}}, 'aws')
    assert stopped['status'] == 'unreachable' and 'hardware' not in stopped


def test_modo_aws_descobre_e_executa_fila_sem_controlador_externo(tmp_path, encoded_password, monkeypatch):
    import backend.cloud_worker as worker

    calls = []
    class Native:
        def __init__(self, config):
            pass
        def inventory(self):
            return {'10.3.21.4': {'running': True, 'hardware': {'logical_cpus': 2, 'memory_gib': 2, 'storage_gib': 30, 'disk_count': 1}}}
        def close(self):
            pass
    monkeypatch.setattr(worker, 'AwsInventory', Native)
    def execute(job):
        calls.append(job)
        return 'Aceito pela API AWS.'
    monkeypatch.setattr(worker, 'execute_cloud', execute)
    cfg = Settings(base_url='http://localhost', environment='development', cookie_secure=False,
        database_path=str(tmp_path/'cloud.db'), session_secret='test-session-secret-that-is-long-enough', collector_token='internal-secret-that-is-long-enough',
        power_mode='aws', aws_region='us-east-2', aws_vpc_id='vpc-0123456789abcdef0',
        bootstrap_admin_email='admin@fiap.com.br', bootstrap_admin_password_hash=encoded_password)
    with TestClient(create_app(cfg), base_url=cfg.base_url, headers={'Origin': cfg.base_url}) as client:
        assert client.post('/auth/login', json={'username':'admin@fiap.com.br','password':'test-account-password-123'}).status_code==200
        client.headers['X-CSRF-Token']=client.get('/api/session').json()['user']['csrf']
        assert client.post('/api/labs',json={'floor':2,'number':1,'label':'Lab 201','students':None,'include_professor':False}).status_code==200
        check = client.post('/api/checks',json={'floor':2}).json()['id']
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            detail=client.get('/api/checks/'+check).json()
            if detail['state']=='completed': break
            time.sleep(0.1)
        assert detail['state']=='completed' and detail['summary']['total']==1
        pc=client.get('/api/dashboard').json()['computers'][0]
        assert pc['hardware_source']=='aws' and pc['id']=='201-4'
        assert client.get('/api/collector/inventory',headers={'Authorization':f'Bearer {cfg.collector_token}'}).status_code==401
        from uuid import uuid4
        action=client.post('/api/actions',json={'action':'wake','computer_ids':['201-4']},headers={'Idempotency-Key':str(uuid4())})
        assert action.status_code==200
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            job=client.get('/api/dashboard').json()['jobs'][0]
            if job['state']=='completed': break
            time.sleep(0.1)
        assert job['state']=='completed' and calls[0]['power']['mode']=='aws'

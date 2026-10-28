"""Energia executada pelo próprio painel nas APIs de nuvem autorizadas."""

import re
from urllib.parse import urlparse
from uuid import UUID

import httpx


def execute_cloud(job):
    config = job['power']
    if job['action'] not in ('wake', 'shutdown', 'restart'):
        raise ValueError('Comando de energia não permitido.')
    if config['mode'] == 'aws':
        return aws_power(job, config)
    if config['mode'] == 'azure':
        return azure_power(job, config)
    raise ValueError('Provedor de nuvem inválido.')


def aws_power(job, config):
    import boto3

    region, vpc = config['aws_region'], config['aws_vpc_id']
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', region) or not re.fullmatch(r'vpc-[0-9a-f]{8,17}', vpc):
        raise ValueError('Configure a região e a VPC da AWS.')
    ec2 = boto3.client('ec2', region_name=region)
    matches = [instance for page in ec2.get_paginator('describe_instances').paginate(Filters=[
        {'Name': 'vpc-id', 'Values': [vpc]}, {'Name': 'private-ip-address', 'Values': [job['ip']]},
        {'Name': 'tag:FIAPSwitchManaged', 'Values': ['true']},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ]) for reservation in page['Reservations'] for instance in reservation['Instances']]
    if len(matches) != 1 or matches[0]['PrivateIpAddress'] != job['ip'] or matches[0]['VpcId'] != vpc:
        raise ValueError('O IP deve identificar uma única EC2 na VPC configurada.')
    operation = {'wake': ec2.start_instances, 'shutdown': ec2.stop_instances, 'restart': ec2.reboot_instances}[job['action']]
    operation(InstanceIds=[matches[0]['InstanceId']])
    return 'Comando aceito pela API da AWS. Aguarde a próxima leitura de estado.'


def azure_power(job, config):
    from azure.identity import DefaultAzureCredential

    subscription, group = config['azure_subscription_id'], config['azure_resource_group']
    UUID(subscription)
    if not re.fullmatch(r'[A-Za-z0-9_.()-]{1,90}', group):
        raise ValueError('Configure o grupo de recursos do Azure.')
    scope = f'/subscriptions/{subscription}/resourceGroups/{group}'
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    try:
        token = credential.get_token('https://management.azure.com/.default').token
        with httpx.Client(base_url='https://management.azure.com', headers={'Authorization': f'Bearer {token}'}, timeout=30, follow_redirects=False) as client:
            path = scope + '/providers/Microsoft.Network/networkInterfaces?api-version=2026-01-01'
            matches = set()
            while path:
                url = urlparse(path)
                if url.netloc and (url.scheme != 'https' or url.netloc != 'management.azure.com'):
                    raise ValueError('A paginação do Azure saiu do endpoint autorizado.')
                if not url.path.lower().startswith(scope.lower() + '/providers/microsoft.network/networkinterfaces'):
                    raise ValueError('A paginação do Azure saiu do grupo configurado.')
                response = client.get(path)
                response.raise_for_status()
                data = response.json()
                for nic in data.get('value', []):
                    properties = nic.get('properties', {})
                    if any(ip.get('properties', {}).get('privateIPAddress') == job['ip'] for ip in properties.get('ipConfigurations', [])):
                        machine = properties.get('virtualMachine', {}).get('id')
                        if machine:
                            matches.add(machine)
                path = data.get('nextLink')
            prefix = scope + '/providers/Microsoft.Compute/virtualMachines/'
            if len(matches) != 1:
                raise ValueError('O IP deve identificar uma única VM no grupo configurado.')
            machine = matches.pop()
            if not machine.lower().startswith(prefix.lower()) or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', machine[len(prefix):]):
                raise ValueError('A VM está fora do grupo configurado.')
            vm = client.get(f'{machine}?api-version=2025-04-01')
            vm.raise_for_status()
            if vm.json().get('tags', {}).get('FIAPSwitchManaged') != 'true':
                raise ValueError('A VM não foi marcada para gerenciamento pelo painel.')
            action = {'wake': 'start', 'shutdown': 'deallocate', 'restart': 'restart'}[job['action']]
            response = client.post(f'{machine}/{action}?api-version=2025-04-01')
            response.raise_for_status()
        return 'Comando aceito pela API do Azure. Aguarde a próxima leitura de estado.'
    finally:
        credential.close()

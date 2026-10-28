"""Inventário da configuração das VMs pelas APIs, sem credenciais WinRM."""

from urllib.parse import urlparse

import httpx


def machine_report(computer, machines, provider):
    machine = machines.get(computer['ip'])
    if not machine:
        return {'computer_id': computer['id'], 'status': 'unknown', 'source': provider, 'issue': 'IP fora do inventário de VMs autorizado.'}
    if not machine['running']:
        return {'computer_id': computer['id'], 'status': 'unreachable', 'source': provider, 'issue': 'A VM está desligada ou em transição no provedor.'}
    return {'computer_id': computer['id'], 'status': 'online', 'source': provider, 'metrics': {'os': f'Windows ({provider.upper()})'},
            'hardware': machine['hardware']}


class AwsInventory:
    def __init__(self, config):
        import boto3
        self.client = boto3.client('ec2', region_name=config['aws_region'])
        self.config = config

    def inventory(self):
        nodes = [node for page in self.client.get_paginator('describe_instances').paginate(Filters=[
            {'Name': 'vpc-id', 'Values': [self.config['aws_vpc_id']]},
            {'Name': 'platform', 'Values': ['windows']}, {'Name': 'tag:FIAPSwitchManaged', 'Values': ['true']},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
        ]) for reservation in page['Reservations'] for node in reservation['Instances']]
        types = {item['InstanceType']: item for item in self.client.describe_instance_types(InstanceTypes=sorted({n['InstanceType'] for n in nodes}))['InstanceTypes']} if nodes else {}
        ids = sorted({disk['Ebs']['VolumeId'] for node in nodes for disk in node.get('BlockDeviceMappings', []) if 'Ebs' in disk})
        volumes = {}
        for offset in range(0, len(ids), 200):
            volumes.update({disk['VolumeId']: disk for disk in self.client.describe_volumes(VolumeIds=ids[offset:offset + 200])['Volumes']})
        machines = {}
        for node in nodes:
            kind = types[node['InstanceType']]
            disks = [volumes[disk['Ebs']['VolumeId']] for disk in node.get('BlockDeviceMappings', []) if 'Ebs' in disk]
            cpus = node.get('CpuOptions', {})
            hardware = {'cpu_model': kind.get('ProcessorInfo', {}).get('Manufacturer', '') + ' ' + node['InstanceType'],
                'logical_cpus': cpus.get('CoreCount', 0) * cpus.get('ThreadsPerCore', 0) or kind['VCpuInfo']['DefaultVCpus'],
                'memory_gib': kind['MemoryInfo']['SizeInMiB'] / 1024,
                'storage_gib': sum(disk['Size'] for disk in disks), 'disk_count': len(disks),
                'gpu_models': [gpu.get('Manufacturer', '') + ' ' + gpu['Name'] for gpu in kind.get('GpuInfo', {}).get('Gpus', [])]}
            local = kind.get('InstanceStorageInfo', {}).get('Disks', [])
            hardware['disk_count'] += sum(disk['Count'] for disk in local)
            hardware['storage_gib'] += sum(disk['Count'] * disk['SizeInGB'] * 10**9 / 2**30 for disk in local)
            ip = node['PrivateIpAddress']
            if ip in machines:
                raise ValueError('Há VMs com IP privado ambíguo no escopo configurado.')
            machines[ip] = {'running': node['State']['Name'] == 'running', 'hardware': hardware}
        return machines

    def close(self):
        self.client.close()


class AzureInventory:
    def __init__(self, config):
        from azure.identity import DefaultAzureCredential
        self.config = config
        self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self.client = httpx.Client(base_url='https://management.azure.com', headers={'Authorization': 'Bearer ' + self.credential.get_token('https://management.azure.com/.default').token}, timeout=30, follow_redirects=False)
        self.subscription = f"/subscriptions/{config['azure_subscription_id']}"
        self.scope = self.subscription + f"/resourceGroups/{config['azure_resource_group']}"

    def get(self, path):
        url = urlparse(path)
        if url.netloc and (url.netloc != 'management.azure.com' or url.scheme != 'https'):
            raise ValueError('O endpoint Azure não é autorizado.')
        if not url.path.lower().startswith(self.subscription.lower() + '/'):
            raise ValueError('A consulta saiu da assinatura configurada.')
        result = self.client.get(path)
        result.raise_for_status()
        return result.json()

    def pages(self, path):
        while path:
            result = self.get(path)
            yield from result.get('value', [])
            path = result.get('nextLink')

    def inventory(self):
        machines, skus, seen = {}, None, set()
        nics = self.pages(self.scope + '/providers/Microsoft.Network/networkInterfaces?api-version=2026-01-01')
        for nic in nics:
            properties = nic.get('properties', {})
            machine_id = properties.get('virtualMachine', {}).get('id', '')
            prefix = self.scope.lower() + '/providers/microsoft.compute/virtualmachines/'
            if not machine_id.lower().startswith(prefix) or machine_id in seen or properties.get('primary') is False:
                continue
            vm = self.get(machine_id + '?api-version=2025-04-01&$expand=instanceView')
            if vm.get('tags', {}).get('FIAPSwitchManaged') != 'true' or vm['properties']['storageProfile']['osDisk'].get('osType') != 'Windows':
                continue
            seen.add(machine_id)
            configs = properties.get('ipConfigurations', [])
            chosen = next((ip for ip in configs if ip.get('properties', {}).get('primary')), configs[0] if configs else {})
            ip = chosen.get('properties', {}).get('privateIPAddress')
            if not ip:
                continue
            if ip in machines:
                raise ValueError('Há VMs com IP privado ambíguo no escopo configurado.')
            if skus is None:
                skus = {sku['name']: sku for sku in self.pages(self.subscription + '/providers/Microsoft.Compute/skus?api-version=2021-07-01') if sku.get('resourceType') == 'virtualMachines'}
            size = vm['properties']['hardwareProfile']['vmSize']
            capabilities = {item['name']: item['value'] for item in skus.get(size, {}).get('capabilities', [])}
            hardware = {'cpu_model': size}
            if capabilities.get('vCPUsAvailable') or capabilities.get('vCPUs'):
                hardware['logical_cpus'] = int(capabilities.get('vCPUsAvailable') or capabilities['vCPUs'])
            if capabilities.get('MemoryGB'):
                hardware['memory_gib'] = float(capabilities['MemoryGB'])
            storage = vm['properties']['storageProfile']
            disks = [storage['osDisk'], *storage.get('dataDisks', [])]
            sizes = []
            for disk in disks:
                value = disk.get('diskSizeGB')
                if value is None and disk.get('managedDisk', {}).get('id'):
                    value = self.get(disk['managedDisk']['id'] + '?api-version=2025-01-02')['properties'].get('diskSizeGB')
                sizes.append(value)
            hardware['disk_count'] = len(disks)
            if all(size is not None for size in sizes):
                hardware['storage_gib'] = sum(sizes)
            statuses = vm['properties'].get('instanceView', {}).get('statuses', [])
            machines[ip] = {'running': any(s.get('code') == 'PowerState/running' for s in statuses), 'hardware': hardware}
        return machines

    def close(self):
        self.client.close()
        self.credential.close()

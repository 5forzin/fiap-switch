"""Execução interna dos modos de nuvem, sem um controlador externo."""

import asyncio
import ipaddress
import logging

import httpx

from backend.native_cloud import AwsInventory, AzureInventory, machine_report
from backend.power import execute_cloud

log = logging.getLogger('cloud-worker')


async def run_cloud(app, cfg):
    async def internal(scope, receive, send):
        await app({**scope, 'fiap_internal_collector': True}, receive, send)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal), base_url=cfg.base_url,
                                 headers={'Authorization': f'Bearer {cfg.collector_token}'}, timeout=30, trust_env=False) as api:
        requested = asyncio.Event()

        async def post(path, value=None):
            response = await api.post(path, json=value)
            response.raise_for_status()
            return response.json()

        async def commands():
            while True:
                try:
                    job = (await post('/api/collector/claim'))['job']
                    if job:
                        try:
                            message = await asyncio.to_thread(execute_cloud, job)
                            state = 'completed'
                        except Exception as error:
                            message, state = f'O provedor recusou o comando ({type(error).__name__}).', 'failed'
                        await post(f"/api/collector/jobs/{job['id']}/result", {'lease_token': job['lease_token'], 'state': state, 'message': message})
                    pending = await api.get('/api/collector/checks')
                    pending.raise_for_status()
                    if pending.json()['pending']:
                        requested.set()
                except Exception as error:
                    log.warning('Falha na fila de nuvem (%s).', type(error).__name__)
                await asyncio.sleep(2)

        async def scan():
            while True:
                native = None
                try:
                    factory = AwsInventory if cfg.power_mode == 'aws' else AzureInventory
                    native = await asyncio.to_thread(factory, cfg.power_config())
                    machines = await asyncio.to_thread(native.inventory)
                    inventory = await api.get('/api/collector/inventory')
                    inventory.raise_for_status()
                    reports = [machine_report(pc, machines, cfg.power_mode) for pc in inventory.json()]
                    for offset in range(0, len(reports), 100):
                        await post('/api/collector/reports', {'reports': reports[offset:offset + 100]})
                    tasks = await api.get('/api/collector/discovery')
                    tasks.raise_for_status()
                    for task in tasks.json():
                        network, known = ipaddress.ip_network(task['subnet']), set(task['known_ips'])
                        found = []
                        for address, machine in machines.items():
                            ip = ipaddress.ip_address(address)
                            number = int(ip) - int(network.network_address)
                            if ip in network and 1 <= number <= 100 and address not in known and machine['running']:
                                found.append({'number': number, 'status': 'online', 'source': cfg.power_mode,
                                              'hardware': machine['hardware'], 'metrics': {'os': f'Windows ({cfg.power_mode.upper()})'}})
                        await post(f"/api/collector/discovery/{task['check_id']}/{task['lab_id']}", {'computers': found})
                except Exception as error:
                    log.warning('Falha na leitura da nuvem (%s).', type(error).__name__)
                finally:
                    if native:
                        await asyncio.to_thread(native.close)
                try:
                    await asyncio.wait_for(requested.wait(), 60)
                except TimeoutError:
                    pass
                requested.clear()

        async with asyncio.TaskGroup() as group:
            group.create_task(commands())
            group.create_task(scan())

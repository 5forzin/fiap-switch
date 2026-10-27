import concurrent.futures
import ipaddress
import json
import logging
import os
import socket
import time
import threading
from urllib.parse import urlparse

import httpx
import winrm
from dotenv import load_dotenv

load_dotenv(os.getenv('COLLECTOR_ENV', '.env.collector'))
log = logging.getLogger('collector')

HEALTH_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
$processors = @(Get-CimInstance Win32_Processor)
$cpu = ($processors | Measure-Object -Property LoadPercentage -Average).Average
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$system = Get-CimInstance Win32_ComputerSystem
$physicalDisks = @(Get-CimInstance Win32_DiskDrive)
$gpuModels = $null
try { $gpuModels = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch { }
$adapter = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' | Where-Object { $_.DefaultIPGateway } | Select-Object -First 1
[ordered]@{
    cpu = [math]::Round([double]$cpu, 1)
    memory = [math]::Round((1 - $os.FreePhysicalMemory / $os.TotalVisibleMemorySize) * 100, 1)
    disk_free = [math]::Round($disk.FreeSpace / $disk.Size * 100, 1)
    uptime_hours = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
    os = $os.Caption
    mac = $adapter.MACAddress
    hardware = [ordered]@{
        cpu_model = [string]$processors[0].Name
        logical_cpus = [int]$system.NumberOfLogicalProcessors
        memory_gib = [math]::Round($system.TotalPhysicalMemory / 1GB, 2)
        storage_gib = [math]::Round(($physicalDisks | Measure-Object -Property Size -Sum).Sum / 1GB, 2)
        disk_count = $physicalDisks.Count
        gpu_models = $gpuModels
    }
} | ConvertTo-Json -Compress
'''
POWER_SCRIPTS = {
    'shutdown': 'shutdown.exe /s /t 60 /d p:0:0 /c "FIAP support: scheduled shutdown in 60 seconds. Save your work."; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }',
    'restart': 'shutdown.exe /r /t 60 /d p:0:0 /c "FIAP support: scheduled restart in 60 seconds. Save your work."; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }',
}


class Controller:
    def __init__(self):
        self.base_url = os.environ['SWITCH_URL'].rstrip('/')
        if urlparse(self.base_url).scheme != 'https':
            raise ValueError('Collector communication requires HTTPS.')
        self.networks = [ipaddress.ip_network(c.strip()) for c in os.getenv('ALLOWED_CIDRS', '10.3.0.0/16').split(',')]
        self.username = os.environ['WINRM_USERNAME']
        self.password = os.environ.get('WINRM_PASSWORD', '')
        self.transport = os.getenv('WINRM_TRANSPORT', 'ntlm')
        if self.transport not in ('ntlm', 'kerberos'):
            raise ValueError('Only NTLM and Kerberos authentication are supported.')
        if self.transport == 'ntlm' and not self.password:
            raise ValueError('WINRM_PASSWORD is required for NTLM.')
        self.scheme = os.getenv('WINRM_SCHEME', 'https')
        if self.scheme != 'https' and self.transport != 'kerberos':
            raise ValueError('HTTP is supported only with Kerberos and message encryption.')
        self.port = int(os.getenv('WINRM_PORT', '5986' if self.scheme == 'https' else '5985'))
        self.ca = os.getenv('WINRM_CA_BUNDLE') or None
        self.workers = min(16, max(1, int(os.getenv('PROBE_WORKERS', '8'))))
        self.interval = max(15, int(os.getenv('PROBE_INTERVAL', '60')))
        self.connect_timeout = min(10, max(0.5, float(os.getenv('TCP_CONNECT_TIMEOUT', '2'))))
        self.check_requested = threading.Event()
        self.client = httpx.Client(base_url=self.base_url, timeout=20,
            headers={'Authorization': f"Bearer {os.environ['COLLECTOR_TOKEN']}"}, follow_redirects=False)

    def target(self, computer):
        ip = ipaddress.ip_address(computer['ip'])
        if not any(ip in network for network in self.networks):
            raise ValueError('Computer is outside allowed LAN networks.')
        host = computer.get('hostname') or str(ip)
        if self.transport == 'kerberos' and not computer.get('hostname'):
            raise ValueError('Kerberos requires a registered DNS hostname.')
        if host != str(ip):
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, self.port, family=socket.AF_INET)}
            if not addresses or any(not any(address in network for network in self.networks) for address in addresses):
                raise ValueError('Hostname resolved outside the allowed LAN networks.')
            if ip not in addresses:
                raise ValueError('Hostname does not resolve to the registered IP.')
        return f'{self.scheme}://{host}:{self.port}/wsman'

    def session(self, computer):
        return winrm.Session(self.target(computer), auth=(self.username, self.password),
            transport=self.transport, server_cert_validation='validate', ca_trust_path=self.ca,
            message_encryption='always', operation_timeout_sec=15, read_timeout_sec=20)

    def probe(self, computer):
        try:
            self.target(computer)
            with socket.create_connection((computer['ip'], self.port), timeout=self.connect_timeout):
                pass
            result = self.session(computer).run_ps(HEALTH_SCRIPT)
            if result.status_code:
                return {'computer_id': computer['id'], 'status': 'unreachable', 'issue': 'WinRM health query was rejected.'}
            metrics = json.loads(result.std_out.decode('utf-8-sig').strip())
            mac = metrics.pop('mac', '') or ''
            hardware = metrics.pop('hardware', None)
            return {'computer_id': computer['id'], 'status': 'online', 'metrics': metrics, 'mac': mac, 'hardware': hardware}
        except Exception as error:
            # Error text can contain authentication details; report only the exception class.
            return {'computer_id': computer['id'], 'status': 'unreachable', 'issue': f'WinRM could not verify this computer ({type(error).__name__}).'}

    def wake(self, computer):
        self.target(computer)
        raw = bytes.fromhex(computer['mac'].replace(':', '').replace('-', ''))
        if len(raw) != 6 or raw[0] & 1 or raw == bytes(6):
            raise ValueError('A unicast MAC address is required.')
        broadcast = ipaddress.ip_address(computer['broadcast'])
        subnet = ipaddress.ip_network(f"{computer['ip']}/24", strict=False)
        if broadcast != subnet.broadcast_address or not any(broadcast in network for network in self.networks):
            raise ValueError('Invalid laboratory broadcast address.')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                sock.sendto(b'\xff' * 6 + raw * 16, (str(broadcast), 9))
        return 'Wake-on-LAN packet sent. Verify power state on the next health check.'

    def execute(self, job):
        if job['action'] == 'wake':
            return self.wake(job)
        if job['action'] not in POWER_SCRIPTS:
            raise ValueError('Unsupported power action.')
        result = self.session(job).run_ps(POWER_SCRIPTS[job['action']])
        if result.status_code:
            raise RuntimeError('Windows rejected the power command.')
        return 'Windows accepted the command with a 60-second notice. Delivery does not confirm power state.'

    def post(self, path, body=None):
        response = self.client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def poll(self):
        job = self.post('/api/collector/claim')['job']
        if not job:
            return
        try:
            message, state = self.execute(job), 'completed'
        except Exception as error:
            message, state = f'Command failed ({type(error).__name__}). Verify before retrying.', 'failed'
        # An uncertain delivery is never executed a second time automatically.
        self.post(f"/api/collector/jobs/{job['id']}/result", {'lease_token': job['lease_token'], 'state': state, 'message': message})
        log.info('Job %s: %s', job['id'], state)

    def monitor(self):
        while True:
            try:
                response = self.client.get('/api/collector/inventory')
                response.raise_for_status()
                computers = response.json()
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
                    batch = []
                    flushed = time.monotonic()
                    futures = [pool.submit(self.probe, computer) for computer in computers]
                    for future in concurrent.futures.as_completed(futures):
                        batch.append(future.result())
                        if len(batch) >= 20 or time.monotonic()-flushed >= 5:
                            self.post('/api/collector/reports', {'reports': batch})
                            batch = []
                            flushed = time.monotonic()
                    if batch:
                        self.post('/api/collector/reports', {'reports': batch})
                log.info('Checked %s computers.', len(computers))
            except Exception as error:
                log.warning('Health cycle failed (%s).', type(error).__name__)
            self.check_requested.wait(self.interval)
            self.check_requested.clear()

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(self.monitor)
            while True:
                try:
                    self.poll()
                    pending = self.client.get('/api/collector/checks')
                    pending.raise_for_status()
                    if pending.json()['pending']:
                        self.check_requested.set()
                except Exception as error:
                    log.warning('Command polling failed (%s).', type(error).__name__)
                time.sleep(2)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Controller().run()

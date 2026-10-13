"""Validate the deployed API against the disposable AWS WinRM laboratory."""
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

from infra.aws_lab import clients, command, save

URL = 'https://fiap-switch.13.140.191.31.sslip.io'
STATE = Path('runtime/aws-lab.json')


def await_command(ssm, command_id, instance, timeout=300):
    deadline = time.time()+timeout
    while time.time() < deadline:
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(2)
            continue
        if result['Status'] == 'Success':
            return result['StandardOutputContent'].strip()
        if result['Status'] in ('Failed', 'Cancelled', 'TimedOut'):
            raise RuntimeError(f"SSM command {command_id} failed: {result['StandardErrorContent'][-500:]}")
        time.sleep(3)
    raise TimeoutError(f'SSM command {command_id} timed out.')


def main():
    state = json.loads(STATE.read_text())
    credentials = json.loads(Path('runtime/temporary-login.json').read_text())
    ec2, _, ssm = clients()
    with httpx.Client(base_url=URL, headers={'Origin': URL}, timeout=30) as api:
        def request(method, path, **kwargs):
            response = api.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        request('POST', '/auth/temporary', json=credentials)
        session = request('GET', '/api/session')
        api.headers['X-CSRF-Token'] = session['user']['csrf']
        assert session['user']['id'] == 'temporary-admin'
        print('HTTPS temporary authentication: passed.', flush=True)
        dashboard = request('GET', '/api/dashboard')
        if not any(lab['id'] == '301' for lab in dashboard['labs']):
            request('POST', '/api/labs', json={'floor': 3, 'number': 1, 'label': 'AWS validation · Lab 301', 'students': 11, 'include_professor': False})

        read_token = "from pathlib import Path\nfor line in Path('/opt/fiap-switch/.env.production').read_text().splitlines():\n    if line.startswith('COLLECTOR_TOKEN='): print(line.split('=',1)[1])\n"
        token = subprocess.run(['ssh', '-i', r'C:\Users\antho\.ssh\id_rsa', '-o', 'BatchMode=yes', 'link@13.140.191.31', 'python3 -'], input=read_token, capture_output=True, text=True, check=True).stdout.strip()
        import base64
        env = f"SWITCH_URL={URL}\nCOLLECTOR_TOKEN={token}\nWINRM_USERNAME={state['winrm_user']}\nWINRM_PASSWORD={state['winrm_password']}\nWINRM_CA_BUNDLE=/opt/fiap-switch/winrm-lab.pem\nPROBE_INTERVAL=15\n"
        start = f"set -eu\ncd /opt/fiap-switch\nprintf '%s' '{base64.b64encode(env.encode()).decode()}' | base64 -d > .env.collector\nchmod 600 .env.collector\nsystemctl stop fiap-switch-collector.service 2>/dev/null || true\nsystemd-run --unit=fiap-switch-collector --working-directory=/opt/fiap-switch /opt/fiap-switch/.venv312/bin/python -m collector.service\n"
        await_command(ssm, command(ssm, state['controller'], start), state['controller'])
        print('AWS LAN collector connected.', flush=True)

        def wait_state(predicate, label, timeout=240):
            deadline = time.time()+timeout
            while time.time() < deadline:
                data = request('GET', '/api/dashboard')
                if predicate(data):
                    print(label, flush=True)
                    return data
                time.sleep(5)
            raise TimeoutError(label)

        def target(data):
            return next(pc for pc in data['computers'] if pc['id'] == '301-11')

        initial = wait_state(lambda d: target(d)['status'] == 'online', 'Live WinRM telemetry on the website: passed.')
        assert target(initial)['metrics']['os'].startswith('Microsoft Windows Server 2022')
        assert target(initial)['mac']
        before = await_command(ssm, command(ssm, state['windows'], '(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")', True), state['windows'])
        job = request('POST', '/api/actions', json={'action': 'restart', 'computer_ids': ['301-11']}, headers={'Idempotency-Key': str(uuid4())})['job_ids'][0]
        wait_state(lambda d: any(j['id'] == job and j['state'] == 'completed' for j in d['jobs']), 'Restart delivered through the website queue: passed.')
        # Wait for the actual reboot, not merely command acceptance.
        deadline = time.time()+300
        after = before
        while time.time() < deadline:
            time.sleep(15)
            info = ssm.describe_instance_information(Filters=[{'Key': 'InstanceIds', 'Values': [state['windows']]}])['InstanceInformationList']
            if not info or info[0]['PingStatus'] != 'Online':
                continue
            try:
                after = await_command(ssm, command(ssm, state['windows'], '(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")', True), state['windows'], 60)
            except (RuntimeError, TimeoutError):
                continue
            if after != before:
                break
        assert after != before, 'Windows did not reboot.'
        wait_state(lambda d: target(d)['status'] == 'online' and target(d)['metrics'].get('uptime_hours', 99) < 0.1, 'Windows reboot and new uptime verified: passed.')
        job = request('POST', '/api/actions', json={'action': 'shutdown', 'computer_ids': ['301-11']}, headers={'Idempotency-Key': str(uuid4())})['job_ids'][0]
        wait_state(lambda d: any(j['id'] == job and j['state'] == 'completed' for j in d['jobs']), 'Shutdown delivered through the website queue: passed.')
        deadline = time.time()+240
        while time.time() < deadline:
            status = ec2.describe_instances(InstanceIds=[state['windows']])['Reservations'][0]['Instances'][0]['State']['Name']
            if status == 'stopped':
                break
            time.sleep(10)
        assert status == 'stopped', 'Windows did not stop.'
        wait_state(lambda d: target(d)['status'] == 'unreachable', 'Shutdown and truthful unreachable state verified: passed.')
        state['e2e_passed'] = {'restart_boot_changed': True, 'shutdown_state': status, 'verified_at': time.time(), 'before_boot': before, 'after_boot': after}
        save(state)
        print('End-to-end validation passed. Destroy the disposable AWS lab next.', flush=True)


if __name__ == '__main__':
    main()

"""Administração do host web por SSM, sem abrir SSH."""

import json
import time
from pathlib import Path

import boto3


class AwsHost:
    def __init__(self):
        self.path = Path('runtime/aws-web.json')
        self.state = json.loads(self.path.read_text())
        self.session = boto3.Session(profile_name='fiap-switch', region_name=self.state['region'])
        self.ssm = self.session.client('ssm')

    def save(self):
        self.path.write_text(json.dumps(self.state, indent=2))

    def shell(self, script, timeout=900):
        command = self.ssm.send_command(InstanceIds=[self.state['instance']], DocumentName='AWS-RunShellScript',
            Parameters={'commands': [script], 'executionTimeout': [str(timeout)]}, TimeoutSeconds=timeout)['Command']['CommandId']
        deadline = time.monotonic() + timeout + 30
        while time.monotonic() < deadline:
            try:
                result = self.ssm.get_command_invocation(CommandId=command, InstanceId=self.state['instance'])
            except self.ssm.exceptions.InvocationDoesNotExist:
                time.sleep(2)
                continue
            if result['Status'] == 'Success':
                return result['StandardOutputContent'].strip()
            if result['Status'] in ('Failed', 'TimedOut', 'Cancelled'):
                raise RuntimeError(f"SSM {command}: {result['Status']}. " + result['StandardErrorContent'][-1800:])
            time.sleep(3)
        raise TimeoutError(f'Comando SSM {command} sem conclusão.')

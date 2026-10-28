"""Prepara a configuração privada do controlador sem senha na linha de comando."""

import argparse
import getpass
import json
import os
import shutil
from pathlib import Path

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--enrollment', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--ca', type=Path)
    args = parser.parse_args()
    args.root = args.root.resolve()
    data = args.root / 'data'
    data.mkdir(exist_ok=True, mode=0o700)
    path = args.root / '.env.collector'
    values = dict(dotenv_values(args.config or path)) if (args.config or path).exists() else {}
    values['WINRM_USERNAME'] = values.get('WINRM_USERNAME') or input('Conta de serviço WinRM: ').strip()
    values['WINRM_PASSWORD'] = values.get('WINRM_PASSWORD') or getpass.getpass('Senha da conta WinRM: ')
    enrollment = json.loads(args.enrollment.read_text(encoding='utf-8'))
    private_invite = data / f"enrollment-{enrollment['id']}.json"
    private_invite.write_text(json.dumps(enrollment), encoding='utf-8')
    os.chmod(private_invite, 0o600)
    if args.ca:
        shutil.copyfile(args.ca, args.root / 'winrm-ca.pem')
        values['WINRM_CA_BUNDLE'] = str(args.root / 'winrm-ca.pem')
    values.update(SWITCH_URL=enrollment['url'], COLLECTOR_ENROLLMENT=str(private_invite),
                  COLLECTOR_DEVICE_FILE=str(data / f"device-{enrollment['id']}.json"),
                  WINRM_TRANSPORT=values.get('WINRM_TRANSPORT') or 'ntlm', WINRM_SCHEME='https', WINRM_PORT='5986',
                  ALLOWED_CIDRS=values.get('ALLOWED_CIDRS') or '10.3.0.0/16')
    values.pop('COLLECTOR_TOKEN', None)
    path.write_text(''.join(f"{key}='" + str(value or '').replace("'", "\\'") + "'\n" for key, value in values.items()), encoding='utf-8')
    os.chmod(path, 0o600)
    print('Configuração privada pronta. O controlador aguardará a aprovação no painel.')


if __name__ == '__main__':
    main()

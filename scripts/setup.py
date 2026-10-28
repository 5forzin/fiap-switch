"""Instalação inicial em Linux ou Windows, com administrador gerado automaticamente."""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from backend.config import Settings
from backend.models import UserInput
from backend.passwords import hash_password


def configure(args, folder=Path('.')):
    path = folder / '.env'
    if path.exists():
        print('Configuração existente preservada. Nenhuma conta ou senha foi alterada.')
        return dotenv_values(path), None
    mode = args.mode
    if not mode:
        if not sys.stdin.isatty():
            raise ValueError('Informe --mode fiap, aws ou azure.')
        choice = input('Ambiente: 1) FIAP produção  2) AWS  3) Azure [1]: ').strip() or '1'
        mode = {'1': 'fiap', '2': 'aws', '3': 'azure'}.get(choice)
        if not mode:
            raise ValueError('Ambiente inválido.')
    values = {
        'APP_MODE': 'production', 'AUTH_MODE': 'local', 'POWER_MODE': mode,
        'BASE_URL': args.url.rstrip('/'), 'DATABASE_PATH': 'data/switch.db',
        'SESSION_SECRET': secrets.token_urlsafe(48), 'COLLECTOR_TOKEN': secrets.token_urlsafe(48),
        'COLLECTOR_ID': 'campus', 'WEB_PORT': str(args.port),
        'ENVIRONMENT': 'development' if urlparse(args.url).hostname in ('localhost', '127.0.0.1', '::1') else 'production',
        'COOKIE_SECURE': str(urlparse(args.url).scheme == 'https').lower(),
        'AWS_REGION': args.aws_region, 'AWS_VPC_ID': args.aws_vpc_id or '',
        'AZURE_SUBSCRIPTION_ID': args.azure_subscription_id or '', 'AZURE_RESOURCE_GROUP': args.azure_resource_group or '',
    }
    for key in (['AWS_VPC_ID'] if mode == 'aws' else ['AZURE_SUBSCRIPTION_ID', 'AZURE_RESOURCE_GROUP'] if mode == 'azure' else []):
        if not values[key] and sys.stdin.isatty():
            values[key] = input(f'{key}: ').strip()
    password = secrets.token_urlsafe(18)
    account = UserInput(email=args.admin_email, name='Administrador inicial', role='admin', password=password)
    values.update(BOOTSTRAP_ADMIN_EMAIL=account.email, BOOTSTRAP_ADMIN_NAME=account.name, BOOTSTRAP_ADMIN_PASSWORD_HASH=hash_password(password))
    cfg = Settings(_env_file=None, **{k.lower(): v for k, v in values.items()})
    cfg.validate_runtime()
    # A criação exclusiva impede sobrescrever uma configuração de outra instalação.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
        output.write(''.join(f"{key}='" + value.replace("'", "\\'") + "'\n" for key, value in values.items()))
    return values, password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['fiap', 'aws', 'azure'])
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--admin-email', default='admin@fiap.com.br')
    parser.add_argument('--aws-region', default='us-east-2')
    parser.add_argument('--aws-vpc-id')
    parser.add_argument('--azure-subscription-id')
    parser.add_argument('--azure-resource-group')
    parser.add_argument('--no-start', action='store_true', help='Gera a configuração sem iniciar o Docker.')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('A porta deve estar entre 1 e 65535.')
    try:
        values, password = configure(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if password:
        print(f"\nAcesso inicial: {values['BASE_URL']}\nE-mail: {values['BOOTSTRAP_ADMIN_EMAIL']}\nSenha: {password}\n")
        print('Guarde a senha. Ela não será exibida novamente. Altere os dados da conta após entrar.')
        if values['POWER_MODE'] == 'fiap':
            print('Em Controladores, gere o pacote do servidor Windows ou Linux e aprove sua conexão.')
    if not args.no_start:
        if not shutil.which('docker'):
            raise SystemExit('Instale Docker com Compose e execute o instalador novamente. A configuração já foi salva.')
        subprocess.run(['docker', 'compose', '--env-file', '.env', 'up', '-d', '--build'], check=True)
        print(f"Painel iniciado: {values['BASE_URL']}")


if __name__ == '__main__':
    main()

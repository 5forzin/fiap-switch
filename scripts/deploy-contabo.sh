#!/bin/sh
set -eu
cd /opt/fiap-switch
if sudo -n docker ps --format '{{.Names}}' | grep -qx 'fiap-switch-web-1'; then
    sudo -n docker exec -i fiap-switch-web-1 python - <<'PY'
import os
import sqlite3
import time
from pathlib import Path
folder = Path('/app/data/backups')
folder.mkdir(exist_ok=True, mode=0o700)
target = folder / f'switch-{time.strftime("%Y%m%d-%H%M%S")}.db'
with sqlite3.connect('/app/data/switch.db') as source, sqlite3.connect(target) as backup:
    source.backup(backup)
os.chmod(target, 0o600)
print('Backup do banco de dados criado.')
PY
fi
tar -xzf /tmp/fiap-switch-source.tar.gz
python3 - <<'PY'
import os
import secrets
from pathlib import Path
path = Path('.env.production')
if path.exists():
    existing = path.read_text().splitlines()
    auth_mode = next((line.split('=', 1)[1] for line in existing if line.startswith('AUTH_MODE=')), 'local')
    lines = [line for line in existing
             if not line.startswith(('TEMPORARY_', 'APP_MODE=', 'ENVIRONMENT=', 'AUTH_MODE='))]
else:
    auth_mode = 'local'
    values = {
        'BASE_URL': 'https://fiap-switch.13.140.191.31.sslip.io', 'DATABASE_PATH': 'data/switch.db',
        'SESSION_SECRET': secrets.token_urlsafe(48), 'COLLECTOR_TOKEN': secrets.token_urlsafe(48),
        'COOKIE_SECURE': 'true', 'COLLECTOR_ID': 'campus',
        'WEB_PORT': '4341', 'APP_ENV_FILE': '.env.production',
    }
    lines = [f'{key}={value}' for key, value in values.items()]
path.write_text('\n'.join(lines + ['APP_MODE=production', 'ENVIRONMENT=production', f'AUTH_MODE={auth_mode}'])+'\n')
os.chmod(path, 0o600)
PY
sudo -n docker compose -p fiap-switch --env-file .env.production up -d --build
if [ -f .env.demo ]; then
    sudo -n docker compose -p fiap-switch-demo --env-file .env.demo down
fi
sudo -n python3 - <<'PY'
import time
from pathlib import Path
path = Path('/etc/caddy/Caddyfile')
original = path.read_text()
block = '''# fiap-switch begin
fiap-switch-demo.13.140.191.31.sslip.io {
    redir https://fiap-switch.13.140.191.31.sslip.io{uri} permanent
}
fiap-switch.13.140.191.31.sslip.io {
    encode zstd gzip
    reverse_proxy 127.0.0.1:4341
    header {
        Strict-Transport-Security "max-age=31536000"
        -Server
    }
}
# fiap-switch end'''
if '# fiap-switch begin' in original:
    start = original.index('# fiap-switch begin')
    end = original.index('# fiap-switch end', start)+len('# fiap-switch end')
    updated = original[:start]+block+original[end:]
else:
    updated = original+'\n\n'+block+'\n'
if updated != original:
    path.with_name(f'Caddyfile.before-switch-{time.strftime("%Y%m%d-%H%M%S")}').write_text(original)
    path.write_text(updated)
PY
sudo -n /usr/local/bin/caddy-l4 validate --config /etc/caddy/Caddyfile
sudo -n systemctl reload caddy
sudo -n docker compose -p fiap-switch --env-file .env.production ps

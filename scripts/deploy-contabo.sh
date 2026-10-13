#!/bin/sh
set -eu
cd /opt/fiap-switch
tar -xzf /tmp/fiap-switch-source.tar.gz
python3 - <<'PY'
import os
import secrets
from pathlib import Path
for mode, host, port in [
    ('demo', 'fiap-switch-demo.13.140.191.31.sslip.io', 4340),
    ('production', 'fiap-switch.13.140.191.31.sslip.io', 4341),
]:
    path = Path(f'.env.{mode}')
    if path.exists():
        continue
    values = {
        'APP_MODE': mode, 'BASE_URL': f'https://{host}', 'DATABASE_PATH': 'data/switch.db',
        'SESSION_SECRET': secrets.token_urlsafe(48), 'COLLECTOR_TOKEN': secrets.token_urlsafe(48),
        'COOKIE_SECURE': 'true', 'ENTRA_TENANT_ID': '11dbbfe2-89b8-4549-be10-cec364e59551',
        'COLLECTOR_ID': 'campus', 'WEB_PORT': str(port), 'APP_ENV_FILE': str(path),
    }
    path.write_text('\n'.join(f'{key}={value}' for key,value in values.items())+'\n')
    os.chmod(path, 0o600)
PY
sudo -n docker compose -p fiap-switch-demo --env-file .env.demo up -d --build
sudo -n docker compose -p fiap-switch --env-file .env.production up -d
if ! sudo -n grep -q '# fiap-switch begin' /etc/caddy/Caddyfile; then
    sudo -n cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-fiap-switch
    sudo -n tee -a /etc/caddy/Caddyfile >/dev/null <<'CADDY'

# fiap-switch begin
fiap-switch-demo.13.140.191.31.sslip.io {
    encode zstd gzip
    reverse_proxy 127.0.0.1:4340
    header {
        Strict-Transport-Security "max-age=31536000"
        -Server
    }
}
fiap-switch.13.140.191.31.sslip.io {
    encode zstd gzip
    reverse_proxy 127.0.0.1:4341
    header {
        Strict-Transport-Security "max-age=31536000"
        -Server
    }
}
# fiap-switch end
CADDY
fi
sudo -n /usr/local/bin/caddy-l4 validate --config /etc/caddy/Caddyfile
sudo -n systemctl reload caddy
sudo -n docker compose -p fiap-switch-demo --env-file .env.demo ps
sudo -n docker compose -p fiap-switch --env-file .env.production ps

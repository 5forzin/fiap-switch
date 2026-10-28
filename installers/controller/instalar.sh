#!/bin/sh
set -eu
umask 027
if [ "$(id -u)" -ne 0 ]; then
    echo 'Execute como administrador: sudo sh instalar.sh --fingerprint IMPRESSAO_DO_PAINEL'
    exit 1
fi
package=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fingerprint=''
config=''
ca=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --fingerprint) fingerprint=$2; shift 2 ;;
        --config) config=$2; shift 2 ;;
        --ca) ca=$2; shift 2 ;;
        *) echo 'Opção inválida.'; exit 1 ;;
    esac
done
test -n "$fingerprint" || { echo 'Informe --fingerprint com o valor exibido no painel.'; exit 1; }
root=/opt/fiap-switch-controller
python=${PYTHON:-python3}
"$python" -m venv "$root/bootstrap"
"$root/bootstrap/bin/python" -m pip install --disable-pip-version-check cryptography==50.0.2
"$root/bootstrap/bin/python" "$package/verify.py" --fingerprint "$fingerprint"
id fiap-switch-controller >/dev/null 2>&1 || useradd --system --home-dir "$root" --shell /usr/sbin/nologin fiap-switch-controller
mkdir -p "$root/app" "$root/data"
cp -R "$package/collector" "$root/app/"
"$python" -m venv "$root/venv"
"$root/venv/bin/python" -m pip install --disable-pip-version-check -r "$package/requirements-controller.txt"
set -- --root "$root" --enrollment "$package/enrollment.json"
if [ -n "$config" ]; then set -- "$@" --config "$config"; fi
if [ -n "$ca" ]; then set -- "$@" --ca "$ca"; fi
"$root/venv/bin/python" "$package/configure.py" "$@"
chown -R root:fiap-switch-controller "$root"
chmod -R g+rX "$root/app" "$root/venv"
chmod 750 "$root" "$root/app"
chmod 640 "$root/.env.collector"
if [ -f "$root/winrm-ca.pem" ]; then chmod 640 "$root/winrm-ca.pem"; fi
chown -R fiap-switch-controller:fiap-switch-controller "$root/data"
chmod 700 "$root/data"
cat > /etc/systemd/system/fiap-switch-controller.service <<EOF
[Unit]
Description=Controlador FIAP Switch
After=network-online.target
Wants=network-online.target
[Service]
User=fiap-switch-controller
Group=fiap-switch-controller
WorkingDirectory=$root/app
Environment=COLLECTOR_ENV=$root/.env.collector
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$root/venv/bin/python -m collector.service
Restart=on-failure
RestartSec=15
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$root/data
UMask=0077
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now fiap-switch-controller
echo 'Controlador iniciado. Confira a solicitação na conta de administrador do painel.'

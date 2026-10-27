# FIAP Switch

Agentless laboratory power control with individual password accounts, administrator and floor-scoped monitor access, WinRM health reporting, and Wake-on-LAN. Microsoft Entra SSO is available as an optional authentication mode.

The overview summarizes floors 1–7, 10, and 11. Computer lists default to a floor and laboratory, with search, hardware filters, and 50-row pages. Saved verifications and daily observations identify each computer's last response and last confirmed online time. Administrators configure a hardware standard per laboratory; scans record dated mismatches and their resolution.

React + TypeScript, FastAPI, a transactional SQLite command queue, and a Python LAN controller. The interface uses the official FIAP logo and the Gotham Book, Light, and Bold fonts observed in the FIAP intranet.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend
$env:ENVIRONMENT='development'
$env:BASE_URL='http://localhost:8000'
$env:COOKIE_SECURE='false'
$env:DATABASE_PATH='data/switch.db'
$env:SESSION_SECRET=[Guid]::NewGuid().ToString()+[Guid]::NewGuid().ToString()
.venv\Scripts\python -m scripts.create_admin --email admin@fiap.com.br --name Administrator
.venv\Scripts\python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Enter an independent application password when prompted. Open `http://localhost:8000`, sign in, and register laboratories and users. A new database contains no fabricated computers or accounts.

## Validate

```powershell
.venv\Scripts\python -m pytest -q
npm run build --prefix frontend
```

Production requires HTTPS, private session/controller secrets, an initial administrator, and a controller connected to the laboratory LAN. See [operations](docs/operations.md) for deployment, accounts, addressing, backup, and AWS validation.

The AWS validation on 2026-10-13 verified live health and MAC telemetry, restart with a changed Windows boot timestamp, and shutdown with EC2's stopped state over encrypted, certificate-validated WinRM and the deployed HTTPS queue. The disposable instances, disks, networking, and temporary IAM profile were removed afterward. Physical Wake-on-LAN and the FIAP LAN connection require on-campus validation.

## Brand assets

- Logo: [FIAP official SVG](https://www.fiap.com.br/svg/fiap.svg).
- Fonts: `GothamHTF-Book.woff`, `GothamHTF-Light.woff`, and `GothamHTF-Bold.woff`, observed under `/Content/fonts/` in the [FIAP intranet](https://intranet.fiap.com.br/home).

FIAP branding and Gotham fonts retain their respective owners' rights. This independent laboratory project is not an official FIAP service.

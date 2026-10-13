# FIAP Switch

Agentless laboratory power control with an administrator dashboard, floor-scoped monitor access, corporate Microsoft Entra SSO, WinRM health reporting, and Wake-on-LAN.

React + TypeScript frontend, FastAPI backend, transactional SQLite queue, and a Python LAN controller. The visual design follows the FIAP intranet's charcoal header, light gray workspace, white panels, and pink accents.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
cd frontend
npm ci
npm run build
cd ..
$env:APP_MODE='demo'
$env:BASE_URL='http://localhost:8000'
$env:COOKIE_SECURE='false'
$env:DATABASE_PATH='data/demo.db'
$env:SESSION_SECRET=[Guid]::NewGuid().ToString()+[Guid]::NewGuid().ToString()
.venv\Scripts\python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`. The demo includes clearly labeled simulated devices on all nine floors. Administrator, floor 3 monitor, and read-only profiles exercise the same authorization rules as production. Real collectors are disabled in demo mode.

## Validate

```powershell
.venv\Scripts\python -m pytest -q
cd frontend
npm run build
```

Production requires HTTPS, private session/controller secrets, the FIAP Entra application registration, an initial administrator object ID, and a controller connected to the laboratory LAN. See [operations](docs/operations.md) for setup, addressing, state semantics, and AWS test infrastructure.

This is an independent project for FIAP laboratory operations. It is not an official FIAP service.

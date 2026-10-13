import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from backend.config import Settings, get_settings
from backend.db import FLOORS, allowed, connect, initialize, inventory, user_public
from backend.models import ActionInput, ComputerInput, JobResult, LabInput, Reports, TemporaryLogin, UserInput
from backend.passwords import verify_password


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def audit(db, actor, action, target, computer_id=None, detail=''):
    db.execute('INSERT INTO audit(actor,action,target,computer_id,created,detail) VALUES (?,?,?,?,?,?)',
               (actor, action, target, computer_id, time.time(), detail))


def create_app(settings: Settings | None = None):
    cfg = settings or get_settings()
    cfg.validate_runtime()

    async def maintenance():
        while True:
            await asyncio.sleep(2)
            with connect(cfg.database_path) as db:
                db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
                db.execute('DELETE FROM login_attempts WHERE created < ?', (time.time()-900,))
                expired = db.execute("SELECT * FROM jobs WHERE state='running' AND lease_until<?", (time.time(),)).fetchall()
                for job in expired:
                    db.execute("UPDATE jobs SET state='failed',finished=?,message=? WHERE id=?",
                               (time.time(), 'Collector lease expired. Execution outcome is unknown; verify before retrying.', job['id']))
                    audit(db, 'system', 'lease_expired', job['id'], job['computer_id'])
                db.execute("UPDATE jobs SET state='failed',finished=?,message='Command expired before delivery.' WHERE state='queued' AND created<?", (time.time(), time.time()-300))
                if cfg.app_mode == 'demo':
                    db.execute('UPDATE computers SET last_seen=?', (time.time(),))
                    db.execute('UPDATE collectors SET last_seen=?', (time.time(),))
                    for job in db.execute("SELECT * FROM jobs WHERE state='queued' AND created<?", (time.time()-2,)).fetchall():
                        state = 'offline' if job['action'] == 'shutdown' else 'online'
                        metrics = {'cpu': 5, 'memory': 32, 'disk_free': 68, 'uptime_hours': 0, 'os': 'Windows 11 Enterprise'} if state == 'online' else {}
                        db.execute('UPDATE computers SET status=?,metrics=?,issue=? WHERE id=?', (state, json.dumps(metrics), '', job['computer_id']))
                        db.execute("UPDATE jobs SET state='completed',finished=?,message='Simulated command completed.' WHERE id=?", (time.time(), job['id']))
                        audit(db, 'simulator', 'completed', job['action'], job['computer_id'])

    @asynccontextmanager
    async def lifespan(app):
        initialize(cfg)
        task = asyncio.create_task(maintenance())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title='FIAP Switch', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = cfg
    app.add_middleware(SessionMiddleware, secret_key=cfg.session_secret, https_only=cfg.cookie_secure,
                       same_site='lax', max_age=28800, session_cookie='switch_session')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlparse(cfg.base_url).hostname, 'localhost', '127.0.0.1', 'testserver'])
    oauth = OAuth()
    if cfg.sso_ready:
        oauth.register('entra', client_id=cfg.entra_client_id, client_secret=cfg.entra_client_secret,
                       server_metadata_url=f'https://login.microsoftonline.com/{cfg.entra_tenant_id}/v2.0/.well-known/openid-configuration',
                       client_kwargs={'scope': 'openid profile email', 'code_challenge_method': 'S256'})

    @app.middleware('http')
    async def security(request, call_next):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH') and not request.url.path.startswith('/api/collector/'):
            if request.headers.get('origin') != cfg.base_url:
                return JSONResponse({'detail': 'Invalid request origin.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith(('/api/', '/auth/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def current_user(request: Request):
        sid = request.session.get('sid')
        if not sid:
            raise HTTPException(401, 'Sign in to continue.')
        with connect(cfg.database_path) as db:
            row = db.execute('SELECT u.*, s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires>? AND u.enabled=1', (digest(sid), time.time())).fetchone()
        if not row:
            raise HTTPException(401, 'Session expired or access revoked.')
        if row['id'] == 'temporary-admin' and not cfg.temporary_login_ready:
            raise HTTPException(401, 'Temporary access has ended. Sign in through corporate SSO.')
        user = user_public(row)
        if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), user['csrf']):
            raise HTTPException(403, 'Invalid CSRF token.')
        return user

    def admin(user=Depends(current_user)):
        if user['role'] != 'admin':
            raise HTTPException(403, 'Administrator access required.')
        return user

    def collector(request: Request):
        if cfg.app_mode == 'demo':
            raise HTTPException(404)
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied, f'Bearer {cfg.collector_token}'):
            raise HTTPException(401, 'Invalid collector credentials.')
        return cfg.collector_id

    def start_session(request, user_id):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with connect(cfg.database_path) as db:
            previous = request.session.get('sid')
            if previous:
                db.execute('DELETE FROM sessions WHERE id=?', (digest(previous),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(sid), user_id, time.time()+28800, csrf))
            audit(db, user_id, 'login', user_id)
        request.session.clear()
        request.session['sid'] = sid

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': cfg.app_mode}

    @app.get('/api/session')
    def session(request: Request):
        try:
            user = current_user(request)
        except HTTPException:
            user = None
        return {'user': user, 'mode': cfg.app_mode, 'sso_ready': cfg.sso_ready, 'temporary_login_ready': cfg.temporary_login_ready, 'floors': FLOORS}

    @app.post('/auth/temporary')
    def temporary_login(payload: TemporaryLogin, request: Request):
        if not cfg.temporary_login_ready:
            raise HTTPException(404)
        ip = request.client.host if request.client else 'unknown'
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            recent = db.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=? AND created>?', (ip, time.time()-900)).fetchone()[0]
            if recent >= 5:
                raise HTTPException(429, 'Too many sign-in attempts. Try again in 15 minutes.')
            db.execute('INSERT INTO login_attempts VALUES (?,?)', (ip, time.time()))
        valid_password = verify_password(payload.password, cfg.temporary_password_hash)
        if not valid_password or not secrets.compare_digest(payload.username, cfg.temporary_username):
            raise HTTPException(401, 'Invalid username or password.')
        with connect(cfg.database_path) as db:
            enabled = db.execute("SELECT 1 FROM users WHERE id='temporary-admin' AND enabled=1").fetchone()
            if not enabled:
                raise HTTPException(401, 'Invalid username or password.')
            db.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
        start_session(request, 'temporary-admin')
        return {'ok': True}

    @app.post('/auth/demo')
    def demo_login(request: Request, role: str = 'admin'):
        if cfg.app_mode != 'demo' or role not in ('admin', 'monitor', 'viewer'):
            raise HTTPException(404)
        start_session(request, f'demo-{role}')
        return {'ok': True}

    @app.get('/auth/login')
    async def login(request: Request):
        if cfg.app_mode == 'demo' or not cfg.sso_ready:
            raise HTTPException(503, 'Corporate SSO is not configured.')
        request.session.clear()
        return await oauth.entra.authorize_redirect(request, f'{cfg.base_url}/auth/callback', domain_hint='fiap.com.br')

    @app.get('/auth/callback')
    async def callback(request: Request):
        if cfg.app_mode != 'production' or not cfg.sso_ready:
            raise HTTPException(404)
        try:
            token = await oauth.entra.authorize_access_token(request)
            info = token['userinfo']
            if info.get('tid') != cfg.entra_tenant_id or not info.get('oid'):
                raise ValueError('Invalid tenant.')
            oid = str(UUID(info['oid']))
            email = info.get('preferred_username', '').lower()
            if not email.endswith('@fiap.com.br') or email.count('@') != 1:
                raise ValueError('Invalid domain.')
            uid = f'{cfg.entra_tenant_id}:{oid}'
            with connect(cfg.database_path) as db:
                user = db.execute('SELECT * FROM users WHERE id=? AND enabled=1', (uid,)).fetchone()
                if not user:
                    raise HTTPException(403, 'Your account has not been granted access. Contact support.')
                db.execute('UPDATE users SET email=?,name=? WHERE id=?', (email, str(info.get('name', email))[:100], uid))
            start_session(request, uid)
            return RedirectResponse('/')
        except HTTPException:
            raise
        except Exception as error:
            logging.getLogger('auth').warning('Corporate authentication failed (%s).', type(error).__name__)
            request.session.clear()
            raise HTTPException(401, 'Corporate authentication failed.')

    @app.post('/auth/logout')
    def logout(request: Request, user=Depends(current_user)):
        with connect(cfg.database_path) as db:
            db.execute('DELETE FROM sessions WHERE id=?', (digest(request.session['sid']),))
        request.session.clear()
        return {'ok': True}

    @app.get('/api/dashboard')
    def dashboard(user=Depends(current_user)):
        with connect(cfg.database_path) as db:
            computers = []
            for row in inventory(db):
                if allowed(user, row):
                    item = dict(row)
                    item['metrics'] = json.loads(item['metrics'])
                    item['stale'] = not item['last_seen'] or time.time()-item['last_seen'] > cfg.stale_seconds
                    if item['stale']:
                        item['status'] = 'unknown'
                        item['metrics'] = {}
                    item['can_control'] = allowed(user, row, True)
                    computers.append(item)
            lab_ids = {c['lab_id'] for c in computers}
            labs = [dict(l) for l in db.execute('SELECT * FROM labs ORDER BY floor,number') if user['role'] == 'admin' or l['id'] in lab_ids]
            visible = {c['id'] for c in computers}
            jobs = [dict(j) for j in db.execute('SELECT j.*,c.hostname,c.lab_id FROM jobs j JOIN computers c ON c.id=j.computer_id ORDER BY j.created DESC LIMIT 1000') if j['computer_id'] in visible][:100]
            collectors = [dict(c) for c in db.execute('SELECT * FROM collectors')] if user['role'] == 'admin' else []
        return {'computers': computers, 'labs': labs, 'jobs': jobs, 'collectors': collectors, 'updated_at': time.time()}

    @app.post('/api/actions')
    def actions(payload: ActionInput, request: Request, user=Depends(current_user)):
        try:
            request_id = str(UUID(request.headers.get('idempotency-key', '')))
        except ValueError:
            raise HTTPException(400, 'An Idempotency-Key UUID is required.')
        ids = sorted(set(payload.computer_ids))
        fingerprint = digest(json.dumps([payload.action, ids]))
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM requests WHERE id=? AND actor_id=?', (request_id, user['id'])).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise HTTPException(409, 'Idempotency key was already used for another request.')
                return {'job_ids': json.loads(prior['jobs'])}
            lookup = {c['id']: c for c in inventory(db)}
            for cid in ids:
                if cid not in lookup or not allowed(user, lookup[cid], True):
                    raise HTTPException(403, 'You cannot control one or more selected computers.')
                if payload.action == 'wake' and not lookup[cid]['mac']:
                    raise HTTPException(409, 'Register a MAC address before using Wake-on-LAN.')
                if db.execute("SELECT 1 FROM jobs WHERE computer_id=? AND state IN ('queued','running')", (cid,)).fetchone():
                    raise HTTPException(409, 'A selected computer already has a pending command.')
            job_ids = []
            for cid in ids:
                jid = str(uuid4())
                db.execute('INSERT INTO jobs(id,computer_id,actor_id,action,created) VALUES (?,?,?,?,?)', (jid, cid, user['id'], payload.action, time.time()))
                audit(db, user['id'], payload.action, lookup[cid]['hostname'] or lookup[cid]['ip'], cid)
                job_ids.append(jid)
            db.execute('INSERT INTO requests VALUES (?,?,?,?)', (request_id, user['id'], fingerprint, json.dumps(job_ids)))
        return {'job_ids': job_ids}

    @app.get('/api/users')
    def users(user=Depends(admin)):
        with connect(cfg.database_path) as db:
            return [user_public(row) for row in db.execute('SELECT * FROM users ORDER BY name')]

    @app.put('/api/users')
    def save_user(payload: UserInput, user=Depends(admin)):
        if cfg.app_mode == 'production':
            try:
                uid = f'{cfg.entra_tenant_id}:{UUID(payload.oid)}'
            except ValueError:
                raise HTTPException(422, 'Enter the user object ID from the FIAP tenant.')
        else:
            uid = payload.oid
        if uid == user['id'] and (not payload.enabled or payload.role != 'admin'):
            raise HTTPException(409, 'You cannot remove your own administrator access.')
        with connect(cfg.database_path) as db:
            db.execute('''INSERT INTO users VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                email=excluded.email,name=excluded.name,role=excluded.role,enabled=excluded.enabled,grants=excluded.grants''',
                       (uid, payload.email, payload.name, payload.role, int(payload.enabled), payload.grants.model_dump_json()))
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            audit(db, user['id'], 'permissions_updated', uid, detail=payload.grants.model_dump_json())
        return {'ok': True}

    @app.post('/api/labs')
    def add_lab(payload: LabInput, user=Depends(admin)):
        lid = f'{payload.floor}{payload.number:02d}'
        octet = int(f'{payload.floor}{payload.number}')
        with connect(cfg.database_path) as db:
            try:
                db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?)', (lid, payload.floor, payload.number,
                    payload.label, f'10.3.{octet}.0/24', f'10.3.{octet}.255', cfg.collector_id))
                for pc in list(range(1, payload.students+1)) + ([100] if payload.include_professor else []):
                    db.execute('INSERT INTO computers(id,lab_id,number,ip) VALUES (?,?,?,?)', (f'{lid}-{pc}', lid, pc, f'10.3.{octet}.{pc}'))
                audit(db, user['id'], 'lab_created', lid)
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'This laboratory or subnet already exists.')
        return {'id': lid}

    @app.put('/api/computers/{cid}')
    def edit_computer(cid: str, payload: ComputerInput, user=Depends(admin)):
        with connect(cfg.database_path) as db:
            if not db.execute('SELECT 1 FROM computers WHERE id=?', (cid,)).fetchone():
                raise HTTPException(404)
            db.execute('UPDATE computers SET hostname=?,mac=? WHERE id=?', (payload.hostname, payload.mac, cid))
            audit(db, user['id'], 'computer_updated', cid, cid)
        return {'ok': True}

    @app.get('/api/audit')
    def events(user=Depends(current_user)):
        with connect(cfg.database_path) as db:
            visible = {c['id'] for c in inventory(db) if allowed(user, c)}
            return [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 1000')
                    if user['role'] == 'admin' or r['computer_id'] in visible][:100]

    @app.get('/api/collector/inventory')
    def collector_inventory(cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            db.execute('UPDATE collectors SET last_seen=? WHERE id=?', (time.time(), cid))
            return [dict(c) for c in inventory(db) if c['collector_id'] == cid]

    @app.post('/api/collector/reports')
    def reports(payload: Reports, cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            valid = {r['id'] for r in inventory(db) if r['collector_id'] == cid}
            if any(r.computer_id not in valid for r in payload.reports):
                raise HTTPException(403, 'Computer is not assigned to this collector.')
            for r in payload.reports:
                db.execute('UPDATE computers SET status=?,metrics=?,last_seen=?,issue=?,mac=CASE WHEN mac=\'\' AND ? != \'\' THEN ? ELSE mac END WHERE id=?',
                           (r.status, json.dumps(r.metrics), time.time(), r.issue, r.mac, r.mac, r.computer_id))
            db.execute('UPDATE collectors SET last_seen=? WHERE id=?', (time.time(), cid))
        return {'ok': True}

    @app.post('/api/collector/claim')
    def claim(cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE collectors SET last_seen=? WHERE id=?', (time.time(), cid))
            candidates = db.execute('''SELECT j.*,c.ip,c.hostname,c.mac,c.lab_id,l.broadcast,l.floor FROM jobs j
                JOIN computers c ON c.id=j.computer_id JOIN labs l ON l.id=c.lab_id
                WHERE j.state='queued' AND l.collector_id=? AND j.created>? ORDER BY j.created''', (cid, time.time()-300)).fetchall()
            row = None
            for candidate in candidates:
                actor = db.execute('SELECT * FROM users WHERE id=?', (candidate['actor_id'],)).fetchone()
                machine = {'id': candidate['computer_id'], 'lab_id': candidate['lab_id'], 'floor': candidate['floor']}
                temporary_access_ended = actor and actor['id'] == 'temporary-admin' and not cfg.temporary_login_ready
                if actor and not temporary_access_ended and allowed(user_public(actor), machine, True):
                    row = candidate
                    break
                db.execute("UPDATE jobs SET state='failed',finished=?,message='Access was revoked before command delivery.' WHERE id=?", (time.time(), candidate['id']))
                audit(db, cid, 'command_revoked', candidate['action'], candidate['computer_id'])
            if row is None:
                return {'job': None}
            lease = secrets.token_urlsafe(32)
            db.execute("UPDATE jobs SET state='running',lease_until=?,lease_token=? WHERE id=?", (time.time()+120, digest(lease), row['id']))
            return {'job': {**dict(row), 'lease_token': lease}}

    @app.post('/api/collector/jobs/{jid}/result')
    def result(jid: str, payload: JobResult, cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            row = db.execute('SELECT j.*,l.collector_id FROM jobs j JOIN computers c ON c.id=j.computer_id JOIN labs l ON l.id=c.lab_id WHERE j.id=?', (jid,)).fetchone()
            if not row or row['collector_id'] != cid:
                raise HTTPException(404)
            if row['state'] != 'running' or row['lease_until'] < time.time() or not secrets.compare_digest(row['lease_token'] or '', digest(payload.lease_token)):
                raise HTTPException(409, 'Job lease is invalid or expired.')
            db.execute('UPDATE jobs SET state=?,message=?,finished=? WHERE id=?', (payload.state, payload.message, time.time(), jid))
            audit(db, cid, payload.state, row['action'], row['computer_id'], payload.message)
        return {'ok': True}

    root = Path(cfg.frontend_path)
    if (root / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=root / 'assets'), name='assets')

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith(('api/', 'auth/')):
            raise HTTPException(404)
        if not (root / 'index.html').exists():
            raise HTTPException(503, 'Frontend has not been built.')
        return FileResponse(root / 'index.html')

    return app

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
from fastapi.exceptions import RequestValidationError
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from backend.config import Settings, get_settings
from backend.db import FLOORS, allowed, connect, initialize, inventory, user_public
from backend.models import ActionInput, ComputerInput, JobResult, LabInput, LabQuantityInput, Reports, PasswordChange, PasswordLogin, UserInput
from backend.passwords import hash_password, needs_rehash, verify_password
from backend.checks import register_checks, save_observation
from backend.discovery import register_discovery
from backend.controllers import register_controllers, verify_device
from backend.hardware import register_hardware
from backend.audit import activity_clause, audit


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


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
                               (time.time(), 'O prazo de execução do controlador expirou. O resultado é desconhecido; verifique antes de tentar novamente.', job['id']))
                    audit(db, 'system', 'lease_expired', job['id'], job['computer_id'], initiator_id=job['actor_id'], actor_role=job['actor_role'])
                db.execute("UPDATE jobs SET state='failed',finished=?,message='O comando expirou antes do envio.' WHERE state='queued' AND created<?", (time.time(), time.time()-300))
                db.execute("UPDATE checks SET state='failed',finished=?,message='O controlador não concluiu esta verificação em 15 minutos.' WHERE state='queued' AND created<?", (time.time(),time.time()-900))

    @asynccontextmanager
    async def lifespan(app):
        initialize(cfg)
        task = asyncio.create_task(maintenance())
        cloud_task = None
        if cfg.power_mode in ('aws', 'azure') and cfg.cloud_worker_enabled:
            from backend.cloud_worker import run_cloud
            cloud_task = asyncio.create_task(run_cloud(app, cfg))
        yield
        if cloud_task:
            cloud_task.cancel()
            try:
                await cloud_task
            except asyncio.CancelledError:
                pass
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title='FIAP Switch', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = cfg
    app.add_middleware(SessionMiddleware, secret_key=cfg.session_secret, https_only=cfg.cookie_secure,
                       same_site='lax', max_age=28800, session_cookie='switch_session')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlparse(cfg.base_url).hostname, 'localhost', '127.0.0.1'])
    oauth = OAuth()
    if cfg.auth_mode == 'entra':
        oauth.register('entra', client_id=cfg.entra_client_id, client_secret=cfg.entra_client_secret,
                       server_metadata_url=f'https://login.microsoftonline.com/{cfg.entra_tenant_id}/v2.0/.well-known/openid-configuration',
                       client_kwargs={'scope': 'openid profile email', 'code_challenge_method': 'S256'})

    @app.middleware('http')
    async def security(request, call_next):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH') and not (request.url.path.startswith('/api/collector/') or request.url.path == '/api/enrollment'):
            if request.headers.get('origin') != cfg.base_url:
                return JSONResponse({'detail': 'Origem da solicitação inválida.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith(('/api/', '/auth/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        details = []
        for item in error.errors():
            context = item.get('ctx', {})
            messages = {
                'missing': 'Campo obrigatório.',
                'literal_error': 'Selecione uma opção válida.',
                'string_too_short': f"Use pelo menos {context.get('min_length')} caracteres.",
                'string_too_long': f"Use no máximo {context.get('max_length')} caracteres.",
                'greater_than_equal': f"O valor mínimo é {context.get('ge')}.",
                'less_than_equal': f"O valor máximo é {context.get('le')}.",
                'int_parsing': 'Informe um número inteiro válido.',
                'float_parsing': 'Informe um número válido.',
                'string_pattern_mismatch': 'Use o formato permitido para este campo.',
            }
            message = item['msg'].removeprefix('Value error, ') if item['type'] == 'value_error' else messages.get(item['type'], 'Valor inválido.')
            details.append({'loc': item['loc'], 'msg': message, 'type': item['type']})
        return JSONResponse({'detail': details}, status_code=422)

    def account_can_sign_in(db, uid):
        if cfg.auth_mode == 'local':
            return bool(db.execute('SELECT 1 FROM credentials WHERE user_id=?', (uid,)).fetchone())
        return uid.startswith(f'{cfg.entra_tenant_id}:')

    def current_user(request: Request):
        sid = request.session.get('sid')
        if not sid:
            raise HTTPException(401, 'Entre para continuar.')
        with connect(cfg.database_path) as db:
            row = db.execute('SELECT u.*, s.csrf, EXISTS(SELECT 1 FROM credentials c WHERE c.user_id=u.id) AS password_login FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires>? AND u.enabled=1', (digest(sid), time.time())).fetchone()
            if row and not account_can_sign_in(db, row['id']):
                row = None
        if not row:
            raise HTTPException(401, 'Sessão expirada ou acesso revogado.')
        user = user_public(row)
        if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), user['csrf']):
            raise HTTPException(403, 'Token de segurança inválido. Atualize a página.')
        return user

    def admin(user=Depends(current_user)):
        if user['role'] != 'admin':
            raise HTTPException(403, 'Acesso de administrador necessário.')
        return user

    def operator(user=Depends(current_user)):
        if user['role'] not in ('admin', 'support'):
            raise HTTPException(403, 'Acesso de administrador ou suporte necessário.')
        return user

    async def collector(request: Request):
        supplied = request.headers.get('authorization', '')
        if request.scope.get('fiap_internal_collector') and secrets.compare_digest(supplied, f'Bearer {cfg.collector_token}'):
            return cfg.collector_id
        device = await verify_device(request, cfg)
        return device['collector_id']

    def persist_session(db, request, user_id):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        previous = request.session.get('sid')
        if previous:
            db.execute('DELETE FROM sessions WHERE id=?', (digest(previous),))
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(sid), user_id, time.time()+28800, csrf))
        audit(db, user_id, 'login', user_id)
        request.session.clear()
        request.session['sid'] = sid

    def start_session(request, user_id):
        with connect(cfg.database_path) as db:
            persist_session(db, request, user_id)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': cfg.app_mode}

    @app.get('/api/session')
    def session(request: Request):
        try:
            user = current_user(request)
        except HTTPException:
            user = None
        return {'user': user, 'auth_mode': cfg.auth_mode, 'floors': FLOORS, 'power_mode': cfg.power_mode}

    dummy_hash = hash_password(secrets.token_urlsafe(32))

    @app.post('/auth/login')
    def password_login(payload: PasswordLogin, request: Request):
        if cfg.auth_mode != 'local':
            raise HTTPException(404)
        ip = request.client.host if request.client else 'unknown'
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            recent_ip = db.execute('SELECT COUNT(*) FROM login_attempts WHERE ip=? AND created>?', (ip, time.time()-900)).fetchone()[0]
            recent_user = db.execute('SELECT COUNT(*) FROM login_attempts WHERE username=? AND created>?', (payload.username, time.time()-900)).fetchone()[0]
            if recent_ip >= 5 or recent_user >= 10:
                raise HTTPException(429, 'Muitas tentativas de entrada. Tente novamente em 15 minutos.')
            db.execute('INSERT INTO login_attempts(ip,created,username) VALUES (?,?,?)', (ip, time.time(), payload.username))
            credential = db.execute('SELECT c.*,u.enabled FROM credentials c JOIN users u ON u.id=c.user_id WHERE c.username=?', (payload.username,)).fetchone()
        encoded = credential['password_hash'] if credential else dummy_hash
        valid = verify_password(payload.password, encoded)
        if not valid or not credential or not credential['enabled']:
            raise HTTPException(401, 'E-mail ou senha inválidos.')
        upgraded = hash_password(payload.password) if needs_rehash(encoded) else None
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT c.password_hash,u.enabled FROM credentials c JOIN users u ON u.id=c.user_id WHERE c.user_id=?', (credential['user_id'],)).fetchone()
            if not latest or not latest['enabled'] or not secrets.compare_digest(latest['password_hash'], encoded):
                raise HTTPException(401, 'E-mail ou senha inválidos.')
            db.execute('DELETE FROM login_attempts WHERE ip=? AND username=?', (ip, payload.username))
            if upgraded:
                db.execute('UPDATE credentials SET password_hash=? WHERE user_id=?', (upgraded, credential['user_id']))
            persist_session(db, request, credential['user_id'])
        return {'ok': True}

    @app.get('/auth/login')
    async def login(request: Request):
        if cfg.auth_mode != 'entra':
            raise HTTPException(503, 'O acesso corporativo ainda não foi configurado.')
        request.session.clear()
        return await oauth.entra.authorize_redirect(request, f'{cfg.base_url}/auth/callback', domain_hint='fiap.com.br')

    @app.get('/auth/callback')
    async def callback(request: Request):
        if cfg.auth_mode != 'entra':
            raise HTTPException(404)
        try:
            token = await oauth.entra.authorize_access_token(request)
            info = token['userinfo']
            if info.get('tid') != cfg.entra_tenant_id or not info.get('oid'):
                raise ValueError('Diretório corporativo inválido.')
            oid = str(UUID(info['oid']))
            email = info.get('preferred_username', '').lower()
            if not email.endswith('@fiap.com.br') or email.count('@') != 1:
                raise ValueError('Domínio inválido.')
            uid = f'{cfg.entra_tenant_id}:{oid}'
            with connect(cfg.database_path) as db:
                user = db.execute('SELECT * FROM users WHERE id=? AND enabled=1', (uid,)).fetchone()
                if not user:
                    raise HTTPException(403, 'Sua conta não tem acesso. Fale com o administrador.')
                db.execute('UPDATE users SET email=?,name=? WHERE id=?', (email, str(info.get('name', email))[:100], uid))
            start_session(request, uid)
            return RedirectResponse('/')
        except HTTPException:
            raise
        except Exception as error:
            logging.getLogger('auth').warning('Falha na autenticação corporativa (%s).', type(error).__name__)
            request.session.clear()
            raise HTTPException(401, 'Falha na autenticação corporativa.')

    @app.post('/auth/logout')
    def logout(request: Request, user=Depends(current_user)):
        with connect(cfg.database_path) as db:
            db.execute('DELETE FROM sessions WHERE id=?', (digest(request.session['sid']),))
        request.session.clear()
        return {'ok': True}

    @app.post('/auth/password')
    def change_password(payload: PasswordChange, request: Request, user=Depends(current_user)):
        if cfg.auth_mode != 'local':
            raise HTTPException(409, 'A alteração de senha é gerenciada pelo provedor de identidade.')
        with connect(cfg.database_path) as db:
            credential = db.execute('SELECT password_hash FROM credentials WHERE user_id=?', (user['id'],)).fetchone()
        if not credential or not verify_password(payload.current_password, credential['password_hash']):
            raise HTTPException(400, 'A senha atual está incorreta.')
        if payload.new_password.isspace():
            raise HTTPException(422, 'Informe uma senha válida.')
        encoded = hash_password(payload.new_password)
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires>? AND u.enabled=1', (digest(request.session['sid']), time.time())).fetchone()
            latest = db.execute('SELECT password_hash FROM credentials WHERE user_id=?', (user['id'],)).fetchone()
            if not active or not latest or not secrets.compare_digest(latest['password_hash'], credential['password_hash']):
                raise HTTPException(401, 'Sessão expirada ou acesso revogado.')
            db.execute('UPDATE credentials SET password_hash=?,changed=? WHERE user_id=?', (encoded, time.time(), user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            audit(db, user['id'], 'password_changed', user['email'])
            persist_session(db, request, user['id'])
        return {'ok': True}

    @app.get('/api/dashboard')
    def dashboard(user=Depends(current_user)):
        with connect(cfg.database_path) as db:
            computers = []
            for row in inventory(db):
                if allowed(user, row):
                    item = dict(row)
                    item['metrics'] = json.loads(item['metrics'])
                    item['hardware'] = json.loads(item['hardware'])
                    item['hardware_differences'] = json.loads(item['hardware_differences'])
                    item['stale'] = not item['last_seen'] or time.time()-item['last_seen'] > cfg.stale_seconds
                    if item['stale']:
                        item['status'] = 'unknown'
                        item['metrics'] = {}
                        if item['hardware_state'] != 'unconfigured':
                            item['hardware_state'] = 'unknown'
                    item['can_control'] = allowed(user, row, True)
                    computers.append(item)
            lab_ids = {c['lab_id'] for c in computers}
            labs = [dict(l) for l in db.execute('SELECT * FROM labs ORDER BY floor,number') if l['id'] in lab_ids or allowed(user, {'id': '', 'lab_id': l['id'], 'floor': l['floor']})]
            standards = {r['lab_id']: json.loads(r['settings']) for r in db.execute('SELECT * FROM hardware_standards')}
            for lab in labs:
                lab['hardware_standard'] = standards.get(lab['id'])
                quantity = db.execute('SELECT students FROM lab_inventory WHERE lab_id=?', (lab['id'],)).fetchone()
                lab['student_count'] = quantity['students'] if quantity else db.execute('SELECT COUNT(*) FROM computers WHERE lab_id=? AND number<100', (lab['id'],)).fetchone()[0]
            visible = {c['id'] for c in computers}
            privacy, privacy_values = activity_clause(user, 'j')
            placeholders = ','.join('?' for _ in visible)
            jobs = [dict(j) for j in db.execute(f'SELECT j.*,c.hostname,c.lab_id,u.name AS actor_name FROM jobs j JOIN computers c ON c.id=j.computer_id JOIN users u ON u.id=j.actor_id WHERE {privacy} AND j.computer_id IN ({placeholders}) ORDER BY j.created DESC LIMIT 100', [*privacy_values, *visible])] if visible else []
            collectors = [dict(c) for c in db.execute('SELECT * FROM collectors')] if user['role'] in ('admin', 'support') else []
            controller_requests = db.execute("SELECT COUNT(*) FROM controller_devices WHERE state='pending'").fetchone()[0] if user['role']=='admin' else 0
        return {'computers': computers, 'labs': labs, 'jobs': jobs, 'collectors': collectors, 'controller_requests': controller_requests, 'updated_at': time.time()}

    @app.post('/api/actions')
    def actions(payload: ActionInput, request: Request, user=Depends(current_user)):
        try:
            request_id = str(UUID(request.headers.get('idempotency-key', '')))
        except ValueError:
            raise HTTPException(400, 'Informe um UUID no cabeçalho Idempotency-Key.')
        ids = sorted(set(payload.computer_ids))
        fingerprint = digest(json.dumps([payload.action, ids]))
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM requests WHERE id=? AND actor_id=?', (request_id, user['id'])).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise HTTPException(409, 'Esta chave de idempotência já foi usada em outra solicitação.')
                return {'job_ids': json.loads(prior['jobs'])}
            lookup = {c['id']: c for c in inventory(db)}
            for cid in ids:
                if cid not in lookup or not allowed(user, lookup[cid], True):
                    raise HTTPException(403, 'Você não tem permissão para controlar um ou mais computadores selecionados.')
                if cfg.power_mode == 'fiap' and payload.action == 'wake' and not lookup[cid]['mac']:
                    raise HTTPException(409, 'Cadastre o endereço MAC antes de usar Wake-on-LAN.')
                if db.execute("SELECT 1 FROM jobs WHERE computer_id=? AND state IN ('queued','running')", (cid,)).fetchone():
                    raise HTTPException(409, 'Um computador selecionado já tem um comando pendente.')
            job_ids = []
            for cid in ids:
                jid = str(uuid4())
                db.execute('INSERT INTO jobs(id,computer_id,actor_id,action,created,actor_role) VALUES (?,?,?,?,?,?)', (jid, cid, user['id'], payload.action, time.time(), user['role']))
                audit(db, user['id'], payload.action, lookup[cid]['hostname'] or lookup[cid]['ip'], cid)
                job_ids.append(jid)
            db.execute('INSERT INTO requests VALUES (?,?,?,?)', (request_id, user['id'], fingerprint, json.dumps(job_ids)))
        return {'job_ids': job_ids}

    @app.get('/api/users')
    def users(user=Depends(admin)):
        with connect(cfg.database_path) as db:
            return [user_public(row) for row in db.execute('SELECT u.*, EXISTS(SELECT 1 FROM credentials c WHERE c.user_id=u.id) AS password_login FROM users u WHERE u.deleted_at IS NULL ORDER BY name')]

    @app.put('/api/users')
    def save_user(payload: UserInput, request: Request, user=Depends(admin)):
        with connect(cfg.database_path) as db:
            existing = db.execute('SELECT * FROM users WHERE id=? AND deleted_at IS NULL', (payload.id,)).fetchone() if payload.id else None
            if payload.id and not existing:
                raise HTTPException(404, 'Usuário não encontrado.')
            if existing:
                uid = existing['id']
            elif cfg.auth_mode == 'entra':
                try:
                    uid = f'{cfg.entra_tenant_id}:{UUID(payload.oid or "")}'
                except ValueError:
                    raise HTTPException(422, 'Informe o ID do objeto no diretório da FIAP.')
            else:
                uid = str(uuid4())
            if uid == user['id'] and (not payload.enabled or payload.role != 'admin'):
                raise HTTPException(409, 'Você não pode remover seu próprio acesso de administrador.')
            if cfg.auth_mode == 'local' and not existing and not payload.password:
                raise HTTPException(422, 'Informe uma senha para o novo usuário.')
            if cfg.auth_mode == 'entra' and payload.password:
                raise HTTPException(422, 'As senhas são gerenciadas pelo Microsoft Entra.')
            known_labs = {row['id'] for row in db.execute('SELECT id FROM labs')}
            known_computers = {row['id'] for row in db.execute('SELECT id FROM computers')}
            if set(payload.grants.labs)-known_labs or set(payload.grants.computers)-known_computers:
                raise HTTPException(422, 'Selecione laboratórios e computadores cadastrados.')
        encoded = hash_password(payload.password) if payload.password else None
        with connect(cfg.database_path) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                if payload.id and not db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone():
                    raise HTTPException(404, 'Usuário não encontrado.')
                if db.execute('SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id!=? AND deleted_at IS NULL', (payload.email, uid)).fetchone():
                    raise HTTPException(409, 'Já existe um usuário com este e-mail.')
                db.execute('''INSERT INTO users(id,email,name,role,enabled,grants) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    email=excluded.email,name=excluded.name,role=excluded.role,enabled=excluded.enabled,grants=excluded.grants,deleted_at=NULL''',
                    (uid, payload.email, payload.name, payload.role, int(payload.enabled), payload.grants.model_dump_json()))
                if cfg.auth_mode == 'local':
                    if encoded:
                        db.execute('''INSERT INTO credentials VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                            username=excluded.username,password_hash=excluded.password_hash,changed=excluded.changed''',
                            (uid, payload.email, encoded, time.time()))
                        db.execute('DELETE FROM login_attempts WHERE username=?', (payload.email,))
                    else:
                        db.execute('UPDATE credentials SET username=? WHERE user_id=?', (payload.email, uid))
                db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
                audit(db, user['id'], 'user_updated' if existing else 'user_created', payload.email, detail=payload.grants.model_dump_json())
                if encoded and existing:
                    audit(db, user['id'], 'password_reset', payload.email)
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'Já existe um usuário com este e-mail.')
        if uid == user['id']:
            start_session(request, uid)
        return {'id': uid}

    @app.delete('/api/users/{uid}')
    def delete_user(uid: str, user=Depends(admin)):
        if uid == user['id']:
            raise HTTPException(409, 'Você não pode excluir sua própria conta de administrador.')
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute('SELECT * FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone()
            if not account:
                raise HTTPException(404, 'Usuário não encontrado.')
            db.execute("UPDATE users SET enabled=0,deleted_at=?,grants='{}' WHERE id=?", (time.time(), uid))
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            db.execute('DELETE FROM credentials WHERE user_id=?', (uid,))
            audit(db, user['id'], 'user_deleted', account['email'])
        return {'ok': True}

    @app.post('/api/labs')
    def add_lab(payload: LabInput, user=Depends(operator)):
        lid = f'{payload.floor}{payload.number:02d}'
        octet = int(f'{payload.floor}{payload.number}')
        with connect(cfg.database_path) as db:
            try:
                db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?)', (lid, payload.floor, payload.number,
                    payload.label, f'10.3.{octet}.0/24', f'10.3.{octet}.255', cfg.collector_id))
                db.execute('INSERT INTO lab_inventory(lab_id,students) VALUES (?,?)', (lid, payload.students))
                for pc in list(range(1, (payload.students or 0)+1)) + ([100] if payload.include_professor else []):
                    db.execute('INSERT INTO computers(id,lab_id,number,ip) VALUES (?,?,?,?)', (f'{lid}-{pc}', lid, pc, f'10.3.{octet}.{pc}'))
                audit(db, user['id'], 'lab_created', lid)
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'Este laboratório ou sub-rede já está cadastrado.')
        return {'id': lid}

    @app.put('/api/labs/{lid}/quantity')
    def update_quantity(lid: str, payload: LabQuantityInput, user=Depends(operator)):
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            lab = db.execute('SELECT * FROM labs WHERE id=?', (lid,)).fetchone()
            if not lab:
                raise HTTPException(404, 'Laboratório não encontrado.')
            current = {r['number'] for r in db.execute('SELECT number FROM computers WHERE lab_id=? AND number<100', (lid,))}
            if payload.students is not None:
                if payload.students < len(current):
                    raise HTTPException(409, 'A quantidade não pode ser menor que o total de computadores já cadastrados.')
                total = len(current)
                for number in range(1, 100):
                    if total >= payload.students:
                        break
                    if number not in current:
                        db.execute('INSERT INTO computers(id,lab_id,number,ip) VALUES (?,?,?,?)', (f'{lid}-{number}', lid, number, f"{lab['subnet'].rsplit('.',1)[0]}.{number}"))
                        total += 1
            db.execute('INSERT INTO lab_inventory(lab_id,students) VALUES (?,?) ON CONFLICT(lab_id) DO UPDATE SET students=excluded.students', (lid, payload.students))
            audit(db, user['id'], 'lab_quantity_updated', lid)
        return {'ok': True}

    @app.put('/api/computers/{cid}')
    def edit_computer(cid: str, payload: ComputerInput, user=Depends(operator)):
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
            privacy, values = activity_clause(user, 'a', 'initiator_id')
            if user['role'] not in ('admin', 'support'):
                privacy = f"(a.initiator_id=? OR a.actor_role='system') AND (a.computer_id IN ({','.join('?' for _ in visible)}) OR (a.computer_id IS NULL AND a.initiator_id=?))"
                values = [user['id'], *visible, user['id']]
            return [dict(r) for r in db.execute(f'SELECT a.*,u.name AS actor_name FROM audit a LEFT JOIN users u ON u.id=a.initiator_id WHERE {privacy} ORDER BY a.id DESC LIMIT 100', values)]

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
                raise HTTPException(403, 'O computador não pertence a este controlador.')
            for r in payload.reports:
                r.source = 'winrm' if cfg.power_mode == 'fiap' else cfg.power_mode
                save_observation(db, r, time.time())
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
                if actor and account_can_sign_in(db, actor['id']) and allowed(user_public(actor), machine, True):
                    row = candidate
                    break
                db.execute("UPDATE jobs SET state='failed',finished=?,message='O acesso foi revogado antes do envio do comando.' WHERE id=?", (time.time(), candidate['id']))
                audit(db, cid, 'command_revoked', candidate['action'], candidate['computer_id'], initiator_id=candidate['actor_id'], actor_role=candidate['actor_role'])
            if row is None:
                return {'job': None}
            lease = secrets.token_urlsafe(32)
            db.execute("UPDATE jobs SET state='running',lease_until=?,lease_token=? WHERE id=?", (time.time()+120, digest(lease), row['id']))
            return {'job': {**dict(row), 'lease_token': lease, 'power': cfg.power_config()}}

    @app.post('/api/collector/jobs/{jid}/result')
    def result(jid: str, payload: JobResult, cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            row = db.execute('SELECT j.*,l.collector_id FROM jobs j JOIN computers c ON c.id=j.computer_id JOIN labs l ON l.id=c.lab_id WHERE j.id=?', (jid,)).fetchone()
            if not row or row['collector_id'] != cid:
                raise HTTPException(404)
            if row['state'] != 'running' or row['lease_until'] < time.time() or not secrets.compare_digest(row['lease_token'] or '', digest(payload.lease_token)):
                raise HTTPException(409, 'O prazo de execução do comando é inválido ou expirou.')
            db.execute('UPDATE jobs SET state=?,message=?,finished=? WHERE id=?', (payload.state, payload.message, time.time(), jid))
            audit(db, cid, payload.state, row['action'], row['computer_id'], payload.message, initiator_id=row['actor_id'], actor_role=row['actor_role'])
        return {'ok': True}

    register_checks(app,cfg,current_user,collector)
    register_discovery(app,cfg,collector)
    register_controllers(app,cfg,admin)
    register_hardware(app,cfg,current_user,operator)

    root = Path(cfg.frontend_path)
    if (root / 'brand').exists():
        app.mount('/brand', StaticFiles(directory=root / 'brand'), name='brand')
    if (root / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=root / 'assets'), name='assets')

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith(('api/', 'auth/')):
            raise HTTPException(404)
        if not (root / 'index.html').exists():
            raise HTTPException(503, 'A interface ainda não foi compilada.')
        return FileResponse(root / 'index.html')

    return app

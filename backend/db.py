import json
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

FLOORS = [1, 2, 3, 4, 5, 6, 7, 10, 11]


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(settings):
    with connect(settings.database_path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','monitor','viewer')),
                enabled INTEGER NOT NULL DEFAULT 1, grants TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS credentials (
                user_id TEXT PRIMARY KEY REFERENCES users(id),
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, changed REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS labs (
                id TEXT PRIMARY KEY, floor INTEGER NOT NULL, number INTEGER NOT NULL,
                label TEXT NOT NULL, subnet TEXT NOT NULL, broadcast TEXT NOT NULL,
                collector_id TEXT NOT NULL, UNIQUE(floor,number), UNIQUE(subnet)
            );
            CREATE TABLE IF NOT EXISTS computers (
                id TEXT PRIMARY KEY, lab_id TEXT NOT NULL REFERENCES labs(id),
                number INTEGER NOT NULL, hostname TEXT NOT NULL DEFAULT '',
                ip TEXT UNIQUE NOT NULL, mac TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'unknown', last_seen REAL,
                metrics TEXT NOT NULL DEFAULT '{}', issue TEXT NOT NULL DEFAULT '',
                UNIQUE(lab_id,number)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                expires REAL NOT NULL, csrf TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collectors (
                id TEXT PRIMARY KEY, last_seen REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, computer_id TEXT NOT NULL REFERENCES computers(id),
                actor_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL,
                lease_until REAL, lease_token TEXT, finished REAL, message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT NOT NULL, actor_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                jobs TEXT NOT NULL, PRIMARY KEY(id,actor_id)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                action TEXT NOT NULL, target TEXT NOT NULL, computer_id TEXT,
                created REAL NOT NULL, detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, created REAL NOT NULL, username TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS login_attempts_created ON login_attempts(created);
            CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created);
            CREATE INDEX IF NOT EXISTS audit_created ON audit(created);
        ''')
        if 'username' not in {r['name'] for r in db.execute('PRAGMA table_info(login_attempts)')}:
            db.execute("ALTER TABLE login_attempts ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        mode = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
        if mode and mode['value'] != settings.app_mode:
            raise ValueError('Demo and production must use separate databases.')
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('mode',?)", (settings.app_mode,))
        db.execute('INSERT OR IGNORE INTO collectors(id) VALUES (?)', (settings.collector_id,))
        legacy = db.execute("SELECT * FROM users WHERE id='temporary-admin'").fetchone()
        legacy_hash = db.execute("SELECT value FROM metadata WHERE key='temporary_password_hash'").fetchone()
        if legacy and legacy_hash:
            db.execute('INSERT OR IGNORE INTO credentials VALUES (?,?,?,?)',
                       (legacy['id'], legacy['email'].lower(), legacy_hash['value'], time.time()))
            db.execute("DELETE FROM metadata WHERE key='temporary_password_hash'")
        if settings.auth_mode == 'local' and settings.bootstrap_admin_email and settings.bootstrap_admin_password_hash:
            if not db.execute('SELECT 1 FROM credentials').fetchone():
                uid = str(uuid4())
                email = settings.bootstrap_admin_email.lower()
                db.execute('INSERT INTO users(id,email,name,role) VALUES (?,?,?,?)',
                           (uid, email, settings.bootstrap_admin_name, 'admin'))
                db.execute('INSERT INTO credentials VALUES (?,?,?,?)',
                           (uid, email, settings.bootstrap_admin_password_hash, time.time()))
        if settings.auth_mode == 'entra' and settings.bootstrap_admin_oid and settings.entra_tenant_id:
            db.execute('INSERT OR IGNORE INTO users(id,email,name,role) VALUES (?,?,?,?)', (
                f'{settings.entra_tenant_id}:{settings.bootstrap_admin_oid}',
                'admin@fiap.com.br', 'Administrator', 'admin'))



def user_public(row):
    user = {key: row[key] for key in ('id', 'email', 'name', 'role')}
    user['enabled'] = bool(row['enabled'])
    user['grants'] = json.loads(row['grants'])
    if 'password_login' in row.keys():
        user['auth_type'] = 'local' if row['password_login'] else 'entra'
    if 'csrf' in row.keys():
        user['csrf'] = row['csrf']
    return user


def allowed(user, computer, control=False):
    if not user['enabled'] or (control and user['role'] == 'viewer'):
        return False
    if user['role'] == 'admin':
        return True
    grants = user['grants']
    return (computer['floor'] in grants.get('floors', [])
            or computer['lab_id'] in grants.get('labs', [])
            or computer['id'] in grants.get('computers', []))


def inventory(db):
    return db.execute('''SELECT c.*, l.floor, l.label AS lab_label, l.broadcast,
        l.collector_id FROM computers c JOIN labs l ON l.id=c.lab_id ORDER BY l.floor,l.number,c.number''').fetchall()

import json
import sqlite3
import time
from contextlib import contextmanager

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
            CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS login_attempts_created ON login_attempts(created);
            CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created);
            CREATE INDEX IF NOT EXISTS audit_created ON audit(created);
        ''')
        mode = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
        if mode and mode['value'] != settings.app_mode:
            raise ValueError('Demo and production must use separate databases.')
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('mode',?)", (settings.app_mode,))
        db.execute('INSERT OR IGNORE INTO collectors(id) VALUES (?)', (settings.collector_id,))
        if settings.temporary_login_ready:
            db.execute("INSERT OR IGNORE INTO users(id,email,name,role) VALUES ('temporary-admin',?,?,'admin')", (settings.temporary_email, settings.temporary_name))
            previous = db.execute("SELECT value FROM metadata WHERE key='temporary_password_hash'").fetchone()
            if not previous or previous['value'] != settings.temporary_password_hash:
                db.execute("DELETE FROM sessions WHERE user_id='temporary-admin'")
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('temporary_password_hash',?)", (settings.temporary_password_hash,))
        if settings.app_mode == 'production' and settings.bootstrap_admin_oid and settings.entra_tenant_id:
            db.execute('INSERT OR IGNORE INTO users(id,email,name,role) VALUES (?,?,?,?)', (
                f'{settings.entra_tenant_id}:{settings.bootstrap_admin_oid}',
                'admin@fiap.com.br', 'Administrator', 'admin'))
        if settings.app_mode == 'demo' and not db.execute('SELECT 1 FROM users').fetchone():
            for uid, email, name, role, grants in [
                ('demo-admin', 'support@fiap.com.br', 'Support team', 'admin', {}),
                ('demo-monitor', 'monitor.demo@fiap.com.br', 'Floor monitor', 'monitor', {'floors': [3]}),
                ('demo-viewer', 'viewer.demo@fiap.com.br', 'Floor observer', 'viewer', {'floors': [3]}),
            ]:
                db.execute('INSERT INTO users(id,email,name,role,grants) VALUES (?,?,?,?,?)',
                           (uid, email, name, role, json.dumps(grants)))
            for floor in FLOORS:
                for number in (1, 2):
                    octet = int(f'{floor}{number}')
                    lab_id = f'{floor}{number:02d}'
                    db.execute('INSERT INTO labs VALUES (?,?,?,?,?,?,?)', (
                        lab_id, floor, number, f'Lab {lab_id}', f'10.3.{octet}.0/24',
                        f'10.3.{octet}.255', settings.collector_id))
                    for pc in list(range(1, 13)) + [100]:
                        seed = floor * 19 + number * 13 + pc
                        state = 'online' if seed % 7 < 4 else 'offline' if seed % 7 < 6 else 'unreachable'
                        metrics = {'cpu': seed % 27 + 3, 'memory': seed % 39 + 20,
                                   'disk_free': seed % 50 + 25, 'uptime_hours': seed % 48 + 2,
                                   'os': 'Windows 11 Enterprise'} if state == 'online' else {}
                        db.execute('INSERT INTO computers VALUES (?,?,?,?,?,?,?,?,?,?)', (
                            f'{lab_id}-{pc}', lab_id, pc, f'LAB{lab_id}-PC{pc:03d}',
                            f'10.3.{octet}.{pc}', f'02:03:{octet:02X}:00:00:{pc:02X}',
                            state, time.time(), json.dumps(metrics),
                            'WinRM did not respond (simulated).' if state == 'unreachable' else ''))


def user_public(row):
    return {**dict(row), 'enabled': bool(row['enabled']), 'grants': json.loads(row['grants'])}


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

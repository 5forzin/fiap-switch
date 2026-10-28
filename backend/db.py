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
                role TEXT NOT NULL CHECK(role IN ('admin','support','monitor','viewer')),
                enabled INTEGER NOT NULL DEFAULT 1, grants TEXT NOT NULL DEFAULT '{}', deleted_at REAL
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
            CREATE TABLE IF NOT EXISTS lab_inventory (
                lab_id TEXT PRIMARY KEY REFERENCES labs(id),
                students INTEGER CHECK(students BETWEEN 0 AND 99)
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
            CREATE TABLE IF NOT EXISTS checks (
                id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, created REAL NOT NULL,
                finished REAL, state TEXT NOT NULL DEFAULT 'queued', message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS check_results (
                check_id TEXT NOT NULL REFERENCES checks(id), computer_id TEXT NOT NULL,
                floor INTEGER NOT NULL, lab_id TEXT NOT NULL, hostname TEXT NOT NULL,
                ip TEXT NOT NULL, number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',
                checked_at REAL, last_online REAL, issue TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(check_id,computer_id)
            );
            CREATE TABLE IF NOT EXISTS daily_status (
                day TEXT NOT NULL, computer_id TEXT NOT NULL, floor INTEGER NOT NULL,
                lab_id TEXT NOT NULL, hostname TEXT NOT NULL, ip TEXT NOT NULL, number INTEGER NOT NULL,
                status TEXT NOT NULL, first_check REAL NOT NULL, checked_at REAL NOT NULL,
                last_online REAL, online_checks INTEGER NOT NULL DEFAULT 0, total_checks INTEGER NOT NULL DEFAULT 1,
                issue TEXT NOT NULL DEFAULT '', PRIMARY KEY(day,computer_id)
            );
            CREATE INDEX IF NOT EXISTS checks_created ON checks(created);
            CREATE TABLE IF NOT EXISTS check_discovery (
                check_id TEXT NOT NULL REFERENCES checks(id), lab_id TEXT NOT NULL REFERENCES labs(id),
                state TEXT NOT NULL DEFAULT 'queued', added INTEGER NOT NULL DEFAULT 0,
                finished REAL, message TEXT NOT NULL DEFAULT '', PRIMARY KEY(check_id,lab_id)
            );
            CREATE INDEX IF NOT EXISTS check_results_pending ON check_results(computer_id,checked_at);
            CREATE INDEX IF NOT EXISTS daily_status_day ON daily_status(day,floor);
            CREATE INDEX IF NOT EXISTS daily_status_computer ON daily_status(computer_id,last_online);
            CREATE TABLE IF NOT EXISTS hardware_standards (
                lab_id TEXT PRIMARY KEY REFERENCES labs(id), settings TEXT NOT NULL,
                updated REAL NOT NULL, updated_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hardware_events (
                id TEXT PRIMARY KEY, computer_id TEXT NOT NULL, lab_id TEXT NOT NULL, floor INTEGER NOT NULL,
                detected REAL NOT NULL, last_seen REAL NOT NULL, resolved REAL,
                expected TEXT NOT NULL, actual TEXT NOT NULL, differences TEXT NOT NULL,
                resolution TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS hardware_events_computer ON hardware_events(computer_id,resolved);
            CREATE UNIQUE INDEX IF NOT EXISTS hardware_events_open ON hardware_events(computer_id) WHERE resolved IS NULL;
        ''')
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
        if "'support'" not in schema:
            db.commit()
            db.execute('PRAGMA foreign_keys=OFF')
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute("""CREATE TABLE users_updated (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','support','monitor','viewer')),
                    enabled INTEGER NOT NULL DEFAULT 1, grants TEXT NOT NULL DEFAULT '{}', deleted_at REAL
                )""")
                db.execute('INSERT INTO users_updated(id,email,name,role,enabled,grants) SELECT id,email,name,role,enabled,grants FROM users')
                db.execute('DROP TABLE users')
                db.execute('ALTER TABLE users_updated RENAME TO users')
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Falha na integridade das contas após a migração.')
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute('PRAGMA foreign_keys=ON')
        additions = {
            'users': {'deleted_at': 'REAL'},
            'jobs': {'actor_role': "TEXT NOT NULL DEFAULT ''"},
            'checks': {'actor_role': "TEXT NOT NULL DEFAULT ''"},
            'audit': {'initiator_id': "TEXT NOT NULL DEFAULT ''", 'actor_role': "TEXT NOT NULL DEFAULT ''"},
            'computers': {'hardware': "TEXT NOT NULL DEFAULT '{}'", 'hardware_state': "TEXT NOT NULL DEFAULT 'unconfigured'",
                'hardware_checked_at': 'REAL', 'hardware_differences': "TEXT NOT NULL DEFAULT '[]'", 'hardware_mismatch_since': 'REAL'},
            'check_results': {'hardware': "TEXT NOT NULL DEFAULT '{}'", 'hardware_state': "TEXT NOT NULL DEFAULT 'unknown'", 'hardware_differences': "TEXT NOT NULL DEFAULT '[]'"},
            'daily_status': {'hardware': "TEXT NOT NULL DEFAULT '{}'", 'hardware_state': "TEXT NOT NULL DEFAULT 'unknown'", 'hardware_differences': "TEXT NOT NULL DEFAULT '[]'"},
        }
        for table, fields in additions.items():
            existing = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            for name, definition in fields.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        for table in ('jobs', 'checks'):
            db.execute(f"UPDATE {table} SET actor_role=COALESCE((SELECT role FROM users WHERE users.id={table}.actor_id),'unknown') WHERE actor_role='' ")
        db.execute("UPDATE audit SET initiator_id=actor WHERE initiator_id=''")
        db.execute("UPDATE audit SET actor_role=COALESCE((SELECT role FROM users WHERE users.id=audit.initiator_id),CASE WHEN actor='collector' AND action IN ('hardware_mismatch','hardware_resolved') THEN 'system' ELSE 'unknown' END) WHERE actor_role=''")
        if 'username' not in {r['name'] for r in db.execute('PRAGMA table_info(login_attempts)')}:
            db.execute("ALTER TABLE login_attempts ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        mode = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
        if mode and mode['value'] != settings.app_mode:
            raise ValueError('Demonstração e produção exigem bancos de dados separados.')
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('mode',?)", (settings.app_mode,))
        previous_power = db.execute("SELECT value FROM metadata WHERE key='power_mode'").fetchone()
        if (previous_power['value'] if previous_power else 'fiap') != settings.power_mode:
            db.execute("UPDATE jobs SET state='failed',finished=?,message='O modo de operação mudou. Confira o equipamento e envie um novo comando.' WHERE state='queued'", (time.time(),))
        db.execute("INSERT INTO metadata(key,value) VALUES ('power_mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (settings.power_mode,))
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
                'admin@fiap.com.br', 'Administrador', 'admin'))



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
    if user['role'] in ('admin', 'support'):
        return True
    grants = user['grants']
    if user['role'] == 'monitor':
        floors = grants.get('floors', [])
        return len(floors) == 1 and computer['floor'] == floors[0]
    return (computer['floor'] in grants.get('floors', [])
            or computer['lab_id'] in grants.get('labs', [])
            or computer['id'] in grants.get('computers', []))


def inventory(db):
    return db.execute('''SELECT c.*, l.floor, l.label AS lab_label, l.broadcast,
        l.collector_id FROM computers c JOIN labs l ON l.id=c.lab_id ORDER BY l.floor,l.number,c.number''').fetchall()

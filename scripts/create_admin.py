"""Create the first local administrator without putting its password in shell arguments."""
from getpass import getpass
import argparse
import time
from uuid import uuid4

from pydantic import ValidationError

from backend.config import Settings
from backend.db import connect, initialize
from backend.models import UserInput
from backend.passwords import hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    cfg = Settings()
    cfg.validate_runtime()
    if cfg.auth_mode != 'local':
        raise SystemExit('Set AUTH_MODE=local to create a password account.')
    initialize(cfg)
    with connect(cfg.database_path) as db:
        if db.execute("SELECT 1 FROM users u JOIN credentials c ON c.user_id=u.id WHERE u.role='admin' AND u.enabled=1").fetchone():
            raise SystemExit('An administrator already exists. Manage accounts in the website.')
    password = getpass('Password: ')
    if password != getpass('Confirm password: '):
        raise SystemExit('Passwords do not match.')
    try:
        payload = UserInput(email=args.email, name=args.name, role='admin', password=password)
    except ValidationError as error:
        raise SystemExit('; '.join(item['msg'] for item in error.errors()))
    encoded, uid = hash_password(password), str(uuid4())
    with connect(cfg.database_path) as db:
        db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', (uid, payload.email, payload.name, 'admin', 1, '{}'))
        db.execute('INSERT INTO credentials VALUES (?,?,?,?)', (uid, payload.email, encoded, time.time()))
        db.execute('INSERT INTO audit(actor,action,target,created) VALUES (?,?,?,?)', (uid, 'user_created', payload.email, time.time()))
    print(f'Administrator created: {payload.email}')


if __name__ == '__main__':
    main()

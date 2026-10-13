import base64
import hashlib
import secrets

SCRYPT_N = 65536
SCRYPT_R = 8
SCRYPT_P = 2


def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=128*1024*1024)
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(key).decode()


def needs_rehash(encoded):
    return not encoded.startswith(f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$')


def verify_password(password, encoded):
    try:
        parts = encoded.split('$')
        if len(parts) == 3:
            algorithm, salt, expected = parts
            n, r, p = 16384, 8, 1
        else:
            algorithm, n, r, p, salt, expected = parts
            n, r, p = int(n), int(r), int(p)
        if algorithm != 'scrypt':
            return False
        key = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=n, r=r, p=p, dklen=32, maxmem=128*1024*1024)
        return secrets.compare_digest(key, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import FLOORS, connect
from backend.passwords import hash_password

PASSWORD = "test-account-password-123"


@pytest.fixture(scope="session")
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def client(tmp_path, encoded_password):
    cfg = Settings(
        base_url="https://testserver",
        session_secret="test-session-secret-that-is-long-enough",
        collector_token="test-collector-token-that-is-long-enough",
        cloud_worker_enabled=False,
        database_path=str(tmp_path / "switch.db"),
    )
    app = create_app(cfg)
    @app.middleware('http')
    async def internal_collector_fixture(request, call_next):
        if request.headers.get('Authorization') == f'Bearer {cfg.collector_token}':
            request.scope['fiap_internal_collector'] = True
        return await call_next(request)
    with TestClient(app, base_url=cfg.base_url) as c:
        with connect(cfg.database_path) as db:
            for role in ("admin", "support", "monitor", "viewer"):
                db.execute(
                    "INSERT INTO users(id,email,name,role,enabled,grants) VALUES (?,?,?,?,?,?)",
                    (
                        f"test-{role}",
                        f"{role}@fiap.com.br",
                        role.title(),
                        role,
                        1,
                        json.dumps({"floors": [3]}),
                    ),
                )
                db.execute(
                    "INSERT INTO credentials VALUES (?,?,?,?)",
                    (
                        f"test-{role}",
                        f"{role}@fiap.com.br",
                        encoded_password,
                        time.time(),
                    ),
                )
            for floor in FLOORS:
                for number in (1, 2):
                    lid, octet = f"{floor}{number:02d}", int(f"{floor}{number}")
                    db.execute(
                        "INSERT INTO labs VALUES (?,?,?,?,?,?,?)",
                        (
                            lid,
                            floor,
                            number,
                            f"Lab {lid}",
                            f"10.3.{octet}.0/24",
                            f"10.3.{octet}.255",
                            "campus",
                        ),
                    )
                    for pc in list(range(1, 13)) + [100]:
                        db.execute(
                            "INSERT INTO computers(id,lab_id,number,ip,mac) VALUES (?,?,?,?,?)",
                            (
                                f"{lid}-{pc}",
                                lid,
                                pc,
                                f"10.3.{octet}.{pc}",
                                "00:11:22:33:44:55",
                            ),
                        )
        c.headers["origin"] = cfg.base_url
        yield c

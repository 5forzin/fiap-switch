import json
import time

import pytest
from fastapi.testclient import TestClient
from test_access import PASSWORD, action, login

from backend.audit import audit
from backend.config import Settings
from backend.db import connect, initialize


def test_suporte_opera_todos_os_andares_sem_gerenciar_contas(client):
    login(client, "support")
    painel = client.get("/api/dashboard").json()
    assert {pc["floor"] for pc in painel["computers"]} == {1, 2, 3, 4, 5, 6, 7, 10, 11}
    assert all(pc["can_control"] for pc in painel["computers"])
    assert action(client, ["201-1", "301-1"]).status_code == 200
    assert client.get("/api/users").status_code == 403
    assert (
        client.put(
            "/api/users",
            json={
                "email": "novo@fiap.com.br",
                "name": "Novo usuário",
                "role": "support",
                "password": PASSWORD,
            },
        ).status_code
        == 403
    )
    assert client.delete("/api/users/test-monitor").status_code == 403
    assert (
        client.post(
            "/api/labs",
            json={"floor": 1, "number": 3, "label": "Sala de teste", "students": None},
        ).status_code
        == 200
    )
    assert (
        client.put("/api/computers/301-2", json={"hostname": "PC-301-2"}).status_code
        == 200
    )
    assert (
        client.put(
            "/api/labs/301/hardware-standard", json={"memory_gib": 2}
        ).status_code
        == 200
    )
    assert client.post("/api/checks", json={"floor": 2}).status_code == 200


@pytest.mark.parametrize(
    "grants",
    [
        {},
        {"floors": [2, 3]},
        {"floors": [3], "computers": ["201-1"]},
        {"labs": ["301"]},
    ],
)
def test_monitor_exige_exatamente_um_andar(client, grants):
    login(client)
    resposta = client.put(
        "/api/users",
        json={
            "email": "novo@fiap.com.br",
            "name": "Monitor de teste",
            "role": "monitor",
            "password": "senha123",
            "grants": grants,
        },
    )
    assert resposta.status_code == 422


def test_grants_antigos_nao_contornam_o_andar_do_monitor(client):
    with connect(client.app.state.settings.database_path) as db:
        db.execute(
            "UPDATE users SET grants=? WHERE id=?",
            (
                json.dumps({"floors": [3], "computers": ["201-1"], "labs": ["201"]}),
                "test-monitor",
            ),
        )
    login(client, "monitor")
    assert {pc["floor"] for pc in client.get("/api/dashboard").json()["computers"]} == {
        3
    }
    assert action(client, ["201-1"]).status_code == 403


def test_senha_de_oito_caracteres_e_nome_completo(client):
    login(client)
    cadastro = {
        "email": "nova@fiap.com.br",
        "name": "Pessoa de Nome Completo",
        "role": "support",
        "password": "senha123",
    }
    resultado = client.put("/api/users", json=cadastro)
    assert resultado.status_code == 200
    assert (
        client.post(
            "/auth/login",
            json={"username": cadastro["email"], "password": cadastro["password"]},
        ).status_code
        == 200
    )
    sessao = client.get("/api/session").json()["user"]
    assert sessao["name"] == cadastro["name"] and sessao["role"] == "support"


def test_exclusao_revoga_acesso_preserva_autoria_e_rejeita_fila(client):
    login(client)
    monitor = TestClient(client.app, base_url=client.app.state.settings.base_url)
    monitor.headers["origin"] = client.app.state.settings.base_url
    try:
        login(monitor, "monitor")
        fila = action(monitor, ["301-1"]).json()["job_ids"][0]
        assert client.delete("/api/users/test-admin").status_code == 409
        assert client.delete("/api/users/test-monitor").status_code == 200
        assert "test-monitor" not in {u["id"] for u in client.get("/api/users").json()}
        assert monitor.get("/api/dashboard").status_code == 401
        assert (
            monitor.post(
                "/auth/login",
                json={"username": "monitor@fiap.com.br", "password": PASSWORD},
            ).status_code
            == 401
        )
        assert (
            client.put(
                "/api/users",
                json={
                    "id": "test-monitor",
                    "email": "monitor@fiap.com.br",
                    "name": "Monitor",
                    "role": "monitor",
                    "grants": {"floors": [3]},
                },
            ).status_code
            == 404
        )
        cabecalho = {
            "authorization": f"Bearer {client.app.state.settings.collector_token}"
        }
        assert (
            client.post("/api/collector/claim", headers=cabecalho).json()["job"] is None
        )
        with connect(client.app.state.settings.database_path) as db:
            assert (
                db.execute("SELECT state FROM jobs WHERE id=?", (fila,)).fetchone()[
                    "state"
                ]
                == "failed"
            )
            assert db.execute(
                "SELECT deleted_at FROM users WHERE id='test-monitor'"
            ).fetchone()[0]
            assert db.execute(
                "SELECT 1 FROM audit WHERE action='user_deleted'"
            ).fetchone()
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        novo = client.put(
            "/api/users",
            json={
                "email": "monitor@fiap.com.br",
                "name": "Outra pessoa",
                "role": "monitor",
                "grants": {"floors": [2]},
                "password": "senha123",
            },
        )
        assert novo.status_code == 200 and novo.json()["id"] != "test-monitor"
    finally:
        monitor.close()


def test_comandos_admin_privados_inclusive_resultado_do_controlador(client):
    login(client)
    privado = action(client, ["301-1"]).json()["job_ids"][0]
    check_privado = client.post("/api/checks", json={"floor": 3}).json()["id"]
    cabecalho = {"authorization": f"Bearer {client.app.state.settings.collector_token}"}
    job = client.post("/api/collector/claim", headers=cabecalho).json()["job"]
    assert (
        client.post(
            f"/api/collector/jobs/{job['id']}/result",
            headers=cabecalho,
            json={
                "lease_token": job["lease_token"],
                "state": "completed",
                "message": "Comando aceito.",
            },
        ).status_code
        == 200
    )
    for perfil in ("support", "monitor"):
        login(client, perfil)
        assert privado not in {
            j["id"] for j in client.get("/api/dashboard").json()["jobs"]
        }
        assert all(
            ev["actor_role"] != "admin" for ev in client.get("/api/audit").json()
        )
        assert client.get("/api/checks/" + check_privado).status_code == 404
        assert check_privado not in {c["id"] for c in client.get("/api/checks").json()}
    check_monitor = client.post("/api/checks", json={"floor": 3}).json()["id"]
    monitor = action(client, ["301-2"]).json()["job_ids"][0]
    login(client, "support")
    assert monitor in {j["id"] for j in client.get("/api/dashboard").json()["jobs"]}
    assert client.get("/api/checks/" + check_monitor).status_code == 200
    suporte = action(client, ["201-1"]).json()["job_ids"][0]
    login(client)
    assert {privado, monitor, suporte} <= {
        j["id"] for j in client.get("/api/dashboard").json()["jobs"]
    }


def test_privacidade_admin_independe_do_perfil_atual_do_autor(client):
    login(client)
    privado = action(client, ["301-1"]).json()["job_ids"][0]
    with connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE users SET role='support' WHERE id='test-admin'")
    login(client, "support")
    assert privado not in {j["id"] for j in client.get("/api/dashboard").json()["jobs"]}
    assert all(e["actor_role"] != "admin" for e in client.get("/api/audit").json())


def test_outro_administrador_nao_ve_acoes_privadas(client):
    login(client)
    privado = action(client, ["301-1"]).json()["job_ids"][0]
    assert (
        client.put(
            "/api/users",
            json={
                "email": "outro@fiap.com.br",
                "name": "Outro administrador",
                "role": "admin",
                "password": "senha123",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/login",
            json={"username": "outro@fiap.com.br", "password": "senha123"},
        ).status_code
        == 200
    )
    client.headers["x-csrf-token"] = client.get("/api/session").json()["user"]["csrf"]
    assert privado not in {j["id"] for j in client.get("/api/dashboard").json()["jobs"]}
    assert not any(
        e["initiator_id"] == "test-admin" for e in client.get("/api/audit").json()
    )


def test_limite_de_auditoria_e_aplicado_depois_da_privacidade(client):
    with connect(client.app.state.settings.database_path) as db:
        audit(db, "test-monitor", "wake", "301-1", "301-1")
        for _ in range(1001):
            audit(db, "test-admin", "wake", "301-2", "301-2")
    login(client, "support")
    eventos = client.get("/api/audit").json()
    assert any(e["initiator_id"] == "test-monitor" for e in eventos)
    assert not any(e["actor_role"] == "admin" for e in eventos)


def test_migracao_preserva_credenciais_sessoes_e_chaves_estrangeiras(
    tmp_path, encoded_password
):
    config = Settings(database_path=str(tmp_path / "legado.db"))
    with connect(config.database_path) as db:
        db.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','monitor','viewer')),enabled INTEGER NOT NULL DEFAULT 1,grants TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE credentials(user_id TEXT PRIMARY KEY REFERENCES users(id),username TEXT NOT NULL UNIQUE COLLATE NOCASE,password_hash TEXT NOT NULL,changed REAL NOT NULL);
        CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL,csrf TEXT NOT NULL);""")
        db.execute(
            "INSERT INTO users VALUES('antigo','admin@fiap.com.br','Administrador','admin',1,'{}')"
        )
        db.execute(
            "INSERT INTO credentials VALUES(?,?,?,?)",
            ("antigo", "admin@fiap.com.br", encoded_password, time.time()),
        )
        db.execute("INSERT INTO sessions VALUES('sessao','antigo',9999999999,'csrf')")
    initialize(config)
    with connect(config.database_path) as db:
        assert (
            db.execute("SELECT password_hash FROM credentials").fetchone()[0]
            == encoded_password
        )
        assert db.execute("SELECT user_id FROM sessions").fetchone()[0] == "antigo"
        db.execute(
            "INSERT INTO users(id,email,name,role) VALUES('suporte','suporte@fiap.com.br','Suporte','support')"
        )
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_quantidade_indefinida_preserva_equipamentos_e_pode_ser_definida(client):
    login(client, "support")
    criada = client.post(
        "/api/labs",
        json={
            "floor": 4,
            "number": 3,
            "label": "Quantidade indefinida",
            "students": None,
            "include_professor": False,
        },
    )
    assert criada.status_code == 200
    painel = client.get("/api/dashboard").json()
    assert next(s for s in painel["labs"] if s["id"] == "403")["student_count"] is None
    assert not [p for p in painel["computers"] if p["lab_id"] == "403"]
    assert client.put("/api/labs/403/quantity", json={"students": 3}).status_code == 200
    assert (
        client.put("/api/labs/403/quantity", json={"students": None}).status_code == 200
    )
    assert (
        len(
            [
                p
                for p in client.get("/api/dashboard").json()["computers"]
                if p["lab_id"] == "403"
            ]
        )
        == 3
    )
    assert client.put("/api/labs/403/quantity", json={"students": 2}).status_code == 409
    assert (
        client.put("/api/labs/403/quantity", json={"students": 100}).status_code == 422
    )
    login(client, "monitor")
    assert (
        client.put("/api/labs/403/quantity", json={"students": None}).status_code == 403
    )

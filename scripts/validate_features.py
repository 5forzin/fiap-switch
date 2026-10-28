"""Valida a aplicação publicada com as máquinas Windows do laboratório na AWS."""

import argparse
import base64
import json
import secrets
import time
from pathlib import Path
from uuid import uuid4

import httpx
from botocore.exceptions import ClientError

from scripts.live_validation import URL, Validation


def prepare(v):
    with v.client() as admin:
        v.state.setdefault("fixture_labs", [])
        for floor in (1, 2, 3, 4, 5, 6, 7, 10, 11):
            lid = f"{floor}01"
            if lid in v.state["fixture_labs"]:
                continue
            response = admin.post(
                "/api/labs",
                json={
                    "floor": floor,
                    "number": 1,
                    "label": f"Validação AWS · Laboratório {lid}",
                    "students": 4 if floor == 3 else 0,
                    "include_professor": floor != 3,
                },
            )
            assert response.status_code == 200, response.text
            v.state["fixture_labs"].append(lid)
            v.save()
        data = admin.get("/api/dashboard").json()
        assert {pc["floor"] for pc in data["computers"]} == {
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            10,
            11,
        }
        pcs = {pc["id"]: pc for pc in data["computers"]}
        assert (
            pcs["301-4"]["ip"] == "10.3.31.4"
            and pcs["1001-100"]["ip"] == "10.3.101.100"
        )
        assert all(pc["status"] == "unknown" for pc in pcs.values())
        v.record(
            "inventory_all_floors_addressing_and_unknown_state",
            {"floors": 9, "computers": len(pcs)},
        )
        assert (
            admin.post(
                "/api/labs",
                json={"floor": 3, "number": 1, "label": "Duplicate", "students": 1},
            ).status_code
            == 409
        )
        assert (
            admin.post(
                "/api/labs",
                json={"floor": 9, "number": 1, "label": "Invalid", "students": 1},
            ).status_code
            == 422
        )
        assert (
            admin.put(
                "/api/computers/301-4", json={"mac": "FF:FF:FF:FF:FF:FF"}
            ).status_code
            == 422
        )
        assert v.action(admin, ["301-4"], "wake").status_code == 409
        v.record(
            "duplicate_laboratory_invalid_floor_mac_and_missing_wake_mac_rejected", True
        )
        for key, role, grants in (
            ("floor3", "monitor", {"floors": [3]}),
            ("floor2", "monitor", {"floors": [2]}),
            ("viewer", "viewer", {"floors": [3]}),
        ):
            if key in v.accounts:
                continue
            email, password = (
                f"e2e-20261027-{key}@fiap.com.br",
                secrets.token_urlsafe(24),
            )
            payload = {
                "email": email,
                "name": f"Validação AWS {key}",
                "role": role,
                "grants": grants,
                "password": password,
            }
            response = admin.put("/api/users", json=payload)
            assert response.status_code == 200, response.text
            v.accounts[key] = {
                "username": email,
                "password": password,
                "payload": {**payload, "password": None, "id": response.json()["id"]},
            }
            v.save()
        session = admin.get("/api/session").json()
        assert (
            admin.put(
                "/api/users",
                json={
                    "email": "other@example.com",
                    "name": "Invalid",
                    "role": "monitor",
                    "password": secrets.token_urlsafe(24),
                },
            ).status_code
            == 422
        )
        assert (
            admin.put(
                "/api/users",
                json={
                    "id": session["user"]["id"],
                    "email": session["user"]["email"],
                    "name": session["user"]["name"],
                    "role": "admin",
                    "enabled": False,
                },
            ).status_code
            == 409
        )
        assert (
            admin.post(
                "/api/actions", json={"action": "shutdown", "computer_ids": ["301-4"]}
            ).status_code
            == 400
        )
        assert v.action(admin, ["301-4"], "unsupported").status_code == 422
        assert admin.get("/auth/login").status_code == 503
        v.record(
            "corporate_email_self_lockout_action_validation_and_inactive_sso", True
        )
    with httpx.Client(
        base_url=URL, proxy="socks5://127.0.0.1:1080", timeout=30
    ) as anonymous:
        assert anonymous.get("/api/dashboard").status_code == 401
        assert (
            anonymous.post(
                "/auth/login", json={"username": "test", "password": "test"}
            ).status_code
            == 403
        )
        assert anonymous.post("/api/collector/claim").status_code == 401
        v.record("anonymous_cross_origin_and_unauthenticated_collector_rejected", True)


def accounts(v):
    with (
        v.client() as admin,
        v.client("floor3") as monitor,
        v.client("viewer") as viewer,
    ):
        assert {
            pc["floor"] for pc in monitor.get("/api/dashboard").json()["computers"]
        } == {3}
        assert monitor.get("/api/users").status_code == 403
        assert (
            monitor.post(
                "/api/labs",
                json={"floor": 3, "number": 5, "label": "Unauthorized", "students": 0},
            ).status_code
            == 403
        )
        assert v.action(monitor, ["301-4", "202-100"], "restart").status_code == 403
        assert admin.get("/api/dashboard").json()["jobs"] == []
        assert v.action(viewer, ["301-4"], "restart").status_code == 403
        assert all(
            not pc["can_control"]
            for pc in viewer.get("/api/dashboard").json()["computers"]
        )
        assert v.action(monitor, ["301-4"], "restart", key="invalid").status_code == 400
        assert (
            monitor.post(
                "/api/actions",
                json={"action": "restart", "computer_ids": ["301-4"]},
                headers={
                    "Idempotency-Key": "00000000-0000-4000-8000-000000000001",
                    "X-CSRF-Token": "wrong",
                },
            ).status_code
            == 403
        )
        v.record(
            "floor_scope_read_only_role_atomic_batch_admin_and_csrf_permissions", True
        )
        payload = {
            **v.accounts["floor3"]["payload"],
            "grants": {"floors": [2]},
        }
        assert admin.put("/api/users", json=payload).status_code == 200
        assert monitor.get("/api/dashboard").status_code == 401
        with v.client("floor3") as specific:
            assert {
                pc["floor"] for pc in specific.get("/api/dashboard").json()["computers"]
            } == {2}
        assert (
            admin.put(
                "/api/users", json={**payload, "grants": {"labs": ["202"]}}
            ).status_code
            == 422
        )
        assert (
            admin.put("/api/users", json=v.accounts["floor3"]["payload"]).status_code
            == 200
        )
        v.record("single_floor_assignment_and_immediate_session_revocation", True)
        old_password = v.accounts["viewer"]["password"]
        new_password = secrets.token_urlsafe(24)
        assert (
            viewer.post(
                "/auth/password",
                json={"current_password": "wrong", "new_password": new_password},
            ).status_code
            == 400
        )
        assert (
            viewer.post(
                "/auth/password",
                json={"current_password": old_password, "new_password": new_password},
            ).status_code
            == 200
        )
        v.accounts["viewer"]["password"] = new_password
        v.save()
        assert (
            admin.put(
                "/api/users", json={**v.accounts["viewer"]["payload"], "enabled": False}
            ).status_code
            == 200
        )
        assert viewer.get("/api/dashboard").status_code == 401
        reset = secrets.token_urlsafe(24)
        assert (
            admin.put(
                "/api/users",
                json={
                    **v.accounts["viewer"]["payload"],
                    "enabled": True,
                    "password": reset,
                },
            ).status_code
            == 200
        )
        v.accounts["viewer"]["password"] = reset
        v.save()
        with v.client("viewer") as restored:
            assert restored.get("/api/dashboard").status_code == 200
            assert restored.post("/auth/logout").status_code == 200
            assert restored.get("/api/dashboard").status_code == 401
        v.record("password_change_admin_reset_disable_reenable_and_logout", True)
        with httpx.Client(
            base_url=URL,
            proxy="socks5://127.0.0.1:1080",
            headers={"Origin": URL},
            timeout=30,
        ) as bad:
            for _ in range(5):
                assert (
                    bad.post(
                        "/auth/login",
                        json={
                            "username": v.accounts["floor2"]["username"],
                            "password": "incorrect",
                        },
                    ).status_code
                    == 401
                )
            assert (
                bad.post(
                    "/auth/login",
                    json={
                        "username": v.accounts["floor2"]["username"],
                        "password": v.accounts["floor2"]["password"],
                    },
                ).status_code
                == 429
            )
        assert (
            admin.put(
                "/api/users",
                json={
                    **v.accounts["floor2"]["payload"],
                    "password": v.accounts["floor2"]["password"],
                },
            ).status_code
            == 200
        )
        with v.client("floor2") as recovered:
            assert recovered.get("/api/dashboard").status_code == 200
        v.record("persistent_login_attempt_limit_and_administrative_recovery", True)


def configure(v):
    certificates = "\n".join(
        v.powershell(t["instance_id"], "Get-Content C:\\winrm-lab.pem -Raw")
        for t in v.state["targets"]
    )
    token = v.ssh(
        "from pathlib import Path\nfor line in Path('/opt/fiap-switch/.env.production').read_text().splitlines():\n    if line.startswith('COLLECTOR_TOKEN='): print(line.split('=',1)[1])\n"
    )
    env = f"SWITCH_URL={URL}\nCOLLECTOR_TOKEN={token}\nWINRM_USERNAME={v.state['winrm_user']}\nWINRM_PASSWORD={v.state['winrm_password']}\nWINRM_CA_BUNDLE=C:/fiap-switch/winrm-lab.pem\nPROBE_INTERVAL=15\n"
    runner = "import os,sys,logging\nfrom pathlib import Path\nsource=Path('C:/fiap-switch/source-path.txt').read_text().strip()\nos.chdir(source)\nsys.path.insert(0,source)\nlogging.basicConfig(filename='C:/fiap-switch/collector.log',level=logging.INFO)\nfrom collector.service import Controller\nController().run()\n"
    encode = lambda value: base64.b64encode(value.encode()).decode()
    script = f"""$ErrorActionPreference='Stop'
$root='C:\\fiap-switch'
$source=(Get-Content "$root\\source-path.txt" -Raw).Trim()
[IO.File]::WriteAllBytes("$root\\winrm-lab.pem",[Convert]::FromBase64String('{encode(certificates)}'))
[IO.File]::WriteAllBytes("$source\\.env.collector",[Convert]::FromBase64String('{encode(env)}'))
[IO.File]::WriteAllBytes("$root\\run_collector.py",[Convert]::FromBase64String('{encode(runner)}'))
icacls $root /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
$action=New-ScheduledTaskAction -Execute "$root\\python\\python.exe" -Argument "$root\\run_collector.py" -WorkingDirectory $source
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName 'FIAP-Switch-Collector' -Action $action -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtStartup) -User SYSTEM -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName 'FIAP-Switch-Collector'
Get-ScheduledTask -TaskName 'FIAP-Switch-Collector' | Select-Object TaskName,State | ConvertTo-Json -Compress
"""
    print(v.powershell(v.state["controller"], script))
    v.record("windows_controller_scheduled_task_with_verified_winrm_certificates", True)


def health(v):
    with v.client() as admin:

        def online():
            computers = {
                pc["id"]: pc for pc in admin.get("/api/dashboard").json()["computers"]
            }
            return (
                computers
                if all(
                    computers[t["computer_id"]]["status"] == "online"
                    for t in v.state["targets"]
                )
                else None
            )

        pcs = v.wait(online, "A telemetria real dos Windows não chegou.", timeout=240)
        for target in v.state["targets"]:
            pc = pcs[target["computer_id"]]
            assert pc["mac"] and pc["metrics"]["os"].startswith(
                "Microsoft Windows Server 2022"
            )
            assert all(
                key in pc["metrics"]
                for key in ("cpu", "memory", "disk_free", "uptime_hours")
            )
            target["mac"] = pc["mac"]
        v.save()
        v.record(
            "live_winrm_cpu_memory_disk_uptime_os_and_mac_autodiscovery",
            {
                t["computer_id"]: pcs[t["computer_id"]]["metrics"]
                for t in v.state["targets"]
            },
        )


def collector_client(v):
    token = v.ssh(
        "from pathlib import Path\nfor line in Path('/opt/fiap-switch/.env.production').read_text().splitlines():\n    if line.startswith('COLLECTOR_TOKEN='): print(line.split('=',1)[1])\n"
    )
    return httpx.Client(
        base_url=URL,
        proxy="socks5://127.0.0.1:1080",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def pause(v):
    print(
        v.powershell(
            v.state["controller"],
            "Stop-ScheduledTask -TaskName FIAP-Switch-Collector; Get-ScheduledTask -TaskName FIAP-Switch-Collector | Select-Object State | ConvertTo-Json -Compress",
        )
    )
    with v.client() as admin, collector_client(v) as collector:
        lease = v.action(admin, ["202-100"], "wake")
        assert lease.status_code == 200
        claimed = collector.post("/api/collector/claim").json()["job"]
        assert claimed["computer_id"] == "202-100"
        assert (
            collector.post(
                f"/api/collector/jobs/{claimed['id']}/result",
                json={
                    "state": "completed",
                    "message": "Teste de token inválido",
                    "lease_token": "incorrect",
                },
            ).status_code
            == 409
        )
        assert (
            collector.post(
                "/api/collector/reports",
                json={
                    "reports": [{"computer_id": "not-in-inventory", "status": "online"}]
                },
            ).status_code
            == 403
        )
        assert (
            collector.post(
                "/api/collector/reports",
                json={"reports": [{"computer_id": "301-4", "status": "offline"}]},
            ).status_code
            == 422
        )
        key = str(uuid4())
        queued = v.action(admin, ["301-4"], "wake", key=key)
        assert queued.status_code == 200
        assert v.action(admin, ["301-4"], "wake", key=key).json() == queued.json()
        assert v.action(admin, ["301-4"], "restart", key=key).status_code == 409
        assert v.action(admin, ["301-4"], "restart").status_code == 409
        v.state["outage"] = {
            "started": time.time(),
            "lease_job": claimed["id"],
            "queued_job": queued.json()["job_ids"][0],
        }
        v.record(
            "live_idempotency_conflicting_commands_collector_scope_and_lease_token",
            True,
        )
        before = admin.get("/api/dashboard").json()["jobs"]
        print(
            v.ssh(
                "import subprocess\nsubprocess.run(['sudo','-n','docker','restart','fiap-switch-web-1'],check=True)\n"
            )
        )

        def recovered():
            try:
                r = admin.get("/api/dashboard")
                return r.json() if r.status_code == 200 else None
            except httpx.HTTPError:
                return None

        after = v.wait(recovered, "O servidor web não voltou.", timeout=60)
        assert {j["id"] for j in before} <= {j["id"] for j in after["jobs"]}
        assert any(
            j["id"] == v.state["outage"]["queued_job"] and j["state"] == "queued"
            for j in after["jobs"]
        )
        v.record(
            "queue_and_authenticated_session_survive_real_web_container_restart", True
        )
    print(
        "Controlador interrompido. Verifique a expiração da leitura e da execução após 125 segundos; a fila, após 305 segundos.",
        flush=True,
    )


def stale(v):
    assert time.time() - v.state["outage"]["started"] >= 125, (
        "Aguarde o prazo real de 120 segundos."
    )
    with v.client() as admin:
        data = admin.get("/api/dashboard").json()
        assert all(
            pc["status"] == "unknown" and pc["stale"] and not pc["metrics"]
            for pc in data["computers"]
        )
        job = next(j for j in data["jobs"] if j["id"] == v.state["outage"]["lease_job"])
        assert job["state"] == "failed" and "desconhecido" in job["message"].lower()
        v.record(
            "actual_collector_outage_stale_health_and_interrupted_lease_timeout",
            {
                "elapsed_seconds": round(time.time() - v.state["outage"]["started"]),
                "lease_state": job["state"],
            },
        )


def revoke(v):
    with v.client() as admin, v.client("floor2") as monitor:
        queued = v.action(monitor, ["202-100"], "wake")
        assert queued.status_code == 200
        assert (
            admin.put(
                "/api/users", json={**v.accounts["floor2"]["payload"], "grants": {}}
            ).status_code
            == 200
        )
        assert monitor.get("/api/dashboard").status_code == 401
        v.state["outage"]["revoked_job"] = queued.json()["job_ids"][0]
        v.save()


def resume(v):
    assert time.time() - v.state["outage"]["started"] >= 305, (
        "Aguarde o prazo real de cinco minutos da fila."
    )
    with v.client() as admin:
        data = admin.get("/api/dashboard").json()
        job = next(
            j for j in data["jobs"] if j["id"] == v.state["outage"]["queued_job"]
        )
        assert job["state"] == "failed" and "expir" in job["message"].lower()
        v.record(
            "actual_five_minute_undelivered_command_expiry",
            {"elapsed_seconds": round(time.time() - v.state["outage"]["started"])},
        )
        print(
            v.powershell(
                v.state["controller"],
                "Start-ScheduledTask -TaskName FIAP-Switch-Collector",
            )
        )

        def rejected():
            jobs = admin.get("/api/dashboard").json()["jobs"]
            return next(
                (
                    j
                    for j in jobs
                    if j["id"] == v.state["outage"]["revoked_job"]
                    and j["state"] == "failed"
                ),
                None,
            )

        revoked = v.wait(rejected, "O comando sem permissão não foi rejeitado.", 60)
        assert "revog" in revoked["message"].lower()
        assert (
            admin.put("/api/users", json=v.accounts["floor2"]["payload"]).status_code
            == 200
        )
        v.record("actual_queued_permission_revocation_rechecked_before_execution", True)
    health(v)


def failure(v):
    target = v.state["targets"][0]
    before = v.boot_times()[target["computer_id"]]
    print(
        v.powershell(
            target["instance_id"],
            "Stop-Service WinRM -Force; (Get-Service WinRM).Status.ToString()",
        )
    )
    try:
        with v.client() as admin:
            result = v.action(admin, [target["computer_id"]], "restart")
            assert result.status_code == 200
            jid = result.json()["job_ids"][0]

            def failed():
                data = admin.get("/api/dashboard").json()
                job = next(j for j in data["jobs"] if j["id"] == jid)
                pc = next(
                    p for p in data["computers"] if p["id"] == target["computer_id"]
                )
                return (
                    job
                    if job["state"] == "failed" and pc["status"] == "unreachable"
                    else None
                )

            job = v.wait(
                failed, "A falha do WinRM não foi informada corretamente.", 120
            )
            assert "falhou" in job["message"].lower()
        assert v.boot_times()[target["computer_id"]] == before
        v.record(
            "real_winrm_service_failure_command_failure_and_no_false_power_state",
            {"job": jid, "boot_unchanged": True},
        )
    finally:
        print(
            v.powershell(
                target["instance_id"],
                "Start-Service WinRM; (Get-Service WinRM).Status.ToString()",
            )
        )
    health(v)


def restart_before(v):
    v.state["power"] = {"started": time.time(), "before": v.boot_times()}
    v.save()
    print(
        "Horários de inicialização registrados. Reinicie as máquinas pela interface.",
        flush=True,
    )


def restart_after(v):
    with v.client() as admin:

        def delivered():
            jobs = [
                j
                for j in admin.get("/api/dashboard").json()["jobs"]
                if j["created"] >= v.state["power"]["started"]
                and j["action"] == "restart"
            ]
            return (
                jobs
                if len(jobs) == 2 and all(j["state"] == "completed" for j in jobs)
                else None
            )

        jobs = v.wait(
            delivered, "Os dois comandos de reinicialização não foram enviados.", 180
        )
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                after = v.boot_times()
                if all(
                    after[cid] != before
                    for cid, before in v.state["power"]["before"].items()
                ):
                    v.state["power"]["after"] = after
                    v.record(
                        "browser_bulk_restart_actual_windows_boot_timestamps_changed",
                        {
                            "jobs": [j["id"] for j in jobs],
                            "before": v.state["power"]["before"],
                            "after": after,
                        },
                    )
                    health(v)
                    return
            except (ClientError, RuntimeError, TimeoutError) as error:
                print(
                    f"Aguardando a reinicialização do Windows: {type(error).__name__}",
                    flush=True,
                )
            time.sleep(10)
        raise TimeoutError("Os horários reais de inicialização não mudaram.")


def shutdown_after(v):
    with v.client() as admin:
        jobs = v.wait(
            lambda: [
                j
                for j in admin.get("/api/dashboard").json()["jobs"]
                if j["action"] == "shutdown" and j["state"] == "completed"
            ],
            "O desligamento não foi enviado.",
            120,
        )
        stopped = v.wait(
            lambda: (
                (states if all(s == "stopped" for s in states.values()) else None)
                if (states := v.target_states())
                else None
            ),
            "Os alvos Windows não desligaram.",
            300,
        )
        v.record(
            "browser_shutdown_actual_ec2_stopped",
            {"states": stopped, "jobs": [j["id"] for j in jobs]},
        )
        check_id = admin.post("/api/checks", json={}).json()["id"]

        def checked():
            result = admin.get(f"/api/checks/{check_id}").json()
            return result if result["state"] == "completed" else None

        result = v.wait(checked, "As máquinas desligadas não foram verificadas.", 180)
        assert result["summary"]["unreachable"] == 2
        assert all(pc["last_online"] for pc in result["computers"])
        v.record(
            "fresh_scan_identifies_both_unresponsive_ips_and_preserves_last_online",
            {
                "check_id": check_id,
                "summary": result["summary"],
                "computers": [
                    {
                        k: pc[k]
                        for k in (
                            "computer_id",
                            "ip",
                            "status",
                            "last_online",
                            "checked_at",
                        )
                    }
                    for pc in result["computers"]
                ],
            },
        )


def wake_capture(v):
    controller = v.state["controller"]
    # A ENA não expôs os pacotes ao PktMon. A captura usa a recepção UDP local.
    listener = (
        "import socket,json; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
        "s.bind(('',9)); s.settimeout(30); "
        "packets=[s.recvfrom(4096)[0].hex() for _ in range(3)]; "
        "print(json.dumps({'packets':packets}))"
    )
    encoded = base64.b64encode(listener.encode()).decode()
    command = v.send(
        controller,
        f'''& C:\\fiap-switch\\python\\python.exe -c "import base64; exec(base64.b64decode('{encoded}'))"''',
    )
    time.sleep(3)
    with v.client() as admin:
        pc = next(
            p
            for p in admin.get("/api/dashboard").json()["computers"]
            if p["id"] == "301-4"
        )
        response = v.action(admin, [pc["id"]], "wake")
        response.raise_for_status()
        jid = response.json()["job_ids"][0]

        def delivered():
            job = next(
                j for j in admin.get("/api/dashboard").json()["jobs"] if j["id"] == jid
            )
            return job if job["state"] == "completed" else None

        v.wait(delivered, "O pacote Wake-on-LAN não foi enviado.", 120)
    packets = json.loads(v.wait_command(controller, command, 90))["packets"]
    expected = b"\xff" * 6 + bytes.fromhex(pc["mac"].replace(":", "")) * 16
    assert len(packets) == 3 and all(
        bytes.fromhex(packet) == expected for packet in packets
    )
    assert all(s == "stopped" for s in v.target_states().values())
    v.record(
        "real_wake_magic_packet_captured_on_windows_controller",
        {
            "job": jid,
            "received_udp_datagrams": len(packets),
            "payload_bytes": len(expected),
            "magic_packet_payload_matches_target_mac": True,
            "limit": "A EC2 não oferece Wake-on-LAN por broadcast; o ligamento físico exige validação no campus.",
        },
    )


def persistence(v):
    with v.client() as admin:
        before = {
            "checks": admin.get("/api/checks").json(),
            "history": admin.get("/api/history").json(),
            "events": admin.get("/api/hardware-events").json(),
        }
        v.ssh(
            "import subprocess; subprocess.run(['sudo','-n','docker','restart','fiap-switch-web-1'],check=True)"
        )
        v.wait(
            lambda: admin.get("/healthz").status_code == 200,
            "A reinicialização do servidor web falhou.",
            90,
        )
        after = {
            "checks": admin.get("/api/checks").json(),
            "history": admin.get("/api/history").json(),
            "events": admin.get("/api/hardware-events").json(),
        }
        assert before == after
        v.record(
            "daily_history_hardware_incidents_and_checks_survive_actual_web_restart",
            True,
        )


def export_results(v):
    output = {
        "date": "2026-10-27",
        "environment": "Aplicação publicada por HTTPS, controlador Windows Server 2022 e dois alvos WinRM HTTPS em uma VPC isolada na AWS.",
        "results": v.results,
        "limitations": [
            "O Wake-on-LAN físico exige validação na LAN do campus, pois a VPC da AWS não oferece broadcast.",
            "O SSO Microsoft Entra exige o cadastro da aplicação no diretório FIAP; as contas locais com senha foram testadas.",
            "A mudança diária de data foi validada por testes automatizados, sem alterar os relógios reais.",
        ],
    }
    Path("docs/validation-2026-10-27.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
    print(
        f"Exportadas {len(v.results)} verificações sem dados privados de infraestrutura.",
        flush=True,
    )


def cleanup(v):
    print(
        v.powershell(
            v.state["controller"],
            "Stop-ScheduledTask -TaskName FIAP-Switch-Collector; 'Controlador parado.'",
        ),
        flush=True,
    )
    fixture = {
        "labs": v.state["fixture_labs"],
        "users": [a["payload"]["id"] for a in v.accounts.values()],
    }
    script = """
import json
from backend.db import connect
fixture = json.loads(FIXTURE)
with connect('/app/data/switch.db') as db:
    labs = [r['id'] for r in db.execute('SELECT id,label FROM labs') if r['id'] in fixture['labs'] and r['label'].startswith('AWS validation')]
    users = [r['id'] for r in db.execute('SELECT id,email FROM users') if r['id'] in fixture['users'] and r['email'].startswith('e2e-20261027-')]
    computers = [r['id'] for r in db.execute('SELECT id,lab_id FROM computers') if r['lab_id'] in labs]
    jobs = [r['id'] for r in db.execute('SELECT id,computer_id,actor_id FROM jobs') if r['computer_id'] in computers or r['actor_id'] in users]
    for r in db.execute('SELECT id,actor_id,jobs FROM requests').fetchall():
        if r['actor_id'] in users or any(j in jobs for j in json.loads(r['jobs'])):
            db.execute('DELETE FROM requests WHERE id=? AND actor_id=?', (r['id'],r['actor_id']))
    for jid in jobs:
        db.execute('DELETE FROM jobs WHERE id=?', (jid,))
    checks = {r['check_id'] for r in db.execute('SELECT check_id,lab_id FROM check_results') if r['lab_id'] in labs}
    for lid in labs:
        for table in ('check_results','daily_status','hardware_events','hardware_standards','lab_inventory','computers'):
            db.execute(f'DELETE FROM {table} WHERE lab_id=?', (lid,))
        db.execute('DELETE FROM labs WHERE id=?', (lid,))
    for cid in checks:
        db.execute('DELETE FROM checks WHERE id=? AND NOT EXISTS(SELECT 1 FROM check_results WHERE check_id=?)', (cid,cid))
    for uid in users:
        for table in ('sessions','credentials'):
            db.execute(f'DELETE FROM {table} WHERE user_id=?', (uid,))
        db.execute('DELETE FROM users WHERE id=?', (uid,))
    db.execute("DELETE FROM login_attempts WHERE username LIKE 'e2e-20261027-%'")
    print(json.dumps({'removed_labs':len(labs),'removed_computers':len(computers),'removed_users':len(users),'retained_audit':True}))
""".replace("FIXTURE", repr(json.dumps(fixture)))
    removed = json.loads(v.ssh(script, docker=True))
    v.ec2.stop_instances(InstanceIds=v.state["instances"])

    def stopped():
        states = {
            i["InstanceId"]: i["State"]["Name"]
            for r in v.ec2.describe_instances(InstanceIds=v.state["instances"])[
                "Reservations"
            ]
            for i in r["Instances"]
        }
        return states if all(s == "stopped" for s in states.values()) else None

    states = v.wait(stopped, "As instâncias do laboratório não desligaram.", 300)
    v.record(
        "owned_validation_fixtures_removed_and_all_three_ec2_instances_stopped",
        {
            **removed,
            "states": states,
            "storage": "Volumes EBS criptografados mantidos com as instâncias desligadas para reutilização.",
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=[
            "prepare",
            "accounts",
            "configure",
            "health",
            "pause",
            "stale",
            "revoke",
            "resume",
            "failure",
            "restart_before",
            "restart_after",
            "shutdown_after",
            "wake_capture",
            "persistence",
            "export_results",
            "cleanup",
        ],
    )
    args = parser.parse_args()
    globals()[args.phase](Validation())

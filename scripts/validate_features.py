"""Exercise the published application against owned Windows EC2 fixtures."""

import argparse
import base64
import secrets
import time
from uuid import uuid4

import httpx

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
                    "label": f"AWS validation · Lab {lid}",
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
                "name": f"AWS validation {key}",
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
            "grants": {"computers": ["202-100"]},
        }
        assert admin.put("/api/users", json=payload).status_code == 200
        assert monitor.get("/api/dashboard").status_code == 401
        with v.client("floor3") as specific:
            assert [
                pc["id"] for pc in specific.get("/api/dashboard").json()["computers"]
            ] == ["202-100"]
        assert (
            admin.put(
                "/api/users", json={**payload, "grants": {"labs": ["202"]}}
            ).status_code
            == 200
        )
        with v.client("floor3") as lab:
            assert {
                pc["lab_id"] for pc in lab.get("/api/dashboard").json()["computers"]
            } == {"202"}
        assert (
            admin.put("/api/users", json=v.accounts["floor3"]["payload"]).status_code
            == 200
        )
        v.record("laboratory_computer_grants_and_immediate_session_revocation", True)
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

        pcs = v.wait(online, "Actual Windows telemetry did not arrive.", timeout=240)
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
                    "message": "Invalid token test",
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

        after = v.wait(recovered, "Web process did not recover.", timeout=60)
        assert {j["id"] for j in before} <= {j["id"] for j in after["jobs"]}
        assert any(
            j["id"] == v.state["outage"]["queued_job"] and j["state"] == "queued"
            for j in after["jobs"]
        )
        v.record(
            "queue_and_authenticated_session_survive_real_web_container_restart", True
        )
    print(
        "Collector outage started. Real stale/lease checks become due after 125 seconds; queued expiry after 305 seconds.",
        flush=True,
    )


def stale(v):
    assert time.time() - v.state["outage"]["started"] >= 125, (
        "Wait for the actual 120-second timeout."
    )
    with v.client() as admin:
        data = admin.get("/api/dashboard").json()
        assert all(
            pc["status"] == "unknown" and pc["stale"] and not pc["metrics"]
            for pc in data["computers"]
        )
        job = next(j for j in data["jobs"] if j["id"] == v.state["outage"]["lease_job"])
        assert job["state"] == "failed" and "unknown" in job["message"].lower()
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
        "Wait for the actual five-minute queue timeout."
    )
    with v.client() as admin:
        data = admin.get("/api/dashboard").json()
        job = next(
            j for j in data["jobs"] if j["id"] == v.state["outage"]["queued_job"]
        )
        assert job["state"] == "failed" and "expired" in job["message"].lower()
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

        revoked = v.wait(rejected, "Revoked queued command was not rejected.", 60)
        assert "revoked" in revoked["message"].lower()
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

            job = v.wait(failed, "WinRM outage was not reported truthfully.", 120)
            assert "failed" in job["message"].lower()
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
        "Actual boot timestamps recorded. Submit the restart from the web interface.",
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

        jobs = v.wait(delivered, "Both restart commands were not delivered.", 180)
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
            except Exception as error:
                print(f"Waiting for Windows reboot: {type(error).__name__}", flush=True)
            time.sleep(10)
        raise TimeoutError("Actual Windows boot timestamps did not change.")


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
        ],
    )
    args = parser.parse_args()
    globals()[args.phase](Validation())

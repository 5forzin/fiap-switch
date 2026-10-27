from datetime import UTC, datetime

from test_access import login

from backend.checks import record_observation
from backend.db import connect, inventory
from backend.hardware import compare_hardware
from backend.models import Report

HARDWARE = {
    "cpu_model": "Intel Core i7",
    "logical_cpus": 8,
    "memory_gib": 15.8,
    "storage_gib": 512,
    "disk_count": 1,
    "gpu_models": ["NVIDIA RTX 4060"],
}
STANDARD = {
    "cpu_model": "Core i7",
    "logical_cpus": 8,
    "memory_gib": 16,
    "storage_gib": 512,
    "disk_count": 1,
    "gpu_model": "RTX 4060",
    "match_mode": "exact",
}


def report(client, cid="301-1", status="online", hardware=HARDWARE):
    token = client.app.state.settings.collector_token
    payload = {"computer_id": cid, "status": status}
    if status == "online":
        payload["hardware"] = hardware
        payload["metrics"] = {"cpu": 10}
    response = client.post(
        "/api/collector/reports",
        json={"reports": [payload]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text


def test_checks_scope_permission_and_duplicate_request(client):
    assert client.post("/api/checks", json={}).status_code == 401
    login(client, "viewer")
    assert client.post("/api/checks", json={}).status_code == 403
    login(client, "monitor")
    first = client.post("/api/checks", json={"floor": 3})
    assert first.status_code == 200
    assert client.post("/api/checks", json={"floor": 3}).json() == first.json()
    assert client.post("/api/checks", json={"floor": 2}).status_code == 404
    assert client.post("/api/checks", json={"floor": 9}).status_code == 422
    detail = client.get("/api/checks/" + first.json()["id"]).json()
    assert detail["summary"]["total"] == 26 and detail["state"] == "queued"
    assert {pc["floor"] for pc in detail["computers"]} == {3}


def test_fresh_results_complete_checks_and_remain_immutable(client):
    login(client)
    response = client.post("/api/checks", json={"lab_id": "301"})
    cid = response.json()["id"]
    assert client.get(
        "/api/collector/checks",
        headers={
            "Authorization": f"Bearer {client.app.state.settings.collector_token}"
        },
    ).json()["pending"]
    for number in list(range(1, 13)) + [100]:
        report(
            client, f"301-{number}", status="online" if number != 2 else "unreachable"
        )
    detail = client.get("/api/checks/" + cid).json()
    assert (
        detail["state"] == "completed"
        and detail["summary"]["online"] == 12
        and detail["summary"]["unreachable"] == 1
    )
    before = detail["computers"][0]
    report(client, "301-1", status="unreachable")
    after = client.get("/api/checks/" + cid).json()["computers"][0]
    assert before == after and after["status"] == "online"
    assert not client.get(
        "/api/collector/checks",
        headers={
            "Authorization": f"Bearer {client.app.state.settings.collector_token}"
        },
    ).json()["pending"]


def test_daily_history_timezone_last_online_and_day_rollover(client):
    cfg = client.app.state.settings
    first = datetime(2026, 10, 28, 2, 59, 59, tzinfo=UTC).timestamp()
    with connect(cfg.database_path) as db:
        pc = next(row for row in inventory(db) if row["id"] == "301-1")
        record_observation(db, pc, Report(computer_id=pc["id"], status="online"), first)
        record_observation(
            db, pc, Report(computer_id=pc["id"], status="unreachable"), first + 2
        )
        rows = db.execute("SELECT * FROM daily_status ORDER BY day").fetchall()
        assert [r["day"] for r in rows] == ["2026-10-27", "2026-10-28"]
        assert rows[1]["last_online"] == first and rows[1]["status"] == "unreachable"
    login(client, "monitor")
    assert len(client.get("/api/history").json()) == 2
    current = client.get("/api/history/2026-10-28").json()
    assert (
        current["summary"]["unreachable"] == 1
        and current["computers"][0]["last_online"] == first
    )


def test_history_scope_status_and_server_pagination(client):
    login(client)
    for cid in ("201-1", "301-1", "301-2"):
        report(client, cid, status="online" if cid != "301-2" else "unreachable")
    day = client.get("/api/history").json()[0]["day"]
    login(client, "monitor")
    page = client.get(f"/api/history/{day}?size=1").json()
    assert (
        page["summary"]["total"] == 2
        and len(page["computers"]) == 1
        and page["total"] == 2
    )
    missing = client.get(f"/api/history/{day}?status=unreachable").json()
    assert [pc["computer_id"] for pc in missing["computers"]] == ["301-2"]
    assert client.get(f"/api/history/{day}?floor=2").json()["summary"]["total"] == 0
    assert client.get(f"/api/history/{day}?size=1000").status_code == 422


def test_hardware_standard_requires_administrator_and_valid_requirements(client):
    login(client, "monitor")
    assert (
        client.put("/api/labs/301/hardware-standard", json=STANDARD).status_code == 403
    )
    login(client)
    assert client.put("/api/labs/301/hardware-standard", json={}).status_code == 422
    assert (
        client.put(
            "/api/labs/301/hardware-standard", json={**STANDARD, "memory_gib": -1}
        ).status_code
        == 422
    )
    assert (
        client.put("/api/labs/301/hardware-standard", json=STANDARD).status_code == 200
    )
    lab = next(
        l for l in client.get("/api/dashboard").json()["labs"] if l["id"] == "301"
    )
    assert lab["hardware_standard"]["memory_gib"] == 16


def test_hardware_mismatch_date_persists_and_resolution_is_recorded(client):
    login(client)
    assert (
        client.put("/api/labs/301/hardware-standard", json=STANDARD).status_code == 200
    )
    bad = {**HARDWARE, "memory_gib": 8}
    report(client, hardware=bad)
    report(client, hardware=bad)
    events = client.get("/api/hardware-events").json()
    assert len(events) == 1 and events[0]["resolved"] is None
    pc = next(
        p
        for p in client.get("/api/dashboard").json()["computers"]
        if p["id"] == "301-1"
    )
    assert (
        pc["hardware_state"] == "mismatch"
        and pc["hardware_mismatch_since"] == events[0]["detected"]
    )
    assert "RAM (GiB)" in pc["hardware_differences"][0]
    report(client)
    resolved = client.get("/api/hardware-events").json()[0]
    assert (
        resolved["resolved"] is not None
        and resolved["resolution"] == "Hardware matches"
    )
    pc = next(
        p
        for p in client.get("/api/dashboard").json()["computers"]
        if p["id"] == "301-1"
    )
    assert pc["hardware_state"] == "match" and pc["hardware_mismatch_since"] is None


def test_saved_check_keeps_hardware_evidence_after_resolution(client):
    login(client)
    client.put("/api/labs/301/hardware-standard", json=STANDARD)
    cid = client.post("/api/checks", json={"lab_id": "301"}).json()["id"]
    report(client, hardware={**HARDWARE, "storage_gib": 256})
    day = client.get("/api/history").json()[0]["day"]
    assert (
        client.get("/api/checks/" + cid).json()["summary"]["hardware_mismatches"] == 1
    )
    report(client)
    assert (
        client.get("/api/checks/" + cid).json()["summary"]["hardware_mismatches"] == 1
    )
    assert (
        client.get("/api/history/" + day).json()["summary"]["hardware_mismatches"] == 0
    )


def test_missing_telemetry_is_unverified_and_network_loss_does_not_resolve_issue(
    client,
):
    login(client)
    client.put("/api/labs/301/hardware-standard", json=STANDARD)
    report(client, hardware={})
    pc = next(
        p
        for p in client.get("/api/dashboard").json()["computers"]
        if p["id"] == "301-1"
    )
    assert pc["hardware_state"] == "unknown"
    report(client, hardware={**HARDWARE, "logical_cpus": 4})
    before = client.get("/api/hardware-events").json()[0]
    report(client, status="unreachable")
    after = client.get("/api/hardware-events").json()[0]
    assert before["detected"] == after["detected"] and after["resolved"] is None
    pc = next(
        p
        for p in client.get("/api/dashboard").json()["computers"]
        if p["id"] == "301-1"
    )
    assert pc["hardware_state"] == "unknown"
    assert pc["hardware_mismatch_since"] == before["detected"]
    assert pc["hardware"]["logical_cpus"] == 4
    day = client.get("/api/history").json()[0]["day"]
    assert (
        client.get("/api/history/" + day).json()["computers"][0]["hardware_state"]
        == "unknown"
    )
    report(client)
    cfg = client.app.state.settings
    with connect(cfg.database_path) as db:
        db.execute("UPDATE computers SET last_seen=1 WHERE id='301-1'")
    pc = next(
        p
        for p in client.get("/api/dashboard").json()["computers"]
        if p["id"] == "301-1"
    )
    assert pc["hardware_state"] == "unknown" and pc["status"] == "unknown"


def test_hardware_tolerance_minimum_and_gpu_comparison():
    assert compare_hardware(HARDWARE, STANDARD)[0] == "match"
    assert compare_hardware({**HARDWARE, "memory_gib": 8}, STANDARD)[0] == "mismatch"
    assert (
        compare_hardware(
            {**HARDWARE, "logical_cpus": 16}, {**STANDARD, "match_mode": "minimum"}
        )[0]
        == "match"
    )
    assert compare_hardware({**HARDWARE, "logical_cpus": 16}, STANDARD)[0] == "mismatch"
    assert compare_hardware({**HARDWARE, "gpu_models": None}, STANDARD)[0] == "unknown"
    assert compare_hardware({**HARDWARE, "gpu_models": []}, STANDARD)[0] == "mismatch"


def test_hardware_event_scope_cannot_leak_other_floors(client):
    login(client)
    for lab, cid in (("201", "201-1"), ("301", "301-1")):
        client.put(f"/api/labs/{lab}/hardware-standard", json=STANDARD)
        report(client, cid, hardware={**HARDWARE, "memory_gib": 8})
    login(client, "monitor")
    events = client.get("/api/hardware-events").json()
    assert len(events) == 1 and events[0]["floor"] == 3

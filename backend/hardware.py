import json
import time
from uuid import uuid4

from fastapi import Depends, HTTPException

from backend.db import allowed, connect
from backend.models import HardwareStandard


def compare_hardware(actual, standard):
    differences, missing = [], []
    labels = {
        "logical_cpus": "Logical CPUs",
        "memory_gib": "RAM (GiB)",
        "storage_gib": "Storage (GiB)",
        "disk_count": "Physical disks",
        "cpu_model": "CPU model",
        "gpu_model": "GPU model",
    }
    for key in ("logical_cpus", "memory_gib", "storage_gib", "disk_count"):
        expected = standard.get(key)
        if expected is None:
            continue
        value = actual.get(key)
        if value is None:
            missing.append(f"No reading for {labels[key]}")
            continue
        tolerance = expected * 0.05 if key.endswith("_gib") else 0
        valid = (
            value >= expected - tolerance
            if standard.get("match_mode") == "minimum"
            else abs(value - expected) <= tolerance
        )
        if not valid:
            differences.append(f"{labels[key]}: expected {expected}, detected {value}")
    for key in ("cpu_model", "gpu_model"):
        expected = standard.get(key)
        if not expected:
            continue
        values = actual.get("gpu_models") if key == "gpu_model" else [actual.get(key)]
        if values is None or any(value is None for value in values):
            missing.append(f"No reading for {labels[key]}")
        elif not any(expected.casefold() in value.casefold() for value in values):
            differences.append(
                f"{labels[key]}: expected {expected}, detected {', '.join(values) or 'none'}"
            )
    return (
        "mismatch" if differences else "unknown" if missing else "match"
    ), differences + missing


def update_hardware(db, computer, actual, now):
    row = db.execute(
        "SELECT settings FROM hardware_standards WHERE lab_id=?", (computer["lab_id"],)
    ).fetchone()
    standard = json.loads(row["settings"]) if row else None
    state, differences = (
        compare_hardware(actual, standard)
        if standard and standard["enabled"]
        else ("unconfigured", [])
    )
    incident = db.execute(
        "SELECT * FROM hardware_events WHERE computer_id=? AND resolved IS NULL",
        (computer["id"],),
    ).fetchone()
    since = incident["detected"] if incident else None
    if state == "mismatch":
        if incident and incident["expected"] != json.dumps(standard, sort_keys=True):
            db.execute(
                "UPDATE hardware_events SET resolved=?,resolution='Standard updated' WHERE id=?",
                (now, incident["id"]),
            )
            incident = None
        if not incident:
            since = now
            db.execute(
                "INSERT INTO hardware_events(id,computer_id,lab_id,floor,detected,last_seen,expected,actual,differences) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    computer["id"],
                    computer["lab_id"],
                    computer["floor"],
                    now,
                    now,
                    json.dumps(standard, sort_keys=True),
                    json.dumps(actual),
                    json.dumps(differences),
                ),
            )
            db.execute(
                "INSERT INTO audit(actor,action,target,computer_id,created,detail) VALUES (?,?,?,?,?,?)",
                (
                    "collector",
                    "hardware_mismatch",
                    computer["id"],
                    computer["id"],
                    now,
                    json.dumps(differences),
                ),
            )
        else:
            db.execute(
                "UPDATE hardware_events SET last_seen=?,actual=?,differences=? WHERE id=?",
                (now, json.dumps(actual), json.dumps(differences), incident["id"]),
            )
    elif state in ("match", "unconfigured"):
        if incident:
            db.execute(
                "UPDATE hardware_events SET resolved=?,resolution=? WHERE id=?",
                (
                    now,
                    "Hardware matches" if state == "match" else "Standard disabled",
                    incident["id"],
                ),
            )
            db.execute(
                "INSERT INTO audit(actor,action,target,computer_id,created) VALUES (?,?,?,?,?)",
                ("collector", "hardware_resolved", computer["id"], computer["id"], now),
            )
        since = None
    db.execute(
        "UPDATE computers SET hardware=?,hardware_state=?,hardware_checked_at=?,hardware_differences=?,hardware_mismatch_since=? WHERE id=?",
        (
            json.dumps(actual),
            state,
            now,
            json.dumps(differences),
            since,
            computer["id"],
        ),
    )


def register_hardware(app, cfg, current_user, admin):
    @app.put("/api/labs/{lab_id}/hardware-standard")
    def save_standard(lab_id: str, payload: HardwareStandard, user=Depends(admin)):
        with connect(cfg.database_path) as db:
            if not db.execute("SELECT 1 FROM labs WHERE id=?", (lab_id,)).fetchone():
                raise HTTPException(404, "Laboratory not found.")
            now = time.time()
            db.execute(
                "INSERT INTO hardware_standards VALUES (?,?,?,?) ON CONFLICT(lab_id) DO UPDATE SET settings=excluded.settings,updated=excluded.updated,updated_by=excluded.updated_by",
                (lab_id, payload.model_dump_json(), now, user["id"]),
            )
            db.execute(
                "UPDATE computers SET hardware_state='unknown' WHERE lab_id=?",
                (lab_id,),
            )
            db.execute(
                "INSERT INTO audit(actor,action,target,created,detail) VALUES (?,?,?,?,?)",
                (
                    user["id"],
                    "hardware_standard_updated",
                    lab_id,
                    now,
                    payload.model_dump_json(),
                ),
            )
        return {"ok": True}

    @app.get("/api/hardware-events")
    def events(floor: int | None = None, user=Depends(current_user)):
        with connect(cfg.database_path) as db:
            rows = db.execute(
                "SELECT * FROM hardware_events ORDER BY detected DESC LIMIT 1000"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if (floor is None or item["floor"] == floor) and allowed(
                user,
                {
                    "id": item["computer_id"],
                    "lab_id": item["lab_id"],
                    "floor": item["floor"],
                },
            ):
                for key in ("expected", "actual", "differences"):
                    item[key] = json.loads(item[key])
                result.append(item)
        return result[:100]

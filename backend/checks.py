import json
import time
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query

from backend.db import allowed, connect, inventory
from backend.models import CheckInput

LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")


def public_result(row):
    result = dict(row)
    for key in ("hardware", "hardware_differences"):
        result[key] = json.loads(result[key])
    return result


def scope_clause(user, floor=None):
    clauses, values = [], []
    if user["role"] != "admin":
        access = []
        for key, field in (
            ("floors", "floor"),
            ("labs", "lab_id"),
            ("computers", "computer_id"),
        ):
            granted = user["grants"].get(key, [])
            if granted:
                access.append(f"{field} IN ({','.join('?' for _ in granted)})")
                values.extend(granted)
        clauses.append("(" + " OR ".join(access) + ")" if access else "0")
    if floor is not None:
        clauses.append("floor=?")
        values.append(floor)
    return " AND ".join(clauses) or "1", values


SUMMARY_SQL = """COUNT(*) AS total,COALESCE(SUM(status='online'),0) AS online,
    COALESCE(SUM(status='unreachable'),0) AS unreachable,COALESCE(SUM(status='unknown'),0) AS unknown,
    COALESCE(SUM(hardware_state='mismatch'),0) AS hardware_mismatches"""


def query_page(db, table, where, values, state, page, size):
    summary = dict(
        db.execute(
            f"SELECT {SUMMARY_SQL} FROM {table} WHERE {where}", values
        ).fetchone()
    )
    if state != "all":
        where += " AND status=?"
        values = [*values, state]
    total = db.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {where}", values
    ).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY floor,lab_id,number LIMIT ? OFFSET ?",
        [*values, size, (page - 1) * size],
    ).fetchall()
    return {
        "summary": summary,
        "total": total,
        "page": page,
        "page_size": size,
        "computers": [public_result(row) for row in rows],
    }


def record_observation(db, computer, report, now):
    day = datetime.fromtimestamp(now, LOCAL_ZONE).date().isoformat()
    previous = db.execute(
        "SELECT MAX(last_online) FROM daily_status WHERE computer_id=?",
        (computer["id"],),
    ).fetchone()[0]
    last_online = now if report.status == "online" else previous
    hardware_state = (
        computer["hardware_state"] if report.status == "online" else "unknown"
    )
    db.execute(
        """INSERT INTO daily_status(day,computer_id,floor,lab_id,hostname,ip,number,status,first_check,checked_at,last_online,online_checks,total_checks,issue,hardware,hardware_state,hardware_differences)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?) ON CONFLICT(day,computer_id) DO UPDATE SET
        hostname=excluded.hostname,status=excluded.status,checked_at=excluded.checked_at,last_online=excluded.last_online,
        online_checks=daily_status.online_checks+excluded.online_checks,total_checks=daily_status.total_checks+1,
        issue=excluded.issue,hardware=excluded.hardware,hardware_state=excluded.hardware_state,hardware_differences=excluded.hardware_differences""",
        (
            day,
            computer["id"],
            computer["floor"],
            computer["lab_id"],
            computer["hostname"],
            computer["ip"],
            computer["number"],
            report.status,
            now,
            now,
            last_online,
            int(report.status == "online"),
            report.issue,
            computer["hardware"],
            hardware_state,
            computer["hardware_differences"],
        ),
    )
    db.execute(
        """UPDATE check_results SET status=?,checked_at=?,last_online=?,issue=?,hardware=?,hardware_state=?,hardware_differences=?
        WHERE computer_id=? AND checked_at IS NULL AND check_id IN (SELECT id FROM checks WHERE state='queued' AND created<=?)""",
        (
            report.status,
            now,
            last_online,
            report.issue,
            computer["hardware"],
            hardware_state,
            computer["hardware_differences"],
            computer["id"],
            now,
        ),
    )
    completed = db.execute(
        "SELECT id,actor_id FROM checks WHERE state='queued' AND NOT EXISTS(SELECT 1 FROM check_results r WHERE r.check_id=checks.id AND r.checked_at IS NULL)"
    ).fetchall()
    for check in completed:
        db.execute(
            "UPDATE checks SET state='completed',finished=? WHERE id=?",
            (now, check["id"]),
        )
        db.execute(
            "INSERT INTO audit(actor,action,target,created) VALUES (?,?,?,?)",
            (check["actor_id"], "check_completed", check["id"], now),
        )


def register_checks(app, cfg, current_user, collector):
    @app.post("/api/checks")
    def run_check(payload: CheckInput, user=Depends(current_user)):
        if user["role"] == "viewer":
            raise HTTPException(403, "Observer accounts cannot request checks.")
        with connect(cfg.database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            pcs = [
                pc
                for pc in inventory(db)
                if allowed(user, pc)
                and (payload.floor is None or pc["floor"] == payload.floor)
                and (not payload.lab_id or pc["lab_id"] == payload.lab_id)
            ]
            if not pcs:
                raise HTTPException(404, "No computers are assigned to this selection.")
            ids = {pc["id"] for pc in pcs}
            for old in db.execute(
                "SELECT id FROM checks WHERE actor_id=? AND state='queued'",
                (user["id"],),
            ):
                if ids == {
                    row["computer_id"]
                    for row in db.execute(
                        "SELECT computer_id FROM check_results WHERE check_id=?",
                        (old["id"],),
                    )
                }:
                    return {"id": old["id"]}
            cid, now = str(uuid4()), time.time()
            db.execute(
                "INSERT INTO checks(id,actor_id,created) VALUES (?,?,?)",
                (cid, user["id"], now),
            )
            db.executemany(
                "INSERT INTO check_results(check_id,computer_id,floor,lab_id,hostname,ip,number) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        cid,
                        pc["id"],
                        pc["floor"],
                        pc["lab_id"],
                        pc["hostname"],
                        pc["ip"],
                        pc["number"],
                    )
                    for pc in pcs
                ],
            )
            db.execute(
                "INSERT INTO audit(actor,action,target,created) VALUES (?,?,?,?)",
                (user["id"], "check_requested", cid, now),
            )
        return {"id": cid}

    @app.get("/api/checks")
    def checks(floor: int | None = None, user=Depends(current_user)):
        where, values = scope_clause(user, floor)
        with connect(cfg.database_path) as db:
            rows = db.execute(
                f"SELECT c.*,{SUMMARY_SQL} FROM checks c JOIN check_results r ON r.check_id=c.id WHERE {where} GROUP BY c.id ORDER BY c.created DESC LIMIT 50",
                values,
            ).fetchall()
        return [
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "actor_id",
                        "created",
                        "finished",
                        "state",
                        "message",
                    )
                },
                "summary": {
                    key: row[key]
                    for key in (
                        "total",
                        "online",
                        "unreachable",
                        "unknown",
                        "hardware_mismatches",
                    )
                },
            }
            for row in rows
        ]

    @app.get("/api/checks/{check_id}")
    def check_detail(
        check_id: str,
        floor: int | None = None,
        status: str = Query("all", pattern="^(all|online|unreachable|unknown)$"),
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=100),
        user=Depends(current_user),
    ):
        with connect(cfg.database_path) as db:
            check = db.execute(
                "SELECT * FROM checks WHERE id=?", (check_id,)
            ).fetchone()
            where, values = scope_clause(user, floor)
            result = query_page(
                db,
                "check_results",
                where + " AND check_id=?",
                values + [check_id],
                status,
                page,
                size,
            )
        if not check or not result["summary"]["total"]:
            raise HTTPException(404, "Verification not found.")
        return {**dict(check), **result}

    @app.get("/api/history")
    def history(floor: int | None = None, user=Depends(current_user)):
        where, values = scope_clause(user, floor)
        with connect(cfg.database_path) as db:
            rows = db.execute(
                f"SELECT day,{SUMMARY_SQL} FROM daily_status WHERE {where} GROUP BY day ORDER BY day DESC",
                values,
            ).fetchall()
        return [
            {
                "day": row["day"],
                "summary": {
                    key: row[key]
                    for key in (
                        "total",
                        "online",
                        "unreachable",
                        "unknown",
                        "hardware_mismatches",
                    )
                },
            }
            for row in rows
        ]

    @app.get("/api/history/{day}")
    def history_detail(
        day: date,
        floor: int | None = None,
        status: str = Query("all", pattern="^(all|online|unreachable|unknown)$"),
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=100),
        user=Depends(current_user),
    ):
        with connect(cfg.database_path) as db:
            where, values = scope_clause(user, floor)
            result = query_page(
                db,
                "daily_status",
                where + " AND day=?",
                values + [day.isoformat()],
                status,
                page,
                size,
            )
        return {"day": day.isoformat(), **result}

    @app.get("/api/collector/checks")
    def pending_checks(cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            pending = db.execute(
                "SELECT 1 FROM check_results r JOIN checks c ON c.id=r.check_id JOIN labs l ON l.id=r.lab_id WHERE c.state='queued' AND r.checked_at IS NULL AND l.collector_id=? LIMIT 1",
                (cid,),
            ).fetchone()
        return {"pending": bool(pending)}

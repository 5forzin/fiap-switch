import json
import time
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query

from backend.db import allowed, connect, inventory
from backend.models import CheckInput
from backend.audit import activity_clause, activity_visible, audit
from backend.hardware import update_hardware

LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")


def public_result(row):
    result = dict(row)
    for key in ("hardware", "hardware_differences"):
        result[key] = json.loads(result[key])
    return result


def scope_clause(user, floor=None):
    clauses, values = [], []
    if user["role"] == "monitor":
        floors = user["grants"].get("floors", [])
        if len(floors) == 1:
            clauses.append("floor=?")
            values.extend(floors)
        else:
            clauses.append("0")
    elif user["role"] not in ("admin", "support"):
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


def permitted_labs(db, user, floor=None):
    return [lab['id'] for lab in db.execute('SELECT * FROM labs')
            if (floor is None or lab['floor'] == floor)
            and allowed(user, {'id': '', 'lab_id': lab['id'], 'floor': lab['floor']})]


def discovery_scope(db, user, check_id, floor=None):
    labs = set(permitted_labs(db, user, floor))
    return [dict(row) for row in db.execute('SELECT * FROM check_discovery WHERE check_id=?', (check_id,)) if row['lab_id'] in labs]


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
    complete_checks(db, now)


def save_observation(db, report, now):
    db.execute(
        "UPDATE computers SET status=?,metrics=?,last_seen=?,issue=?,mac=CASE WHEN mac='' AND ? != '' THEN ? ELSE mac END WHERE id=?",
        (report.status, json.dumps(report.metrics), now, report.issue, report.mac, report.mac, report.computer_id),
    )
    computer = db.execute('SELECT c.*,l.floor FROM computers c JOIN labs l ON l.id=c.lab_id WHERE c.id=?', (report.computer_id,)).fetchone()
    if report.status == 'online' and report.hardware is not None:
        update_hardware(db, computer, report.hardware.model_dump(exclude_none=True), now)
    else:
        db.execute("UPDATE computers SET hardware_state=CASE WHEN hardware_state='unconfigured' THEN 'unconfigured' ELSE 'unknown' END WHERE id=?", (report.computer_id,))
    computer = db.execute('SELECT c.*,l.floor FROM computers c JOIN labs l ON l.id=c.lab_id WHERE c.id=?', (report.computer_id,)).fetchone()
    record_observation(db, computer, report, now)


def complete_checks(db, now):
    completed = db.execute(
        """SELECT id,actor_id,actor_role FROM checks WHERE state='queued'
        AND NOT EXISTS(SELECT 1 FROM check_results r WHERE r.check_id=checks.id AND r.checked_at IS NULL)
        AND NOT EXISTS(SELECT 1 FROM check_discovery d WHERE d.check_id=checks.id AND d.state='queued')"""
    ).fetchall()
    for check in completed:
        db.execute(
            "UPDATE checks SET state='completed',finished=? WHERE id=?",
            (now, check["id"]),
        )
        audit(
            db,
            check["actor_id"],
            "check_completed",
            check["id"],
            actor_role=check["actor_role"],
        )


def register_checks(app, cfg, current_user, collector):
    @app.post("/api/checks")
    def run_check(payload: CheckInput, user=Depends(current_user)):
        if user["role"] == "viewer":
            raise HTTPException(
                403, "Contas de observador não podem iniciar verificações."
            )
        with connect(cfg.database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            pcs = [
                pc
                for pc in inventory(db)
                if allowed(user, pc)
                and (payload.floor is None or pc["floor"] == payload.floor)
                and (not payload.lab_id or pc["lab_id"] == payload.lab_id)
            ]
            discovery_labs = [
                lab for lab in db.execute('SELECT l.* FROM labs l JOIN lab_inventory i ON i.lab_id=l.id WHERE i.students IS NULL')
                if allowed(user, {'id': '', 'lab_id': lab['id'], 'floor': lab['floor']})
                and (payload.floor is None or lab['floor'] == payload.floor)
                and (not payload.lab_id or lab['id'] == payload.lab_id)
            ]
            if not pcs and not discovery_labs:
                raise HTTPException(404, "Nenhum computador permitido nesta seleção.")
            ids = {pc["id"] for pc in pcs}
            for old in db.execute(
                "SELECT id FROM checks WHERE actor_id=? AND state='queued'",
                (user["id"],),
            ):
                if {lab['id'] for lab in discovery_labs} == {
                    row['lab_id'] for row in db.execute('SELECT lab_id FROM check_discovery WHERE check_id=?', (old['id'],))
                } and ids == {
                    row["computer_id"]
                    for row in db.execute(
                        "SELECT computer_id FROM check_results WHERE check_id=?",
                        (old["id"],),
                    )
                }:
                    return {"id": old["id"]}
            cid, now = str(uuid4()), time.time()
            db.execute(
                "INSERT INTO checks(id,actor_id,created,actor_role) VALUES (?,?,?,?)",
                (cid, user["id"], now, user["role"]),
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
            db.executemany('INSERT INTO check_discovery(check_id,lab_id) VALUES (?,?)', [(cid, lab['id']) for lab in discovery_labs])
            audit(db, user["id"], "check_requested", cid)
        return {"id": cid}

    @app.get("/api/checks")
    def checks(floor: int | None = None, user=Depends(current_user)):
        where, values = scope_clause(user, floor)
        privacy, privacy_values = activity_clause(user)
        with connect(cfg.database_path) as db:
            labs = permitted_labs(db, user, floor)
            placeholders = ','.join('?' for _ in labs) or 'NULL'
            rows = db.execute(
                f"""SELECT c.* FROM checks c WHERE {privacy} AND
                (EXISTS(SELECT 1 FROM check_results r WHERE r.check_id=c.id AND {where})
                OR EXISTS(SELECT 1 FROM check_discovery d WHERE d.check_id=c.id AND d.lab_id IN ({placeholders})))
                ORDER BY c.created DESC LIMIT 50""",
                [*privacy_values, *values, *labs],
            ).fetchall()
            return [{**dict(row), 'summary': dict(db.execute(
                f'SELECT {SUMMARY_SQL} FROM check_results WHERE check_id=? AND {where}',
                [row['id'], *values],
            ).fetchone()), 'discovery': discovery_scope(db, user, row['id'], floor)} for row in rows]

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
            if not check or not activity_visible(
                user, check["actor_id"], check["actor_role"]
            ):
                raise HTTPException(404, "Verificação não encontrada.")
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
            discovery = discovery_scope(db, user, check_id, floor)
        if not result["summary"]["total"] and not discovery:
            raise HTTPException(404, "Verificação não encontrada.")
        return {**dict(check), **result, 'discovery': discovery}

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
                """SELECT 1 FROM checks c WHERE c.state='queued' AND (
                EXISTS(SELECT 1 FROM check_results r JOIN labs l ON l.id=r.lab_id WHERE r.check_id=c.id AND r.checked_at IS NULL AND l.collector_id=?)
                OR EXISTS(SELECT 1 FROM check_discovery d JOIN labs l ON l.id=d.lab_id WHERE d.check_id=c.id AND d.state='queued' AND l.collector_id=?)) LIMIT 1""",
                (cid, cid),
            ).fetchone()
        return {"pending": bool(pending)}

"""Auditoria com autoria preservada e ações de administrador privadas."""

import time


def audit(
    db,
    actor,
    action,
    target,
    computer_id=None,
    detail="",
    initiator_id=None,
    actor_role=None,
):
    initiator_id = initiator_id or actor
    if actor_role is None:
        account = db.execute(
            "SELECT role FROM users WHERE id=?", (initiator_id,)
        ).fetchone()
        actor_role = (
            account["role"]
            if account
            else "system"
            if actor in ("system", "collector")
            else "unknown"
        )
    db.execute(
        "INSERT INTO audit(actor,action,target,computer_id,created,detail,initiator_id,actor_role) VALUES (?,?,?,?,?,?,?,?)",
        (
            actor,
            action,
            target,
            computer_id,
            time.time(),
            detail,
            initiator_id,
            actor_role,
        ),
    )


def activity_visible(user, initiator_id, actor_role):
    if user["id"] == initiator_id:
        return True
    if actor_role in ("", "unknown", "admin"):
        return False
    return user["role"] in ("admin", "support") or actor_role == "system"


def activity_clause(user, alias="c", initiator="actor_id"):
    if user["role"] in ("admin", "support"):
        return (
            f"({alias}.{initiator}=? OR {alias}.actor_role IN ('support','monitor','viewer','system'))",
            [user["id"]],
        )
    return f"{alias}.{initiator}=?", [user["id"]]

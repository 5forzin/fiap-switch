"""Cadastro idempotente de computadores encontrados pelo controlador da sala."""

import ipaddress
import time

from fastapi import Depends, HTTPException

from backend.audit import audit
from backend.checks import complete_checks, save_observation
from backend.db import connect
from backend.models import DiscoveryResult, Report


def register_discovery(app, cfg, collector):
    @app.get('/api/collector/discovery')
    def pending_discovery(cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            tasks = []
            for row in db.execute("""SELECT d.*,l.subnet,i.students FROM check_discovery d
                JOIN checks c ON c.id=d.check_id JOIN labs l ON l.id=d.lab_id
                JOIN lab_inventory i ON i.lab_id=l.id
                WHERE c.state='queued' AND d.state='queued' AND l.collector_id=? ORDER BY c.created""", (cid,)).fetchall():
                if row['students'] is not None:
                    db.execute("UPDATE check_discovery SET state='completed',finished=?,message=? WHERE check_id=? AND lab_id=?",
                               (time.time(), 'Descoberta cancelada: a quantidade da sala foi definida.', row['check_id'], row['lab_id']))
                    continue
                tasks.append({'check_id': row['check_id'], 'lab_id': row['lab_id'], 'subnet': row['subnet'],
                              'known_ips': [pc['ip'] for pc in db.execute('SELECT ip FROM computers WHERE lab_id=?', (row['lab_id'],))]})
            complete_checks(db, time.time())
        return tasks

    @app.post('/api/collector/discovery/{check_id}/{lab_id}')
    def discovered(check_id: str, lab_id: str, payload: DiscoveryResult, cid=Depends(collector)):
        with connect(cfg.database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute("""SELECT d.*,l.subnet,l.floor,l.collector_id,i.students,c.actor_id,c.actor_role,c.state AS check_state
                FROM check_discovery d JOIN labs l ON l.id=d.lab_id JOIN lab_inventory i ON i.lab_id=l.id
                JOIN checks c ON c.id=d.check_id WHERE d.check_id=? AND d.lab_id=?""", (check_id, lab_id)).fetchone()
            if not task or task['collector_id'] != cid:
                raise HTTPException(404, 'Descoberta não encontrada para este controlador.')
            if task['state'] != 'queued':
                return {'ok': True, 'added': 0}
            if task['check_state'] != 'queued':
                raise HTTPException(409, 'Esta verificação já foi encerrada.')
            if task['students'] is not None:
                db.execute("UPDATE check_discovery SET state='completed',finished=?,message=? WHERE check_id=? AND lab_id=?",
                           (time.time(), 'Descoberta cancelada: a quantidade da sala foi definida.', check_id, lab_id))
                complete_checks(db, time.time())
                return {'ok': True, 'added': 0}
            network = ipaddress.ip_network(task['subnet'])
            added, now = 0, time.time()
            for found in {pc.number: pc for pc in payload.computers}.values():
                computer_id, ip = f'{lab_id}-{found.number}', str(network.network_address + found.number)
                existing = db.execute('SELECT * FROM computers WHERE id=? OR ip=? OR (lab_id=? AND number=?)',
                                      (computer_id, ip, lab_id, found.number)).fetchall()
                if any(pc['id'] != computer_id or pc['ip'] != ip or pc['lab_id'] != lab_id for pc in existing):
                    raise HTTPException(409, 'Um endereço encontrado conflita com o inventário cadastrado.')
                if not existing:
                    db.execute('INSERT INTO computers(id,lab_id,number,ip) VALUES (?,?,?,?)', (computer_id, lab_id, found.number, ip))
                    added += 1
                    audit(db, cid, 'computer_discovered', ip, computer_id, initiator_id=task['actor_id'], actor_role=task['actor_role'])
                db.execute('''INSERT OR IGNORE INTO check_results(check_id,computer_id,floor,lab_id,hostname,ip,number)
                    VALUES (?,?,?,?,?,?,?)''', (check_id, computer_id, task['floor'], lab_id, existing[0]['hostname'] if existing else '', ip, found.number))
                report = Report(computer_id=computer_id, **found.model_dump(exclude={'number'}))
                report.source = 'winrm' if cfg.power_mode == 'fiap' else cfg.power_mode
                save_observation(db, report, now)
            db.execute("UPDATE check_discovery SET state='completed',added=?,finished=? WHERE check_id=? AND lab_id=?",
                       (added, now, check_id, lab_id))
            complete_checks(db, now)
            db.execute('UPDATE collectors SET last_seen=? WHERE id=?', (now, cid))
        return {'ok': True, 'added': added}

"""Prepara as máquinas e contas da apresentação sem publicar credenciais."""

import argparse
import json
import secrets
from pathlib import Path

from scripts.live_validation import Validation

ARQUIVO = Path("runtime/apresentacao.json")


def salvar_acessos(acesso):
    ARQUIVO.write_text(
        json.dumps(acesso, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    administrador = json.loads(Path("runtime/login.json").read_text(encoding="utf-8"))
    linhas = [
        "FIAP SWITCH — ACESSOS DA APRESENTAÇÃO",
        "Uso privado. Não publicar no GitHub.",
        "Painel: http://localhost:8011/",
        "",
        "ADMINISTRADOR",
        f"E-mail: {administrador['username']}",
        f"Senha: {administrador['password']}",
    ]
    for chave, conta in sorted(acesso["contas"].items()):
        titulo = "SUPORTE" if chave == "support" else f"MONITOR DO {chave[-1]}º ANDAR"
        linhas.extend(
            ["", titulo, f"E-mail: {conta['username']}", f"Senha: {conta['password']}"]
        )
    Path("runtime/acessos-apresentacao.txt").write_text(
        "\n".join(linhas) + "\n", encoding="utf-8"
    )


def preparar():
    validacao = Validation()
    estado = validacao.state
    instancias = [
        i
        for r in validacao.ec2.describe_instances(InstanceIds=estado["instances"])[
            "Reservations"
        ]
        for i in r["Instances"]
    ]
    assert all(
        {t["Key"]: t["Value"] for t in i.get("Tags", [])}.get("Project")
        == "fiap-switch-e2e-20261027"
        for i in instancias
    )
    validacao.ec2.start_instances(InstanceIds=estado["instances"])
    acesso = (
        json.loads(ARQUIVO.read_text(encoding="utf-8"))
        if ARQUIVO.exists()
        else {"contas": {}}
    )
    with validacao.client() as administrador:
        existentes = {
            s["id"]: s for s in administrador.get("/api/dashboard").json()["labs"]
        }
        for andar, numero, alunos, professor in ((3, 1, 4, False), (2, 2, 0, True)):
            sala = f"{andar}{numero:02}"
            if sala in existentes:
                assert (
                    estado.get("presentation", {}).get("enabled")
                    and existentes[sala]["label"] == f"Laboratório {sala}"
                ), "A sala já contém dados externos à apresentação."
                continue
            resposta = administrador.post(
                "/api/labs",
                json={
                    "floor": andar,
                    "number": numero,
                    "label": f"Laboratório {sala}",
                    "students": alunos,
                    "include_professor": professor,
                },
            )
            resposta.raise_for_status()
        for andar in (2, 3):
            chave = f"floor{andar}"
            conta = acesso["contas"].get(
                chave,
                {
                    "username": f"monitor.{andar}@fiap.com.br",
                    "password": secrets.token_urlsafe(20),
                },
            )
            usuarios = administrador.get("/api/users").json()
            existente = next(
                (u for u in usuarios if u["email"] == conta["username"]), None
            )
            if existente:
                assert existente["id"] == conta.get("id"), (
                    "A conta já pertence a outro cadastro."
                )
            cadastro = {
                "email": conta["username"],
                "name": f"Monitor do {andar}º andar",
                "role": "monitor",
                "grants": {"floors": [andar]},
                "password": conta["password"],
            }
            if existente:
                cadastro["id"] = existente["id"]
            resposta = administrador.put("/api/users", json=cadastro)
            resposta.raise_for_status()
            conta["id"] = resposta.json()["id"]
            acesso["contas"][chave] = conta
            validacao.accounts[chave] = {
                **conta,
                "payload": {**cadastro, "id": conta["id"], "password": None},
            }
        for sala, memoria in (("202", 2), ("301", 2)):
            administrador.put(
                f"/api/labs/{sala}/hardware-standard",
                json={
                    "logical_cpus": 2,
                    "memory_gib": memoria,
                    "storage_gib": 30,
                    "disk_count": 1,
                    "match_mode": "exact",
                },
            ).raise_for_status()
    validacao.ssh(
        """
from backend.db import connect
with connect('/app/data/switch.db') as banco:
    sala = banco.execute("SELECT label FROM labs WHERE id='301'").fetchone()
    assert sala and sala['label'] == 'Laboratório 301'
    for computador in ('301-1','301-2','301-3'):
        banco.execute('DELETE FROM computers WHERE id=?', (computador,))
""",
        docker=True,
    )
    salvar_acessos(acesso)
    validacao.state["presentation"] = {
        "enabled": True,
        "leave_running": True,
        "accounts": [c["username"] for c in acesso["contas"].values()],
    }
    validacao.save()
    print("Apresentação preparada. Credenciais privadas em runtime/apresentacao.json.")


def preparar_suporte():
    validacao = Validation()
    acesso = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    conta = acesso["contas"].get(
        "support",
        {"username": "suporte.labs@fiap.com.br", "password": secrets.token_urlsafe(20)},
    )
    with validacao.client() as administrador:
        existente = next(
            (
                u
                for u in administrador.get("/api/users").json()
                if u["email"] == conta["username"]
            ),
            None,
        )
        cadastro = {
            "email": conta["username"],
            "name": "Suporte dos laboratórios",
            "role": "support",
            "grants": {},
        }
        if existente:
            assert existente["id"] == conta.get("id") and existente["role"] == "support"
            conta["id"] = existente["id"]
        else:
            resposta = administrador.put(
                "/api/users", json={**cadastro, "password": conta["password"]}
            )
            resposta.raise_for_status()
            conta["id"] = resposta.json()["id"]
        validacao.accounts["support"] = {
            **conta,
            "payload": {**cadastro, "id": conta["id"]},
        }
        acesso["contas"]["support"] = conta
    validacao.state["presentation"]["accounts"] = [
        c["username"] for c in acesso["contas"].values()
    ]
    validacao.save()
    salvar_acessos(acesso)
    print(
        "Conta de suporte preparada. Credenciais mantidas no arquivo privado de acessos."
    )


def alterar_hardware():
    validacao = Validation()
    estado = validacao.state
    apresentacao = estado["presentation"]
    alvo = next(a for a in estado["targets"] if a["computer_id"] == "301-4")
    maquina = validacao.ec2.describe_instances(InstanceIds=[alvo["instance_id"]])[
        "Reservations"
    ][0]["Instances"][0]
    assert {t["Key"]: t["Value"] for t in maquina["Tags"]}.get(
        "Project"
    ) == "fiap-switch-e2e-20261027"
    with validacao.client() as administrador:
        administrador.put(
            "/api/labs/301/hardware-standard",
            json={
                "logical_cpus": 2,
                "memory_gib": 2,
                "storage_gib": 30,
                "disk_count": 1,
            },
        ).raise_for_status()
        volume = apresentacao.get("hardware_volume")
        if not volume:

            def original():
                pc = next(
                    p
                    for p in administrador.get("/api/dashboard").json()["computers"]
                    if p["id"] == "301-4"
                )
                return (
                    pc
                    if pc["status"] == "online" and pc["hardware_state"] == "match"
                    else None
                )

            antes = validacao.wait(
                original, "O inventário original não foi confirmado.", 120
            )
            apresentacao["hardware_before"] = antes["hardware"]
            volume = validacao.ec2.create_volume(
                AvailabilityZone=maquina["Placement"]["AvailabilityZone"],
                Size=8,
                VolumeType="gp3",
                Encrypted=True,
                TagSpecifications=[
                    {
                        "ResourceType": "volume",
                        "Tags": [
                            {"Key": "Project", "Value": "fiap-switch-e2e-20261027"},
                            {
                                "Key": "Name",
                                "Value": "fiap-switch-hardware-apresentacao",
                            },
                        ],
                    }
                ],
            )["VolumeId"]
            apresentacao["hardware_volume"] = volume
            validacao.save()
            print("Disco adicional de 8 GiB criado e registrado.", flush=True)
        disco = validacao.ec2.describe_volumes(VolumeIds=[volume])["Volumes"][0]
        assert {t["Key"]: t["Value"] for t in disco["Tags"]}.get(
            "Project"
        ) == "fiap-switch-e2e-20261027"
        if not disco["Attachments"]:
            validacao.ec2.get_waiter("volume_available").wait(
                VolumeIds=[volume], WaiterConfig={"Delay": 2, "MaxAttempts": 30}
            )
            validacao.ec2.attach_volume(
                VolumeId=volume, InstanceId=alvo["instance_id"], Device="/dev/sdf"
            )
        else:
            assert all(
                a["InstanceId"] == alvo["instance_id"] for a in disco["Attachments"]
            )
        validacao.ec2.get_waiter("volume_in_use").wait(
            VolumeIds=[volume], WaiterConfig={"Delay": 2, "MaxAttempts": 30}
        )
        validacao.ec2.modify_instance_attribute(
            InstanceId=alvo["instance_id"],
            BlockDeviceMappings=[
                {"DeviceName": "/dev/sdf", "Ebs": {"DeleteOnTermination": True}}
            ],
        )
        print(
            validacao.powershell(
                alvo["instance_id"],
                "Update-HostStorageCache; Get-CimInstance Win32_DiskDrive | Select-Object Model,Size | ConvertTo-Json -Compress",
            ),
            flush=True,
        )

        def divergente():
            pc = next(
                p
                for p in administrador.get("/api/dashboard").json()["computers"]
                if p["id"] == "301-4"
            )
            return (
                pc
                if pc["status"] == "online"
                and pc["hardware_state"] == "mismatch"
                and pc["hardware"]["disk_count"] == 2
                and pc["hardware"]["storage_gib"] >= 37.5
                else None
            )

        depois = validacao.wait(
            divergente, "O disco novo não apareceu na leitura real do WinRM.", 180
        )
        apresentacao["hardware_after"] = depois["hardware"]
        apresentacao["hardware_detected_at"] = depois["hardware_mismatch_since"]
        apresentacao["hardware_differences"] = depois["hardware_differences"]
        validacao.save()
        print(
            "Hardware alterado e detectado: 38 GiB, dois discos; padrão preservado em 30 GiB e um disco.",
            flush=True,
        )


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument(
        "acao",
        choices=["preparar", "hardware", "suporte"],
        nargs="?",
        default="preparar",
    )
    opcoes = argumentos.parse_args()
    {"hardware": alterar_hardware, "suporte": preparar_suporte, "preparar": preparar}[
        opcoes.acao
    ]()

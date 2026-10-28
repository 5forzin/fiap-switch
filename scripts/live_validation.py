"""Apoio à validação real de Windows por HTTPS; dados privados ficam em runtime/."""

import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import boto3
import httpx

STATE = Path("runtime/aws-validation-20261027.json")
URL = "https://fiap-switch.13.140.191.31.sslip.io"


class Validation:
    def __init__(self):
        self.state = json.loads(STATE.read_text())
        self.url = self.state.get('web_url', URL)
        session = boto3.Session(
            profile_name="fiap-switch", region_name=self.state["region"]
        )
        self.ec2, self.ssm = session.client("ec2"), session.client("ssm")
        self.accounts = self.state.setdefault("accounts", {})
        self.results = self.state.setdefault("results", {})

    def save(self):
        STATE.write_text(json.dumps(self.state, indent=2))

    def record(self, name, evidence):
        self.results[name] = {
            "passed": True,
            "verified_at": time.time(),
            "evidence": evidence,
        }
        self.save()
        print(f"APROVADO: {name}", flush=True)

    @contextmanager
    def client(self, account=None):
        api = httpx.Client(
            base_url=self.url,
            proxy="socks5://127.0.0.1:1080",
            headers={"Origin": self.url},
            timeout=30,
        )
        try:
            credentials = (
                self.accounts[account]
                if account
                else json.loads(Path("runtime/login.json").read_text())
            )
            response = api.post(
                "/auth/login",
                json={key: credentials[key] for key in ("username", "password")},
            )
            response.raise_for_status()
            api.headers["X-CSRF-Token"] = api.get("/api/session").json()["user"]["csrf"]
            yield api
        finally:
            api.close()

    def ssh(self, script, docker=False):
        remote = (
            "sudo -n docker exec -i fiap-switch-web-1 python -"
            if docker
            else "python3 -"
        )
        result = subprocess.run(
            [
                "ssh",
                "-i",
                r"C:\Users\antho\.ssh\id_rsa",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                "-o",
                r'ProxyCommand="C:\Program Files\Git\mingw64\bin\connect.exe" -S 127.0.0.1:1080 %h %p',
                "link@13.140.191.31",
                remote,
            ],
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return result.stdout.strip()

    def send(self, instance, script, timeout=180):
        response = self.ssm.send_command(
            InstanceIds=[instance],
            DocumentName="AWS-RunPowerShellScript",
            Parameters={"commands": [script], "executionTimeout": [str(timeout)]},
            TimeoutSeconds=timeout,
        )
        return response["Command"]["CommandId"]

    def wait_command(self, instance, command_id, timeout=240):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self.ssm.get_command_invocation(
                    CommandId=command_id, InstanceId=instance
                )
            except self.ssm.exceptions.InvocationDoesNotExist:
                time.sleep(2)
                continue
            if result["Status"] == "Success":
                return result["StandardOutputContent"].strip()
            if result["Status"] in ("Failed", "Cancelled", "TimedOut"):
                raise RuntimeError(
                    f"Comando SSM {command_id}: {result['Status']}. Consulte o diagnóstico resumido."
                )
            time.sleep(3)
        raise TimeoutError(f"Comando SSM {command_id} excedeu o tempo limite.")

    def powershell(self, instance, script, timeout=180):
        return self.wait_command(
            instance, self.send(instance, script, timeout), timeout + 60
        )

    def action(self, api, ids, action="restart", key=None):
        return api.post(
            "/api/actions",
            json={"action": action, "computer_ids": ids},
            headers={"Idempotency-Key": key or str(uuid4())},
        )

    def wait(self, predicate, label, timeout=240):
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(4)
        raise TimeoutError(label)

    def target_states(self):
        return {
            i["InstanceId"]: i["State"]["Name"]
            for reservation in self.ec2.describe_instances(
                InstanceIds=[t["instance_id"] for t in self.state["targets"]]
            )["Reservations"]
            for i in reservation["Instances"]
        }

    def boot_times(self):
        return {
            t["computer_id"]: self.powershell(
                t["instance_id"],
                '(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")',
            )
            for t in self.state["targets"]
        }

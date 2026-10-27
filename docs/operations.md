# Operations

The website runs on Contabo. A single controller inside the FIAP LAN polls the website over HTTPS, runs fixed WinRM commands, and sends health reports. No software is installed on laboratory computers. No inbound connection from the internet to the FIAP LAN is required.

## Accounts

Set `AUTH_MODE=local` (the default). After filling the private `.env`, create the first administrator interactively:

```sh
docker compose exec web python -m scripts.create_admin --email admin@fiap.com.br --name Administrator
```

Passwords belong to this application, independently of the FIAP domain password. The administrator creates accounts in **User access**, assigns a role and floors/laboratories/computers, and can reset passwords or disable accounts. Email addresses must use `@fiap.com.br`. There is no public signup or preset password. Users change their own passwords with the key button in the header.

New passwords require 15–128 characters. Salted scrypt hashes use N=65536, r=8, p=2. Login attempts are limited to five per IP and ten per account in 15 minutes; successful authentication clears that IP/account's attempts. Session cookies are signed, HttpOnly, Secure, and SameSite=Lax, with an eight-hour lifetime. Writes require a same-origin request and a session CSRF token. Password changes, administrative resets, grant changes, and account disablement revoke existing sessions. Queued commands recheck current permissions before delivery.

The deployment migrates the prior administrator password hash into the account credential table and upgrades its hash on successful login. It preserves the account ID, grants, and audit history. Existing databases marked as demonstration data cannot run in production.

## Optional corporate SSO

Set `AUTH_MODE=entra` only after FIAP IT registers a single-tenant Microsoft Entra web application. The FIAP discovery endpoint resolves to tenant `11dbbfe2-89b8-4549-be10-cec364e59551`.

1. Register the exact HTTPS redirect URI: `https://<your-host>/auth/callback`.
2. Set `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and `ENTRA_CLIENT_SECRET` in the private environment.
3. Set `BOOTSTRAP_ADMIN_OID` to the initial administrator's directory object ID, then restart the website.
4. Add users by their FIAP directory object ID and corporate email in **User access**.

Authlib validates the OIDC signatures, issuer, audience, state, nonce, and PKCE. The tenant ID and object ID identify the authorized principal; the email suffix alone does not grant access. In Entra mode, local passwords and local sessions are rejected. Account grants remain administrator-managed.

## LAN controller

Install Python 3.12+ on the server in the FIAP LAN. Linux and Windows are supported.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item collector/.env.example .env.collector
.venv\Scripts\python -m collector.service
```

On Linux, use `.venv/bin/python` and the provided systemd service. On Windows, create a Task Scheduler task that starts `python -m collector.service` at boot with the repository as its working directory. Run it under the dedicated service account. Set **Do not start a new instance** and restart on failure. Keep `.env.collector` readable only by that account and administrators.

Configure `SWITCH_URL`, the matching `COLLECTOR_TOKEN`, `WINRM_USERNAME`, and `WINRM_PASSWORD`. These stay on the controller. Use a dedicated domain service account with only the required remote management and shutdown rights. Validate any Just Enough Administration endpoint against the fixed health and shutdown scripts before rollout.

Default: WinRM HTTPS on 5986 with NTLM, message encryption, and certificate verification. `WINRM_CA_BUNDLE` accepts a PEM bundle for the institution's certificate authority. For Kerberos, install `pywinrm[kerberos]`, configure the domain's Kerberos environment and service-account ticket renewal, and register DNS hostnames that resolve to each inventory IP. Kerberos permits HTTP on 5985 with message encryption. The implementation never disables TLS validation, enables Basic authentication, or configures wildcard TrustedHosts.

Register laboratories and MAC addresses before issuing Wake-on-LAN. Successful health probes fill missing MAC addresses using the default-gateway adapter and preserve an already registered MAC. Review it if a workstation has multiple network interfaces. The controller restricts all targets and broadcast addresses to `ALLOWED_CIDRS`.

Wake-on-LAN requires firmware/NIC support and suitable VLAN routing. The server's access to an IP does not imply it can send a directed broadcast into that VLAN. Coordinate directed broadcasts with FIAP networking. EC2 does not emulate physical Wake-on-LAN; AWS tests validate packet construction, WinRM monitoring, restart, and shutdown separately.

## Inventory and addressing

Supported floors: 1, 2, 3, 4, 5, 6, 7, 10, 11. Laboratory number is 1–9 within each floor; this preserves the concatenated third-octet convention without collisions.

| Laboratory | Subnet | Student example | Professor |
| --- | --- | --- | --- |
| 301 | 10.3.31.0/24 | 10.3.31.1 | 10.3.31.100 |
| 202 | 10.3.22.0/24 | 10.3.22.1 | 10.3.22.100 |
| 1001 | 10.3.101.0/24 | 10.3.101.1 | 10.3.101.100 |

The lab wizard creates PCs 1 through the entered student count, plus the optional professor PC 100. It starts with unknown state and does not scan networks outside the inventory.

## State and commands

The overview shows floor summaries without rendering every computer. Opening **Computers** selects the first accessible floor and its first laboratory. Changing the floor selects its first laboratory automatically. An explicit **All assigned floors** selection is available for authorized cross-floor operations; tables remain paginated.

- **Online:** WinRM returned a valid health report.
- **Unreachable:** WinRM could not verify the computer. This does not prove the computer is powered off.
- **Unknown:** no report, or the last report is older than 120 seconds.

Every command is authorized server-side against the current user grants. Mixed unauthorized batches are rejected in full. Commands use an idempotency key, expire after five minutes before delivery, and have a two-minute execution lease. An expired lease is marked failed with unknown outcome and is never automatically retried.

Shut down and restart schedule a 60-second Windows notice. Windows scheduled shutdown implies forced application termination at the end of that notice; users must save their work. The UI requires confirmation. **Delivered** means Windows accepted the command or a Wake-on-LAN packet was sent. Actual reachability comes from the next probe.

## Floor checks and daily history

Use **Checks & history** to request a fresh verification for a floor, or all assigned floors. The controller wakes its monitoring cycle, probes the computers, and saves each result. Identical pending requests are reused. Completed verification results are immutable, including the hardware measured during that verification. A verification expires after 15 minutes if the controller cannot finish it.

Every real controller report also updates the current day's observation for that computer, using `America/Sao_Paulo`. A new daily record is created automatically on the first observation of each day. Daily history keeps the latest status, first and latest observation times, last confirmed online time, and hardware result. No record is fabricated for a day without observations. Historical results remain subject to the user's current floor, laboratory, and computer grants; results are paginated on the server.

**No response** identifies computers whose WinRM health could not be verified. A missing network response cannot establish that electrical power is off. **Not verified** identifies missing or stale observations. Both the UI and saved results include observation times so that old data is not presented as a fresh verification.

## Hardware standards

In **Inventory**, select a floor and set each laboratory's hardware standard. Optional requirements cover CPU model, logical CPU count, RAM, total physical storage, disk count, and GPU model. Model requirements use case-insensitive substring matching. Capacities are expressed in GiB.

**Exact inventory** compares configured counts and capacities; **Minimum capacities** accepts values at or above the configured capacities. RAM and storage allow a 5% tolerance in both modes to account for reported usable capacity. Empty requirements are ignored. Missing required telemetry is marked unverified and cannot produce a successful match.

Real WinRM/CIM observations create a hardware incident on the first mismatch, preserve its detection time through subsequent mismatches, and record when fresh data resolves it. The expected standard, detected hardware, differences, and resolution remain in the incident history. Disabling a standard is recorded separately from a successful hardware match. Changing the standard requires a new observation before the current result is confirmed.

The collector uses a short TCP connectivity check before WinRM negotiation and sends completed probes in batches as they arrive, preventing unresponsive machines from blocking earlier healthy results. `TCP_CONNECT_TIMEOUT` defaults to two seconds; its allowed runtime range is 0.5–10 seconds.

## Hosting and backup

Copy `.env.example` to `.env`, fill in private configuration, then run `docker compose up -d --build` and create the first administrator. Bind the container to loopback and place it behind Caddy or another HTTPS proxy. The container runs as a non-root user with a read-only filesystem and a dedicated SQLite data volume. Use one web replica; SQLite queues are transactional and persistent.

Back up SQLite with its online backup API, not by copying a live WAL database. Retain the deployment's `.env` in a secure backup. Rotate the controller token on both ends together. Restrict host administration and review command/audit retention with FIAP IT.

## Disposable AWS laboratory

Authenticate using `aws login --profile fiap-switch --region sa-east-1`. No permanent access key is required.

```powershell
python -m pip install -r requirements-dev.txt
python infra/aws_lab.py provision
python infra/aws_lab.py status
python -m scripts.e2e_remote
python infra/aws_lab.py stop
python infra/aws_lab.py destroy
```

Provisioning uses one Linux t3.micro controller and one Windows Server 2022 t3.small target, private WinRM access, SSM administration, encrypted EBS, and IMDSv2. AWS reserves subnet addresses `.0`–`.3`; the target uses `10.3.31.11`. A generated local password and resource IDs stay in ignored `runtime/aws-lab.json`. The Windows local-account policy is used only in this disposable lab, not in the FIAP domain configuration.

For an entirely Windows laboratory across two floors, use a separate state file:

```powershell
python infra/aws_lab.py provision --region us-east-2 --state runtime/aws-validation-20261027.json --name fiap-switch-e2e-20261027 --windows-controller --targets 2
```

This creates a Windows controller at `10.3.31.10`, a student workstation at `10.3.31.4`, and a professor workstation at `10.3.22.100`. WinRM certificates include private IP and DNS SANs. The Windows controller bootstrap verifies the Python installer's signature; its collector runs as a startup scheduled task with private configuration restricted to SYSTEM and administrators. `scripts/live_validation.py` and `scripts/validate_features.py` exercise the deployed HTTPS API and actual Windows transitions. Local validation uses the authorized SOCKS5 endpoint at `127.0.0.1:1080`; `scripts/browser_gateway.py` forwards the UI through that proxy while validating upstream TLS and preserving production cookie settings. The gateway binds only to loopback and is a test tool.

The account may restrict instance types to its Free Tier eligible set. `t3.small` was accepted for the Windows target during validation. If provisioning fails partway through, inspect the recorded resource state and destroy it before creating another lab. `stop` leaves chargeable EBS volumes; `destroy` removes all owned resources and volumes.

The remote E2E harness requires ignored website credentials in `runtime/login.json`, the lab state, and authorized SSH access to Contabo. It starts the controller, checks live WinRM telemetry over the deployed HTTPS API, issues restart and shutdown through the queue, and verifies a changed Windows boot time and the EC2 stopped state. Its laboratory is labeled **AWS validation**, with synthetic unused PCs alongside the actual EC2 target. Remove this inventory fixture after validation and destroy the test resources.

## References

- [Microsoft OIDC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
- [Microsoft token claims](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)
- [WinRM security](https://learn.microsoft.com/en-us/powershell/scripting/learn/remoting/winrmsecurity)
- [pywinrm](https://github.com/diyan/pywinrm)
- [AWS CLI console sign-in](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)

- [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [NIST authentication requirements](https://pages.nist.gov/800-63-4/sp800-63b.html)

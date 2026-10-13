# Operations

The website runs on Contabo. A single controller inside the FIAP LAN polls the website over HTTPS, runs fixed WinRM commands, and sends health reports. No software is installed on laboratory computers. No inbound connection from the internet to the FIAP LAN is required.

## Corporate SSO

Microsoft's public discovery endpoint for `fiap.com.br` resolves to tenant `11dbbfe2-89b8-4549-be10-cec364e59551`. Confirm this with FIAP IT before production use.

Register a **single-tenant web application** in Microsoft Entra ID:

1. Register the exact HTTPS redirect URI: `https://<your-host>/auth/callback`.
2. Set `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and `ENTRA_CLIENT_SECRET` in the server's private `.env`.
3. Set `BOOTSTRAP_ADMIN_OID` to the FIAP directory object ID of the initial administrator, then restart the website.
4. Add users by their Entra object ID and corporate email. Assign their floors, laboratories, or individual computers in **User access**.

The application validates OIDC signatures, issuer, audience, state, nonce and PKCE through Authlib. Tenant ID and stable object ID select the authorized principal. `@fiap.com.br` is an additional sign-in restriction; an email suffix alone never grants access. Tokens and domain passwords are not sent to the frontend. Revoking access or changing grants invalidates existing sessions.

An email address alone cannot register an application or supply its client credentials. Corporate sign-in stays disabled until registration is complete. The demonstration uses a separate database and cannot connect to real controllers.

### Temporary access

When authorized, an initial local administrator can use a username and an independent password until FIAP IT completes registration. Set `TEMPORARY_LOGIN_ENABLED=true`, `TEMPORARY_USERNAME`, `TEMPORARY_PASSWORD_HASH`, `TEMPORARY_NAME`, and `TEMPORARY_EMAIL` in the private server environment. Generate the hash with `backend.passwords.hash_password`; quote its value in the environment file. Never use a domain password for this local account.

The password uses scrypt with a random salt. Sign-in requires same-origin HTTPS requests and is limited to five failed attempts per IP per 15 minutes. Cookies are signed, HttpOnly and Secure. Password rotation invalidates temporary sessions on the next server startup. Once all three Entra settings are configured, both temporary sign-in and existing temporary sessions are rejected automatically. Domain users continue to be identified by Entra object IDs; temporary access does not replace their SSO identities.

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

- **Online:** WinRM returned a valid health report.
- **Unreachable:** WinRM could not verify the computer. This does not prove the computer is powered off.
- **Unknown:** no report, or the last report is older than 120 seconds.
- **Offline:** a confirmed simulated power state, currently available only in the demo. Production cannot infer physical power state from network silence.

Every command is authorized server-side against the current user grants. Mixed unauthorized batches are rejected in full. Commands use an idempotency key, expire after five minutes before delivery, and have a two-minute execution lease. An expired lease is marked failed with unknown outcome and is never automatically retried.

Shut down and restart schedule a 60-second Windows notice. Windows scheduled shutdown implies forced application termination at the end of that notice; users must save their work. The UI requires confirmation. **Delivered** means Windows accepted the command or a Wake-on-LAN packet was sent. Actual reachability comes from the next probe.

## Hosting and backup

Copy `.env.example` to `.env`, fill in private configuration, then run `docker compose up -d --build`. Bind the container to loopback and place it behind Caddy or another HTTPS proxy. The container runs as a non-root user with a read-only filesystem and a dedicated SQLite data volume. Use one web replica; SQLite queues are transactional and persistent.

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

The account may restrict instance types to its Free Tier eligible set. `t3.small` was accepted for the Windows target during validation. If provisioning fails partway through, inspect the recorded resource state and destroy it before creating another lab. `stop` leaves chargeable EBS volumes; `destroy` removes all owned resources and volumes.

The remote E2E harness requires ignored temporary website credentials, the lab state, and authorized SSH access to Contabo. It starts the controller, checks live WinRM telemetry over the deployed HTTPS API, issues restart and shutdown through the queue, and verifies a changed Windows boot time and the EC2 stopped state. Its laboratory is labeled **AWS validation**, with synthetic unused PCs alongside the actual EC2 target. Remove this inventory fixture after validation and destroy the test resources.

## References

- [Microsoft OIDC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
- [Microsoft token claims](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)
- [WinRM security](https://learn.microsoft.com/en-us/powershell/scripting/learn/remoting/winrmsecurity)
- [pywinrm](https://github.com/diyan/pywinrm)
- [AWS CLI console sign-in](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)

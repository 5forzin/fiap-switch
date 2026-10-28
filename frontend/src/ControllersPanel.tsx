import { useEffect, useState } from "react";
import type { Api } from "./inventory";

type Device = {
  id: string;
  platform: string;
  state: string;
  hostname: string;
  source_ip: string;
  local_ips: string;
  fingerprint: string;
  last_seen: number | null;
};
type Data = { devices: Device[]; issuer_fingerprint: string; mode: string };
const states: Record<string, string> = {
  issued: "Aguardando instalação",
  pending: "Aguardando aprovação",
  approved: "Aprovado",
  rejected: "Rejeitado",
  revoked: "Revogado",
};

export function ControllersPanel({
  api,
  csrf,
  onError,
}: {
  api: Api;
  csrf: string;
  onError: (message: string) => void;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setData(await api<Data>("/api/controllers"));
  }
  useEffect(() => {
    let active = true;
    const load = () =>
      api<Data>("/api/controllers")
        .then((next) => {
          if (active) setData(next);
        })
        .catch((error) => {
          if (active) onError(error.message);
        });
    load();
    const id = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);
  async function download(platform: string) {
    setBusy(true);
    try {
      const response = await fetch("/api/controllers/packages", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ platform }),
      });
      if (!response.ok)
        throw new Error(
          (await response.json()).detail || "Não foi possível gerar o pacote.",
        );
      const url = URL.createObjectURL(await response.blob()),
        link = document.createElement("a");
      link.href = url;
      link.download = `fiap-controlador-${platform}.zip`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      await refresh();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function decide(id: string, operation: string) {
    setBusy(true);
    try {
      await api(`/api/controllers/${id}/${operation}`, "POST", {});
      await refresh();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!data) return <div className="empty">Carregando…</div>;
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Servidores conectados</h2>
      </div>
      {data.mode === "fiap" ? (
        <div className="controller-downloads">
          <button
            className="primary"
            disabled={busy}
            onClick={() => download("windows")}
          >
            Baixar para Windows
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => download("linux")}
          >
            Baixar para Linux
          </button>
          <p>
            O convite expira em 30 minutos. Confira a impressão digital no
            servidor antes de aprovar.
          </p>
          <details>
            <summary>Assinatura e instalação</summary>
            <code className="fingerprint">{data.issuer_fingerprint}</code>
            <p>
              Windows:{" "}
              <code>.\instalar.ps1 -Fingerprint {data.issuer_fingerprint}</code>
            </p>
            <p>
              Linux:{" "}
              <code>
                sudo sh instalar.sh --fingerprint {data.issuer_fingerprint}
              </code>
            </p>
          </details>
        </div>
      ) : (
        <p className="controller-downloads">
          O modo {data.mode.toUpperCase()} opera sem controlador externo.
        </p>
      )}
      {data.devices.length ? (
        <div className="controller-list">
          {data.devices.map((device) => (
            <article
              className={`controller-card ${device.state}`}
              key={device.id}
            >
              <div>
                <h3>{device.hostname || "Novo controlador"}</h3>
                <span className="muted">
                  {device.platform === "windows" ? "Windows" : "Linux"} ·{" "}
                  {states[device.state] || device.state}
                </span>
              </div>
              {device.source_ip && (
                <p>
                  Origem: <strong>{device.source_ip}</strong>
                </p>
              )}
              {device.local_ips !== "[]" && (
                <p>Rede local: {JSON.parse(device.local_ips).join(", ")}</p>
              )}
              {device.fingerprint && (
                <code className="fingerprint">{device.fingerprint}</code>
              )}
              <div className="controller-actions">
                {device.state === "pending" && (
                  <>
                    <button
                      className="primary"
                      disabled={busy || data.mode !== "fiap"}
                      onClick={() => decide(device.id, "approve")}
                    >
                      Aprovar conexão
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => decide(device.id, "reject")}
                    >
                      Rejeitar
                    </button>
                  </>
                )}
                {device.state === "approved" && (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() => decide(device.id, "revoke")}
                  >
                    Revogar acesso
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty">Aguardando conexão do servidor.</div>
      )}
    </section>
  );
}

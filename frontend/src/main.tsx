import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowUpRight,
  Building2,
  Check,
  Clock3,
  Cpu,
  KeyRound,
  LayoutDashboard,
  ListFilter,
  LogOut,
  Menu,
  Monitor,
  MoreHorizontal,
  Plus,
  Power,
  RefreshCw,
  Search,
  Server,
  Settings2,
  Trash2,
  Users,
  X,
} from "lucide-react";
import "./style.css";
import { ChecksPanel } from "./ChecksPanel";
import { HardwareStandardForm } from "./HardwareStandardForm";
import { LabQuantityForm, QuantityFields } from "./LabQuantityForm";
import type { Hardware, HardwareStandard } from "./inventory";
import { quantidade } from "./inventory";

type User = {
  id: string;
  name: string;
  email: string;
  role: string;
  enabled: boolean;
  auth_type?: "local" | "entra";
  csrf?: string;
  grants: { floors?: number[]; labs?: string[]; computers?: string[] };
};
type Session = {
  user: User | null;
  auth_mode: "local" | "entra";
  power_mode: "fiap" | "aws" | "azure";
  floors: number[];
};
type PC = {
  id: string;
  lab_id: string;
  lab_label: string;
  floor: number;
  number: number;
  hostname: string;
  ip: string;
  mac: string;
  status: string;
  last_seen: number | null;
  issue: string;
  stale: boolean;
  can_control: boolean;
  hardware: Hardware;
  hardware_state: string;
  hardware_checked_at: number | null;
  hardware_mismatch_since: number | null;
  hardware_differences: string[];
  metrics: {
    cpu?: number;
    memory?: number;
    disk_free?: number;
    uptime_hours?: number;
    os?: string;
  };
};
type Lab = {
  id: string;
  floor: number;
  number: number;
  label: string;
  subnet: string;
  broadcast: string;
  hardware_standard?: HardwareStandard | null;
  student_count: number | null;
};
type Job = {
  id: string;
  computer_id: string;
  hostname: string;
  action: string;
  state: string;
  created: number;
  finished: number | null;
  message: string;
  actor_name?: string;
};
type Dashboard = {
  computers: PC[];
  labs: Lab[];
  jobs: Job[];
  collectors: { id: string; last_seen: number | null }[];
  updated_at: number;
};
type Audit = {
  id: number;
  actor: string;
  actor_name?: string | null;
  action: string;
  target: string;
  created: number;
  detail: string;
};
type View =
  "overview" | "computers" | "activity" | "access" | "inventory" | "checks";
const labels: Record<string, string> = {
  online: "Ligado",
  offline: "Desligado",
  unreachable: "Sem resposta",
  unknown: "Não verificado",
  wake: "Ligar",
  shutdown: "Desligar",
  restart: "Reiniciar",
  queued: "Na fila",
  running: "Em execução",
  completed: "Enviado",
  failed: "Falhou",
  admin: "Administrador",
  support: "Suporte",
  monitor: "Monitor",
  viewer: "Observador",
  login: "Entrada",
  password_changed: "Senha alterada",
  password_reset: "Senha redefinida",
  user_created: "Usuário cadastrado",
  user_updated: "Usuário atualizado",
  user_deleted: "Conta excluída",
  lab_quantity_updated: "Quantidade de computadores atualizada",
  lab_created: "Laboratório cadastrado",
  computer_updated: "Computador atualizado",
  computer_discovered: "Computador descoberto",
  command_revoked: "Comando revogado",
  lease_expired: "Prazo de execução expirado",
  check_requested: "Verificação solicitada",
  check_completed: "Verificação concluída",
  hardware_mismatch: "Hardware divergente",
  hardware_resolved: "Divergência resolvida",
  hardware_standard_updated: "Padrão de hardware atualizado",
};
const time = (value?: number | null) =>
  value
    ? new Date(value * 1000).toLocaleTimeString("pt-BR", {
        timeZone: "America/Sao_Paulo",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
const formatoNumero = new Intl.NumberFormat("pt-BR", {
  maximumFractionDigits: 2,
});
const numero = (valor?: number | null) =>
  valor == null ? "—" : formatoNumero.format(valor);
const date = (value: number) =>
  new Date(value * 1000).toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [data, setData] = useState<Dashboard | null>(null);
  const [view, setView] = useState<View>("overview");
  const [floor, setFloor] = useState<number | null>(null);
  const [lab, setLab] = useState("all");
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [selection, setSelection] = useState<string[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [events, setEvents] = useState<Audit[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState<{
    action: string;
    ids: string[];
  } | null>(null);
  const [detail, setDetail] = useState<PC | null>(null);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [removeUser, setRemoveUser] = useState<User | null>(null);
  const [newLab, setNewLab] = useState(false);
  const [passwordDialog, setPasswordDialog] = useState(false);
  const [page, setPage] = useState(0);
  const [hardwareFilter, setHardwareFilter] = useState("all");
  const [standardLab, setStandardLab] = useState<Lab | null>(null);
  const [inventoryTab, setInventoryTab] = useState<"hardware" | "computers">(
    "hardware",
  );
  const user = session?.user;
  const admin = user?.role === "admin";
  const gestor = admin || user?.role === "support";
  const activeUser = React.useRef(user?.id);
  activeUser.current = user?.id;

  useEffect(() => {
    setData(null);
    setUsers([]);
    setEvents([]);
    setDetail(null);
    setEditUser(null);
    setRemoveUser(null);
    setConfirm(null);
    setPasswordDialog(false);
    setNewLab(false);
    setStandardLab(null);
    setHardwareFilter("all");
    setMenu(false);
    setSelection([]);
    setFloor(null);
    setView("overview");
    setSearch("");
    setStatus("all");
  }, [user?.id]);

  async function api<T>(
    path: string,
    method = "GET",
    body?: unknown,
    headers?: Record<string, string>,
  ): Promise<T> {
    const response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": user?.csrf || "",
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json().catch(() => {
      throw new Error("Servidor indisponível. Tente novamente.");
    });
    if (!response.ok) {
      if (response.status === 401 && activeUser.current === user?.id) {
        setSession((s) => s && { ...s, user: null });
        setData(null);
      }
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : result.detail?.[0]?.msg ||
              "Não foi possível concluir a solicitação.",
      );
    }
    return result;
  }
  async function load() {
    const next = await api<Dashboard>("/api/dashboard");
    if (activeUser.current !== user?.id) return next;
    setData(next);
    setDetail((pc) =>
      pc ? next.computers.find((c) => c.id === pc.id) || null : null,
    );
    return next;
  }
  useEffect(() => {
    api<Session>("/api/session")
      .then(setSession)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!user) return;
    load().catch((e) => setError(e.message));
    const id = window.setInterval(
      () => load().catch((e) => setError(e.message)),
      5000,
    );
    return () => window.clearInterval(id);
  }, [user?.id]);
  useEffect(() => {
    if (!user) return;
    if (view === "access")
      api<User[]>("/api/users")
        .then(setUsers)
        .catch((e) => setError(e.message));
    if (view === "activity")
      api<Audit[]>("/api/audit")
        .then(setEvents)
        .catch((e) => setError(e.message));
  }, [view, user?.id, data?.jobs.length]);
  useEffect(() => {
    setSelection([]);
  }, [floor, view]);
  useEffect(() => {
    setPage(0);
    setSelection([]);
  }, [floor, view, lab, status, search, hardwareFilter]);
  useEffect(() => {
    if (notice) {
      const id = setTimeout(() => setNotice(""), 5000);
      return () => clearTimeout(id);
    }
  }, [notice]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDetail(null);
        setEditUser(null);
        setRemoveUser(null);
        setNewLab(false);
        setConfirm(null);
        setPasswordDialog(false);
        setStandardLab(null);
        setMenu(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  async function signIn(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      await api("/auth/login", "POST", {
        username: form.get("username"),
        password: form.get("password"),
      });
      setSession(await api<Session>("/api/session"));
      setView("overview");
      setFloor(null);
      setData(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function changePassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      await api("/auth/password", "POST", {
        current_password: form.get("current_password"),
        new_password: form.get("new_password"),
      });
      setSession(await api<Session>("/api/session"));
      setPasswordDialog(false);
      setNotice("Senha alterada.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function runAction() {
    if (!confirm) return;
    setBusy(true);
    try {
      const result = await api<{ job_ids: string[] }>(
        "/api/actions",
        "POST",
        { action: confirm.action, computer_ids: confirm.ids },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setNotice(
        `${result.job_ids.length} comando${result.job_ids.length > 1 ? "s enviados" : " enviado"} à fila.`,
      );
      setConfirm(null);
      setSelection([]);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveUser(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!editUser) return;
    setBusy(true);
    const form = new FormData(e.currentTarget);
    try {
      await api("/api/users", "PUT", {
        id: editUser.id || undefined,
        oid: form.get("oid") || undefined,
        password: form.get("password") || undefined,
        name: String(form.get("name")),
        email: String(form.get("email")),
        role: String(form.get("role")),
        enabled: form.get("enabled") === "on",
        grants:
          editUser.role === "viewer"
            ? editUser.grants
            : {
                floors:
                  form.get("role") === "monitor"
                    ? [Number(form.get("floor"))]
                    : [],
              },
      });
      setSession(await api<Session>("/api/session"));
      setEditUser(null);
      setUsers(await api<User[]>("/api/users"));
      setNotice("Usuário salvo.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveLab(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    const f = new FormData(e.currentTarget);
    try {
      await api("/api/labs", "POST", {
        floor: Number(f.get("floor")),
        number: Number(f.get("number")),
        label: f.get("label"),
        students:
          f.get("undefined_students") === "on"
            ? null
            : Number(f.get("students")),
        include_professor: f.get("professor") === "on",
      });
      await load();
      setNewLab(false);
      setNotice("Laboratório adicionado.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function savePC(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!detail) return;
    const f = new FormData(e.currentTarget);
    setBusy(true);
    try {
      await api(`/api/computers/${detail.id}`, "PUT", {
        hostname: f.get("hostname"),
        mac: f.get("mac"),
      });
      await load();
      setNotice("Computador atualizado.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const all = data?.computers || [],
    scoped = all.filter((c) => floor === null || c.floor === floor);
  const filtered = scoped.filter(
    (c) =>
      (lab === "all" || c.lab_id === lab) &&
      (status === "all" || c.status === status) &&
      (hardwareFilter === "all" ||
        (hardwareFilter === "mismatch"
          ? Boolean(c.hardware_mismatch_since)
          : c.hardware_state === hardwareFilter)) &&
      `${c.hostname} ${c.ip} ${c.lab_label} ${c.number === 100 ? "professor" : ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const floors = gestor
      ? session?.floors || []
      : [
          ...new Set([
            ...all.map((c) => c.floor),
            ...(user?.grants.floors || []),
          ]),
        ].sort((a, b) => a - b),
    labs = (data?.labs || []).filter(
      (l) => floor === null || l.floor === floor,
    );
  const pending = new Set(
    (data?.jobs || [])
      .filter((j) => j.state === "queued" || j.state === "running")
      .map((j) => j.computer_id),
  );
  const displayed = filtered.slice(
    view === "overview" ? 0 : page * 50,
    view === "overview" ? 10 : (page + 1) * 50,
  );
  const controlled = displayed.filter(
      (c) => c.can_control && !pending.has(c.id),
    ),
    counts = (state: string) => scoped.filter((c) => c.status === state).length;
  const defaultFloor =
    all[0]?.floor ?? data?.labs[0]?.floor ?? floors[0] ?? null;
  const navigate = (
    next: View,
    selectedFloor: number | null = floor,
    selectedLab?: string,
  ) => {
    setView(next);
    setMenu(false);
    setSearch("");
    setStatus("all");
    setHardwareFilter("all");
    if (next === "overview") {
      setFloor(null);
      setLab("all");
    }
    if (next === "computers" || next === "inventory") {
      const chosen = selectedFloor ?? defaultFloor;
      setFloor(chosen);
      setLab(
        selectedLab ??
          data?.labs.find((item) => item.floor === chosen)?.id ??
          "all",
      );
    }
  };
  const chooseFloor = (value: number | null) => {
    setFloor(value);
    setLab(data?.labs.find((item) => item.floor === value)?.id ?? "all");
  };
  const mismatches = scoped.filter((pc) => Boolean(pc.hardware_mismatch_since));
  if (!session || (!data && user))
    return (
      <div className="loading">
        <img className="brand" src="/brand/fiap.svg" alt="FIAP" />
        <RefreshCw className="spin" size={22} />
        <p role={error ? "alert" : "status"}>{error || "Carregando…"}</p>
      </div>
    );
  if (!user)
    return (
      <div className="login-page">
        <header className="login-head">
          <img className="brand" src="/brand/fiap.svg" alt="FIAP" />
          <span className="product-name">SWITCH</span>
        </header>
        <main className="login-card">
          <h1>Entrar</h1>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          {session.auth_mode === "local" ? (
            <form className="login-form" onSubmit={signIn}>
              <label>
                E-mail
                <input
                  name="username"
                  type="email"
                  autoComplete="username"
                  required
                  maxLength={254}
                  placeholder="nome@fiap.com.br"
                />
              </label>
              <label>
                Senha
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  maxLength={128}
                />
              </label>
              <button className="primary wide" disabled={busy}>
                {busy ? "Entrando…" : "Entrar"}
                <ArrowUpRight size={16} />
              </button>
            </form>
          ) : (
            <a className="primary wide" href="/auth/login">
              Entrar com a Microsoft <ArrowUpRight size={16} />
            </a>
          )}
        </main>
      </div>
    );
  return (
    <div className="app">
      <header className="topbar">
        <button
          className="menu-button"
          onClick={() => setMenu(!menu)}
          aria-label={menu ? "Fechar navegação" : "Abrir navegação"}
          aria-expanded={menu}
        >
          <Menu size={18} />
          <span>MENU</span>
        </button>
        <a
          className="brand-link"
          href="/"
          aria-label="Página inicial do FIAP Switch"
        >
          <img className="brand" src="/brand/fiap.svg" alt="FIAP" />
        </a>
        <span className="product-name">SWITCH</span>
        <div className="top-right">
          <span className="avatar">{user.name[0]}</span>
          <div className="identity">
            <strong>{user.name}</strong>
            <span>{labels[user.role] || user.role}</span>
          </div>
          {session.auth_mode === "local" && (
            <button
              className="icon-button"
              aria-label="Alterar senha"
              title="Alterar senha"
              onClick={() => {
                setError("");
                setPasswordDialog(true);
              }}
            >
              <KeyRound size={17} />
            </button>
          )}
          <button
            className="icon-button"
            aria-label="Sair"
            title="Sair"
            onClick={async () => {
              try {
                await api("/auth/logout", "POST");
                setSession({ ...session, user: null });
                setData(null);
                setMenu(false);
                setError("");
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            <LogOut size={17} />
          </button>
        </div>
      </header>
      {menu && (
        <button
          className="menu-backdrop"
          onClick={() => setMenu(false)}
          aria-label="Fechar navegação"
        />
      )}
      <aside className={`sidebar ${menu ? "open" : ""}`}>
        <div className="nav-section">PAINEL</div>
        <nav>
          {(
            [
              ["overview", LayoutDashboard, "Visão geral"],
              ["computers", Monitor, "Computadores"],
              ["activity", Activity, "Atividade"],
              ["checks", Clock3, "Verificações e histórico"],
            ] as const
          ).map(([key, Icon, title]) => (
            <button
              key={key}
              className={`nav-item ${view === key ? "active" : ""}`}
              onClick={() => navigate(key)}
            >
              <Icon size={18} />
              {title}
              {key === "computers" && (
                <span className="nav-count">{all.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="nav-section floor-label">
          ANDARES <span>{floors.length}</span>
        </div>
        <div className="floor-nav">
          <button
            className={floor === null ? "selected" : ""}
            onClick={() => {
              setFloor(null);
              navigate("overview");
            }}
          >
            <Building2 size={15} />
            {gestor ? "Todos os andares" : "Meus andares"}
          </button>
          {floors.map((f) => (
            <button
              key={f}
              className={floor === f ? "selected" : ""}
              onClick={() => {
                navigate("computers", f);
              }}
            >
              <span className="floor-num">{String(f).padStart(2, "0")}</span>
              Andar {f}
              <span
                className={`floor-dot ${all.some((c) => c.floor === f && c.status === "unreachable") ? "amber" : ""}`}
              />
            </button>
          ))}
        </div>
        {gestor && (
          <>
            <div className="nav-section">ADMINISTRAÇÃO</div>
            {admin && (
              <button
                className={`nav-item ${view === "access" ? "active" : ""}`}
                onClick={() => navigate("access")}
              >
                <Users size={18} />
                Acessos
              </button>
            )}
            <button
              className={`nav-item ${view === "inventory" ? "active" : ""}`}
              onClick={() => navigate("inventory")}
            >
              <Settings2 size={18} />
              Inventário
            </button>
          </>
        )}
      </aside>
      <main>
        <div className="page-heading">
          <h1>
            <Monitor size={19} />
            {view === "overview"
              ? "Laboratórios"
              : view === "computers"
                ? floor === null
                  ? "Computadores"
                  : `Andar ${floor}`
                : view === "activity"
                  ? "Atividade"
                  : view === "access"
                    ? "Usuários"
                    : view === "checks"
                      ? "Verificações e histórico"
                      : "Inventário"}
          </h1>
          <div className="heading-actions">
            <span className="last-update">
              <span className="live-dot" />
              Atualizado às {time(data?.updated_at)}
            </span>
            {view === "access" ? (
              <button
                className="primary"
                onClick={() => {
                  setError("");
                  setEditUser({
                    id: "",
                    name: "",
                    email: "",
                    role: "monitor",
                    enabled: true,
                    grants: {},
                  });
                }}
              >
                <Plus size={16} />
                Adicionar usuário
              </button>
            ) : view === "inventory" ? (
              <button
                className="primary"
                onClick={() => {
                  setError("");
                  setNewLab(true);
                }}
              >
                <Plus size={16} />
                Adicionar laboratório
              </button>
            ) : (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => load().catch((e) => setError(e.message))}
              >
                <RefreshCw size={15} />
                Atualizar
              </button>
            )}
          </div>
        </div>
        {error && (
          <div className="error" role="alert">
            {error}
            <button
              className="icon-button"
              aria-label="Fechar aviso"
              onClick={() => setError("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {(view === "overview" || view === "computers") &&
          mismatches.length > 0 && (
            <div className="hardware-alert" role="alert">
              <Cpu size={18} />
              <span>
                {mismatches.length} divergência
                {mismatches.length === 1 ? "" : "s"} de hardware
              </span>
              <button
                className="text-button"
                onClick={() => {
                  navigate(
                    "computers",
                    mismatches[0].floor,
                    mismatches[0].lab_id,
                  );
                  setHardwareFilter("mismatch");
                }}
              >
                Ver hardware
              </button>
            </div>
          )}
        {(view === "overview" || view === "computers") && (
          <>
            <div className="stats">
              <Stat
                label="COMPUTADORES"
                value={scoped.length}
                icon={<Monitor size={19} />}
              />
              <Stat
                label="LIGADOS"
                value={counts("online")}
                icon={<Power size={19} />}
                tone="green"
              />
              <Stat
                label="SEM RESPOSTA"
                value={counts("unreachable")}
                icon={<Activity size={19} />}
                tone="amber"
              />
              <Stat
                label="NÃO VERIFICADOS"
                value={counts("unknown")}
                icon={<Clock3 size={19} />}
                tone="gray"
              />
            </div>
            {view === "overview" && floors.length > 0 && (
              <section className="panel floor-overview">
                <div className="panel-title">
                  <h2>Situação por andar</h2>
                  <span>{quantidade(floors.length, "andar", "andares")}</span>
                </div>
                <div className="floor-cards">
                  {floors
                    .filter((f) => floor === null || floor === f)
                    .map((f) => {
                      const pcs = all.filter((c) => c.floor === f),
                        online = pcs.filter(
                          (c) => c.status === "online",
                        ).length,
                        attention = pcs.filter((c) =>
                          ["unreachable", "unknown"].includes(c.status),
                        ).length,
                        hardwareIssues = pcs.filter(
                          (pc) => pc.hardware_state === "mismatch",
                        ).length;
                      return (
                        <button
                          key={f}
                          className="floor-card"
                          onClick={() => {
                            navigate("computers", f);
                          }}
                        >
                          <div className="floor-card-head">
                            <span>
                              ANDAR{" "}
                              <strong>{String(f).padStart(2, "0")}</strong>
                            </span>
                            <ArrowUpRight size={17} />
                          </div>
                          <progress
                            className="floor-progress"
                            value={online}
                            max={Math.max(1, pcs.length)}
                            aria-label={`Andar ${f}: ${online} de ${pcs.length} ligados`}
                          />
                          <div className="floor-card-foot">
                            <span>
                              <b>{online}</b> / {pcs.length} ligados
                            </span>
                            <span className={attention ? "attention-text" : ""}>
                              {attention
                                ? `${attention} para verificar`
                                : pcs.length
                                  ? "Tudo verificado"
                                  : "Nenhum computador"}
                            </span>
                          </div>
                          {hardwareIssues > 0 && (
                            <small className="attention-text">
                              {hardwareIssues} divergência
                              {hardwareIssues === 1 ? "" : "s"} de hardware
                            </small>
                          )}
                        </button>
                      );
                    })}
                </div>
              </section>
            )}
            {view === "computers" && (
              <section className="panel computer-panel">
                <div className="panel-title">
                  <h2>
                    Computadores do laboratório{" "}
                    <span className="count-badge">{filtered.length}</span>
                  </h2>
                  <div className="status-legend">
                    <span>
                      <i className="green" />
                      Ligado
                    </span>
                    <span>
                      <i className="gray" />
                      Não verificado
                    </span>
                    <span>
                      <i className="amber" />
                      Atenção
                    </span>
                  </div>
                </div>
                <div className="filters">
                  <label className="select-field">
                    <Building2 size={15} />
                    <select
                      aria-label="Andar"
                      value={floor ?? ""}
                      onChange={(e) =>
                        chooseFloor(
                          e.target.value ? Number(e.target.value) : null,
                        )
                      }
                    >
                      <option value="">Todos os andares permitidos</option>
                      {floors.map((f) => (
                        <option key={f} value={f}>
                          Andar {f}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="search-field">
                    <Search size={16} />
                    <input
                      aria-label="Buscar computadores"
                      placeholder="Buscar nome, IP ou laboratório…"
                      value={search}
                      onChange={(e) => {
                        setSearch(e.target.value);
                        setSelection([]);
                      }}
                    />
                  </label>
                  <label className="select-field">
                    <Building2 size={15} />
                    <select
                      aria-label="Laboratório"
                      value={lab}
                      onChange={(e) => {
                        setLab(e.target.value);
                        setSelection([]);
                      }}
                    >
                      <option value="all">Todos os laboratórios</option>
                      {labs.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="select-field">
                    <ListFilter size={15} />
                    <select
                      aria-label="Situação do computador"
                      value={status}
                      onChange={(e) => {
                        setStatus(e.target.value);
                        setSelection([]);
                      }}
                    >
                      <option value="all">Todas as situações</option>
                      {["online", "unreachable", "unknown"].map((s) => (
                        <option key={s} value={s}>
                          {labels[s]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="select-field">
                    <Cpu size={15} />
                    <select
                      aria-label="Situação do hardware"
                      value={hardwareFilter}
                      onChange={(e) => setHardwareFilter(e.target.value)}
                    >
                      <option value="all">Todos os equipamentos</option>
                      <option value="match">Dentro do padrão</option>
                      <option value="mismatch">Hardware divergente</option>
                      <option value="unknown">Não verificado</option>
                      <option value="unconfigured">Sem padrão</option>
                    </select>
                  </label>
                </div>
                {selection.length > 0 && (
                  <div className="selection-bar">
                    <span>
                      <Check size={16} />
                      {selection.length} selecionado(s)
                    </span>
                    <div>
                      {["wake", "restart", "shutdown"].map((a) => (
                        <button
                          key={a}
                          onClick={() =>
                            setConfirm({ action: a, ids: selection })
                          }
                        >
                          {labels[a]}
                        </button>
                      ))}
                      <button
                        aria-label="Limpar seleção"
                        onClick={() => setSelection([])}
                      >
                        <X size={15} />
                      </button>
                    </div>
                  </div>
                )}
                <div className="table-wrap">
                  <table className="pc-table">
                    <thead>
                      <tr>
                        <th className="check-cell">
                          <input
                            type="checkbox"
                            aria-label="Selecionar todos os computadores visíveis"
                            checked={
                              controlled.length > 0 &&
                              controlled.every((c) => selection.includes(c.id))
                            }
                            disabled={!controlled.length}
                            onChange={(e) =>
                              setSelection(
                                e.target.checked
                                  ? controlled.map((c) => c.id)
                                  : [],
                              )
                            }
                          />
                        </th>
                        <th>COMPUTADOR</th>
                        <th>LABORATÓRIO</th>
                        <th>ENDEREÇO IP</th>
                        <th>SITUAÇÃO</th>
                        <th>HARDWARE</th>
                        <th>CPU</th>
                        <th>ÚLTIMA VERIFICAÇÃO</th>
                        <th className="end">AÇÕES</th>
                      </tr>
                    </thead>
                    <tbody>
                      {displayed.map((c) => (
                        <tr
                          key={c.id}
                          className={
                            selection.includes(c.id) ? "selected-row" : ""
                          }
                        >
                          <td className="check-cell">
                            <input
                              type="checkbox"
                              aria-label={`Selecionar ${c.id}`}
                              checked={selection.includes(c.id)}
                              disabled={!c.can_control || pending.has(c.id)}
                              onChange={(e) =>
                                setSelection((s) =>
                                  e.target.checked
                                    ? [...s, c.id]
                                    : s.filter((id) => id !== c.id),
                                )
                              }
                            />
                          </td>
                          <td>
                            <button
                              className="pc-name"
                              onClick={() => setDetail(c)}
                            >
                              <span
                                className={`pc-icon ${c.number === 100 ? "professor" : ""}`}
                              >
                                <Monitor size={17} />
                              </span>
                              <span>
                                <strong>
                                  {c.number === 100
                                    ? "Computador do professor"
                                    : `Computador ${String(c.number).padStart(2, "0")}`}
                                </strong>
                                <small>{c.hostname || c.id}</small>
                              </span>
                            </button>
                          </td>
                          <td>
                            <span className="lab-text">{c.lab_label}</span>
                            <small>Andar {c.floor}</small>
                          </td>
                          <td className="mono">{c.ip}</td>
                          <td>
                            <span
                              className={`status ${pending.has(c.id) ? "pending" : c.status}`}
                            >
                              <i />
                              {pending.has(c.id)
                                ? "Comando pendente"
                                : labels[c.status]}
                            </span>
                          </td>
                          <td>
                            <span
                              className={`status ${c.hardware_state === "mismatch" ? "unreachable" : c.hardware_state === "match" ? "online" : "unknown"}`}
                            >
                              {c.hardware_state === "match"
                                ? "Conforme"
                                : c.hardware_state === "mismatch"
                                  ? "Divergente"
                                  : c.hardware_state === "unconfigured"
                                    ? "Sem padrão"
                                    : "Não verificado"}
                            </span>
                            {c.hardware_mismatch_since && (
                              <small>
                                Desde {date(c.hardware_mismatch_since)}
                              </small>
                            )}
                          </td>
                          <td>
                            {c.status === "online" ? (
                              <div className="health-cell">
                                <Cpu size={13} />
                                <span>{numero(c.metrics.cpu)}%</span>
                                <span className="mini-meter">
                                  <i
                                    className={`meter-fill w${Math.round((c.metrics.cpu || 0) / 10) * 10}`}
                                  />
                                </span>
                              </div>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                          <td className="muted">{time(c.last_seen)}</td>
                          <td className="end">
                            <div className="row-actions">
                              {c.can_control && (
                                <button
                                  disabled={pending.has(c.id)}
                                  className="icon-button power-action"
                                  aria-label={`Controles de energia de ${c.id}`}
                                  onClick={() => setDetail(c)}
                                >
                                  <Power size={16} />
                                </button>
                              )}
                              <button
                                className="icon-button"
                                aria-label={`Detalhes de ${c.id}`}
                                onClick={() => setDetail(c)}
                              >
                                <MoreHorizontal size={18} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {filtered.length === 0 && (
                    <div className="empty">
                      <Monitor size={26} />
                      <h3>Nenhum computador encontrado</h3>
                      <p>
                        {all.length
                          ? "Ajuste os filtros."
                          : gestor
                            ? "Adicione um laboratório."
                            : "Peça ao administrador para definir seu andar."}
                      </p>
                    </div>
                  )}
                </div>
                <div className="table-footer">
                  <span>
                    {displayed.length} de{" "}
                    {quantidade(filtered.length, "computador", "computadores")}
                  </span>
                  {view === "computers" && filtered.length > 50 && (
                    <div className="pagination">
                      <button
                        className="secondary small"
                        disabled={page === 0}
                        onClick={() => {
                          setPage(page - 1);
                          setSelection([]);
                        }}
                      >
                        Anterior
                      </button>
                      <span>
                        Página {page + 1} de {Math.ceil(filtered.length / 50)}
                      </span>
                      <button
                        className="secondary small"
                        disabled={(page + 1) * 50 >= filtered.length}
                        onClick={() => {
                          setPage(page + 1);
                          setSelection([]);
                        }}
                      >
                        Próxima
                      </button>
                    </div>
                  )}
                </div>
              </section>
            )}
            {view === "overview" && (
              <section className="panel">
                <div className="panel-title">
                  <h2>Comandos recentes</h2>
                  <button
                    className="text-button"
                    onClick={() => setView("activity")}
                  >
                    Ver atividade <ArrowUpRight size={14} />
                  </button>
                </div>
                <Jobs jobs={data?.jobs.slice(0, 5) || []} />
              </section>
            )}
          </>
        )}
        {view === "activity" && (
          <>
            <section className="panel">
              <div className="panel-title">
                <h2>Comandos</h2>
                <span>Últimos 100</span>
              </div>
              <Jobs jobs={data?.jobs || []} />
            </section>
            <section className="panel">
              <div className="panel-title">
                <h2>Auditoria</h2>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>HORÁRIO</th>
                      <th>RESPONSÁVEL</th>
                      <th>AÇÃO</th>
                      <th>DESTINO</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((ev) => (
                      <tr key={ev.id}>
                        <td>{date(ev.created)}</td>
                        <td>
                          {ev.actor_name ||
                            (ev.actor === "collector" || ev.actor === "system"
                              ? "Controlador"
                              : ev.actor)}
                        </td>
                        <td>
                          {labels[ev.action] || ev.action.replaceAll("_", " ")}
                        </td>
                        <td>{labels[ev.target] || ev.target}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!events.length && (
                  <div className="empty">Nenhuma atividade registrada.</div>
                )}
              </div>
            </section>
          </>
        )}
        {view === "access" && (
          <section className="panel">
            <div className="panel-title">
              <h2>Contas</h2>
              <span>{users.length} contas</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>USUÁRIO</th>
                    <th>PERFIL</th>
                    <th>PERMISSÕES</th>
                    <th>ACESSO</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <strong>{u.name}</strong>
                        <small>{u.email}</small>
                        <div className="account-actions">
                          <button
                            className="secondary small"
                            onClick={() => {
                              setError("");
                              setEditUser(u);
                            }}
                          >
                            Editar
                          </button>
                          <button
                            className="icon-button"
                            aria-label={`Excluir conta de ${u.name}`}
                            disabled={u.id === user.id}
                            onClick={() => {
                              setError("");
                              setRemoveUser(u);
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </td>
                      <td>{labels[u.role] || u.role}</td>
                      <td>
                        {u.role === "admin" || u.role === "support"
                          ? "Todos os computadores"
                          : [
                              u.grants.floors?.length
                                ? `${u.grants.floors.length === 1 ? "Andar" : "Andares"} ${u.grants.floors.join(", ")}`
                                : "",
                              u.grants.labs?.length
                                ? `Salas ${u.grants.labs.join(", ")}`
                                : "",
                              u.grants.computers?.length
                                ? `${u.grants.computers.length} computadores`
                                : "",
                            ]
                              .filter(Boolean)
                              .join(" · ") || "Nenhum computador permitido"}
                      </td>
                      <td>
                        <span
                          className={`status ${u.enabled ? "online" : "offline"}`}
                        >
                          <i />
                          {u.enabled ? "Ativo" : "Inativo"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {view === "inventory" && (
          <section className="panel">
            <div className="panel-title">
              <h2>Laboratórios cadastrados</h2>
              <span>{quantidade(labs.length, "laboratório")}</span>
            </div>
            <div className="filters">
              <label className="select-field">
                <Building2 size={15} />
                <select
                  aria-label="Andar do inventário"
                  value={floor ?? defaultFloor ?? ""}
                  onChange={(e) => chooseFloor(Number(e.target.value))}
                >
                  {floors.map((f) => (
                    <option key={f} value={f}>
                      Andar {f}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>LABORATÓRIO</th>
                    <th>ANDAR</th>
                    <th>SUB-REDE</th>
                    <th>COMPUTADORES</th>
                    <th>WAKE-ON-LAN</th>
                    <th>PADRÃO DE HARDWARE</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data?.labs
                    .filter((l) => floor === null || l.floor === floor)
                    .map((l) => (
                      <tr key={l.id}>
                        <td>
                          <strong>{l.label}</strong>
                          <small>{l.id}</small>
                          <button
                            className="text-button"
                            onClick={() => {
                              setError("");
                              setInventoryTab("hardware");
                              setStandardLab(l);
                            }}
                          >
                            Configurar sala
                          </button>
                        </td>
                        <td>Andar {l.floor}</td>
                        <td className="mono">{l.subnet}</td>
                        <td>
                          {l.student_count === null ? (
                            <>
                              Indefinido
                              <small>
                                {quantidade(
                                  all.filter((c) => c.lab_id === l.id).length,
                                  "cadastrado",
                                )}
                              </small>
                            </>
                          ) : (
                            all.filter((c) => c.lab_id === l.id).length
                          )}
                        </td>
                        <td>
                          {quantidade(
                            all.filter((c) => c.lab_id === l.id && c.mac)
                              .length,
                            "endereço MAC",
                            "endereços MAC",
                          )}
                        </td>
                        <td>
                          {l.hardware_standard?.enabled
                            ? "Ativo"
                            : "Sem padrão"}
                        </td>
                        <td className="end">
                          <button
                            className="text-button"
                            onClick={() => {
                              navigate("computers", l.floor, l.id);
                            }}
                          >
                            Gerenciar <ArrowUpRight size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
              {!data?.labs.some((l) => floor === null || l.floor === floor) && (
                <div className="empty">Nenhum laboratório neste andar.</div>
              )}
            </div>
          </section>
        )}
        {view === "checks" && (
          <ChecksPanel
            api={api}
            floors={floors}
            initialFloor={floor ?? defaultFloor}
            canRun={user.role !== "viewer"}
            onError={setError}
          />
        )}
        <footer className="main-footer">
          <span>FIAP SWITCH</span>
          {gestor && (
            <span className="controller-status">
              <Server size={13} />
              Controlador{" "}
              {data?.collectors.some(
                (c) => c.last_seen && Date.now() / 1000 - c.last_seen < 120,
              )
                ? "conectado"
                : "desconectado"}
            </span>
          )}
        </footer>
      </main>
      {notice && (
        <div className="toast" role="status">
          <Check size={17} />
          {notice}
        </div>
      )}
      {standardLab && (
        <Modal
          title={`Inventário · ${standardLab.label}`}
          close={() => setStandardLab(null)}
          error={error}
          wide
        >
          <div className="history-tabs">
            <button
              className={inventoryTab === "hardware" ? "active" : ""}
              onClick={() => setInventoryTab("hardware")}
            >
              Padrão de hardware
            </button>
            <button
              className={inventoryTab === "computers" ? "active" : ""}
              onClick={() => setInventoryTab("computers")}
            >
              Computadores
            </button>
          </div>
          {inventoryTab === "computers" ? (
            <LabQuantityForm
              key={standardLab.id}
              value={standardLab.student_count}
              registered={
                all.filter(
                  (pc) => pc.lab_id === standardLab.id && pc.number < 100,
                ).length
              }
              save={async (students) => {
                try {
                  await api(`/api/labs/${standardLab.id}/quantity`, "PUT", {
                    students,
                  });
                  const next = await load();
                  setStandardLab(
                    next.labs.find((l) => l.id === standardLab.id) || null,
                  );
                  setNotice("Quantidade atualizada.");
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            />
          ) : (
            <HardwareStandardForm
              standard={standardLab.hardware_standard}
              references={all
                .filter(
                  (pc) =>
                    pc.lab_id === standardLab.id &&
                    pc.status === "online" &&
                    !pc.stale &&
                    pc.hardware_checked_at &&
                    Date.now() / 1000 - pc.hardware_checked_at < 120 &&
                    pc.hardware.logical_cpus &&
                    pc.hardware.memory_gib &&
                    (pc.hardware_state === "match" ||
                      !standardLab.hardware_standard?.enabled),
                )
                .map((pc) => ({
                  id: pc.id,
                  name: `${pc.hostname || pc.id} · ${pc.hardware_state === "match" ? "Conforme" : "Sem padrão"}`,
                  hardware: pc.hardware,
                }))}
              close={() => setStandardLab(null)}
              save={async (value) => {
                try {
                  await api(
                    `/api/labs/${standardLab.id}/hardware-standard`,
                    "PUT",
                    value,
                  );
                  await load();
                  setStandardLab(null);
                  setNotice("Padrão de hardware salvo.");
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            />
          )}
        </Modal>
      )}
      {confirm && (
        <Modal
          title={labels[confirm.action]}
          close={() => setConfirm(null)}
          error={error}
        >
          <div className="confirm-icon">
            <Power size={28} />
          </div>
          <p className="confirm-copy">
            {labels[confirm.action]} {confirm.ids.length} computador
            {confirm.ids.length > 1 ? "es" : ""}?
          </p>
          {confirm.action !== "wake" && (
            <p className="muted">
              {session?.power_mode === "fiap"
                ? "O Windows exibirá um aviso de 60 segundos. Trabalhos não salvos serão perdidos."
                : "O comando será enviado à API do provedor. Salve os trabalhos antes de continuar."}
            </p>
          )}
          <div className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirm(null)}
            >
              Cancelar
            </button>
            <button className="primary" disabled={busy} onClick={runAction}>
              {busy ? "Enviando…" : "Confirmar"}
            </button>
          </div>
        </Modal>
      )}
      {detail && (
        <Modal
          title={
            detail.number === 100
              ? "Computador do professor"
              : `Computador ${detail.number}`
          }
          close={() => setDetail(null)}
          error={error}
          wide
        >
          <div className="detail-heading">
            <span className={`status ${detail.status}`}>
              <i />
              {labels[detail.status]}
            </span>
            <span>
              {detail.lab_label} · Andar {detail.floor}
            </span>
          </div>
          <dl className="details">
            <div>
              <dt>ENDEREÇO IP</dt>
              <dd className="mono">{detail.ip}</dd>
            </div>
            <div>
              <dt>NOME DE REDE</dt>
              <dd>{detail.hostname || "Não cadastrado"}</dd>
            </div>
            <div>
              <dt>SISTEMA OPERACIONAL</dt>
              <dd>{detail.metrics.os || "—"}</dd>
            </div>
            <div>
              <dt>ÚLTIMA VERIFICAÇÃO</dt>
              <dd>
                {time(detail.last_seen)}
                {detail.stale ? " · desatualizado" : ""}
              </dd>
            </div>
            <div>
              <dt>CPU / MEMÓRIA</dt>
              <dd>
                {numero(detail.metrics.cpu)}% / {numero(detail.metrics.memory)}%
              </dd>
            </div>
            <div>
              <dt>DISCO LIVRE / TEMPO LIGADO</dt>
              <dd>
                {numero(detail.metrics.disk_free)}% /{" "}
                {numero(detail.metrics.uptime_hours)} h
              </dd>
            </div>
          </dl>
          <dl className="details">
            <div>
              <dt>MODELO DE CPU</dt>
              <dd>{detail.hardware?.cpu_model || "—"}</dd>
            </div>
            <div>
              <dt>CPUS LÓGICAS / RAM</dt>
              <dd>
                {numero(detail.hardware?.logical_cpus)} /{" "}
                {numero(detail.hardware?.memory_gib)} GiB
              </dd>
            </div>
            <div>
              <dt>ARMAZENAMENTO / DISCOS</dt>
              <dd>
                {numero(detail.hardware?.storage_gib)} GiB /{" "}
                {numero(detail.hardware?.disk_count)}
              </dd>
            </div>
            <div>
              <dt>GPU</dt>
              <dd>{detail.hardware?.gpu_models?.join(", ") || "—"}</dd>
            </div>
            <div>
              <dt>HARDWARE VERIFICADO</dt>
              <dd>
                {detail.hardware_checked_at
                  ? date(detail.hardware_checked_at)
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>DIVERGÊNCIA DETECTADA</dt>
              <dd>
                {detail.hardware_mismatch_since
                  ? date(detail.hardware_mismatch_since)
                  : "—"}
              </dd>
            </div>
          </dl>
          {detail.hardware_differences?.length > 0 && (
            <div className="issue-note">
              {detail.hardware_differences.map((item) => (
                <div key={item}>{item.replaceAll("_", " ")}</div>
              ))}
            </div>
          )}
          {detail.issue && <div className="issue-note">{detail.issue}</div>}
          {gestor && (
            <form onSubmit={savePC}>
              <label>
                Nome de rede
                <input
                  name="hostname"
                  defaultValue={detail.hostname}
                  maxLength={253}
                />
              </label>
              <label>
                Endereço MAC
                <input
                  name="mac"
                  defaultValue={detail.mac}
                  placeholder="00:11:22:33:44:55"
                />
              </label>
              <button className="secondary small" disabled={busy}>
                Salvar computador
              </button>
            </form>
          )}
          {detail.can_control && (
            <div className="detail-actions">
              {["wake", "restart", "shutdown"].map((a) => (
                <button
                  key={a}
                  className={a === "wake" ? "primary" : "secondary"}
                  disabled={
                    pending.has(detail.id) ||
                    (session?.power_mode === "fiap" &&
                      a === "wake" &&
                      !detail.mac)
                  }
                  onClick={() => {
                    setConfirm({ action: a, ids: [detail.id] });
                    setDetail(null);
                  }}
                >
                  {a === "restart" ? (
                    <RefreshCw size={15} />
                  ) : (
                    <Power size={15} />
                  )}
                  {labels[a]}
                </button>
              ))}
            </div>
          )}
        </Modal>
      )}
      {removeUser && (
        <Modal
          title="Excluir conta"
          close={() => setRemoveUser(null)}
          error={error}
        >
          <p>Excluir a conta de {removeUser.name}?</p>
          <small>
            O acesso e as sessões serão encerrados. A autoria das ações será
            preservada.
          </small>
          <div className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setRemoveUser(null)}
            >
              Cancelar
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api(`/api/users/${removeUser.id}`, "DELETE");
                  setUsers(await api<User[]>("/api/users"));
                  setRemoveUser(null);
                  setNotice("Conta excluída.");
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Excluindo…" : "Excluir conta"}
            </button>
          </div>
        </Modal>
      )}
      {editUser && (
        <Modal
          title={editUser.id ? "Editar usuário" : "Adicionar usuário"}
          close={() => setEditUser(null)}
          error={error}
          wide
        >
          <form onSubmit={saveUser}>
            {session.auth_mode === "entra" && (
              <label>
                ID do objeto no Entra
                <input
                  name="oid"
                  required
                  readOnly={Boolean(editUser.id)}
                  defaultValue={editUser.id.split(":")[1] || ""}
                />
              </label>
            )}
            <label>
              Nome completo
              <input
                name="name"
                required
                maxLength={100}
                defaultValue={editUser.name}
              />
            </label>
            <label>
              E-mail
              <input
                name="email"
                type="email"
                required
                maxLength={254}
                pattern="[^@]+@fiap\.com\.br"
                defaultValue={editUser.email}
                placeholder="nome@fiap.com.br"
              />
            </label>
            {session.auth_mode === "local" && (
              <label>
                {editUser.id ? "Redefinir senha" : "Senha"}
                <input
                  name="password"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={128}
                  required={!editUser.id}
                />
                <small>
                  {editUser.id
                    ? "Deixe em branco para manter a senha. Mínimo de 8 caracteres."
                    : "Mínimo de 8 caracteres."}
                </small>
              </label>
            )}
            <label>
              Perfil
              <select
                name="role"
                value={editUser.role}
                onChange={(e) =>
                  setEditUser({ ...editUser, role: e.target.value })
                }
              >
                <option value="monitor">Monitor</option>
                <option value="support">Suporte</option>
                <option value="admin">Administrador</option>
                {editUser.role === "viewer" && (
                  <option value="viewer">Observador (legado)</option>
                )}
              </select>
            </label>
            {editUser.role === "monitor" && (
              <label>
                Andar do monitor
                <select
                  name="floor"
                  defaultValue={editUser.grants.floors?.[0] ?? ""}
                  required
                >
                  <option value="" disabled>
                    Selecione o andar
                  </option>
                  {session.floors.map((f) => (
                    <option key={f} value={f}>
                      Andar {f}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="checkbox-label">
              <input
                type="checkbox"
                name="enabled"
                defaultChecked={editUser.enabled}
              />
              Ativo
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setEditUser(null)}
              >
                Cancelar
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "Salvando…" : "Salvar"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {passwordDialog && (
        <Modal
          title="Alterar senha"
          close={() => setPasswordDialog(false)}
          error={error}
        >
          <form onSubmit={changePassword}>
            <label>
              Senha atual
              <input
                name="current_password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
              />
            </label>
            <label>
              Nova senha
              <input
                name="new_password"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={128}
              />
              <small>Mínimo de 8 caracteres.</small>
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setPasswordDialog(false)}
              >
                Cancelar
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "Salvando…" : "Salvar"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {newLab && (
        <Modal
          title="Adicionar laboratório"
          close={() => setNewLab(false)}
          error={error}
        >
          <form onSubmit={saveLab}>
            <div className="form-row">
              <label>
                Andar
                <select name="floor" defaultValue={floor || 1}>
                  {session.floors.map((f) => (
                    <option key={f}>{f}</option>
                  ))}
                </select>
              </label>
              <label>
                Número da sala
                <input
                  name="number"
                  type="number"
                  min="1"
                  max="9"
                  defaultValue="1"
                  required
                />
              </label>
            </div>
            <label>
              Nome do laboratório
              <input
                name="label"
                required
                placeholder="Laboratório 301"
                maxLength={80}
              />
            </label>
            <QuantityFields />
            <label className="checkbox-label">
              <input name="professor" type="checkbox" defaultChecked />
              Incluir computador do professor (.100)
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setNewLab(false)}
              >
                Cancelar
              </button>
              <button className="primary" disabled={busy}>
                Adicionar laboratório
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
function Stat({
  label,
  value,
  icon,
  tone = "",
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  tone?: string;
}) {
  return (
    <div className={`stat ${tone}`}>
      <div className="stat-top">
        <span>{label}</span>
        {icon}
      </div>
      <strong>{value}</strong>
    </div>
  );
}
function Jobs({ jobs }: { jobs: Job[] }) {
  return (
    <div className="jobs">
      {jobs.length ? (
        jobs.map((j) => (
          <div key={j.id} className="job-row">
            <span className="job-icon">
              {j.action === "restart" ? (
                <RefreshCw size={17} />
              ) : (
                <Power size={17} />
              )}
            </span>
            <div>
              <strong>{labels[j.action]}</strong>
              <small>
                {j.hostname || j.computer_id}
                {j.actor_name ? ` · ${j.actor_name}` : ""}
              </small>
            </div>
            <span className="job-message">
              {j.message || "Aguardando o controlador"}
            </span>
            <span className={`job-state ${j.state}`}>{labels[j.state]}</span>
            <span className="job-time">{time(j.created)}</span>
          </div>
        ))
      ) : (
        <div className="empty compact">
          <Clock3 size={20} />
          <span>Nenhum comando enviado.</span>
        </div>
      )}
    </div>
  );
}
function Modal({
  title,
  close,
  children,
  wide = false,
  error,
}: {
  error?: string;
  title: string;
  close: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null,
      node = ref.current;
    node?.focus();
    const trap = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || !node) return;
      const elements = [
          ...node.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]",
          ),
        ],
        first = elements[0],
        last = elements[elements.length - 1];
      if (
        e.shiftKey &&
        (document.activeElement === first || document.activeElement === node)
      ) {
        e.preventDefault();
        last?.focus();
      }
      if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className={`modal ${wide ? "wide-modal" : ""}`}
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-title">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Fechar janela"
            onClick={close}
          >
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          {children}
        </div>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

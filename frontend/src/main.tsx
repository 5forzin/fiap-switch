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
  Users,
  X,
} from "lucide-react";
import "./style.css";
import { ChecksPanel } from "./ChecksPanel";
import { HardwareStandardForm } from "./HardwareStandardForm";
import type { Hardware, HardwareStandard } from "./inventory";

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
  action: string;
  target: string;
  created: number;
  detail: string;
};
type View =
  "overview" | "computers" | "activity" | "access" | "inventory" | "checks";
const labels: Record<string, string> = {
  online: "Online",
  offline: "Offline",
  unreachable: "No response",
  unknown: "Unknown",
  wake: "Power on",
  shutdown: "Shut down",
  restart: "Restart",
  queued: "Queued",
  running: "Running",
  completed: "Delivered",
  failed: "Failed",
};
const time = (value?: number | null) =>
  value
    ? new Date(value * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
const date = (value: number) =>
  new Date(value * 1000).toLocaleString([], {
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
  const [newLab, setNewLab] = useState(false);
  const [passwordDialog, setPasswordDialog] = useState(false);
  const [page, setPage] = useState(0);
  const [hardwareFilter, setHardwareFilter] = useState("all");
  const [standardLab, setStandardLab] = useState<Lab | null>(null);
  const user = session?.user;
  const admin = user?.role === "admin";
  const activeUser = React.useRef(user?.id);
  activeUser.current = user?.id;

  useEffect(() => {
    setData(null);
    setUsers([]);
    setEvents([]);
    setDetail(null);
    setEditUser(null);
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
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 401 && activeUser.current === user?.id) {
        setSession((s) => s && { ...s, user: null });
        setData(null);
      }
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : result.detail?.[0]?.msg || "Request failed.",
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
      setNotice("Password changed.");
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
        `${result.job_ids.length} command${result.job_ids.length > 1 ? "s" : ""} queued.`,
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
        grants: {
          floors: form.getAll("floor").map(Number),
          labs: String(form.get("labs") || "")
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
          computers: String(form.get("computers") || "")
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
        },
      });
      setSession(await api<Session>("/api/session"));
      setEditUser(null);
      setUsers(await api<User[]>("/api/users"));
      setNotice("User saved.");
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
        students: Number(f.get("students")),
        include_professor: f.get("professor") === "on",
      });
      await load();
      setNewLab(false);
      setNotice("Laboratory added.");
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
      setNotice("Computer updated.");
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
  const floors = admin
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
        <p role={error ? "alert" : "status"}>{error || "Loading…"}</p>
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
          <h1>Login</h1>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          {session.auth_mode === "local" ? (
            <form className="login-form" onSubmit={signIn}>
              <label>
                Email
                <input
                  name="username"
                  type="email"
                  autoComplete="username"
                  required
                  maxLength={254}
                  placeholder="name@fiap.com.br"
                />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  maxLength={128}
                />
              </label>
              <button className="primary wide" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
                <ArrowUpRight size={16} />
              </button>
            </form>
          ) : (
            <a className="primary wide" href="/auth/login">
              Sign in with Microsoft <ArrowUpRight size={16} />
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
          aria-label={menu ? "Close navigation" : "Open navigation"}
          aria-expanded={menu}
        >
          <Menu size={18} />
          <span>MENU</span>
        </button>
        <a className="brand-link" href="/" aria-label="FIAP Switch home">
          <img className="brand" src="/brand/fiap.svg" alt="FIAP" />
        </a>
        <span className="product-name">SWITCH</span>
        <div className="top-right">
          <span className="avatar">{user.name[0]}</span>
          <div className="identity">
            <strong>{user.name}</strong>
            <span>
              {admin
                ? "Administrator"
                : user.role === "viewer"
                  ? "Observer"
                  : "Monitor"}
            </span>
          </div>
          {session.auth_mode === "local" && (
            <button
              className="icon-button"
              aria-label="Change password"
              title="Change password"
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
            aria-label="Sign out"
            title="Sign out"
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
          aria-label="Close navigation"
        />
      )}
      <aside className={`sidebar ${menu ? "open" : ""}`}>
        <div className="nav-section">WORKSPACE</div>
        <nav>
          {(
            [
              ["overview", LayoutDashboard, "Overview"],
              ["computers", Monitor, "Computers"],
              ["activity", Activity, "Activity"],
              ["checks", Clock3, "Checks & history"],
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
          FLOORS <span>{floors.length}</span>
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
            {admin ? "All floors" : "My floors"}
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
              Floor {f}
              <span
                className={`floor-dot ${all.some((c) => c.floor === f && c.status === "unreachable") ? "amber" : ""}`}
              />
            </button>
          ))}
        </div>
        {admin && (
          <>
            <div className="nav-section">ADMINISTRATION</div>
            <button
              className={`nav-item ${view === "access" ? "active" : ""}`}
              onClick={() => navigate("access")}
            >
              <Users size={18} />
              User access
            </button>
            <button
              className={`nav-item ${view === "inventory" ? "active" : ""}`}
              onClick={() => navigate("inventory")}
            >
              <Settings2 size={18} />
              Inventory
            </button>
          </>
        )}
      </aside>
      <main>
        <div className="page-heading">
          <h1>
            <Monitor size={19} />
            {view === "overview"
              ? "Laboratories"
              : view === "computers"
                ? floor === null
                  ? "Computers"
                  : `Floor ${floor}`
                : view === "activity"
                  ? "Activity"
                  : view === "access"
                    ? "Users"
                    : view === "checks"
                      ? "Checks & history"
                      : "Inventory"}
          </h1>
          <div className="heading-actions">
            <span className="last-update">
              <span className="live-dot" />
              Updated {time(data?.updated_at)}
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
                Add user
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
                Add laboratory
              </button>
            ) : (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => load().catch((e) => setError(e.message))}
              >
                <RefreshCw size={15} />
                Refresh
              </button>
            )}
          </div>
        </div>
        {error && (
          <div className="error" role="alert">
            {error}
            <button
              className="icon-button"
              aria-label="Dismiss error"
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
              <span>{mismatches.length} hardware mismatches detected</span>
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
                Review hardware
              </button>
            </div>
          )}
        {(view === "overview" || view === "computers") && (
          <>
            <div className="stats">
              <Stat
                label="COMPUTERS"
                value={scoped.length}
                icon={<Monitor size={19} />}
              />
              <Stat
                label="ONLINE"
                value={counts("online")}
                icon={<Power size={19} />}
                tone="green"
              />
              <Stat
                label="NO RESPONSE"
                value={counts("unreachable")}
                icon={<Activity size={19} />}
                tone="amber"
              />
              <Stat
                label="UNKNOWN"
                value={counts("unknown")}
                icon={<Clock3 size={19} />}
                tone="gray"
              />
            </div>
            {view === "overview" && floors.length > 0 && (
              <section className="panel floor-overview">
                <div className="panel-title">
                  <h2>Floor status</h2>
                  <span>{floors.length} floors</span>
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
                              FLOOR{" "}
                              <strong>{String(f).padStart(2, "0")}</strong>
                            </span>
                            <ArrowUpRight size={17} />
                          </div>
                          <progress
                            className="floor-progress"
                            value={online}
                            max={Math.max(1, pcs.length)}
                            aria-label={`Floor ${f}: ${online} of ${pcs.length} online`}
                          />
                          <div className="floor-card-foot">
                            <span>
                              <b>{online}</b> / {pcs.length} online
                            </span>
                            <span className={attention ? "attention-text" : ""}>
                              {attention
                                ? `${attention} to check`
                                : pcs.length
                                  ? "All verified"
                                  : "No computers"}
                            </span>
                          </div>
                          {pcs.some(
                            (pc) => pc.hardware_state === "mismatch",
                          ) && (
                            <small className="attention-text">
                              {
                                pcs.filter(
                                  (pc) => pc.hardware_state === "mismatch",
                                ).length
                              }{" "}
                              hardware mismatches
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
                    Laboratory computers{" "}
                    <span className="count-badge">{filtered.length}</span>
                  </h2>
                  <div className="status-legend">
                    <span>
                      <i className="green" />
                      Online
                    </span>
                    <span>
                      <i className="gray" />
                      Unknown
                    </span>
                    <span>
                      <i className="amber" />
                      Attention
                    </span>
                  </div>
                </div>
                <div className="filters">
                  <label className="select-field">
                    <Building2 size={15} />
                    <select
                      aria-label="Floor"
                      value={floor ?? ""}
                      onChange={(e) =>
                        chooseFloor(
                          e.target.value ? Number(e.target.value) : null,
                        )
                      }
                    >
                      <option value="">All assigned floors</option>
                      {floors.map((f) => (
                        <option key={f} value={f}>
                          Floor {f}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="search-field">
                    <Search size={16} />
                    <input
                      aria-label="Search computers"
                      placeholder="Search hostname, IP or lab…"
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
                      aria-label="Laboratory"
                      value={lab}
                      onChange={(e) => {
                        setLab(e.target.value);
                        setSelection([]);
                      }}
                    >
                      <option value="all">All laboratories</option>
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
                      aria-label="Computer status"
                      value={status}
                      onChange={(e) => {
                        setStatus(e.target.value);
                        setSelection([]);
                      }}
                    >
                      <option value="all">All statuses</option>
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
                      aria-label="Hardware status"
                      value={hardwareFilter}
                      onChange={(e) => setHardwareFilter(e.target.value)}
                    >
                      <option value="all">All hardware</option>
                      <option value="match">Matches standard</option>
                      <option value="mismatch">Hardware mismatch</option>
                      <option value="unknown">Not verified</option>
                      <option value="unconfigured">No standard</option>
                    </select>
                  </label>
                </div>
                {selection.length > 0 && (
                  <div className="selection-bar">
                    <span>
                      <Check size={16} />
                      {selection.length} selected
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
                        aria-label="Clear selection"
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
                            aria-label="Select all visible computers"
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
                        <th>COMPUTER</th>
                        <th>LABORATORY</th>
                        <th>IP ADDRESS</th>
                        <th>STATUS</th>
                        <th>HARDWARE</th>
                        <th>CPU</th>
                        <th>LAST CHECK</th>
                        <th className="end">ACTIONS</th>
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
                              aria-label={`Select ${c.id}`}
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
                                    ? "Professor workstation"
                                    : `Computer ${String(c.number).padStart(2, "0")}`}
                                </strong>
                                <small>{c.hostname || c.id}</small>
                              </span>
                            </button>
                          </td>
                          <td>
                            <span className="lab-text">{c.lab_label}</span>
                            <small>Floor {c.floor}</small>
                          </td>
                          <td className="mono">{c.ip}</td>
                          <td>
                            <span
                              className={`status ${pending.has(c.id) ? "pending" : c.status}`}
                            >
                              <i />
                              {pending.has(c.id)
                                ? "Command pending"
                                : labels[c.status]}
                            </span>
                          </td>
                          <td>
                            <span
                              className={`status ${c.hardware_state === "mismatch" ? "unreachable" : c.hardware_state === "match" ? "online" : "unknown"}`}
                            >
                              {c.hardware_state === "match"
                                ? "Matches"
                                : c.hardware_state === "mismatch"
                                  ? "Mismatch"
                                  : c.hardware_state === "unconfigured"
                                    ? "No standard"
                                    : "Not verified"}
                            </span>
                            {c.hardware_mismatch_since && (
                              <small>
                                Since {date(c.hardware_mismatch_since)}
                              </small>
                            )}
                          </td>
                          <td>
                            {c.status === "online" ? (
                              <div className="health-cell">
                                <Cpu size={13} />
                                <span>{c.metrics.cpu ?? "—"}%</span>
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
                                  aria-label={`Power controls for ${c.id}`}
                                  onClick={() => setDetail(c)}
                                >
                                  <Power size={16} />
                                </button>
                              )}
                              <button
                                className="icon-button"
                                aria-label={`Details for ${c.id}`}
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
                      <h3>No computers found</h3>
                      <p>
                        {all.length
                          ? "Adjust your filters."
                          : admin
                            ? "Add a laboratory to start."
                            : "Ask support to assign your floor."}
                      </p>
                    </div>
                  )}
                </div>
                <div className="table-footer">
                  <span>
                    {displayed.length} of {filtered.length} computers
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
                        Previous
                      </button>
                      <span>
                        Page {page + 1} of {Math.ceil(filtered.length / 50)}
                      </span>
                      <button
                        className="secondary small"
                        disabled={(page + 1) * 50 >= filtered.length}
                        onClick={() => {
                          setPage(page + 1);
                          setSelection([]);
                        }}
                      >
                        Next
                      </button>
                    </div>
                  )}
                </div>
              </section>
            )}
            {view === "overview" && (
              <section className="panel">
                <div className="panel-title">
                  <h2>Recent commands</h2>
                  <button
                    className="text-button"
                    onClick={() => setView("activity")}
                  >
                    Activity log <ArrowUpRight size={14} />
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
                <h2>Commands</h2>
                <span>Last 100</span>
              </div>
              <Jobs jobs={data?.jobs || []} />
            </section>
            <section className="panel">
              <div className="panel-title">
                <h2>Audit trail</h2>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>TIME</th>
                      <th>ACTOR</th>
                      <th>ACTION</th>
                      <th>TARGET</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((ev) => (
                      <tr key={ev.id}>
                        <td>{date(ev.created)}</td>
                        <td className="mono">{ev.actor}</td>
                        <td>
                          {labels[ev.action] || ev.action.replaceAll("_", " ")}
                        </td>
                        <td>{ev.target}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!events.length && (
                  <div className="empty">No activity recorded.</div>
                )}
              </div>
            </section>
          </>
        )}
        {view === "access" && (
          <section className="panel">
            <div className="panel-title">
              <h2>Accounts</h2>
              <span>{users.length} accounts</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>USER</th>
                    <th>ROLE</th>
                    <th>SCOPE</th>
                    <th>ACCESS</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <strong>{u.name}</strong>
                        <small>{u.email}</small>
                      </td>
                      <td className="capitalize">{u.role}</td>
                      <td>
                        {u.role === "admin"
                          ? "All computers"
                          : [
                              u.grants.floors?.length
                                ? `Floors ${u.grants.floors.join(", ")}`
                                : "",
                              u.grants.labs?.length
                                ? `Labs ${u.grants.labs.join(", ")}`
                                : "",
                              u.grants.computers?.length
                                ? `${u.grants.computers.length} computers`
                                : "",
                            ]
                              .filter(Boolean)
                              .join(" · ") || "No computers assigned"}
                      </td>
                      <td>
                        <span
                          className={`status ${u.enabled ? "online" : "offline"}`}
                        >
                          <i />
                          {u.enabled ? "Enabled" : "Disabled"}
                        </span>
                      </td>
                      <td className="end">
                        <button
                          className="secondary small"
                          onClick={() => {
                            setError("");
                            setEditUser(u);
                          }}
                        >
                          Edit
                        </button>
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
              <h2>Registered laboratories</h2>
              <span>{data?.labs.length} laboratories</span>
            </div>
            <div className="filters">
              <label className="select-field">
                <Building2 size={15} />
                <select
                  aria-label="Inventory floor"
                  value={floor ?? defaultFloor ?? ""}
                  onChange={(e) => chooseFloor(Number(e.target.value))}
                >
                  {floors.map((f) => (
                    <option key={f} value={f}>
                      Floor {f}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>LABORATORY</th>
                    <th>FLOOR</th>
                    <th>SUBNET</th>
                    <th>COMPUTERS</th>
                    <th>WAKE-ON-LAN</th>
                    <th>HARDWARE STANDARD</th>
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
                        </td>
                        <td>Floor {l.floor}</td>
                        <td className="mono">{l.subnet}</td>
                        <td>{all.filter((c) => c.lab_id === l.id).length}</td>
                        <td>
                          {all.filter((c) => c.lab_id === l.id && c.mac).length}{" "}
                          MAC addresses
                        </td>
                        <td>
                          <button
                            className="secondary small"
                            onClick={() => {
                              setError("");
                              setStandardLab(l);
                            }}
                          >
                            {l.hardware_standard?.enabled
                              ? "Edit standard"
                              : "Set standard"}
                          </button>
                        </td>
                        <td className="end">
                          <button
                            className="text-button"
                            onClick={() => {
                              navigate("computers", l.floor, l.id);
                            }}
                          >
                            Manage <ArrowUpRight size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
              {!data?.labs.length && (
                <div className="empty">Add the first laboratory.</div>
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
          {admin && (
            <span className="controller-status">
              <Server size={13} />
              Controller{" "}
              {data?.collectors.some(
                (c) => c.last_seen && Date.now() / 1000 - c.last_seen < 120,
              )
                ? "connected"
                : "disconnected"}
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
          title={`Hardware standard · ${standardLab.label}`}
          close={() => setStandardLab(null)}
          error={error}
          wide
        >
          <HardwareStandardForm
            standard={standardLab.hardware_standard}
            sample={
              all.find(
                (pc) => pc.lab_id === standardLab.id && pc.hardware_checked_at,
              )?.hardware
            }
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
                setNotice("Hardware standard saved.");
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          />
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
            {labels[confirm.action]} {confirm.ids.length} computer
            {confirm.ids.length > 1 ? "s" : ""}?
          </p>
          {confirm.action !== "wake" && (
            <p className="muted">
              Unsaved work will be lost after the 60-second Windows notice.
            </p>
          )}
          <div className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirm(null)}
            >
              Cancel
            </button>
            <button className="primary" disabled={busy} onClick={runAction}>
              {busy ? "Submitting…" : "Confirm"}
            </button>
          </div>
        </Modal>
      )}
      {detail && (
        <Modal
          title={
            detail.number === 100
              ? "Professor workstation"
              : `Computer ${detail.number}`
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
              {detail.lab_label} · Floor {detail.floor}
            </span>
          </div>
          <dl className="details">
            <div>
              <dt>IP ADDRESS</dt>
              <dd className="mono">{detail.ip}</dd>
            </div>
            <div>
              <dt>HOSTNAME</dt>
              <dd>{detail.hostname || "Not registered"}</dd>
            </div>
            <div>
              <dt>OPERATING SYSTEM</dt>
              <dd>{detail.metrics.os || "—"}</dd>
            </div>
            <div>
              <dt>LAST CHECK</dt>
              <dd>
                {time(detail.last_seen)}
                {detail.stale ? " · stale" : ""}
              </dd>
            </div>
            <div>
              <dt>CPU / MEMORY</dt>
              <dd>
                {detail.metrics.cpu ?? "—"}% / {detail.metrics.memory ?? "—"}%
              </dd>
            </div>
            <div>
              <dt>DISK FREE / UPTIME</dt>
              <dd>
                {detail.metrics.disk_free ?? "—"}% /{" "}
                {detail.metrics.uptime_hours ?? "—"} h
              </dd>
            </div>
          </dl>
          <dl className="details">
            <div>
              <dt>CPU MODEL</dt>
              <dd>{detail.hardware?.cpu_model || "—"}</dd>
            </div>
            <div>
              <dt>LOGICAL CPUS / RAM</dt>
              <dd>
                {detail.hardware?.logical_cpus ?? "—"} /{" "}
                {detail.hardware?.memory_gib ?? "—"} GiB
              </dd>
            </div>
            <div>
              <dt>STORAGE / DISKS</dt>
              <dd>
                {detail.hardware?.storage_gib ?? "—"} GiB /{" "}
                {detail.hardware?.disk_count ?? "—"}
              </dd>
            </div>
            <div>
              <dt>GPU</dt>
              <dd>{detail.hardware?.gpu_models?.join(", ") || "—"}</dd>
            </div>
            <div>
              <dt>HARDWARE VERIFIED</dt>
              <dd>
                {detail.hardware_checked_at
                  ? date(detail.hardware_checked_at)
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>MISMATCH DETECTED</dt>
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
          {admin && (
            <form onSubmit={savePC}>
              <label>
                Hostname
                <input
                  name="hostname"
                  defaultValue={detail.hostname}
                  maxLength={253}
                />
              </label>
              <label>
                MAC address
                <input
                  name="mac"
                  defaultValue={detail.mac}
                  placeholder="00:11:22:33:44:55"
                />
              </label>
              <button className="secondary small" disabled={busy}>
                Save computer
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
                    pending.has(detail.id) || (a === "wake" && !detail.mac)
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
      {editUser && (
        <Modal
          title={editUser.id ? "Edit user" : "Add user"}
          close={() => setEditUser(null)}
          error={error}
          wide
        >
          <form onSubmit={saveUser}>
            {session.auth_mode === "entra" && (
              <label>
                Entra object ID
                <input
                  name="oid"
                  required
                  readOnly={Boolean(editUser.id)}
                  defaultValue={editUser.id.split(":")[1] || ""}
                />
              </label>
            )}
            <label>
              Name
              <input
                name="name"
                required
                maxLength={100}
                defaultValue={editUser.name}
              />
            </label>
            <label>
              Email
              <input
                name="email"
                type="email"
                required
                maxLength={254}
                pattern="[^@]+@fiap\.com\.br"
                defaultValue={editUser.email}
                placeholder="name@fiap.com.br"
              />
            </label>
            {session.auth_mode === "local" && (
              <label>
                {editUser.id ? "Reset password" : "Password"}
                <input
                  name="password"
                  type="password"
                  autoComplete="new-password"
                  minLength={15}
                  maxLength={128}
                  required={!editUser.id}
                />
                <small>
                  {editUser.id
                    ? "Leave empty to keep the current password. Minimum 15 characters."
                    : "Minimum 15 characters."}
                </small>
              </label>
            )}
            <label>
              Role
              <select name="role" defaultValue={editUser.role}>
                <option value="monitor">Monitor</option>
                <option value="viewer">Observer</option>
                <option value="admin">Administrator</option>
              </select>
            </label>
            <fieldset>
              <legend>Floors</legend>
              <div className="floor-options">
                {session.floors.map((f) => (
                  <label key={f}>
                    <input
                      type="checkbox"
                      name="floor"
                      value={f}
                      defaultChecked={editUser.grants.floors?.includes(f)}
                    />
                    {f}
                  </label>
                ))}
              </div>
            </fieldset>
            <label>
              Additional laboratories
              <input
                name="labs"
                defaultValue={editUser.grants.labs?.join(", ")}
                placeholder="301, 302"
              />
            </label>
            <label>
              Additional computers
              <input
                name="computers"
                defaultValue={editUser.grants.computers?.join(", ")}
                placeholder="301-1, 301-100"
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                name="enabled"
                defaultChecked={editUser.enabled}
              />
              Enabled
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setEditUser(null)}
              >
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {passwordDialog && (
        <Modal
          title="Change password"
          close={() => setPasswordDialog(false)}
          error={error}
        >
          <form onSubmit={changePassword}>
            <label>
              Current password
              <input
                name="current_password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
              />
            </label>
            <label>
              New password
              <input
                name="new_password"
                type="password"
                autoComplete="new-password"
                required
                minLength={15}
                maxLength={128}
              />
              <small>Minimum 15 characters.</small>
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setPasswordDialog(false)}
              >
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {newLab && (
        <Modal
          title="Add laboratory"
          close={() => setNewLab(false)}
          error={error}
        >
          <form onSubmit={saveLab}>
            <div className="form-row">
              <label>
                Floor
                <select name="floor" defaultValue={floor || 1}>
                  {session.floors.map((f) => (
                    <option key={f}>{f}</option>
                  ))}
                </select>
              </label>
              <label>
                Laboratory number
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
              Laboratory name
              <input
                name="label"
                required
                placeholder="Lab 301"
                maxLength={80}
              />
            </label>
            <label>
              Student computers
              <input
                name="students"
                type="number"
                min="0"
                max="99"
                defaultValue="30"
                required
              />
            </label>
            <label className="checkbox-label">
              <input name="professor" type="checkbox" defaultChecked />
              Include professor workstation (.100)
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setNewLab(false)}
              >
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                Add laboratory
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
              <small>{j.hostname || j.computer_id}</small>
            </div>
            <span className="job-message">
              {j.message || "Awaiting controller"}
            </span>
            <span className={`job-state ${j.state}`}>{labels[j.state]}</span>
            <span className="job-time">{time(j.created)}</span>
          </div>
        ))
      ) : (
        <div className="empty compact">
          <Clock3 size={20} />
          <span>No commands yet.</span>
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
            aria-label="Close dialog"
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

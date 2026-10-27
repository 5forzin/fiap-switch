import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { Api, Hardware } from "./inventory";

type Summary = {
  total: number;
  online: number;
  unreachable: number;
  unknown: number;
  hardware_mismatches: number;
};
type Day = { day: string; summary: Summary };
type Run = {
  id: string;
  created: number;
  state: string;
  message: string;
  summary: Summary;
};
type Result = {
  computer_id: string;
  hostname: string;
  ip: string;
  lab_id: string;
  floor: number;
  status: string;
  checked_at: number | null;
  last_online: number | null;
  issue: string;
  hardware: Hardware;
  hardware_state: string;
  hardware_differences: string[];
  online_checks?: number;
};
type Page = {
  computers: Result[];
  summary: Summary;
  total: number;
  page: number;
  state?: string;
  message?: string;
};
const when = (value?: number | null) =>
  value ? new Date(value * 1000).toLocaleString() : "—";
const stateLabel: Record<string, string> = {
  online: "Online",
  unreachable: "No response",
  unknown: "Not verified",
  queued: "Checking",
  completed: "Completed",
  failed: "Failed",
  match: "Matches",
  mismatch: "Mismatch",
  unconfigured: "No standard",
};

export function ChecksPanel({
  api,
  floors,
  initialFloor,
  canRun,
  onError,
}: {
  api: Api;
  floors: number[];
  initialFloor: number | null;
  canRun: boolean;
  onError: (error: string) => void;
}) {
  const apiRef = useRef(api);
  apiRef.current = api;
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const [floor, setFloor] = useState(
    initialFloor === null ? "all" : String(initialFloor),
  );
  const [mode, setMode] = useState<"daily" | "runs">("daily");
  const [days, setDays] = useState<Day[]>([]),
    [runs, setRuns] = useState<Run[]>([]);
  const [day, setDay] = useState(""),
    [run, setRun] = useState(""),
    [status, setStatus] = useState("all"),
    [page, setPage] = useState(1);
  const [result, setResult] = useState<Page | null>(null),
    [busy, setBusy] = useState(false);
  const scope = floor === "all" ? "" : `floor=${floor}`;
  useEffect(() => {
    setPage(1);
    setResult(null);
  }, [floor, day, run, mode, status]);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const [dates, checks] = await Promise.all([
          apiRef.current<Day[]>(`/api/history?${scope}`),
          apiRef.current<Run[]>(`/api/checks?${scope}`),
        ]);
        if (!active) return;
        setDays(dates);
        setRuns(checks);
        const chosenDay = dates.some((d) => d.day === day)
          ? day
          : dates[0]?.day || "";
        const chosenRun = checks.some((c) => c.id === run)
          ? run
          : checks[0]?.id || "";
        if (chosenDay !== day) setDay(chosenDay);
        if (chosenRun !== run) setRun(chosenRun);
        const path =
          mode === "daily"
            ? chosenDay
              ? `/api/history/${chosenDay}`
              : ""
            : chosenRun
              ? `/api/checks/${chosenRun}`
              : "";
        if (path) {
          const next = await apiRef.current<Page>(
            `${path}?${scope}&status=${status}&page=${page}&size=50`,
          );
          if (active) setResult(next);
        } else setResult(null);
      } catch (e) {
        if (active) errorRef.current((e as Error).message);
      }
    };
    refresh();
    const id = setInterval(refresh, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [scope, day, run, mode, status, page]);
  async function check() {
    setBusy(true);
    try {
      const next = await apiRef.current<{ id: string }>(
        "/api/checks",
        "POST",
        floor === "all" ? {} : { floor: Number(floor) },
      );
      setMode("runs");
      setRun(next.id);
      setPage(1);
      setStatus("all");
    } catch (e) {
      errorRef.current((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Floor verification</h2>
        {canRun && (
          <button className="primary" disabled={busy} onClick={check}>
            <RefreshCw size={15} />
            {busy
              ? "Requesting…"
              : floor === "all"
                ? "Check my floors"
                : `Check floor ${floor}`}
          </button>
        )}
      </div>
      <div className="history-tabs">
        <button
          className={mode === "daily" ? "active" : ""}
          onClick={() => setMode("daily")}
        >
          Daily history
        </button>
        <button
          className={mode === "runs" ? "active" : ""}
          onClick={() => setMode("runs")}
        >
          Verifications
        </button>
      </div>
      <div className="filters history-filters">
        <label>
          Floor
          <select
            aria-label="History floor"
            value={floor}
            onChange={(e) => setFloor(e.target.value)}
          >
            <option value="all">All assigned floors</option>
            {floors.map((f) => (
              <option key={f} value={f}>
                Floor {f}
              </option>
            ))}
          </select>
        </label>
        {mode === "daily" ? (
          <label>
            Date
            <select
              aria-label="History date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
            >
              {!days.length && <option value="">No history</option>}
              {days.map((d) => (
                <option key={d.day} value={d.day}>
                  {d.day}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            Verification
            <select
              aria-label="Verification"
              value={run}
              onChange={(e) => setRun(e.target.value)}
            >
              {!runs.length && <option value="">No verifications</option>}
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {when(r.created)} · {stateLabel[r.state]}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Status
          <select
            aria-label="History status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="online">Online</option>
            <option value="unreachable">No response</option>
            <option value="unknown">Not verified</option>
          </select>
        </label>
      </div>
      {result ? (
        <>
          <div className="check-summary">
            <span>{result.summary.total} computers</span>
            <span>{result.summary.online} online</span>
            <span>{result.summary.unreachable} no response</span>
            <span>{result.summary.unknown} unverified</span>
            <span
              className={
                result.summary.hardware_mismatches ? "attention-text" : ""
              }
            >
              {result.summary.hardware_mismatches} hardware mismatches
            </span>
            {mode === "runs" && (
              <strong>{stateLabel[result.state || "unknown"]}</strong>
            )}
          </div>
          {result.message && <div className="issue-note">{result.message}</div>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>COMPUTER</th>
                  <th>FLOOR / LAB</th>
                  <th>STATUS</th>
                  <th>LAST CONFIRMED ONLINE</th>
                  <th>HARDWARE</th>
                  <th>CHECKED</th>
                </tr>
              </thead>
              <tbody>
                {result.computers.map((pc) => (
                  <tr key={pc.computer_id}>
                    <td>
                      <strong>{pc.hostname || pc.computer_id}</strong>
                      <small className="mono">{pc.ip}</small>
                    </td>
                    <td>
                      {pc.floor} / {pc.lab_id}
                    </td>
                    <td>
                      <span className={`status ${pc.status}`}>
                        {stateLabel[pc.status]}
                      </span>
                      {pc.issue && (
                        <small className="wrap-text">{pc.issue}</small>
                      )}
                    </td>
                    <td>{when(pc.last_online)}</td>
                    <td>
                      <span
                        className={`status ${pc.hardware_state === "mismatch" ? "unreachable" : pc.hardware_state === "match" ? "online" : "unknown"}`}
                      >
                        {stateLabel[pc.hardware_state] || "Not verified"}
                      </span>
                      {pc.hardware_differences.map((d) => (
                        <small className="wrap-text" key={d}>
                          {d.replaceAll("_", " ")}
                        </small>
                      ))}
                    </td>
                    <td>{when(pc.checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!result.computers.length && (
              <div className="empty">No computers match this status.</div>
            )}
          </div>
          <div className="table-footer">
            <span>{result.total} results</span>
            {result.total > 50 && (
              <div className="pagination">
                <button
                  className="secondary small"
                  disabled={page === 1}
                  onClick={() => setPage(page - 1)}
                >
                  Previous
                </button>
                <span>
                  Page {page} of {Math.ceil(result.total / 50)}
                </span>
                <button
                  className="secondary small"
                  disabled={page * 50 >= result.total}
                  onClick={() => setPage(page + 1)}
                >
                  Next
                </button>
              </div>
            )}
          </div>
          <p className="history-note">
            No response may indicate a network or WinRM failure. Daily history
            keeps each computer's latest result and last confirmed online time.
          </p>
        </>
      ) : (
        <div className="empty">
          {mode === "daily"
            ? "No observations recorded for this floor."
            : "Run a floor verification to create a saved result."}
        </div>
      )}
    </section>
  );
}

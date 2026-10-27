import { useState } from "react";
import type { Hardware, HardwareStandard } from "./inventory";

export function HardwareStandardForm({
  standard,
  sample,
  save,
  close,
}: {
  standard?: HardwareStandard | null;
  sample?: Hardware;
  save: (value: HardwareStandard) => Promise<void>;
  close: () => void;
}) {
  const [value, setValue] = useState<HardwareStandard>(
    standard || { enabled: true, match_mode: "exact" },
  );
  const [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await save(value);
        } finally {
          setBusy(false);
        }
      }}
    >
      {sample && (
        <button
          type="button"
          className="text-button"
          onClick={() =>
            setValue({
              enabled: true,
              match_mode: "exact",
              cpu_model: sample.cpu_model,
              logical_cpus: sample.logical_cpus,
              memory_gib: sample.memory_gib,
              storage_gib: sample.storage_gib,
              disk_count: sample.disk_count,
              gpu_model: sample.gpu_models?.[0],
            })
          }
        >
          Use detected hardware
        </button>
      )}
      <label>
        Comparison
        <select
          value={value.match_mode}
          onChange={(e) =>
            setValue({
              ...value,
              match_mode: e.target.value as HardwareStandard["match_mode"],
            })
          }
        >
          <option value="exact">Exact inventory</option>
          <option value="minimum">Minimum capacities</option>
        </select>
      </label>
      <label>
        CPU model contains
        <input
          maxLength={200}
          value={value.cpu_model || ""}
          onChange={(e) =>
            setValue({ ...value, cpu_model: e.target.value || null })
          }
        />
      </label>
      <div className="form-row">
        {(
          [
            ["logical_cpus", "Logical CPUs", 1, 4096],
            ["memory_gib", "RAM (GiB)", 0.5, 65536],
          ] as const
        ).map(([key, label, min, max]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              step={key === "memory_gib" ? "0.01" : "1"}
              min={min}
              max={max}
              value={value[key] ?? ""}
              onChange={(e) =>
                setValue({
                  ...value,
                  [key]: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </label>
        ))}
      </div>
      <div className="form-row">
        {(
          [
            ["storage_gib", "Storage (GiB)", 1, 1000000],
            ["disk_count", "Physical disks", 1, 100],
          ] as const
        ).map(([key, label, min, max]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              step={key === "storage_gib" ? "0.01" : "1"}
              min={min}
              max={max}
              value={value[key] ?? ""}
              onChange={(e) =>
                setValue({
                  ...value,
                  [key]: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </label>
        ))}
      </div>
      <label>
        GPU model contains
        <input
          maxLength={200}
          value={value.gpu_model || ""}
          onChange={(e) =>
            setValue({ ...value, gpu_model: e.target.value || null })
          }
        />
      </label>
      <small>
        Empty fields are ignored. RAM and storage allow a 5% capacity tolerance.
      </small>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(e) => setValue({ ...value, enabled: e.target.checked })}
        />
        Standard enabled
      </label>
      <div className="modal-actions">
        <button type="button" className="secondary" onClick={close}>
          Cancel
        </button>
        <button className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save standard"}
        </button>
      </div>
    </form>
  );
}

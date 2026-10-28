import { useState } from "react";
import type { Hardware, HardwareStandard } from "./inventory";

export function HardwareStandardForm({
  standard,
  references,
  save,
  close,
}: {
  standard?: HardwareStandard | null;
  references: { id: string; name: string; hardware: Hardware }[];
  save: (value: HardwareStandard) => Promise<void>;
  close: () => void;
}) {
  const [value, setValue] = useState<HardwareStandard>(
    standard || { enabled: true, match_mode: "exact" },
  );
  const [busy, setBusy] = useState(false);
  const [reference, setReference] = useState(references[0]?.id || "");
  const selectedReference = references.some((pc) => pc.id === reference)
    ? reference
    : references[0]?.id || "";
  const sample = references.find((pc) => pc.id === selectedReference)?.hardware;
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
      {references.length > 0 ? (
        <>
          <label>
            Computador de referência
            <select
              value={selectedReference}
              onChange={(e) => setReference(e.target.value)}
            >
              {references.map((pc) => (
                <option key={pc.id} value={pc.id}>
                  {pc.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="text-button"
            disabled={!sample}
            onClick={() =>
              sample &&
              setValue({
                enabled: true,
                match_mode: value.match_mode,
                cpu_model: sample.cpu_model,
                logical_cpus: sample.logical_cpus,
                memory_gib: sample.memory_gib,
                storage_gib: sample.storage_gib,
                disk_count: sample.disk_count,
                gpu_model: sample.gpu_models?.[0],
              })
            }
          >
            Copiar hardware do computador
          </button>
        </>
      ) : (
        <small>Nenhum computador conforme com leitura atual nesta sala.</small>
      )}
      <label>
        Comparação
        <select
          value={value.match_mode}
          onChange={(e) =>
            setValue({
              ...value,
              match_mode: e.target.value as HardwareStandard["match_mode"],
            })
          }
        >
          <option value="exact">Inventário exato</option>
          <option value="minimum">Capacidades mínimas</option>
        </select>
      </label>
      <label>
        Modelo de CPU contém
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
            ["logical_cpus", "CPUs lógicas", 1, 4096],
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
            ["storage_gib", "Armazenamento (GiB)", 1, 1000000],
            ["disk_count", "Discos físicos", 1, 100],
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
        Modelo de GPU contém
        <input
          maxLength={200}
          value={value.gpu_model || ""}
          onChange={(e) =>
            setValue({ ...value, gpu_model: e.target.value || null })
          }
        />
      </label>
      <small>
        Campos vazios são ignorados. RAM e armazenamento têm tolerância de 5%.
      </small>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(e) => setValue({ ...value, enabled: e.target.checked })}
        />
        Padrão ativo
      </label>
      <div className="modal-actions">
        <button type="button" className="secondary" onClick={close}>
          Cancelar
        </button>
        <button className="primary" disabled={busy}>
          {busy ? "Salvando…" : "Salvar padrão"}
        </button>
      </div>
    </form>
  );
}

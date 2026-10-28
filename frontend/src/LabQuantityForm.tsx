import { useState } from "react";
import { quantidade } from "./inventory";

export function QuantityFields({ value = 30 }: { value?: number | null }) {
  const [indefinido, setIndefinido] = useState(value === null);
  return (
    <>
      <label>
        Computadores de alunos
        <input
          name="students"
          type="number"
          min={0}
          max={99}
          defaultValue={value ?? 30}
          disabled={indefinido}
          required={!indefinido}
        />
      </label>
      <label className="checkbox-label">
        <input
          name="undefined_students"
          type="checkbox"
          checked={indefinido}
          onChange={(e) => setIndefinido(e.target.checked)}
        />
        Indefinido
      </label>
    </>
  );
}

export function LabQuantityForm({
  value,
  registered,
  save,
}: {
  value: number | null;
  registered: number;
  save: (value: number | null) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        const form = new FormData(e.currentTarget);
        setBusy(true);
        try {
          await save(
            form.get("undefined_students") === "on"
              ? null
              : Number(form.get("students")),
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <QuantityFields value={value} />
      <small>
        {quantidade(
          registered,
          "computador cadastrado",
          "computadores cadastrados",
        )}
        . A opção Indefinido preserva o inventário atual.
      </small>
      <div className="modal-actions">
        <button className="primary" disabled={busy}>
          {busy ? "Salvando…" : "Salvar quantidade"}
        </button>
      </div>
    </form>
  );
}

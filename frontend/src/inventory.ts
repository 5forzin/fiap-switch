export type Api = <T>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;
export type Hardware = {
  cpu_model?: string;
  logical_cpus?: number;
  memory_gib?: number;
  storage_gib?: number;
  disk_count?: number;
  gpu_models?: string[];
};
export type HardwareStandard = {
  enabled: boolean;
  match_mode: "exact" | "minimum";
  cpu_model?: string | null;
  logical_cpus?: number | null;
  memory_gib?: number | null;
  storage_gib?: number | null;
  disk_count?: number | null;
  gpu_model?: string | null;
};

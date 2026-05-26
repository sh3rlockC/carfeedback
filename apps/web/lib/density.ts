export type DensityMode = "comfortable" | "compact";

export const DENSITY_STORAGE_KEY = "koubei-density-mode";
export const defaultDensityMode: DensityMode = "comfortable";

export function readDensityMode(): DensityMode {
  if (typeof window === "undefined") {
    return defaultDensityMode;
  }
  const stored = window.localStorage.getItem(DENSITY_STORAGE_KEY);
  return stored === "compact" || stored === "comfortable" ? stored : defaultDensityMode;
}

export function writeDensityMode(mode: DensityMode) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(DENSITY_STORAGE_KEY, mode);
}

export function nextDensityMode(mode: DensityMode): DensityMode {
  return mode === "compact" ? "comfortable" : "compact";
}

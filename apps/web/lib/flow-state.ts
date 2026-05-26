import type {
  ComparisonOptionsResponse,
  ComparisonProgressResponse,
  ComparisonVehicleInput,
  JobProgressResponse,
  SelectedCandidates,
  VehicleResolveResponse,
} from "@/lib/api-types";

const FLOW_STATE_KEY = "koubei-demo-flow";

export type FlowState = {
  accessVersion: string | null;
  mode: "single" | "comparison" | null;
  vehicleQuery: string | null;
  vehicleResolve: VehicleResolveResponse | null;
  selectedCandidates: SelectedCandidates | null;
  jobId: string | null;
  jobProgress: JobProgressResponse | null;
  comparisonId: string | null;
  comparisonOptions: ComparisonOptionsResponse | null;
  comparisonVehicles: ComparisonVehicleInput[] | null;
  comparisonProgress: ComparisonProgressResponse | null;
};

const initialState: FlowState = {
  accessVersion: null,
  mode: null,
  vehicleQuery: null,
  vehicleResolve: null,
  selectedCandidates: null,
  jobId: null,
  jobProgress: null,
  comparisonId: null,
  comparisonOptions: null,
  comparisonVehicles: null,
  comparisonProgress: null,
};

function readState(): FlowState {
  if (typeof window === "undefined") {
    return initialState;
  }

  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(FLOW_STATE_KEY);
  } catch {
    return initialState;
  }
  if (!raw) {
    return initialState;
  }

  try {
    return { ...initialState, ...(JSON.parse(raw) as Partial<FlowState>) };
  } catch {
    return initialState;
  }
}

function writeState(state: FlowState) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.setItem(FLOW_STATE_KEY, JSON.stringify(state));
  } catch {
    // Storage can be unavailable in restricted browser contexts; keep the UI usable without persistence.
  }
}

export function getFlowState() {
  return readState();
}

export function setFlowState(patch: Partial<FlowState>) {
  writeState({ ...readState(), ...patch });
}

export function clearFlowState() {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.removeItem(FLOW_STATE_KEY);
  } catch {
    // Ignore storage failures so compatibility routes can still render their guard states.
  }
}

import { withBasePath } from "./paths";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type JsonValue = Record<string, unknown> | unknown[];

function getErrorMessage(payload: unknown, fallback: string) {
  if (typeof payload === "string") {
    return payload;
  }

  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }

  return fallback;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(withBasePath(path), {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    throw new ApiError(response.status, getErrorMessage(payload, response.statusText));
  }

  if (response.status === 204) {
    return null as T;
  }

  return (await response.json()) as T;
}

export function toJsonBody(value: JsonValue) {
  return JSON.stringify(value);
}

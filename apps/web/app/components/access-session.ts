"use client";

import { useEffect, useState } from "react";
import { ApiError, apiRequest, toJsonBody } from "@/lib/api";
import type { AccessVerifyResponse, AdminRuntimeInfoResponse } from "@/lib/api-types";
import { ACCESS_CONTROL_ENABLED_DEFAULT } from "@/lib/access-config";

const ACCESS_VERSION_KEY = "koubei-access-version";

function readAccessVersion() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.sessionStorage.getItem(ACCESS_VERSION_KEY);
}

function writeAccessVersion(version: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(ACCESS_VERSION_KEY, version);
}

function clearAccessVersion() {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(ACCESS_VERSION_KEY);
}

export type AccessState = "checking" | "authorized" | "unauthorized";

export type AccessSessionController = {
  accessState: AccessState;
  accessVersion: string | null;
  accessControlEnabled: boolean;
  runtime: AdminRuntimeInfoResponse | null;
  error: string;
  authorize: (passphrase: string) => Promise<AccessVerifyResponse>;
  refresh: () => Promise<boolean>;
  reset: () => void;
};

export function useAccessSession(): AccessSessionController {
  const [accessState, setAccessState] = useState<AccessState>(ACCESS_CONTROL_ENABLED_DEFAULT ? "checking" : "authorized");
  const [accessVersion, setAccessVersion] = useState<string | null>(() => readAccessVersion());
  const [accessControlEnabled, setAccessControlEnabled] = useState(ACCESS_CONTROL_ENABLED_DEFAULT);
  const [runtime, setRuntime] = useState<AdminRuntimeInfoResponse | null>(null);
  const [error, setError] = useState("");

  const reset = () => {
    clearAccessVersion();
    setAccessVersion(null);
    setAccessControlEnabled(true);
    setRuntime(null);
    setError("");
    setAccessState("unauthorized");
  };

  const refresh = async () => {
    try {
      const payload = await apiRequest<AdminRuntimeInfoResponse>("/api/admin/runtime");
      writeAccessVersion(payload.passphrase_version);
      setAccessVersion(payload.passphrase_version);
      setAccessControlEnabled(payload.access_control_enabled);
      setRuntime(payload);
      setError("");
      setAccessState("authorized");
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        reset();
        return false;
      }
      setRuntime(null);
      setError(err instanceof Error ? err.message : "无法校验当前访问状态。");
        setAccessState(readAccessVersion() ? "authorized" : "unauthorized");
      return false;
    }
  };

  useEffect(() => {
    let cancelled = false;

    const sync = async () => {
      setAccessState("checking");
      try {
        const payload = await apiRequest<AdminRuntimeInfoResponse>("/api/admin/runtime");
        if (cancelled) {
          return;
        }
        writeAccessVersion(payload.passphrase_version);
        setAccessVersion(payload.passphrase_version);
        setAccessControlEnabled(payload.access_control_enabled);
        setRuntime(payload);
        setError("");
        setAccessState("authorized");
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          reset();
          return;
        }
        setRuntime(null);
        setError(err instanceof Error ? err.message : "无法校验当前访问状态。");
        setAccessState(readAccessVersion() ? "authorized" : "unauthorized");
      }
    };

    void sync();
    return () => {
      cancelled = true;
    };
  }, []);

  const authorize = async (passphrase: string) => {
    const payload = await apiRequest<AccessVerifyResponse>("/api/access/verify", {
      method: "POST",
      body: toJsonBody({ passphrase }),
    });
    writeAccessVersion(payload.passphrase_version);
    setAccessVersion(payload.passphrase_version);
    setAccessState("authorized");
    setError("");
    await refresh();
    return payload;
  };

  return {
    accessState,
    accessVersion,
    accessControlEnabled,
    runtime,
    error,
    authorize,
    refresh,
    reset,
  };
}

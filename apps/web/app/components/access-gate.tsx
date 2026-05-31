"use client";

import { KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import type { FormEvent } from "react";
import { useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import type { AccessSessionController } from "./access-session";

type AccessGatePanelProps = {
  session: AccessSessionController;
  title?: string;
  description?: string;
  compact?: boolean;
  className?: string;
};

export function AccessGatePanel({
  session,
  title = "访问授权",
  description = "输入本周口令后，当前浏览器会话将解锁任务查询、创建和下载能力。",
  compact = false,
  className = "",
}: AccessGatePanelProps) {
  const [passphrase, setPassphrase] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  if (!session.accessControlEnabled) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = passphrase.trim();
    if (!trimmed) {
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      await session.authorize(trimmed);
      setPassphrase("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "口令校验失败。");
    } finally {
      setSubmitting(false);
    }
  }

  if (session.accessState === "authorized") {
    return (
      <SignalPanel className={`access-gate access-gate-success ${className}`.trim()}>
        <div className="access-gate-head">
          <div>
            <p className="eyebrow">ACCESS</p>
            <h3>{title}</h3>
          </div>
          <StatusPill tone="success">已解锁</StatusPill>
        </div>
        <div className="access-gate-meta">
          <span>
            <ShieldCheck size={16} />
            当前口令版本 {session.runtime?.passphrase_version ?? session.accessVersion ?? "已同步"}
          </span>
          {session.runtime ? <span>环境 {session.runtime.app_env}</span> : null}
        </div>
      </SignalPanel>
    );
  }

  return (
    <SignalPanel className={`access-gate ${compact ? "access-gate-compact" : ""} ${className}`.trim()} tone="warning">
      <div className="access-gate-head">
        <SectionHeader eyebrow="ACCESS" title={title} copy={description} />
        <StatusPill tone={session.accessState === "checking" ? "accent" : "warning"}>
          {session.accessState === "checking" ? "校验中" : "待授权"}
        </StatusPill>
      </div>

      <form className="access-gate-form" onSubmit={handleSubmit}>
        <label className="field">
          <span>访问口令</span>
          <input
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
            placeholder="输入本周口令"
            autoComplete="off"
            disabled={session.accessState === "checking" || submitting}
          />
        </label>
        <button className="button" type="submit" disabled={session.accessState === "checking" || submitting || !passphrase.trim()}>
          <KeyRound size={16} />
          {submitting ? "正在校验" : "解锁工作台"}
        </button>
        <button
          className="button secondary"
          type="button"
          onClick={() => void session.refresh()}
          disabled={session.accessState === "checking" || submitting}
        >
          <RefreshCw size={16} />
          重新检查
        </button>
      </form>

      {error ? <p className="error">{error}</p> : null}
      {session.error ? <p className="error">{session.error}</p> : null}
    </SignalPanel>
  );
}

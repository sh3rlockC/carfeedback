"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";

export default function PassphrasePage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/");
  }, [router]);

  return (
    <main className="terminal-grid">
      <SignalPanel tone="accent" className="stack-lg">
        <SectionHeader
          eyebrow="LEGACY ACCESS"
          title="兼容访问入口"
          copy="当前工作台默认直接进入；系统会自动跳转到总览页。"
        />

        <div className="actions">
          <button className="button" type="button" onClick={() => router.replace("/")}>
            进入工作台
          </button>
          <button className="button secondary" type="button" onClick={() => router.replace("/tasks/new")}>
            新建任务
          </button>
        </div>
      </SignalPanel>

      <aside className="terminal-window stack">
        <div className="meta-row">
          <StatusPill>链接访问</StatusPill>
          <StatusPill tone="success">无账号</StatusPill>
          <StatusPill tone="accent">直接进入</StatusPill>
        </div>
        <div className="terminal-line">
          <span>入口模式</span>
          <strong>外部网页链接</strong>
        </div>
        <div className="terminal-line">
          <span>授权范围</span>
          <strong>当前浏览器会话</strong>
        </div>
        <div className="terminal-line">
          <span>下一步</span>
          <strong>进入工作台</strong>
        </div>
        <p className="status-copy">当前工作台已支持直接创建增量采集任务；旧入口会自动并入新工作台。</p>
      </aside>
    </main>
  );
}

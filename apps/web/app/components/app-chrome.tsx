"use client";

import { LayoutDashboard, Rows3, Shrink, SquarePen, StretchHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import type { AdminRuntimeInfoResponse } from "@/lib/api-types";
import { ACCESS_CONTROL_ENABLED_DEFAULT } from "@/lib/access-config";

type DensityMode = "comfortable" | "compact";

export function AppChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [runtime, setRuntime] = useState<AdminRuntimeInfoResponse | null>(null);
  const [density, setDensity] = useState<DensityMode>("comfortable");

  useEffect(() => {
    const storedDensity = window.localStorage.getItem("koubei-density");
    if (storedDensity === "compact" || storedDensity === "comfortable") {
      setDensity(storedDensity);
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.density = density;
    window.localStorage.setItem("koubei-density", density);
  }, [density]);

  useEffect(() => {
    if (runtime) {
      return;
    }
    let cancelled = false;
    apiRequest<AdminRuntimeInfoResponse>("/api/admin/runtime")
      .then((payload) => {
        if (!cancelled) {
          setRuntime(payload);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRuntime(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runtime]);

  const accessControlEnabled = runtime?.access_control_enabled ?? ACCESS_CONTROL_ENABLED_DEFAULT;

  const navItems = [
    {
      href: "/tasks",
      label: "工作台",
      copy: "运行状态、负载和历史任务",
      icon: LayoutDashboard,
      active: pathname === "/tasks" || pathname === "/",
    },
    {
      href: "/tasks/new",
      label: "新建任务",
      copy: "车型搜索、seriesId 确认和创建",
      icon: SquarePen,
      active: pathname?.startsWith("/tasks/new") ?? false,
    },
    {
      href: "/tasks",
      label: "任务中心",
      copy: "筛选任务并进入结果交付页",
      icon: Rows3,
      active: pathname?.startsWith("/tasks/") ?? false,
    },
  ];

  return (
    <div className="app-shell app-shell-workbench">
      <aside className="sidebar-nav" aria-label="主导航">
        <div className="sidebar-brand">
          <p className="eyebrow">VEHICLE FEEDBACK WORKBENCH</p>
          <h1>车型口碑工作台</h1>
          <p>增量采集、双平台口碑抓取、seriesId 确认和结果交付统一在一个入口完成。</p>
        </div>

        <nav className="sidebar-links">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={`${item.href}-${item.label}`} className={`sidebar-link ${item.active ? "active" : ""}`.trim()} href={item.href}>
                <Icon size={17} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.copy}</small>
                </span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-controls">
          <p className="sidebar-label">界面密度</p>
          <div className="density-toggle" role="group" aria-label="界面密度">
            <button className={density === "comfortable" ? "active" : ""} type="button" onClick={() => setDensity("comfortable")}>
              <StretchHorizontal size={15} />
              标准
            </button>
            <button className={density === "compact" ? "active" : ""} type="button" onClick={() => setDensity("compact")}>
              <Shrink size={15} />
              紧凑
            </button>
          </div>
        </div>

        <div className="sidebar-runtime">
          <span>环境</span>
          <strong>{runtime?.app_env ?? "待同步"}</strong>
          <span>访问方式</span>
          <strong>{accessControlEnabled ? (runtime ? "周口令" : "待同步") : "免口令"}</strong>
        </div>
      </aside>

      <div className="workbench-main">
        <header className="status-bar">
          <div className="status-bar-copy">
            <p className="eyebrow">WORKBENCH STATUS</p>
            <h2>{pathname?.startsWith("/tasks/new") ? "创建任务" : "任务调度面板"}</h2>
            <p>任务查询、创建和结果交付都在当前工作台完成。</p>
          </div>

          <div className="mission-status" aria-label="当前任务状态">
            <div>
              <span>当前入口</span>
              <strong>{pathname?.startsWith("/tasks/new") ? "新建任务" : "任务中心"}</strong>
            </div>
            <div>
              <span>任务类型</span>
              <strong>单车型 / 多车型</strong>
            </div>
            <div>
              <span>结果交付</span>
              <strong>任务详情页</strong>
            </div>
            <div>
              <span>访问状态</span>
              <strong>{accessControlEnabled ? "周口令" : "开放访问"}</strong>
            </div>
            <div>
              <span>运行队列</span>
              <strong>{runtime?.worker_queue_name ?? "待同步"}</strong>
            </div>
          </div>
        </header>

        <div className="content-stage">{children}</div>
      </div>
    </div>
  );
}

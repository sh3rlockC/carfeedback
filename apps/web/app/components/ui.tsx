import Link from "next/link";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { withoutBasePath } from "@/lib/paths";

export type StepKey = "passphrase" | "vehicle" | "candidates" | "progress" | "result";

export type StepItem = {
  key: StepKey;
  label: string;
  eyebrow: string;
  href: string;
};

export const flowSteps: StepItem[] = [
  { key: "passphrase", label: "口令", eyebrow: "01", href: "/passphrase" },
  { key: "vehicle", label: "车型", eyebrow: "02", href: "/vehicle" },
  { key: "candidates", label: "车系", eyebrow: "03", href: "/candidates" },
  { key: "progress", label: "采集", eyebrow: "04", href: "/progress" },
  { key: "result", label: "洞察", eyebrow: "05", href: "/result" },
];

export function stepForPath(pathname: string | null): StepKey {
  const normalizedPathname = withoutBasePath(pathname);

  if (normalizedPathname?.startsWith("/vehicle")) {
    return "vehicle";
  }
  if (normalizedPathname?.startsWith("/candidates")) {
    return "candidates";
  }
  if (normalizedPathname?.startsWith("/progress")) {
    return "progress";
  }
  if (normalizedPathname?.startsWith("/result")) {
    return "result";
  }
  return "passphrase";
}

type SignalPanelProps = ComponentPropsWithoutRef<"section"> & {
  tone?: "default" | "success" | "warning" | "danger" | "accent";
};

export function SignalPanel({
  children,
  className = "",
  tone = "default",
  ...sectionProps
}: SignalPanelProps) {
  return (
    <section className={`signal-panel signal-panel-${tone} ${className}`.trim()} {...sectionProps}>
      {children}
    </section>
  );
}

export function SectionHeader({
  eyebrow,
  title,
  copy,
}: {
  eyebrow: string;
  title: string;
  copy?: string;
}) {
  return (
    <div className="section-header">
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      {copy ? <p className="helper">{copy}</p> : null}
    </div>
  );
}

export function StatusPill({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: "default" | "success" | "warning" | "danger" | "accent";
}) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function StepRail({ activeStep }: { activeStep: StepKey }) {
  const activeIndex = flowSteps.findIndex((step) => step.key === activeStep);

  return (
    <nav className="step-rail" aria-label="任务流程">
      {flowSteps.map((step, index) => {
        const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "pending";
        return (
          <Link key={step.key} className={`step-node step-${state}`} href={step.href}>
            <span>{step.eyebrow}</span>
            <strong>{step.label}</strong>
          </Link>
        );
      })}
    </nav>
  );
}

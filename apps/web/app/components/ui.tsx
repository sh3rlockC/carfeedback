import type { ComponentPropsWithoutRef, ReactNode } from "react";

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

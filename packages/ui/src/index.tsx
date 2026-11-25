import * as RD from "@radix-ui/react-dialog";
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";

import s from "./ui.module.css";

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(" ");

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

export function Button({
  variant = "default",
  size = "md",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "danger-solid";
  size?: "md" | "sm";
}) {
  return (
    <button
      type="button"
      className={cx(
        s.button,
        variant === "primary" && s.primary,
        variant === "danger" && s.danger,
        variant === "danger-solid" && s.dangerSolid,
        size === "sm" && s.small,
        className,
      )}
      {...rest}
    />
  );
}

export function Panel({ title, children, className }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx(s.panel, className)}>
      {title ? <h2 className={s.panelTitle}>{title}</h2> : null}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className={s.stat}>
      <span className={s.statLabel}>{label}</span>
      <span className={cx(s.statValue, tone && tone !== "neutral" && s[`tone-${tone}`])}>{value}</span>
      {hint ? <span className={s.statHint}>{hint}</span> : null}
    </div>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cx(s.badge, tone !== "neutral" && s[`tone-${tone}`])}>{children}</span>;
}

export function Skeleton({ width = "100%", height = 16, style }: { width?: CSSProperties["width"]; height?: number; style?: CSSProperties }) {
  return <span aria-hidden className={s.skeleton} style={{ width, height, ...style }} />;
}

/** Accessible confirm dialog (focus trap, Esc closes, labelled) on Radix. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  children,
  confirmLabel,
  onConfirm,
  busy = false,
  danger = false,
  confirmDisabled = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  busy?: boolean;
  danger?: boolean;
  confirmDisabled?: boolean;
}) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className={s.overlay} />
        <RD.Content className={s.dialog}>
          <RD.Title className={s.dialogTitle}>{title}</RD.Title>
          <RD.Description asChild>
            <div className={s.dialogBody}>{children}</div>
          </RD.Description>
          <div className={s.dialogActions}>
            <RD.Close asChild>
              <Button>Cancel</Button>
            </RD.Close>
            <Button
              variant={danger ? "danger-solid" : "primary"}
              onClick={onConfirm}
              disabled={busy || confirmDisabled}
            >
              {busy ? "Working…" : confirmLabel}
            </Button>
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

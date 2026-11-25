// The states every screen implements (doc 07 §9): loading, empty, error, full-screen engine states.
import { ApiError } from "@imagevault/api-client";
import { Button, Skeleton, type Tone } from "@imagevault/ui";
import type { ReactNode } from "react";

import { useAppPaths } from "../paths";

export function FullScreen({
  title,
  detail,
  action,
  busy,
  tone,
}: {
  title: string;
  detail?: string | undefined;
  action?: { label: string; onClick: () => void };
  busy?: boolean;
  tone?: Tone;
}) {
  return (
    <div className="fullscreen" role={tone === "danger" ? "alert" : "status"}>
      {busy ? <div className="spinner" aria-hidden /> : null}
      <h1 className={tone === "danger" ? "text-danger" : undefined}>{title}</h1>
      {detail ? <p className="muted pre">{detail}</p> : null}
      {action ? (
        <Button variant="primary" onClick={action.onClick}>
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}

export function LoadingRows({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div className="stack" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={18} width={`${90 - i * 12}%`} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children ? <p className="muted">{children}</p> : null}
      {action}
    </div>
  );
}

/** An engine error with what to do next: retry, and where the log is. */
export function ErrorView({ error, onRetry, what }: { error: unknown; onRetry?: () => void; what: string }) {
  const paths = useAppPaths();
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
  const offline = error instanceof ApiError && error.code === "offline";
  return (
    <div className="error-view" role="alert">
      <strong>{offline ? "The engine is not responding." : `Could not load ${what}.`}</strong>
      <span className="muted">{detail}</span>
      {paths?.logs ? <span className="muted small">Log files: {paths.logs}</span> : null}
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

/** Inline result of an action (add folder, retry…): error or confirmation. */
export function Notice({ tone, children, onClose }: { tone: "ok" | "danger"; children: ReactNode; onClose?: () => void }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <span>{children}</span>
      {onClose ? (
        <button type="button" className="link" onClick={onClose} aria-label="Dismiss">
          ✕
        </button>
      ) : null}
    </div>
  );
}

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e);
}

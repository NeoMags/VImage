import { useHealth, useSaveScanOptions, useScanOptions } from "@imagevault/api-client";
import { Button, Panel } from "@imagevault/ui";

import { ErrorView, errorText, LoadingRows } from "../components/States";
import { revealInExplorer } from "../native";
import { useAppPaths } from "../paths";
import { type ThemeChoice, useTheme } from "../theme";

export function Settings() {
  const paths = useAppPaths();
  const [theme, setTheme] = useTheme();
  return (
    <>
      <h1 className="screen-title">Settings</h1>

      <Panel title="Library">
        <dl className="kv">
          <dt>Library folder</dt>
          <dd>
            <span className="mono small">{paths?.library ?? "–"}</span>
            {paths?.library ? (
              <Button size="sm" onClick={() => void revealInExplorer(paths.library!)}>
                Show in Explorer
              </Button>
            ) : null}
          </dd>
          <dt>Log files</dt>
          <dd>
            <span className="mono small">{paths?.logs ?? "–"}</span>
            {paths?.logs ? (
              <Button size="sm" onClick={() => void revealInExplorer(paths.logs!)}>
                Show in Explorer
              </Button>
            ) : null}
          </dd>
        </dl>
        <p className="muted small">Moving the library to another folder comes in a later version.</p>
      </Panel>

      <ScanSettings />

      <Panel title="Appearance">
        <fieldset className="radios">
          <legend className="sr-only">Theme</legend>
          {(
            [
              ["system", "Same as Windows"],
              ["light", "Light"],
              ["dark", "Dark"],
            ] as [ThemeChoice, string][]
          ).map(([value, label]) => (
            <label key={value} className="check">
              <input type="radio" name="theme" value={value} checked={theme === value} onChange={() => setTheme(value)} />
              {label}
            </label>
          ))}
        </fieldset>
      </Panel>

      <About />
    </>
  );
}

function ScanSettings() {
  const opts = useScanOptions();
  const save = useSaveScanOptions();
  return (
    <Panel title="Scanning">
      {opts.isPending ? (
        <LoadingRows rows={1} label="Loading scan settings" />
      ) : opts.isError ? (
        <ErrorView what="scan settings" error={opts.error} onRetry={() => void opts.refetch()} />
      ) : (
        <>
          <label className="check">
            <input
              type="checkbox"
              checked={opts.data.include_online_only ?? false}
              disabled={save.isPending}
              onChange={(e) => save.mutate({ ...opts.data, include_online_only: e.target.checked })}
            />
            Include cloud files that are not downloaded (OneDrive “online-only”)
          </label>
          <p className="muted small">
            Off by default: reading such a file makes Windows download it. Applies from the next scan.
          </p>
          {save.isError ? <p className="text-danger small">{errorText(save.error)}</p> : null}
        </>
      )}
    </Panel>
  );
}

/** Engine version and self-checks; fetched only when this screen is open (the checks take a moment). */
function About() {
  const health = useHealth();
  return (
    <Panel title="About">
      {health.isPending ? (
        <LoadingRows rows={2} label="Checking the engine" />
      ) : health.isError ? (
        <ErrorView what="engine details" error={health.error} onRetry={() => void health.refetch()} />
      ) : (
        <dl className="kv">
          <dt>Engine</dt>
          <dd>
            {health.data.version} · database v{health.data.db_version} · up {Math.round(health.data.uptime_s / 60)} min
          </dd>
          <dt>Self-checks</dt>
          <dd>
            {(["vips", "heif", "stages", "exiftool"] as const).map((k) => (
              <span key={k} className={health.data[k].ok ? "text-ok" : "text-danger"} title={String(health.data[k].error ?? "")}>
                {k} {health.data[k].ok ? "✓" : "✗"}{" "}
              </span>
            ))}
          </dd>
        </dl>
      )}
    </Panel>
  );
}

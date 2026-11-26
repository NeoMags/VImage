// Duplicates (D2). Copies (re-saved, resized, re-compressed) and Similar shots (bursts) found by
// the engine, side by side, suggested keeper first. Resolving HIDES the photos you do not keep:
// no file is ever deleted or moved, and hidden photos come back from the Hidden view.
import {
  type DuplicateTab,
  useDismissDuplicates,
  useDuplicateGroups,
  useDuplicateSummary,
  useRescanDuplicates,
  useResolveAllDuplicates,
  useResolveDuplicates,
} from "@imagevault/api-client";
import type { DuplicateGroup, DuplicateMember } from "@imagevault/shared-types";
import { Badge, Button, ConfirmDialog } from "@imagevault/ui";
import { useState } from "react";
import { useNavigate } from "react-router";

import { EmptyState, ErrorView, errorText, LoadingRows, Notice } from "../components/States";
import { bytes, num, plural } from "../format";
import { formatLabel, setFilters } from "../gallery/filters";
import { useThumbUrl } from "../gallery/thumbs";

type Tab = DuplicateTab | "folders";

const TAB_LABEL: Record<Tab, string> = { copies: "Copies", similar: "Similar shots", folders: "Same file in several folders" };
const TAB_HELP: Record<Tab, string> = {
  copies: "The same picture saved more than once: resized, re-compressed or converted.",
  similar: "Near-identical shots taken within a few minutes of each other, such as bursts.",
  folders: "Identical files in two or more places. They are already one photo in VImage, so there is nothing to tidy here.",
};

export function Duplicates() {
  const [tab, setTab] = useState<Tab>("copies");
  const summary = useDuplicateSummary();
  const rescan = useRescanDuplicates();
  const s = summary.data;
  const count = (t: Tab) => (s ? (t === "folders" ? s.multi_location : s[t]) : null);

  return (
    <div className="assets-screen dup-screen">
      <div className="screen-head">
        <h1 className="screen-title">Duplicates</h1>
        <Button size="sm" disabled={s?.scanning || rescan.isPending} onClick={() => rescan.mutate()}>
          {s?.scanning ? "Scanning…" : "Scan again"}
        </Button>
      </div>
      <p className="muted small">
        Nothing is deleted. Photos you don’t keep are hidden in VImage and can be shown again from the Hidden view.
      </p>
      {s?.scan_error ? <Notice tone="danger">The last scan failed: {s.scan_error}</Notice> : null}
      {rescan.isError ? <Notice tone="danger">{errorText(rescan.error)}</Notice> : null}
      <div className="segmented tri" role="group" aria-label="Kind of duplicate">
        {(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
          <button key={t} type="button" aria-pressed={tab === t} onClick={() => setTab(t)}>
            {TAB_LABEL[t]}
            {count(t) != null ? ` (${num(count(t))})` : ""}
          </button>
        ))}
      </div>
      <p className="muted small">{TAB_HELP[tab]}</p>
      <div className="search-scroll">
        {tab === "folders" ? <SeveralFolders n={s?.multi_location ?? null} /> : <Groups key={tab} tab={tab} />}
      </div>
    </div>
  );
}

function SeveralFolders({ n }: { n: number | null }) {
  const navigate = useNavigate();
  if (n == null) return <LoadingRows rows={1} />;
  if (n === 0) return <EmptyState title="No file is in more than one place" />;
  return (
    <Button
      onClick={() => {
        setFilters({ multi_location: true });
        navigate("/photos");
      }}
    >
      Show {plural(n, "photo")} in All photos
    </Button>
  );
}

function Groups({ tab }: { tab: DuplicateTab }) {
  const [limit, setLimit] = useState(50);
  const groups = useDuplicateGroups(tab, limit);
  const all = useResolveAllDuplicates();
  const [confirm, setConfirm] = useState(false);

  if (groups.isPending) return <LoadingRows rows={3} label="Loading duplicates" />;
  if (groups.isError) return <ErrorView what="the duplicates" error={groups.error} onRetry={() => void groups.refetch()} />;
  const { total, items } = groups.data;
  if (!total)
    return (
      <EmptyState title={tab === "copies" ? "No copies found" : "No similar shots found"}>
        {tab === "similar"
          ? "Similar shots need automatic tags switched on (Settings), so each photo has been analysed."
          : "New photos are checked as they are processed. Use Scan again after changing many folders."}
      </EmptyState>
    );
  return (
    <>
      <div className="dup-bar">
        <span className="muted">{plural(total, "group")}</span>
        <Button size="sm" onClick={() => setConfirm(true)}>
          Keep suggested in every group…
        </Button>
      </div>
      {all.isError ? <Notice tone="danger">{errorText(all.error)}</Notice> : null}
      <ul className="dup-groups" aria-label={TAB_LABEL[tab]}>
        {items.map((g) => (
          <GroupCard key={g.id} group={g} />
        ))}
      </ul>
      {items.length < total ? (
        <Button size="sm" onClick={() => setLimit((l) => Math.min(200, l + 50))} disabled={limit >= 200}>
          {limit >= 200 ? "Resolve some groups to see more" : "Show more groups"}
        </Button>
      ) : null}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Keep the suggested photo in all ${plural(total, "group")}?`}
        confirmLabel="Keep suggested, hide the rest"
        busy={all.isPending}
        onConfirm={() => void all.mutateAsync(tab).then(() => setConfirm(false), () => setConfirm(false))}
      >
        In each group the marked photo is kept and the others are hidden in VImage. Their tags, collections and
        favourite move to the kept photo. No file is deleted, and you can unhide any of them later.
      </ConfirmDialog>
    </>
  );
}

function GroupCard({ group }: { group: DuplicateGroup }) {
  const suggested = group.members.find((m) => m.suggested_keep)?.id ?? group.members[0]!.id;
  const [keep, setKeep] = useState<number[]>([suggested]);
  const resolve = useResolveDuplicates();
  const dismiss = useDismissDuplicates();
  const busy = resolve.isPending || dismiss.isPending;
  const hide = group.members.length - keep.length;
  const toggle = (id: number) =>
    setKeep((k) => (k.includes(id) ? (k.length > 1 ? k.filter((x) => x !== id) : k) : [...k, id]));
  const error = resolve.error ?? dismiss.error;

  return (
    <li className="dup-group">
      <ul className="dup-members" aria-label={`Group of ${group.members.length} photos`}>
        {group.members.map((m) => (
          <Member key={m.id} m={m} kept={keep.includes(m.id)} onToggle={() => toggle(m.id)} level={group.level} />
        ))}
      </ul>
      <div className="dup-actions">
        <Button
          variant="primary"
          size="sm"
          disabled={busy || hide === 0}
          // the first kept photo receives the others' tags: the suggested one when it is kept
          onClick={() => resolve.mutate({ groupId: group.id, keep: keep.includes(suggested) ? [suggested, ...keep.filter((x) => x !== suggested)] : keep })}
        >
          {hide === 0 ? "Keeping all" : `Keep ${keep.length === 1 ? "selected" : plural(keep.length, "photo")}, hide ${plural(hide, "other")}`}
        </Button>
        <Button size="sm" disabled={busy} onClick={() => dismiss.mutate(group.id)}>
          Not duplicates
        </Button>
        {error ? <span className="dup-error small">{errorText(error)}</span> : null}
      </div>
    </li>
  );
}

function Member({ m, kept, onToggle, level }: { m: DuplicateMember; kept: boolean; onToggle: () => void; level: DuplicateTab }) {
  const thumb = useThumbUrl();
  const src = thumb(m.thumb_path);
  const folder = m.path?.includes("/") || m.path?.includes("\\") ? m.path : null;
  return (
    <li>
      <button
        type="button"
        className={kept ? "dup-member kept" : "dup-member"}
        aria-pressed={kept}
        aria-label={`${m.path ?? m.uid}${kept ? ", kept" : ", will be hidden"}`}
        onClick={onToggle}
      >
        <span className="dup-thumb">{src ? <img src={src} alt="" loading="lazy" /> : <span className="muted small">No preview</span>}</span>
        <span className="dup-meta">
          <span>
            {m.suggested_keep ? <Badge tone="ok">Suggested</Badge> : null} {kept ? <Badge tone="info">Keep</Badge> : <Badge>Hide</Badge>}
            {m.is_favorite ? " ★" : ""}
          </span>
          <span>
            {m.width && m.height ? `${num(m.width)} × ${num(m.height)}` : "Size unknown"} · {bytes(m.byte_size)} · {formatLabel(m.mime_type)}
          </span>
          <span className="muted">{m.date ? m.date.slice(0, 16).replace("T", " ") : "No date"}</span>
          <span className="muted dup-path" title={m.path ?? undefined}>
            {folder ?? m.path ?? "No file"}
            {m.locations > 1 ? ` (+${num(m.locations - 1)} more)` : ""}
          </span>
          <span className="muted">
            {level === "copies" ? "Match" : "Look-alike"} {Math.round(m.similarity * 100)}%
          </span>
        </span>
      </button>
    </li>
  );
}

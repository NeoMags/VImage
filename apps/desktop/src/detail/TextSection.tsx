// Text in this photo (K2): what OCR read, editable. Your version is kept when OCR runs again.
import { useAssetText, useAssetTextEdits } from "@imagevault/api-client";
import { Button } from "@imagevault/ui";
import type { AssetText } from "@imagevault/shared-types";
import { useState } from "react";

import { errorText } from "../components/States";

const STATE_LABEL = {
  not_read: "Not read yet.",
  waiting: "Waiting to be read…",
  reading: "Reading the text…",
  failed: "Could not read the text.",
} as const;

export function TextSection({ id, line, onLine }: { id: number; line?: number | null; onLine?: (n: number | null) => void }) {
  const text = useAssetText(id);
  const edits = useAssetTextEdits(id);
  const [draft, setDraft] = useState<string | null>(null);
  const t = text.data;
  const failed = [edits.save, edits.revert, edits.readAgain, edits.setType].find((m) => m.isError);

  return (
    <section className="detail-section" aria-label="Text in this photo">
      <h3>
        Text in this photo{" "}
        {t?.origin === "user" ? <span className="muted small origin">yours</span> : null}
        {t?.origin === "ai" && t.text ? <span className="muted small origin">read by OCR</span> : null}
      </h3>
      {text.isPending ? (
        <p className="muted small">Loading…</p>
      ) : text.isError ? (
        <p className="text-danger small">{errorText(text.error)}</p>
      ) : draft !== null ? (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            edits.save.mutate(draft, { onSuccess: () => setDraft(null) });
          }}
        >
          <textarea aria-label="Edit the text" value={draft} maxLength={100000} rows={8} onChange={(e) => setDraft(e.target.value)} />
          <div className="row">
            <Button size="sm" type="submit" variant="primary" disabled={edits.save.isPending}>
              Save
            </Button>
            <Button size="sm" onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          {t && t.text && t.origin === "ai" && t.lines.length && onLine ? (
            <ol className="small ocr-text ocr-lines" aria-label="Lines of text (click to show on the picture)">
              {t.lines.map((l) => (
                <li key={l.line_no}>
                  <button
                    type="button"
                    aria-pressed={line === l.line_no}
                    className={line === l.line_no ? "on" : undefined}
                    onClick={() => onLine(line === l.line_no ? null : l.line_no)}
                  >
                    {l.text}
                  </button>
                </li>
              ))}
            </ol>
          ) : t && t.text ? (
            <p className="pre small ocr-text">{t.text}</p>
          ) : t && t.state !== "done" ? (
            <p className="muted small">
              {STATE_LABEL[t.state]}
              {t.error ? ` ${t.error}` : ""}
            </p>
          ) : (
            <p className="muted small">No text found.</p>
          )}
          <div className="row muted small">
            <button type="button" className="link" onClick={() => setDraft(t?.text ?? "")}>
              {t?.text ? "Edit" : "Add text"}
            </button>
            {t?.origin === "user" ? (
              <button type="button" className="link" disabled={edits.revert.isPending} onClick={() => edits.revert.mutate()}>
                Use the text read from the photo
              </button>
            ) : null}
            <button type="button" className="link" disabled={edits.readAgain.isPending} onClick={() => edits.readAgain.mutate()}>
              Read again
            </button>
          </div>
        </>
      )}
      {t ? <TypeRow t={t} onSet={(v) => edits.setType.mutate(v)} busy={edits.setType.isPending} /> : null}
      {t && t.entities.length ? <Entities t={t} /> : null}
      {failed ? <p className="text-danger small">{errorText(failed.error)}</p> : null}
    </section>
  );
}

type PictureType = "screenshot" | "scan" | "photo";
const TYPE_NAME: Record<PictureType, string> = { screenshot: "Screenshot", scan: "Scan", photo: "Camera photo" };

/** K3: what kind of picture this is; pick another to correct it. */
function TypeRow({ t, onSet, busy }: { t: AssetText; onSet: (v: PictureType | null) => void; busy: boolean }) {
  return (
    <p className="small row">
      <label>
        Type{" "}
        <select
          aria-label="Picture type"
          disabled={busy}
          value={t.type?.value ?? ""}
          onChange={(e) => onSet((e.target.value || null) as PictureType | null)}
        >
          {t.type ? null : <option value="">Unknown</option>}
          {(Object.keys(TYPE_NAME) as PictureType[]).map((v) => (
            <option key={v} value={v}>
              {TYPE_NAME[v]}
            </option>
          ))}
        </select>
      </label>
      {t.type?.origin === "ai" ? <span className="muted origin">guessed</span> : null}
      {t.type?.origin === "user" ? (
        <button type="button" className="link" disabled={busy} onClick={() => onSet(null)}>
          Let ImageVault decide
        </button>
      ) : null}
    </p>
  );
}

const ENTITY_LABEL: Record<string, string> = {
  url: "Link",
  email: "Email",
  phone: "Phone",
  date: "Date",
  amount: "Amount",
  command: "Command",
  error_message: "Error",
};

/** K3: links, emails, phone numbers, dates, amounts, commands and errors found in the text. */
function Entities({ t }: { t: AssetText }) {
  const [copied, setCopied] = useState<number | null>(null);
  const copy = (id: number, value: string) =>
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(id);
      setTimeout(() => setCopied(null), 1200);
    });
  return (
    <ul className="entities" aria-label="Found in the text">
      {t.entities.map((e) => (
        <li key={e.id} className="small">
          <span className="muted">{ENTITY_LABEL[e.type] ?? e.type}</span>{" "}
          <span className={e.type === "command" || e.type === "error_message" ? "mono" : undefined}>{e.value}</span>
          {e.type === "date" && e.normalized !== e.value ? <span className="muted"> ({e.normalized})</span> : null}{" "}
          <button type="button" className="link" onClick={() => copy(e.id, e.type === "phone" ? e.value : (e.normalized ?? e.value))}>
            {copied === e.id ? "Copied" : "Copy"}
          </button>
        </li>
      ))}
    </ul>
  );
}

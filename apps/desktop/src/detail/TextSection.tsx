// Text in this photo (K2): what OCR read, editable. Your version is kept when OCR runs again.
import { useAssetText, useAssetTextEdits } from "@imagevault/api-client";
import { Button } from "@imagevault/ui";
import { useState } from "react";

import { errorText } from "../components/States";

const STATE_LABEL = {
  not_read: "Not read yet.",
  waiting: "Waiting to be read…",
  reading: "Reading the text…",
  failed: "Could not read the text.",
} as const;

export function TextSection({ id }: { id: number }) {
  const text = useAssetText(id);
  const edits = useAssetTextEdits(id);
  const [draft, setDraft] = useState<string | null>(null);
  const t = text.data;
  const failed = [edits.save, edits.revert, edits.readAgain].find((m) => m.isError);

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
          {t && t.text ? (
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
      {failed ? <p className="text-danger small">{errorText(failed.error)}</p> : null}
    </section>
  );
}

// Text in photos (K2): OCR runs on this PC for every image; the models ship with the app.
import { useOcr, useRereadAllText, useSaveOcrOptions } from "@imagevault/api-client";
import { Button, Panel } from "@imagevault/ui";

import { ErrorView, errorText, LoadingRows } from "../components/States";
import { num } from "../format";

export function OcrPanel() {
  const ocr = useOcr();
  const save = useSaveOcrOptions();
  const again = useRereadAllText();
  if (ocr.isPending)
    return (
      <Panel title="Text in photos">
        <LoadingRows rows={1} label="Loading text reading status" />
      </Panel>
    );
  if (ocr.isError)
    return (
      <Panel title="Text in photos">
        <ErrorView what="text reading status" error={ocr.error} onRetry={() => void ocr.refetch()} />
      </Panel>
    );
  const s = ocr.data;
  return (
    <Panel title="Text in photos">
      <p className="small">
        ImageVault reads the text in screenshots, scans and photos (English for now), so you can search for it. It runs on
        this PC; nothing is uploaded.
      </p>
      <label className="check">
        <input
          type="checkbox"
          checked={s.enabled}
          disabled={save.isPending}
          onChange={(e) => save.mutate({ enabled: e.target.checked })}
        />
        Read text in my photos
      </label>
      <p className="small" role="status" aria-live="polite">
        {num(s.photos_done)} read · {num(s.photos_with_text)} with text
        {s.photos_waiting ? ` · ${num(s.photos_waiting)} waiting` : ""}
        {s.photos_failed ? ` · ${num(s.photos_failed)} could not be read` : ""}
        {s.photos_edited ? ` · ${num(s.photos_edited)} edited by you` : ""}
      </p>
      <Button size="sm" disabled={!s.enabled || again.isPending} onClick={() => again.mutate()}>
        Read all photos again
      </Button>
      <p className="muted small">Text you edited is kept.</p>
      {save.isError || again.isError ? <p className="text-danger small">{errorText((save.error ?? again.error)!)}</p> : null}
    </Panel>
  );
}

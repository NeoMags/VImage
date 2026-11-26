// Knowledge Card (K5): title and summary made from the photo's text; editable (then it is yours and
// never regenerated). Copy the text, save the card as Markdown, or print it to PDF (Windows' own
// "Save as PDF" printer in the print dialog).
import { useAssetCard, useAssetText, useCardEdits } from "@imagevault/api-client";
import type { AssetText, KnowledgeCard } from "@imagevault/shared-types";
import { Button } from "@imagevault/ui";
import { useState } from "react";

import { errorText } from "../components/States";

export function CardSection({ id }: { id: number }) {
  const card = useAssetCard(id);
  const text = useAssetText(id);
  const edits = useCardEdits(id);
  const [draft, setDraft] = useState<{ title: string; summary: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const c = card.data?.card;
  if (!c) return null;
  const failed = [edits.edit, edits.reset].find((m) => m.isError);

  const flash = (msg: string) => {
    setNote(msg);
    setTimeout(() => setNote(null), 1500);
  };
  const saveMarkdown = async () => {
    try {
      const md = await edits.markdown();
      const url = URL.createObjectURL(new Blob([md.markdown], { type: "text/markdown;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = md.file_name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      flash(`Saved ${md.file_name} to your Downloads folder`);
    } catch (e) {
      setNote(errorText(e));
    }
  };

  return (
    <section className="detail-section card" aria-label="Knowledge Card">
      <h3>
        Card <span className="muted small origin">{c.edited ? "edited by you" : "AI-generated"}</span>
      </h3>
      {draft ? (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.title.trim()) edits.edit.mutate(draft, { onSuccess: () => setDraft(null) });
          }}
        >
          <input aria-label="Card title" maxLength={200} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          <textarea aria-label="Card summary" maxLength={2000} rows={3} value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} />
          <div className="row">
            <Button size="sm" type="submit" variant="primary" disabled={edits.edit.isPending || !draft.title.trim()}>
              Save
            </Button>
            <Button size="sm" onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p className="card-title">{c.title}</p>
          {c.summary ? <p className="small">{c.summary}</p> : null}
        </>
      )}
      <div className="row muted small">
        {draft ? null : (
          <button type="button" className="link" onClick={() => setDraft({ title: c.title, summary: c.summary })}>
            Edit card
          </button>
        )}
        {c.edited ? (
          <button type="button" className="link" disabled={edits.reset.isPending} onClick={() => edits.reset.mutate()}>
            Make it from the text again
          </button>
        ) : null}
        <button
          type="button"
          className="link"
          disabled={!text.data?.text}
          onClick={() => void navigator.clipboard?.writeText(text.data?.text ?? "").then(() => flash("Text copied"))}
        >
          Copy text
        </button>
        <button type="button" className="link" onClick={() => void saveMarkdown()}>
          Export .md
        </button>
        <button type="button" className="link" disabled={!text.data} onClick={() => text.data && printCard(c, text.data)}>
          Export PDF
        </button>
      </div>
      {note ? (
        <p className="small" role="status">
          {note}
        </p>
      ) : null}
      {failed ? <p className="text-danger small">{errorText(failed.error)}</p> : null}
    </section>
  );
}

const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);
const LABEL: Record<string, string> = { url: "Link", email: "Email", phone: "Phone", date: "Date", amount: "Amount", command: "Command", error_message: "Error" };

/** Prints just the card from a hidden frame; the print dialog offers "Microsoft Print to PDF". */
export function cardHtml(c: KnowledgeCard, t: AssetText): string {
  const ents = t.entities.map((e) => `<li><b>${esc(LABEL[e.type] ?? e.type)}:</b> ${esc(e.value)}</li>`).join("");
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(c.title)}</title><style>
body{font:12pt/1.45 "Segoe UI",system-ui,sans-serif;margin:2cm;color:#111}h1{font-size:18pt;margin:0 0 .3em}
.meta{color:#555;font-size:10pt}pre{white-space:pre-wrap;font:10pt/1.4 Consolas,monospace;background:#f4f4f4;padding:.8em;border-radius:4px}
</style></head><body><h1>${esc(c.title)}</h1>${c.summary ? `<p>${esc(c.summary)}</p>` : ""}
<p class="meta">${t.type ? esc({ screenshot: "Screenshot", scan: "Scan", photo: "Photo" }[t.type.value]) + " · " : ""}${c.edited ? "Edited by you" : "AI-generated"}</p>
${ents ? `<h2>Found in the text</h2><ul>${ents}</ul>` : ""}<h2>Text</h2><pre>${esc(t.text)}</pre></body></html>`;
}

function printCard(c: KnowledgeCard, t: AssetText) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;width:0;height:0;border:0;left:-9999px";
  document.body.appendChild(frame);
  const doc = frame.contentDocument!;
  doc.open();
  doc.write(cardHtml(c, t));
  doc.close();
  setTimeout(() => {
    frame.contentWindow?.focus();
    frame.contentWindow?.print();
    setTimeout(() => frame.remove(), 60_000); // the dialog is modal in WebView2; remove well after
  }, 50);
}

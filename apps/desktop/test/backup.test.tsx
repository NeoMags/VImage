import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { BackupPanel, ExportPanel } from "../src/screens/Backup";
import { problem, renderWith, stats } from "./helpers";

vi.mock("../src/native", () => ({
  pickFolder: vi.fn(async () => "E:\\Backups"),
  pickBackupFile: vi.fn(async () => "E:\\Backups\\library-auto-v4-20261120T090000Z.db"),
  pickSavePath: vi.fn(async (name: string) => `C:\\Users\\Neo\\Documents\\${name}`),
  revealInExplorer: vi.fn(),
  getAppPaths: vi.fn(async () => null),
  inTauri: () => false,
}));

const backup = (name: string, kind: string, created_at: string) => ({ name, kind, created_at, size: 2_400_000, schema_version: 4 });
const list = (over: Record<string, unknown> = {}) => ({
  backups: [
    backup("library-manual-v4-20261125T100000Z.db", "manual", "2026-11-25T10:00:00+00:00"),
    backup("library-auto-v4-20261124T090000Z.db", "auto", "2026-11-24T09:00:00+00:00"),
  ],
  folder: "C:\\Lib\\backups",
  needs_restore: null,
  ...over,
});

describe("Backup panel", () => {
  it("backs up now, lists backups, and restores one after asking", async () => {
    const { calls } = renderWith(<BackupPanel />, {
      "GET /v1/backups": () => list(),
      "GET /v1/settings/backup": () => ({ keep: 7, copy_to: null }),
      "POST /v1/backups": () => ({
        backup: backup("library-manual-v4-20261125T110000Z.db", "manual", "2026-11-25T11:00:00+00:00"),
        pruned: [],
        copy_error: null,
      }),
      "POST /v1/backups:restore": () => ({
        restored_from: "library-auto-v4-20261124T090000Z.db",
        schema_version: 4,
        pre_restore: "library-pre-restore-v4-20261125T110500Z.db",
        thumbnails_requeued: 0,
      }),
    });
    fireEvent.click(await screen.findByRole("button", { name: "Back up now" }));
    expect(await screen.findByText("Backup made (2.4 MB).")).toBeTruthy();

    const table = await screen.findByRole("table", { name: "Backups" });
    const auto = within(table).getByText("Automatic").closest("tr")!;
    fireEvent.click(within(auto).getByRole("button", { name: "Restore…" }));
    const dialog = screen.getByRole("alertdialog", { name: "Confirm restore" });
    expect(dialog.textContent).toContain("safety copy");
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
    expect(await screen.findByText(/Restored from library-auto-v4-20261124T090000Z\.db\. Safety copy:/)).toBeTruthy();
    const post = calls.find((r) => r.url.endsWith("/v1/backups:restore"))!;
    expect(await post.json()).toEqual({ name: "library-auto-v4-20261124T090000Z.db" });
  });

  it("sets a second-copy folder and shows a refused restore's reason", async () => {
    let saved: unknown = null;
    renderWith(<BackupPanel />, {
      "GET /v1/backups": () => list(),
      "GET /v1/settings/backup": () => saved ?? { keep: 7, copy_to: null },
      "PUT /v1/settings/backup": async (req) => (saved = await req.json()),
      "POST /v1/backups:restore": () => problem(422, "invalid", "this backup is from a newer version of ImageVault"),
    });
    fireEvent.click(await screen.findByRole("button", { name: "Choose…" }));
    expect(await screen.findByText("E:\\Backups")).toBeTruthy();
    expect(saved).toEqual({ keep: 7, copy_to: "E:\\Backups" });

    fireEvent.click(screen.getByRole("button", { name: "Restore from a file…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Confirm restore" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
    expect(await screen.findByText(/Restore failed: .*newer version/)).toBeTruthy();
  });
});

describe("Export panel", () => {
  it("asks where to save, exports, and says where it went", async () => {
    const { calls } = renderWith(<ExportPanel />, {
      "POST /v1/export": () => ({ path: "C:\\Users\\Neo\\Documents\\x.csv", photos: 1234, bytes: 200_000, format: "csv" }),
    });
    fireEvent.click(screen.getByRole("radio", { name: "CSV" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Include hidden photos" }));
    fireEvent.click(screen.getByRole("button", { name: "Export…" }));
    expect(await screen.findByText(/Exported 1,234 photos/)).toBeTruthy();
    const body = (await calls.find((r) => r.url.endsWith("/v1/export"))!.json()) as Record<string, unknown>;
    expect(body.format).toBe("csv");
    expect(body.include_hidden).toBe(true);
    expect(String(body.path)).toMatch(/^C:\\Users\\Neo\\Documents\\ImageVault-\d{4}-\d{2}-\d{2}\.csv$/);
  });
});

describe("Damaged library at start", () => {
  it("offers the backups instead of the app, or an empty start", async () => {
    renderWith(<App />, {
      "GET /v1/backups": () =>
        list({
          needs_restore: "damaged-20261125T120000Z.db",
          backups: [...list().backups, backup("damaged-20261125T120000Z.db", "damaged", "2026-11-25T12:00:00+00:00")],
        }),
      "GET /v1/jobs/stats": () => ({ jobs: {}, by_stage: {}, assets: {}, queue: null }),
      "GET /v1/stats": () => stats(),
      "GET /v1/sources": () => [],
    });
    expect(await screen.findByRole("heading", { name: "Your library could not be opened" })).toBeTruthy();
    const table = await screen.findByRole("table", { name: "Backups" });
    expect(within(table).queryByText("Damaged (set aside)")).toBeNull();
    expect(within(table).getAllByRole("button", { name: "Restore…" })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Start with an empty library" }));
    expect(screen.queryByRole("heading", { name: "Your library could not be opened" })).toBeNull();
  });
});

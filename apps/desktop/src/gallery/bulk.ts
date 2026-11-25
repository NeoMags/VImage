// Bulk edits in chunks: the engine takes at most 5,000 ids per call. Chunks are not one transaction,
// so on a failure the caller learns exactly how many photos were already changed.
import type { BulkBody } from "@imagevault/shared-types";

export const MAX_BULK = 5000;

export interface BulkOutcome {
  done: number;
  total: number;
  error: unknown;
}

export async function runBulk(
  ids: number[],
  change: Omit<BulkBody, "ids">,
  send: (body: BulkBody) => Promise<unknown>,
  onProgress?: (done: number, total: number) => void,
): Promise<BulkOutcome> {
  let done = 0;
  for (let i = 0; i < ids.length; i += MAX_BULK) {
    const chunk = ids.slice(i, i + MAX_BULK);
    try {
      await send({ ...change, ids: chunk });
    } catch (error) {
      return { done, total: ids.length, error };
    }
    done += chunk.length;
    onProgress?.(done, ids.length);
  }
  return { done, total: ids.length, error: null };
}

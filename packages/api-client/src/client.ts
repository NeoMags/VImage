import type { paths } from "@imagevault/shared-types";
import createClient, { type Client } from "openapi-fetch";

import { ApiError, toApiError } from "./errors";

/** Where the engine listens. The desktop shell hands this out (Tauri command get_sidecar_endpoint). */
export interface Endpoint {
  port: number;
  token: string;
}

export type ApiClient = Client<paths> & { endpoint: Endpoint };

export function baseUrl(ep: Endpoint): string {
  return `http://127.0.0.1:${ep.port}`;
}

export function createApiClient(ep: Endpoint, fetchImpl?: (input: Request) => Promise<Response>): ApiClient {
  const client = createClient<paths>({
    baseUrl: baseUrl(ep),
    headers: { Authorization: `Bearer ${ep.token}` },
    ...(fetchImpl ? { fetch: fetchImpl } : {}),
  });
  return Object.assign(client, { endpoint: ep });
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/**
 * Awaits an openapi-fetch call and returns its data, or throws ApiError.
 * A refused connection (engine starting or restarting) becomes ApiError "offline".
 */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  let res: Result<T>;
  try {
    res = await call;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "offline", `Cannot reach the ImageVault engine (${String(e)})`);
  }
  if (!res.response.ok) throw toApiError(res.response.status, res.error);
  return res.data as T;
}

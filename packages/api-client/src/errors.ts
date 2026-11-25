import type { Problem } from "@imagevault/shared-types";

/** One error type for the UI: every failed call, whether the engine answered or not. */
export class ApiError extends Error {
  /** HTTP status, or 0 when the engine could not be reached. */
  readonly status: number;
  /** problem+json `code`, or "offline" when the engine could not be reached. */
  readonly code: Problem["code"] | "offline";
  readonly detail: string;
  readonly errors: Problem["errors"];

  constructor(status: number, code: ApiError["code"], detail: string, errors?: Problem["errors"]) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.errors = errors;
  }

  /** Worth retrying automatically: the engine is starting, restarting or busy. */
  get transient(): boolean {
    return this.code === "offline" || this.code === "busy";
  }
}

function isProblem(body: unknown): body is Problem {
  return (
    typeof body === "object" &&
    body !== null &&
    typeof (body as Problem).code === "string" &&
    typeof (body as Problem).status === "number"
  );
}

/** Turns an engine error body into an ApiError; falls back to the HTTP status if it is not problem+json. */
export function toApiError(status: number, body: unknown): ApiError {
  if (isProblem(body)) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    return new ApiError(status, body.code, detail, body.errors);
  }
  const text = typeof body === "string" && body ? body : `HTTP ${status}`;
  return new ApiError(status, "error", text);
}

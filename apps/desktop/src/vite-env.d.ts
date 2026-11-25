/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set only for the end-to-end test build: lets a plain browser take the engine endpoint from the URL. */
  readonly VITE_IV_E2E?: string;
}

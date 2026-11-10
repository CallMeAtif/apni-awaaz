/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" serves canned, contract-shaped responses (dev only). Off by default. */
  readonly VITE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LIFF_ID: string
  readonly VITE_TEXT_JUDGMENT_LAB_LIFF_ID: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

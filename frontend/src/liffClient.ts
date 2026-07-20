import liff from '@line/liff'

export type LiffContextKind = 'liff_browser' | 'external_browser'

export interface LinePlatformLiffAdapter {
  initialize(liffId: string): Promise<LiffContextKind>
  isLoggedIn(): boolean
  login(redirectUri: string): void
  getIdToken(): string | null
  getAccessToken(): string | null
}

export interface LiffSdkBoundary {
  init(input: { liffId: string }): Promise<unknown>
  isInClient(): boolean
  isLoggedIn(): boolean
  login(input: { redirectUri: string }): void
  getIDToken(): string | null
  getAccessToken(): string | null
}

const rawToken = (value: string | null): string | null =>
  typeof value === 'string' && value.length > 0 ? value : null

export function createLinePlatformLiffAdapter(
  sdk: LiffSdkBoundary = liff as unknown as LiffSdkBoundary,
): LinePlatformLiffAdapter {
  return Object.freeze({
    async initialize(liffId: string) {
      await sdk.init({ liffId })
      return sdk.isInClient() ? 'liff_browser' : 'external_browser'
    },
    isLoggedIn: () => sdk.isLoggedIn(),
    login: (redirectUri: string) => sdk.login({ redirectUri }),
    getIdToken: () => rawToken(sdk.getIDToken()),
    getAccessToken: () => rawToken(sdk.getAccessToken()),
  })
}

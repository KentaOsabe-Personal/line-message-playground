import liff from '@line/liff'

export type LiffContextKind = 'liff_browser' | 'external_browser'

export interface LinePlatformLiffAdapter {
  initialize(liffId: string): Promise<LiffContextKind>
  ensureProfilePermission(): Promise<boolean>
  isLoggedIn(): boolean
  login(redirectUri: string): void
  reauthenticate(redirectUri: string): void
  logout(): void
  getIdToken(): string | null
  getAccessToken(): string | null
}

export interface LiffSdkBoundary {
  init(input: { liffId: string }): Promise<unknown>
  isInClient(): boolean
  isLoggedIn(): boolean
  login(input: { redirectUri: string }): void
  logout(): void
  getIDToken(): string | null
  getAccessToken(): string | null
  permission: {
    query(permission: 'profile'): Promise<{ state: 'granted' | 'prompt' | 'unavailable' }>
    requestAll(): Promise<unknown>
  }
}

const rawToken = (value: string | null): string | null =>
  typeof value === 'string' && value.length > 0 ? value : null

export function createLinePlatformLiffAdapter(
  sdk: LiffSdkBoundary = liff,
  reload: () => void = () => window.location.reload(),
): LinePlatformLiffAdapter {
  return Object.freeze({
    async initialize(liffId: string) {
      await sdk.init({ liffId })
      return sdk.isInClient() ? 'liff_browser' : 'external_browser'
    },
    async ensureProfilePermission() {
      const permission = await sdk.permission.query('profile')
      if (permission.state === 'granted') return true
      if (permission.state === 'unavailable') return false
      await sdk.permission.requestAll()
      return (await sdk.permission.query('profile')).state === 'granted'
    },
    isLoggedIn: () => sdk.isLoggedIn(),
    login: (redirectUri: string) => sdk.login({ redirectUri }),
    reauthenticate: (redirectUri: string) => {
      if (sdk.isInClient()) {
        reload()
        return
      }
      if (sdk.isLoggedIn()) sdk.logout()
      sdk.login({ redirectUri })
    },
    logout: () => sdk.logout(),
    getIdToken: () => rawToken(sdk.getIDToken()),
    getAccessToken: () => rawToken(sdk.getAccessToken()),
  })
}

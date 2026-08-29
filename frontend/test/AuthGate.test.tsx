import { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import AuthGate from '../src/AuthGate'
import { AuthApiError } from '../src/authApi'
import type { AuthApiClient } from '../src/authApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import { createOwnerSessionStorage } from '../src/ownerSessionStorage'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const adapter = (overrides: Partial<LinePlatformLiffAdapter> = {}): LinePlatformLiffAdapter => ({
  initialize: vi.fn().mockResolvedValue('external_browser'),
  isLoggedIn: vi.fn().mockReturnValue(false),
  login: vi.fn(),
  reauthenticate: vi.fn(),
  logout: vi.fn(),
  getIdToken: vi.fn().mockReturnValue(null),
  getAccessToken: vi.fn().mockReturnValue(null),
  ...overrides,
})

const api = (overrides: Partial<AuthApiClient> = {}): AuthApiClient => ({
  bootstrap: vi.fn().mockResolvedValue({ state: 'anonymous' }),
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue({ state: 'anonymous' }),
  ...overrides,
})

describe('AuthGate', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })

  // テストケース: 未loginの外部browserで認証gateを起動する。
  // 期待値: login導線だけを表示し、配信・管理に相当する子Componentをmountしない。
  test('does not mount protected children before authentication', async () => {
    const childMounted = vi.fn()
    const Protected = () => { childMounted(); return <p>保護画面</p> }

    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()}
        authApi={api()}
      ><Protected /></AuthGate>,
    ))

    expect(container.textContent).toContain('LINEでログイン')
    expect(container.textContent).not.toContain('保護画面')
    expect(childMounted).not.toHaveBeenCalled()
  })

  // テストケース: LIFF raw ID tokenをBackendが認証済みsessionへ変換する。
  // 期待値: display name付きconsoleと子Componentを表示し、LINE user IDは表示しない。
  test('mounts protected children only for an authenticated owner', async () => {
    const liffAdapter = adapter({ isLoggedIn: vi.fn().mockReturnValue(true), getIdToken: vi.fn().mockReturnValue('raw-id-token') })
    const authApi = api({
      login: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
    })

    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={liffAdapter}
        authApi={authApi}
      >{({ session }) => <><p>{session.state === 'authenticated' ? session.profile.displayName : ''}</p><p>保護画面</p></>}</AuthGate>,
    ))

    expect(authApi.login).toHaveBeenCalledWith('raw-id-token')
    expect(container.textContent).toContain('Owner')
    expect(container.textContent).toContain('保護画面')
    expect(container.textContent).not.toContain('userId')
  })

  // テストケース: 認証済みownerが現在端末をlogoutする。
  // 期待値: logout後は子Componentをunmountし、未認証状態へ戻る。
  test('logs out only the current frontend session and closes the gate', async () => {
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
    })
    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()}
        authApi={authApi}
      >{({ logout }) => <><p>保護画面</p><button type="button" onClick={() => void logout()}>この端末からログアウト</button></>}</AuthGate>,
    ))

    const logout = [...container.querySelectorAll('button')].find((button) => button.textContent === 'この端末からログアウト')
    await act(async () => logout?.click())

    expect(authApi.logout).toHaveBeenCalledTimes(1)
    expect(container.textContent).not.toContain('保護画面')
  })

  // テストケース: StrictModeが初期effectをsetup・cleanup・setupの順で再実行する。
  // 期待値: cleanup済み世代は認証mutationへ進まず、Backend loginを論理的に1回だけ呼ぶ。
  test('suppresses stale authentication work under StrictMode', async () => {
    const liffAdapter = adapter({ isLoggedIn: vi.fn().mockReturnValue(true), getIdToken: vi.fn().mockReturnValue('raw-id-token') })
    const authApi = api({
      login: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
    })

    await act(async () => root.render(
      <StrictMode>
        <AuthGate
          config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
          liffAdapter={liffAdapter}
          authApi={authApi}
        ><p>保護画面</p></AuthGate>
      </StrictMode>,
    ))

    expect(authApi.login).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('保護画面')
  })

  // テストケース: session bootstrapが401で期限切れを通知する。
  // 期待値: 汎用初期化errorで上書きせず、再ログイン導線へ収束する。
  test('keeps bootstrap 401 as a session invalidation state', async () => {
    const authApi = api({
      bootstrap: vi.fn().mockRejectedValue(new AuthApiError({ code: 'not_authenticated', summary: '認証が必要です。' }, 401)),
    })

    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()}
        authApi={authApi}
      ><p>保護画面</p></AuthGate>,
    ))

    expect(container.textContent).toContain('LINEでログイン')
    expect(container.textContent).not.toContain('本人確認を完了できません')
  })

  // テストケース: raw ID tokenのBackend検証が401を返す。
  // 期待値: verification errorで失効通知を上書きせず、再ログイン導線へ収束する。
  test('keeps login 401 as a session invalidation state', async () => {
    const liffAdapter = adapter({ isLoggedIn: vi.fn().mockReturnValue(true), getIdToken: vi.fn().mockReturnValue('raw-id-token') })
    const authApi = api({
      login: vi.fn().mockRejectedValue(new AuthApiError({ code: 'invalid_identity', summary: '本人確認に失敗しました。' }, 401)),
    })

    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={liffAdapter}
        authApi={authApi}
      ><p>保護画面</p></AuthGate>,
    ))

    expect(container.textContent).toContain('LINEでログイン')
    expect(container.textContent).not.toContain('本人確認を完了できません')
  })

  // テストケース: 現在端末logoutが401で既に失効済みと判定される。
  // 期待値: logout errorで上書きせず、保護画面を閉じて再ログイン導線へ収束する。
  test('keeps logout 401 as a session invalidation state', async () => {
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
      logout: vi.fn().mockRejectedValue(new AuthApiError({ code: 'not_authenticated', summary: '認証が必要です。' }, 401)),
    })
    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()}
        authApi={authApi}
      >{({ logout }) => <><p>保護画面</p><button type="button" onClick={() => void logout()}>この端末からログアウト</button></>}</AuthGate>,
    ))

    const logout = [...container.querySelectorAll('button')].find((button) => button.textContent === 'この端末からログアウト')
    await act(async () => logout?.click())

    expect(container.textContent).toContain('LINEでログイン')
    expect(container.textContent).not.toContain('保護画面')
    expect(container.textContent).not.toContain('ログアウトできません')
  })

  // テストケース: unlink pending sessionでaccount recoveryを描画する。
  // 期待値: render contextへpending stageだけを渡し、通常の保護画面を表示しない。
  test('mounts only render-prop recovery content for an unlinking owner', async () => {
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({
        state: 'unlinking', stage: 'local_deletion_pending', retryAction: 'retry_local_delete',
      }),
    })
    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()}
        authApi={authApi}
      >{({ session }) => <p>{session.state === 'unlinking' ? session.retryAction : '通常画面'}</p>}</AuthGate>,
    ))

    expect(container.textContent).toContain('retry_local_delete')
    expect(container.textContent).not.toContain('通常画面')
  })

  // テストケース: account consoleが全連携解除完了を通知する。
  // 期待値: 認証状態をanonymousへ更新し、通常の保護画面を即座にunmountする。
  test('closes protected content when its render context reports unlink completion', async () => {
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
    })
    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()}
        authApi={authApi}
      >{({ onSessionReceived }) => <button type="button" onClick={() => onSessionReceived({ state: 'anonymous' })}>解除完了</button>}</AuthGate>,
    ))

    await clickButton(container, '解除完了')
    expect(container.textContent).toContain('LINEでログイン')
    expect(container.textContent).not.toContain('解除完了')
  })

  // テストケース: account consoleがtoken失効時の再認証を要求する。
  // 期待値: AuthGateが検証済みredirect URIでLIFF再認証を開始する。
  test('exposes an explicit LIFF reauthentication action to protected content', async () => {
    const liffAdapter = adapter({ reauthenticate: vi.fn() })
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
    })
    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={liffAdapter}
        authApi={authApi}
      >{({ reauthenticate }) => <button type="button" onClick={reauthenticate}>再認証</button>}</AuthGate>,
    ))

    await clickButton(container, '再認証')
    expect(liffAdapter.reauthenticate).toHaveBeenCalledWith('https://example.com/liff')
    expect(container.textContent).not.toContain('再認証')
    expect(container.textContent).toContain('本人確認中')
  })

  // テストケース: pending解除の再認証redirect/reloadから復帰する。
  // 期待値: tab-local markerと新しいaccess tokenが揃った場合だけresume可能として子へ渡す。
  test('marks deauthorization resume ready only after returning from reauthentication', async () => {
    window.sessionStorage.setItem('line-owner:unlink-reauthentication-pending', '1')
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({
        state: 'unlinking', stage: 'deauthorization_pending', retryAction: 'reauthenticate',
      }),
    })
    await act(async () => root.render(
      <AuthGate
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter({ getAccessToken: vi.fn().mockReturnValue('new-access-token') })}
        authApi={authApi}
      >{({ unlinkReauthenticationReady }) => <p>{unlinkReauthenticationReady ? 'resume-ready' : 'reauth-required'}</p>}</AuthGate>,
    ))

    expect(container.textContent).toContain('resume-ready')
    expect(window.sessionStorage.getItem('line-owner:unlink-reauthentication-pending')).toBeNull()
  })

  // テストケース: 未認証ownerが許可済みsubrouteからloginを開始する。
  // 期待値: safe return pathを保存し、固定`/liff` redirectだけをLIFFへ渡す。
  test('stores a safe subroute and starts login with the fixed LIFF redirect', async () => {
    const liffAdapter = adapter()
    const storage = createOwnerSessionStorage(window.sessionStorage)
    await act(async () => root.render(
      <AuthGate currentPathname="/liff/channels" ownerStorage={storage}
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={liffAdapter} authApi={api()}><p>保護画面</p></AuthGate>,
    ))
    await clickButton(container, 'LINEでログイン')
    expect(liffAdapter.login).toHaveBeenCalledWith('https://example.com/liff')
    expect(storage.consumeReturnPath()).toBe('/liff/channels')
  })

  // テストケース: 固定`/liff`へ認証済みで復帰する。
  // 期待値: 保存済みsafe return pathを一回だけ消費して履歴置換する。
  test('consumes the safe return path after authentication', async () => {
    const storage = createOwnerSessionStorage(window.sessionStorage)
    storage.saveReturnPath('/liff/deliveries')
    const replacePath = vi.fn()
    await act(async () => root.render(
      <AuthGate currentPathname="/liff" replacePath={replacePath} ownerStorage={storage}
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()} authApi={api({ bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }) })}>
        <p>保護画面</p>
      </AuthGate>,
    ))
    expect(replacePath).toHaveBeenCalledWith('/liff/deliveries')
    expect(storage.consumeReturnPath()).toBeNull()
  })

  // テストケース: 認証済みownerが明示logoutする。
  // 期待値: Backend・LIFF・owner一時情報を消去し、`/liff`へ履歴置換する。
  test('clears owner state and replaces history after explicit logout', async () => {
    const storage = createOwnerSessionStorage(window.sessionStorage)
    storage.saveReturnPath('/liff/channels')
    storage.saveDeliveryOperationId('123e4567-e89b-42d3-a456-426614174000')
    const replacePath = vi.fn()
    const liffAdapter = adapter()
    await act(async () => root.render(
      <AuthGate currentPathname="/liff/account" replacePath={replacePath} ownerStorage={storage}
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={liffAdapter} authApi={api({ bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }) })}>
        {({ logout }) => <><p>保護画面</p><button type="button" onClick={() => void logout()}>この端末からログアウト</button></>}
      </AuthGate>,
    ))
    await clickButton(container, 'この端末からログアウト')
    expect(liffAdapter.logout).toHaveBeenCalledTimes(1)
    expect(replacePath).toHaveBeenCalledWith('/liff')
    expect(storage.consumeReturnPath()).toBeNull()
    expect(storage.readDeliveryOperationId()).toBeNull()
    expect(container.textContent).not.toContain('保護画面')
  })

  // テストケース: Backend logout成功後にLIFF SDK logoutが失敗する。
  // 期待値: owner一時情報を消去し、旧ownerを再認証しないfail-closed状態へ収束する。
  test('fails closed and clears owner state when LIFF logout fails', async () => {
    const storage = createOwnerSessionStorage(window.sessionStorage)
    storage.saveReturnPath('/liff/channels')
    const replacePath = vi.fn()
    const authApi = api({
      bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
      login: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
    })
    await act(async () => root.render(
      <AuthGate currentPathname="/liff/channels" replacePath={replacePath} ownerStorage={storage}
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter({
          logout: vi.fn(() => { throw new Error('sdk logout failed') }),
          isLoggedIn: vi.fn().mockReturnValue(true), getIdToken: vi.fn().mockReturnValue('old-owner-token'),
        })}
        authApi={authApi}>
        {({ logout }) => <><p>保護画面</p><button type="button" onClick={() => void logout()}>この端末からログアウト</button></>}
      </AuthGate>,
    ))
    await clickButton(container, 'この端末からログアウト')
    expect(replacePath).toHaveBeenCalledWith('/liff')
    expect(storage.consumeReturnPath()).toBeNull()
    expect(container.textContent).not.toContain('保護画面')
    expect(container.textContent).toContain('ログアウトできません')
    expect([...container.querySelectorAll('button')].some((button) => button.textContent === '再試行')).toBe(false)
    expect(authApi.login).not.toHaveBeenCalled()
  })

  // テストケース: 全連携解除中にaccount以外の保護URLを表示する。
  // 期待値: `/liff/account`へ履歴置換し、回復contentだけを描画する。
  test('replaces any protected route with account while unlinking', async () => {
    const replacePath = vi.fn()
    await act(async () => root.render(
      <AuthGate currentPathname="/liff/deliveries" replacePath={replacePath}
        config={{ liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' }}
        liffAdapter={adapter()} authApi={api({ bootstrap: vi.fn().mockResolvedValue({ state: 'unlinking', stage: 'local_deletion_pending', retryAction: 'retry_local_delete' }) })}>
        {({ session }) => <p>{session.state}</p>}
      </AuthGate>,
    ))
    expect(replacePath).toHaveBeenCalledWith('/liff/account')
    expect(container.textContent).toContain('unlinking')
  })
})

async function clickButton(target: HTMLElement, label: string) {
  const button = [...target.querySelectorAll('button')].find((item) => item.textContent === label)
  await act(async () => button?.click())
}

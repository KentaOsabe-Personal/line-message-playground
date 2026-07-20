import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import AccountConsole from '../src/AccountConsole'
import { AccountApiError } from '../src/accountApi'
import type { AccountApiClient } from '../src/accountApi'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channelId = '2c42a18e-2f3d-4dcb-8f13-3cead52af738'
const recipientId = '82e59ae7-b3f7-4298-b9b1-93d15bd42dc6'
const linked = {
  channelId,
  channelLabel: '通知チャネル',
  channelState: 'active' as const,
  linkState: 'linked_enabled' as const,
  friendshipState: 'unknown' as const,
  deliveryAvailable: false,
  recipientId,
}

let container: HTMLDivElement
let root: Root

const api = (overrides: Partial<AccountApiClient> = {}): AccountApiClient => ({
  listChannels: vi.fn().mockResolvedValue([linked]),
  registerRecipient: vi.fn(),
  setRecipientEnabled: vi.fn(),
  unlinkRecipient: vi.fn(),
  previewUnlink: vi.fn(),
  executeUnlink: vi.fn(),
  ...overrides,
})

const click = async (label: string) => {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent === label)
  expect(button).toBeDefined()
  await act(async () => button?.click())
}

describe('AccountConsole', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })

  // テストケース: active ownerがrecipient一覧を表示して無効化する。
  // 期待値: 名称・状態・配信不可を表示し、LINE user IDやopaque IDを画面へ出さない。
  test('renders safe recipient state and applies a target-scoped mutation', async () => {
    const client = api({ setRecipientEnabled: vi.fn().mockResolvedValue({ ...linked, linkState: 'linked_disabled' }) })
    await act(async () => root.render(<AccountConsole
      session={{ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }}
      api={client}
      getAccessToken={() => 'fresh'}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={vi.fn()}
      unlinkReauthenticationReady={false}
      onSessionReceived={vi.fn()}
      refreshSession={vi.fn()}
    />))

    expect(container.textContent).toContain('通知チャネル')
    expect(container.textContent).toContain('配信不可')
    expect(container.textContent).toContain('友だち状態: 不明')
    expect(container.textContent).not.toContain(channelId)
    expect(container.textContent).not.toContain(recipientId)
    await click('無効化')
    expect(client.setRecipientEnabled).toHaveBeenCalledWith(recipientId, false)
    expect(container.textContent).toContain('無効')
  })

  // テストケース: ownerが全連携解除previewを確認して実行する。
  // 期待値: 削除範囲と監査保持を表示し、fresh tokenがない限り実行しない。
  test('requires a fresh token after showing a secret-free unlink preview', async () => {
    const client = api({
      previewUnlink: vi.fn().mockResolvedValue({
        displayName: 'Owner', recipientCount: 1, channelLabels: ['通知チャネル'], deliveryAuditRetained: true,
        confirmationToken: 'opaque-confirmation', expiresAt: '2026-07-20T12:00:00+09:00',
      }),
    })
    const reauthenticate = vi.fn()
    await act(async () => root.render(<AccountConsole
      session={{ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }}
      api={client}
      getAccessToken={() => null}
      reauthenticate={reauthenticate}
      reauthenticateForUnlink={vi.fn()}
      unlinkReauthenticationReady={false}
      onSessionReceived={vi.fn()}
      refreshSession={vi.fn()}
    />))

    await click('全連携解除の内容を確認')
    expect(container.textContent).toContain('Owner')
    expect(container.textContent).toContain('通知チャネル')
    expect(container.textContent).toContain('配信監査記録は保持されます')
    expect(container.textContent).not.toContain('opaque-confirmation')
    await click('確認して全連携解除')
    expect(client.executeUnlink).not.toHaveBeenCalled()
    expect(reauthenticate).toHaveBeenCalledTimes(1)
  })

  // テストケース: deauthorization pendingとlocal deletion pendingを再開する。
  // 期待値: 前者だけfresh tokenを送り、後者はtokenなしでローカル削除だけを再試行する。
  test('offers the only recovery action allowed by each pending stage', async () => {
    const deauthApi = api({ executeUnlink: vi.fn().mockResolvedValue({ state: 'pending', stage: 'deauthorization_pending', retryAction: 'reauthenticate' }) })
    const reauthenticateForUnlink = vi.fn()
    await act(async () => root.render(<AccountConsole
      session={{ state: 'unlinking', stage: 'deauthorization_pending', retryAction: 'reauthenticate' }}
      api={deauthApi}
      getAccessToken={() => 'fresh-token'}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={reauthenticateForUnlink}
      unlinkReauthenticationReady={false}
      onSessionReceived={vi.fn()}
      refreshSession={vi.fn()}
    />))
    expect(container.textContent).not.toContain('配信先管理')
    await click('LINEで再認証して解除を再開')
    expect(reauthenticateForUnlink).toHaveBeenCalledTimes(1)
    expect(deauthApi.executeUnlink).not.toHaveBeenCalled()

    await act(async () => root.render(<AccountConsole
      session={{ state: 'unlinking', stage: 'deauthorization_pending', retryAction: 'reauthenticate' }}
      api={deauthApi}
      getAccessToken={() => 'fresh-token'}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={reauthenticateForUnlink}
      unlinkReauthenticationReady={true}
      onSessionReceived={vi.fn()}
      refreshSession={vi.fn()}
    />))
    await click('LINEで再認証して解除を再開')
    expect(deauthApi.executeUnlink).toHaveBeenCalledWith({ userAccessToken: 'fresh-token' })

    const localApi = api({ executeUnlink: vi.fn().mockResolvedValue({ state: 'completed' }) })
    const onSessionReceived = vi.fn()
    await act(async () => root.render(<AccountConsole
      session={{ state: 'unlinking', stage: 'local_deletion_pending', retryAction: 'retry_local_delete' }}
      api={localApi}
      getAccessToken={() => { throw new Error('token must not be read') }}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={vi.fn()}
      unlinkReauthenticationReady={false}
      onSessionReceived={onSessionReceived}
      refreshSession={vi.fn()}
    />))
    await click('ローカル削除を再開')
    expect(localApi.executeUnlink).toHaveBeenCalledWith({})
    expect(onSessionReceived).toHaveBeenCalledWith({ state: 'anonymous' })
  })

  // テストケース: recovery requestが競合として拒否される。
  // 期待値: 同じLINE requestを再送せずsession状態だけを再取得する。
  test('refreshes session state instead of blindly retrying a conflict', async () => {
    const client = api({ executeUnlink: vi.fn().mockRejectedValue(new AccountApiError({ code: 'unlink_in_progress', summary: '処理中です。' }, 409)) })
    const refreshSession = vi.fn().mockResolvedValue(undefined)
    await act(async () => root.render(<AccountConsole
      session={{ state: 'unlinking', stage: 'local_deletion_pending', retryAction: 'retry_local_delete' }}
      api={client}
      getAccessToken={() => null}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={vi.fn()}
      unlinkReauthenticationReady={false}
      onSessionReceived={vi.fn()}
      refreshSession={refreshSession}
    />))
    await click('ローカル削除を再開')
    expect(client.executeUnlink).toHaveBeenCalledTimes(1)
    expect(refreshSession).toHaveBeenCalledTimes(1)
  })

  // テストケース: 初回解除のconfirmationが期限切れとして拒否される。
  // 期待値: 古いconfirmationを破棄し、session再取得や同じtokenの再送ではなくpreview再取得へ戻す。
  test('discards a stale confirmation and returns to preview', async () => {
    const client = api({
      previewUnlink: vi.fn().mockResolvedValue({
        displayName: 'Owner', recipientCount: 1, channelLabels: ['通知チャネル'], deliveryAuditRetained: true,
        confirmationToken: 'expired-confirmation', expiresAt: '2026-07-20T12:00:00+09:00',
      }),
      executeUnlink: vi.fn().mockRejectedValue(new AccountApiError({
        code: 'stale_confirmation', summary: 'もう一度内容を確認してください。',
      }, 409)),
    })
    const refreshSession = vi.fn()
    await act(async () => root.render(<AccountConsole
      session={{ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }}
      api={client}
      getAccessToken={() => 'fresh-token'}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={vi.fn()}
      unlinkReauthenticationReady={false}
      onSessionReceived={vi.fn()}
      refreshSession={refreshSession}
    />))
    await click('全連携解除の内容を確認')
    await click('確認して全連携解除')

    expect(container.textContent).toContain('もう一度内容を確認してください。')
    expect(container.textContent).toContain('全連携解除の内容を確認')
    expect(container.textContent).not.toContain('確認して全連携解除')
    expect(refreshSession).not.toHaveBeenCalled()
  })

  // テストケース: pending resumeでBackendがaccess token失効を返す。
  // 期待値: requestを再送せずLIFF再認証を開始する。
  test('starts LIFF reauthentication after invalid line proof', async () => {
    const client = api({ executeUnlink: vi.fn().mockRejectedValue(new AccountApiError({
      code: 'invalid_line_proof', summary: 'LINEで再認証してください。',
    }, 401)) })
    const reauthenticateForUnlink = vi.fn()
    await act(async () => root.render(<AccountConsole
      session={{ state: 'unlinking', stage: 'deauthorization_pending', retryAction: 'reauthenticate' }}
      api={client}
      getAccessToken={() => 'expired-token'}
      reauthenticate={vi.fn()}
      reauthenticateForUnlink={reauthenticateForUnlink}
      unlinkReauthenticationReady={true}
      onSessionReceived={vi.fn()}
      refreshSession={vi.fn()}
    />))
    await click('LINEで再認証して解除を再開')

    expect(client.executeUnlink).toHaveBeenCalledTimes(1)
    expect(reauthenticateForUnlink).toHaveBeenCalledTimes(1)
  })
})

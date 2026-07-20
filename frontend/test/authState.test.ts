import { describe, expect, test } from 'vitest'

import { initialAuthState, transitionAuth } from '../src/authState'

describe('LiffAuthController state', () => {
  // テストケース: 外部browser未login、明示login復帰、Backend本人確認成功を順に適用する。
  // 期待値: login_requiredからverifyingを経てauthenticatedへ一意に遷移する。
  test('transitions an external browser login return to authenticated', () => {
    const loginRequired = transitionAuth(initialAuthState, { type: 'login_required' })
    const verifying = transitionAuth(loginRequired, { type: 'verification_started' })
    const authenticated = transitionAuth(verifying, {
      type: 'session_received',
      session: { state: 'authenticated', profile: { displayName: 'Owner', linked: true } },
    })

    expect(loginRequired).toEqual({ kind: 'login_required' })
    expect(verifying).toEqual({ kind: 'verifying' })
    expect(authenticated).toEqual({ kind: 'authenticated', profile: { displayName: 'Owner', linked: true } })
  })

  // テストケース: login取消、401 expiry、初期化失敗、unlink pendingを適用する。
  // 期待値: いずれもauthenticatedを維持せず、安全な再試行可否とstageを返す。
  test('fails closed for cancellation, expiry, initialization failure and unlinking', () => {
    const authenticated = { kind: 'authenticated', profile: { displayName: 'Owner', linked: true } } as const
    expect(transitionAuth(authenticated, { type: 'login_cancelled' })).toEqual({ kind: 'anonymous' })
    expect(transitionAuth(authenticated, { type: 'session_invalidated' })).toEqual({ kind: 'login_required' })
    expect(transitionAuth(initialAuthState, { type: 'failed', code: 'initialization_failed', retryable: true })).toEqual({ kind: 'error', code: 'initialization_failed', retryable: true })
    expect(transitionAuth(initialAuthState, {
      type: 'session_received',
      session: { state: 'unlinking', stage: 'deauthorization_pending', retryAction: 'reauthenticate' },
    })).toEqual({ kind: 'unlinking', stage: 'deauthorization_pending' })
  })
})

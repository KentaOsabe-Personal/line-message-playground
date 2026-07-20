import { useCallback, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'

import { AuthApiError, createAuthApiClient } from './authApi'
import type { AuthApiClient } from './authApi'
import { initialAuthState, transitionAuth } from './authState'
import type { SafeAuthErrorCode } from './authState'
import { createProtectedHttpClient } from './httpApi'
import { createLinePlatformLiffAdapter } from './liffClient'
import type { LinePlatformLiffAdapter } from './liffClient'
import { createLiffRuntimeConfig } from './liffConfig'
import type { LiffRuntimeConfig } from './liffConfig'

type Props = {
  children: ReactNode
  config?: LiffRuntimeConfig
  liffAdapter?: LinePlatformLiffAdapter
  authApi?: AuthApiClient
}

const errorMessage: Record<SafeAuthErrorCode, string> = {
  configuration_invalid: 'LIFFの公開設定を確認できません。',
  initialization_failed: 'LINEログインを初期化できませんでした。',
  token_unavailable: 'LINEの本人確認情報を取得できませんでした。',
  verification_failed: '本人確認を完了できませんでした。',
  logout_failed: 'この端末からログアウトできませんでした。',
}

export default function AuthGate({ children, config, liffAdapter, authApi }: Props) {
  const [state, dispatch] = useReducer(transitionAuth, initialAuthState)
  const generation = useRef(0)
  const adapter = useMemo(() => liffAdapter ?? createLinePlatformLiffAdapter(), [liffAdapter])
  const api = useMemo(() => authApi ?? createAuthApiClient(createProtectedHttpClient({
    onSessionInvalid: () => {
      generation.current += 1
      dispatch({ type: 'session_invalidated' })
    },
  })), [authApi])

  const runtimeConfig = useCallback(() => config ?? createLiffRuntimeConfig({
    liffId: import.meta.env.VITE_LIFF_ID,
    currentOrigin: window.location.origin,
    currentPathname: window.location.pathname,
  }), [config])

  const authenticate = useCallback(async () => {
    const currentGeneration = ++generation.current
    const isCurrent = () => generation.current === currentGeneration
    dispatch({ type: 'restart' })
    let runtime: LiffRuntimeConfig
    try {
      runtime = runtimeConfig()
    } catch {
      if (isCurrent()) dispatch({ type: 'failed', code: 'configuration_invalid', retryable: false })
      return
    }

    try {
      await adapter.initialize(runtime.liffId)
      if (!isCurrent()) return
      const session = await api.bootstrap()
      if (!isCurrent()) return
      if (session.state !== 'anonymous') {
        dispatch({ type: 'session_received', session })
        return
      }
      if (!adapter.isLoggedIn()) {
        dispatch({ type: 'login_required' })
        return
      }
      const idToken = adapter.getIdToken()
      if (idToken === null) {
        dispatch({ type: 'failed', code: 'token_unavailable', retryable: true })
        return
      }
      dispatch({ type: 'verification_started' })
      try {
        const verifiedSession = await api.login(idToken)
        if (isCurrent()) dispatch({ type: 'session_received', session: verifiedSession })
      } catch (error) {
        if (!isCurrent()) return
        if (error instanceof AuthApiError && error.httpStatus === 401) {
          dispatch({ type: 'session_invalidated' })
        } else {
          dispatch({
            type: 'failed',
            code: error instanceof AuthApiError ? 'verification_failed' : 'initialization_failed',
            retryable: true,
          })
        }
      }
    } catch (error) {
      if (!isCurrent()) return
      if (error instanceof AuthApiError && error.httpStatus === 401) {
        dispatch({ type: 'session_invalidated' })
      } else {
        dispatch({ type: 'failed', code: 'initialization_failed', retryable: true })
      }
    }
  }, [adapter, api, runtimeConfig])

  useEffect(() => {
    void authenticate()
    return () => { generation.current += 1 }
  }, [authenticate])

  const startLogin = () => {
    try {
      generation.current += 1
      dispatch({ type: 'verification_started' })
      adapter.login(runtimeConfig().redirectUri)
    } catch {
      dispatch({ type: 'failed', code: 'initialization_failed', retryable: true })
    }
  }

  const logout = async () => {
    const currentGeneration = ++generation.current
    dispatch({ type: 'verification_started' })
    try {
      const session = await api.logout()
      if (generation.current === currentGeneration) dispatch({ type: 'session_received', session })
    } catch (error) {
      if (generation.current !== currentGeneration) return
      if (error instanceof AuthApiError && error.httpStatus === 401) {
        dispatch({ type: 'session_invalidated' })
      } else {
        dispatch({ type: 'failed', code: 'logout_failed', retryable: true })
      }
    }
  }

  if (state.kind === 'authenticated') {
    return (
      <section className="auth-console" aria-label="認証済みコンソール">
        <header className="auth-profile">
          <p><span className="eyebrow">認証済みowner</span><strong>{state.profile.displayName}</strong></p>
          <button type="button" className="secondary" onClick={() => void logout()}>この端末からログアウト</button>
        </header>
        {children}
      </section>
    )
  }
  if (state.kind === 'login_required' || state.kind === 'anonymous') {
    return (
      <section className="auth-gate" aria-live="polite">
        <h2>LINEログインが必要です</h2>
        <p>本人確認が完了すると管理画面を利用できます。</p>
        <button type="button" onClick={startLogin}>LINEでログイン</button>
      </section>
    )
  }
  if (state.kind === 'unlinking') {
    return (
      <section className="auth-gate" aria-live="polite">
        <h2>全連携解除を処理中です</h2>
        <p>{state.stage === 'deauthorization_pending' ? 'LINEでの再認証が必要です。' : 'ローカルデータの削除を再開できます。'}</p>
      </section>
    )
  }
  if (state.kind === 'error') {
    return (
      <section className="auth-gate" role="alert">
        <h2>本人確認を完了できません</h2>
        <p>{errorMessage[state.code]}</p>
        {state.retryable && <button type="button" onClick={() => void authenticate()}>再試行</button>}
      </section>
    )
  }
  return <section className="auth-gate" aria-live="polite"><p>{state.kind === 'verifying' ? '本人確認中です…' : 'LINEログインを初期化しています…'}</p></section>
}

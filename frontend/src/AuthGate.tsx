import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { AuthApiError, createAuthApiClient } from './authApi'
import type { AuthApiClient } from './authApi'
import type { SessionStatus } from './authDto'
import { initialAuthState, transitionAuth } from './authState'
import type { SafeAuthErrorCode } from './authState'
import { createProtectedHttpClient } from './httpApi'
import { createLinePlatformLiffAdapter } from './liffClient'
import type { LinePlatformLiffAdapter } from './liffClient'
import { createLiffRuntimeConfig } from './liffConfig'
import type { LiffRuntimeConfig } from './liffConfig'
import { parseProtectedPath } from './appRoutes'
import { createOwnerSessionStorage } from './ownerSessionStorage'
import type { OwnerSessionStorage } from './ownerSessionStorage'

export type AuthGateProps = {
  children: ReactNode | ((context: AuthGateContext) => ReactNode)
  config?: LiffRuntimeConfig
  liffAdapter?: LinePlatformLiffAdapter
  authApi?: AuthApiClient
  currentPathname?: string
  replacePath?: (path: string) => void
  ownerStorage?: OwnerSessionStorage
}

export type AuthGateContext = {
  session: Extract<SessionStatus, { state: 'authenticated' | 'unlinking' }>
  logout: () => Promise<void>
  getAccessToken: () => string | null
  reauthenticate: () => void
  reauthenticateForUnlink: () => void
  unlinkReauthenticationReady: boolean
  onSessionReceived: (session: SessionStatus) => void
  refreshSession: () => Promise<void>
}

const errorMessage: Record<SafeAuthErrorCode, string> = {
  configuration_invalid: 'LIFFの公開設定を確認できません。',
  initialization_failed: 'LINEログインを初期化できませんでした。',
  token_unavailable: 'LINEの本人確認情報を取得できませんでした。',
  verification_failed: '本人確認を完了できませんでした。',
  logout_failed: 'この端末からログアウトできませんでした。',
}

export default function AuthGate({
  children,
  config,
  liffAdapter,
  authApi,
  currentPathname = window.location.pathname,
  replacePath = (path) => window.history.replaceState(null, '', path),
  ownerStorage,
}: AuthGateProps) {
  const [state, dispatch] = useReducer(transitionAuth, initialAuthState)
  const [unlinkReauthenticationReady, setUnlinkReauthenticationReady] = useState(false)
  const generation = useRef(0)
  const adapter = useMemo(() => liffAdapter ?? createLinePlatformLiffAdapter(), [liffAdapter])
  const storage = useMemo(() => ownerStorage ?? createOwnerSessionStorage(), [ownerStorage])
  const api = useMemo(
    () =>
      authApi ??
      createAuthApiClient(
        createProtectedHttpClient({
          onSessionInvalid: () => {
            generation.current += 1
            dispatch({ type: 'session_invalidated' })
          },
        }),
      ),
    [authApi],
  )

  const runtimeConfig = useCallback(
    () =>
      config ??
      createLiffRuntimeConfig({
        liffId: import.meta.env.VITE_LIFF_ID,
        currentOrigin: window.location.origin,
        currentPathname: window.location.pathname,
      }),
    [config],
  )

  const authenticate = useCallback(async () => {
    const currentGeneration = ++generation.current
    const isCurrent = () => generation.current === currentGeneration
    dispatch({ type: 'restart' })
    setUnlinkReauthenticationReady(false)
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
        if (adapter.isLoggedIn() && !(await adapter.ensureProfilePermission())) {
          if (isCurrent()) dispatch({ type: 'failed', code: 'token_unavailable', retryable: true })
          return
        }
        if (!isCurrent()) return
        if (
          session.state === 'unlinking' &&
          session.stage === 'deauthorization_pending' &&
          storage.readUnlinkReauthenticationPending() &&
          adapter.getAccessToken() !== null
        ) {
          storage.setUnlinkReauthenticationPending(false)
          setUnlinkReauthenticationReady(true)
        } else {
          storage.setUnlinkReauthenticationPending(false)
        }
        dispatch({ type: 'session_received', session })
        return
      }
      if (!adapter.isLoggedIn()) {
        dispatch({ type: 'login_required' })
        return
      }
      if (!(await adapter.ensureProfilePermission())) {
        if (isCurrent()) dispatch({ type: 'failed', code: 'token_unavailable', retryable: true })
        return
      }
      if (!isCurrent()) return
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
  }, [adapter, api, runtimeConfig, storage])

  useEffect(() => {
    void authenticate()
    return () => {
      generation.current += 1
    }
  }, [authenticate])

  const startLogin = () => {
    try {
      const returnPath = parseProtectedPath(currentPathname)
      if (returnPath !== null) storage.saveReturnPath(returnPath)
      generation.current += 1
      dispatch({ type: 'verification_started' })
      adapter.login(runtimeConfig().redirectUri)
    } catch {
      dispatch({ type: 'failed', code: 'initialization_failed', retryable: true })
    }
  }

  const finishOwnerSession = useCallback(
    (error: SafeAuthErrorCode | null = null) => {
      storage.clearAll()
      setUnlinkReauthenticationReady(false)
      replacePath('/liff')
      if (error === null) dispatch({ type: 'session_received', session: { state: 'anonymous' } })
      else dispatch({ type: 'failed', code: error, retryable: false })
    },
    [replacePath, storage],
  )

  const logout = async () => {
    const currentGeneration = ++generation.current
    dispatch({ type: 'verification_started' })
    try {
      await api.logout()
      if (generation.current !== currentGeneration) return
      try {
        adapter.logout()
        finishOwnerSession()
      } catch {
        finishOwnerSession('logout_failed')
      }
    } catch (error) {
      if (generation.current !== currentGeneration) return
      if (error instanceof AuthApiError && error.httpStatus === 401) {
        try {
          adapter.logout()
        } catch {
          /* Local owner state is still cleared below. */
        }
        finishOwnerSession()
      } else {
        dispatch({ type: 'failed', code: 'logout_failed', retryable: true })
      }
    }
  }

  const onSessionReceived = useCallback(
    (session: SessionStatus) => {
      generation.current += 1
      setUnlinkReauthenticationReady(false)
      if (session.state === 'anonymous') {
        storage.clearAll()
        replacePath('/liff')
      } else if (session.state === 'unlinking' && currentPathname !== '/liff/account') {
        replacePath('/liff/account')
      }
      dispatch({ type: 'session_received', session })
    },
    [currentPathname, replacePath, storage],
  )

  const refreshSession = useCallback(async () => {
    const currentGeneration = ++generation.current
    try {
      const session = await api.bootstrap()
      if (generation.current === currentGeneration) dispatch({ type: 'session_received', session })
    } catch (error) {
      if (generation.current !== currentGeneration) return
      if (error instanceof AuthApiError && error.httpStatus === 401) {
        dispatch({ type: 'session_invalidated' })
      } else {
        dispatch({ type: 'failed', code: 'verification_failed', retryable: true })
      }
    }
  }, [api])

  const getAccessToken = useCallback(() => adapter.getAccessToken(), [adapter])

  const reauthenticate = useCallback(() => {
    generation.current += 1
    dispatch({ type: 'verification_started' })
    try {
      adapter.reauthenticate(runtimeConfig().redirectUri)
    } catch {
      dispatch({ type: 'failed', code: 'initialization_failed', retryable: true })
    }
  }, [adapter, runtimeConfig])

  const reauthenticateForUnlink = useCallback(() => {
    generation.current += 1
    setUnlinkReauthenticationReady(false)
    storage.setUnlinkReauthenticationPending(true)
    dispatch({ type: 'verification_started' })
    try {
      adapter.reauthenticate(runtimeConfig().redirectUri)
    } catch {
      storage.setUnlinkReauthenticationPending(false)
      dispatch({ type: 'failed', code: 'initialization_failed', retryable: true })
    }
  }, [adapter, runtimeConfig, storage])

  useEffect(() => {
    if (state.kind === 'unlinking') {
      if (currentPathname !== '/liff/account') replacePath('/liff/account')
      return
    }
    if (state.kind !== 'authenticated' || currentPathname !== '/liff') return
    const returnPath = storage.consumeReturnPath()
    if (returnPath !== null && returnPath !== '/liff') replacePath(returnPath)
  }, [currentPathname, replacePath, state.kind, storage])

  const renderProtectedContent = (
    session: Extract<SessionStatus, { state: 'authenticated' | 'unlinking' }>,
  ) =>
    typeof children === 'function'
      ? children({
          session,
          logout,
          getAccessToken,
          reauthenticate,
          reauthenticateForUnlink,
          unlinkReauthenticationReady,
          onSessionReceived,
          refreshSession,
        })
      : session.state === 'authenticated'
        ? children
        : null

  if (state.kind === 'authenticated') {
    const session: Extract<SessionStatus, { state: 'authenticated' }> = {
      state: 'authenticated',
      profile: state.profile,
    }
    return <>{renderProtectedContent(session)}</>
  }
  if (state.kind === 'login_required' || state.kind === 'anonymous') {
    return (
      <main className="auth-page">
        <section className="auth-gate auth-card" aria-live="polite">
          <div className="auth-brand" aria-hidden="true">
            <span className="auth-brand-mark">
              <svg viewBox="0 0 32 32">
                <path d="M27.8 14.1c0-6.1-5.3-11-11.8-11S4.2 8 4.2 14.1c0 5.5 4.3 10.1 10.1 10.9.4.1.9.3 1 .7.1.3.1.9 0 1.3l-.2 1.2c-.1.4-.3 1.5 1.3.8 1.6-.7 8.7-5.1 11.9-8.8 2.2-2.4 3.5-4.8 3.5-7.8Z" />
              </svg>
            </span>
            <span>LINE Message Playground</span>
          </div>
          <div className="auth-copy">
            <p className="auth-eyebrow">OWNER CONSOLE</p>
            <h1>
              LINEの検証環境へ
              <br />
              ようこそ。
            </h1>
            <p>チャネル管理からテスト配信まで、あなた専用のワークスペースで安全に試せます。</p>
          </div>
          <div className="auth-action-panel">
            <span className="auth-lock-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M12 2.75a6 6 0 0 0-6 6v2.5H4.75A1.75 1.75 0 0 0 3 13v7.25C3 21.22 3.78 22 4.75 22h14.5c.97 0 1.75-.78 1.75-1.75V13c0-.97-.78-1.75-1.75-1.75H18v-2.5a6 6 0 0 0-6-6Zm-4 6a4 4 0 0 1 8 0v2.5H8v-2.5Zm4 6.1a1.65 1.65 0 0 1 .75 3.12v1.28h-1.5v-1.28A1.65 1.65 0 0 1 12 14.85Z" />
              </svg>
            </span>
            <p className="auth-action-eyebrow">OWNER ACCESS</p>
            <h2>管理画面へログイン</h2>
            <p className="auth-action-copy">登録済みのLINEアカウントで本人確認を行ってください。</p>
            <button className="line-login-button" type="button" onClick={startLogin}>
              <span className="line-login-icon" aria-hidden="true">
                <svg viewBox="0 0 32 32">
                  <path d="M27.8 14.1c0-6.1-5.3-11-11.8-11S4.2 8 4.2 14.1c0 5.5 4.3 10.1 10.1 10.9.4.1.9.3 1 .7.1.3.1.9 0 1.3l-.2 1.2c-.1.4-.3 1.5 1.3.8 1.6-.7 8.7-5.1 11.9-8.8 2.2-2.4 3.5-4.8 3.5-7.8Z" />
                </svg>
              </span>
              <span>LINEでログイン</span>
              <span className="line-login-arrow" aria-hidden="true">
                →
              </span>
            </button>
            <p className="auth-assurance">本人確認にはLINE Loginを使用します</p>
          </div>
        </section>
      </main>
    )
  }
  if (state.kind === 'unlinking') {
    return (
      <>
        {renderProtectedContent({
          state: 'unlinking',
          stage: state.stage,
          retryAction: state.retryAction,
        }) ?? (
          <section className="auth-gate" aria-live="polite">
            <h2>全連携解除を処理中です</h2>
            <p>
              {state.stage === 'deauthorization_pending'
                ? 'LINEでの再認証が必要です。'
                : 'ローカルデータの削除を再開できます。'}
            </p>
          </section>
        )}
      </>
    )
  }
  if (state.kind === 'error') {
    return (
      <section className="auth-gate" role="alert">
        <h2>本人確認を完了できません</h2>
        <p>{errorMessage[state.code]}</p>
        {state.retryable && (
          <button type="button" onClick={() => void authenticate()}>
            再試行
          </button>
        )}
      </section>
    )
  }
  return (
    <section className="auth-gate" aria-live="polite">
      <p>{state.kind === 'verifying' ? '本人確認中です…' : 'LINEログインを初期化しています…'}</p>
    </section>
  )
}

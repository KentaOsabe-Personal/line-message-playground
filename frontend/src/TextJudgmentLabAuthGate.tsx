import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { createLinePlatformLiffAdapter } from './liffClient'
import type { LinePlatformLiffAdapter } from './liffClient'
import { createLabHttpClient, LabHttpError } from './textJudgmentLabApi'
import type { LabHttpClient } from './textJudgmentLabApi'
import { createTextJudgmentLabConfig } from './textJudgmentLabConfig'
import type { TextJudgmentLabConfig } from './textJudgmentLabConfig'
import type { LabAccessState } from './textJudgmentLabTypes'

export type LabAuthContext = Readonly<{
  access: LabAccessState
  getValidIdToken: () => string | null
  recheckAccess: () => Promise<void>
  reauthenticate: () => void
  invalidateAccess: (reason: 'auth_expired' | 'access_unavailable') => void
}>

export type TextJudgmentLabAuthGateProps = Readonly<{
  children: ReactNode | ((context: LabAuthContext) => ReactNode)
  config?: TextJudgmentLabConfig
  liffAdapter?: LinePlatformLiffAdapter
  api?: LabHttpClient
}>

type ExpiryBaseline = Readonly<{
  remainingMs: number
  monotonicMs: number
  wallMs: number
}>

export const conservativeRemainingMs = (
  initialRemainingMs: number,
  monotonicStartedMs: number,
  wallStartedMs: number,
  monotonicNowMs: number,
  wallNowMs: number,
): number =>
  initialRemainingMs -
  Math.max(Math.max(0, monotonicNowMs - monotonicStartedMs), Math.max(0, wallNowMs - wallStartedMs))

const remainingAt = (baseline: ExpiryBaseline, monotonicMs: number, wallMs: number): number =>
  conservativeRemainingMs(
    baseline.remainingMs,
    baseline.monotonicMs,
    baseline.wallMs,
    monotonicMs,
    wallMs,
  )

export default function TextJudgmentLabAuthGate({
  children,
  config,
  liffAdapter,
  api,
}: TextJudgmentLabAuthGateProps) {
  const [access, setAccess] = useState<LabAccessState>({ kind: 'initializing' })
  const adapter = useMemo(() => liffAdapter ?? createLinePlatformLiffAdapter(), [liffAdapter])
  const client = useMemo(() => api ?? createLabHttpClient(), [api])
  const token = useRef<string | null>(null)
  const expiry = useRef<ExpiryBaseline | null>(null)
  const generation = useRef(0)
  const hasAuthorized = useRef(false)
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const runtimeConfig = useCallback(
    () =>
      config ??
      createTextJudgmentLabConfig({
        liffId: import.meta.env.VITE_TEXT_JUDGMENT_LAB_LIFF_ID,
        currentOrigin: window.location.origin,
      }),
    [config],
  )

  const clearExpiryTimer = useCallback(() => {
    if (expiryTimer.current !== null) clearTimeout(expiryTimer.current)
    expiryTimer.current = null
  }, [])

  const requireReauthentication = useCallback(() => {
    clearExpiryTimer()
    token.current = null
    expiry.current = null
    setAccess({ kind: 'reauthentication_required' })
  }, [clearExpiryTimer])

  const installExpiry = useCallback(
    (remainingMs: number, expiresAt: string) => {
      clearExpiryTimer()
      const bounded = Math.max(0, remainingMs)
      expiry.current = { remainingMs: bounded, monotonicMs: performance.now(), wallMs: Date.now() }
      if (bounded <= 0) {
        requireReauthentication()
        return
      }
      setAccess({ kind: 'authorized', expiresAt, remainingMs: bounded })
      expiryTimer.current = setTimeout(requireReauthentication, bounded)
    },
    [clearExpiryTimer, requireReauthentication],
  )

  const checkAccess = useCallback(
    async (showChecking = true) => {
      const currentGeneration = ++generation.current
      const currentToken = token.current
      if (currentToken === null) {
        requireReauthentication()
        return
      }
      if (showChecking) setAccess({ kind: 'initializing' })
      const monotonicStarted = performance.now()
      const wallStarted = Date.now()
      try {
        const result = await client.checkAccess(currentToken)
        if (generation.current !== currentGeneration) return
        const roundTripMs = Math.max(
          0,
          performance.now() - monotonicStarted,
          Date.now() - wallStarted,
        )
        const serverRemainingMs = Date.parse(result.expiresAt) - Date.parse(result.serverTime)
        hasAuthorized.current = true
        installExpiry(serverRemainingMs - roundTripMs, result.expiresAt)
      } catch (error) {
        if (generation.current !== currentGeneration) return
        clearExpiryTimer()
        expiry.current = null
        if (error instanceof LabHttpError && error.code === 'reauthentication_required') {
          requireReauthentication()
        } else if (
          error instanceof LabHttpError &&
          (error.code === 'wrong_channel' || error.code === 'not_allowed')
        ) {
          token.current = null
          setAccess({ kind: 'denied', reason: error.code })
        } else {
          setAccess({ kind: 'unavailable' })
        }
      }
    },
    [clearExpiryTimer, client, installExpiry, requireReauthentication],
  )

  useEffect(() => {
    const currentGeneration = ++generation.current
    const initialize = async () => {
      try {
        const runtime = runtimeConfig()
        await adapter.initialize(runtime.liffId)
        if (generation.current !== currentGeneration) return
        const idToken = adapter.getIdToken()
        if (idToken === null) {
          requireReauthentication()
          return
        }
        token.current = idToken
        await checkAccess(false)
      } catch {
        if (generation.current === currentGeneration) setAccess({ kind: 'unavailable' })
      }
    }
    void initialize()
    return () => {
      generation.current += 1
      clearExpiryTimer()
    }
  }, [adapter, checkAccess, clearExpiryTimer, requireReauthentication, runtimeConfig])

  const getValidIdToken = useCallback(() => {
    const baseline = expiry.current
    if (
      token.current === null ||
      baseline === null ||
      remainingAt(baseline, performance.now(), Date.now()) <= 0
    ) {
      requireReauthentication()
      return null
    }
    return token.current
  }, [requireReauthentication])

  useEffect(() => {
    const recheck = () => {
      if (document.visibilityState === 'visible' && token.current !== null) void checkAccess()
    }
    const pageShow = () => {
      if (token.current !== null) void checkAccess()
    }
    document.addEventListener('visibilitychange', recheck)
    window.addEventListener('pageshow', pageShow)
    return () => {
      document.removeEventListener('visibilitychange', recheck)
      window.removeEventListener('pageshow', pageShow)
    }
  }, [checkAccess])

  const reauthenticate = useCallback(() => {
    generation.current += 1
    requireReauthentication()
    try {
      adapter.reauthenticate(runtimeConfig().entryUrl)
    } catch {
      setAccess({ kind: 'unavailable' })
    }
  }, [adapter, requireReauthentication, runtimeConfig])

  const invalidateAccess = useCallback(
    (reason: 'auth_expired' | 'access_unavailable') => {
      if (reason === 'auth_expired') {
        requireReauthentication()
        return
      }
      clearExpiryTimer()
      expiry.current = null
      setAccess({ kind: 'unavailable' })
    },
    [clearExpiryTimer, requireReauthentication],
  )

  const context = useMemo<LabAuthContext>(
    () => ({
      access,
      getValidIdToken,
      recheckAccess: () => checkAccess(),
      reauthenticate,
      invalidateAccess,
    }),
    [access, checkAccess, getValidIdToken, invalidateAccess, reauthenticate],
  )
  const protectedContent = typeof children === 'function' ? children(context) : children
  const showContent = hasAuthorized.current

  return (
    <>
      {access.kind === 'initializing' && (
        <p role="status">
          利用資格を確認しています…
          {showContent && '会話は読取専用です。'}
        </p>
      )}
      {access.kind === 'unavailable' && (
        <section role="alert">
          <p>利用資格を確認できませんでした。会話は読取専用です。</p>
          <button type="button" onClick={() => void checkAccess()}>
            利用確認を再試行
          </button>
        </section>
      )}
      {access.kind === 'reauthentication_required' && (
        <section role="alert">
          <p>再認証が必要です。会話は読取専用です。</p>
          <button type="button" onClick={reauthenticate}>
            再認証
          </button>
        </section>
      )}
      {access.kind === 'denied' && (
        <section role="alert">
          <p>
            {access.reason === 'wrong_channel'
              ? '対応するLINEミニアプリから開き直してください。'
              : 'このラボは利用できません。'}
            {showContent && '会話は読取専用です。'}
          </p>
        </section>
      )}
      {(access.kind === 'authorized' || showContent) && protectedContent}
    </>
  )
}

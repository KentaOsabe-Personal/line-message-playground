import { useCallback, useLayoutEffect, useRef, useState } from 'react'

import type { LabAuthContext } from './TextJudgmentLabAuthGate'
import { LabHttpError, type LabHttpClient } from './textJudgmentLabApi'
import {
  findSuccessfulResult,
  initialLabState,
  isValidDraft,
  transition,
  type LabAction,
  type LabState,
  type ResultRef,
  type Submission,
} from './textJudgmentLabState'

export type SelectionOutcome = 'selected' | 'confirmation_required' | 'ignored'
type Job = {
  submission: Submission
  controller: AbortController
  timer: ReturnType<typeof setTimeout> | null
  monotonicStartedMs: number
  wallStartedMs: number
}
const timeoutMessage = '時間内に判定できませんでした。もう一度送信してください。'
const accessMessage = '利用資格を確認できませんでした。再認証・利用確認後に送信してください。'
const interruptedMessage =
  '利用資格を確認できないため判定を中断しました。再認証・利用確認後に送信してください。'

export function useTextJudgmentLab(api: LabHttpClient, context: LabAuthContext) {
  const [state, setState] = useState(initialLabState)
  const currentState = useRef(state)
  const currentContext = useRef(context)
  const mounted = useRef(false)
  const running = useRef<Job | null>(null)
  const sequence = useRef(0)

  // 同一イベント内の編集・連打にも、最新の純粋状態を同期的に適用する。
  const dispatch = useCallback((action: LabAction): LabState => {
    const next = transition(currentState.current, action)
    currentState.current = next
    setState(next)
    return next
  }, [])
  const release = useCallback((job: Job, abort: boolean) => {
    if (running.current !== job) return
    running.current = null
    if (job.timer !== null) clearTimeout(job.timer)
    if (abort) job.controller.abort()
  }, [])
  const fail = useCallback(
    (job: Job, message: string, abort: boolean) => {
      if (running.current !== job) return
      release(job, abort)
      if (mounted.current) dispatch({ type: 'fail', id: job.submission.id, message })
    },
    [dispatch, release],
  )

  useLayoutEffect(() => {
    currentContext.current = context
    if (context.access.kind !== 'authorized' && running.current)
      fail(running.current, interruptedMessage, true)
  }, [context, fail])
  useLayoutEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (running.current) release(running.current, true)
    }
  }, [release])

  const editable = () =>
    mounted.current && currentContext.current.access.kind === 'authorized' && !running.current
  const setDraft = (draft: string) => {
    if (editable()) dispatch({ type: 'edit', draft })
  }
  const selectSource = (source: ResultRef, replacementConfirmed = false): SelectionOutcome => {
    if (!editable() || !findSuccessfulResult(currentState.current, source)) return 'ignored'
    const composer = currentState.current.composer
    if (composer.kind !== 'editing') return 'ignored'
    if (composer.draft !== '' && !replacementConfirmed) return 'confirmation_required'
    dispatch({ type: 'select', source, replacementConfirmed })
    return 'selected'
  }
  const cancelComparison = () => {
    if (editable()) dispatch({ type: 'cancel_comparison' })
  }
  const completionAllowed = (job: Job): boolean => {
    if (running.current !== job || !mounted.current) return false
    const auth = currentContext.current
    if (auth.access.kind !== 'authorized') {
      fail(job, interruptedMessage, true)
      return false
    }
    if (!auth.getValidIdToken()) {
      fail(job, accessMessage, true)
      auth.invalidateAccess('auth_expired')
      return false
    }
    // バックグラウンドでtimer通知が遅れても期限後の結果は採用しない。
    const elapsed = Math.max(
      performance.now() - job.monotonicStartedMs,
      Date.now() - job.wallStartedMs,
    )
    if (elapsed >= 15000) {
      fail(job, timeoutMessage, true)
      return false
    }
    return true
  }
  const submit = async () => {
    const composer = currentState.current.composer
    if (!editable() || composer.kind !== 'editing' || !isValidDraft(composer.draft)) return
    const token = currentContext.current.getValidIdToken()
    if (!token) {
      currentContext.current.invalidateAccess('auth_expired')
      return
    }
    const next = dispatch({ type: 'start', id: ++sequence.current })
    if (next.composer.kind !== 'pending') return
    const job: Job = {
      submission: next.composer.submission,
      controller: new AbortController(),
      timer: null,
      monotonicStartedMs: performance.now(),
      wallStartedMs: Date.now(),
    }
    running.current = job
    job.timer = setTimeout(() => fail(job, timeoutMessage, true), 15000)
    try {
      const result = await api.judge(
        token,
        { contractVersion: 3, text: job.submission.text },
        job.controller.signal,
      )
      if (!completionAllowed(job)) return
      release(job, false)
      dispatch({ type: 'succeed', id: job.submission.id, result })
    } catch (error) {
      if (!completionAllowed(job)) return
      const code = error instanceof LabHttpError ? error.code : 'judgment_failed'
      const authFailed = code === 'reauthentication_required'
      const unavailable = ['access_unavailable', 'not_allowed', 'wrong_channel'].includes(code)
      fail(
        job,
        code === 'rate_limited'
          ? '送信回数が上限に達しました。少し待ってから送信してください。'
          : authFailed || unavailable
            ? accessMessage
            : '判定できませんでした。入力は残しています。もう一度送信してください。',
        authFailed || unavailable,
      )
      if (authFailed || unavailable)
        currentContext.current.invalidateAccess(authFailed ? 'auth_expired' : 'access_unavailable')
    }
  }
  // 比較画面への統合までは、通常履歴の表示を正本から導出する。
  const turns = state.entries.flatMap((entry) =>
    entry.kind === 'single'
      ? [
          {
            id: entry.id,
            text: entry.text,
            result: entry.outcome.kind === 'succeeded' ? entry.outcome.result : null,
            error: entry.outcome.kind === 'failed' ? entry.outcome.message : null,
          },
        ]
      : [],
  )
  return {
    state,
    pending: state.composer.kind === 'pending',
    setDraft,
    selectSource,
    cancelComparison,
    submit,
    turns,
    draft: state.composer.kind === 'editing' ? state.composer.draft : '',
  }
}

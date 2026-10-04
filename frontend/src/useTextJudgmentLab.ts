import { useCallback, useEffect, useRef, useState } from 'react'

import { LabHttpError, type LabHttpClient } from './textJudgmentLabApi'
import type { JudgmentResponse } from './textJudgmentLabTypes'

export type JudgmentTurn = {
  id: number
  text: string
  result: JudgmentResponse | null
  error: string | null
}

export function useTextJudgmentLab(
  api: LabHttpClient,
  authorized: boolean,
  invalidateAccess: (reason: 'auth_expired' | 'access_unavailable') => void,
) {
  const [turns, setTurns] = useState<JudgmentTurn[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const running = useRef<{ id: number; controller: AbortController; text: string } | null>(null)
  const sequence = useRef(0)
  const finish = useCallback(
    (id: number, result: JudgmentResponse | null, error: string | null) => {
      setTurns((items) => items.map((item) => (item.id === id ? { ...item, result, error } : item)))
      setPending(false)
    },
    [],
  )

  useEffect(() => {
    if (!authorized && running.current) {
      const job = running.current
      running.current = null
      job.controller.abort()
      setDraft(job.text)
      finish(
        job.id,
        null,
        '利用資格を確認できないため判定を中断しました。再認証・利用確認後に送信してください。',
      )
    }
  }, [authorized, finish])

  useEffect(
    () => () => {
      running.current?.controller.abort()
      running.current = null
    },
    [],
  )

  const submit = async (idToken: string) => {
    const text = draft.trim()
    if (!authorized || running.current || !text || [...text].length > 1000) return
    const job = { id: ++sequence.current, controller: new AbortController(), text }
    running.current = job
    setTurns((items) => [...items, { id: job.id, text, result: null, error: null }])
    setDraft('')
    setPending(true)
    const timeout = setTimeout(() => {
      if (running.current !== job) return
      running.current = null
      job.controller.abort()
      setDraft(text)
      finish(job.id, null, '時間内に判定できませんでした。もう一度送信してください。')
    }, 15000)
    try {
      const result = await api.judge(idToken, { contractVersion: 3, text }, job.controller.signal)
      if (running.current !== job) return
      running.current = null
      finish(job.id, result, null)
    } catch (error) {
      if (running.current !== job) return
      running.current = null
      setDraft(text)
      const code = error instanceof LabHttpError ? error.code : 'judgment_failed'
      const authFailed = code === 'reauthentication_required'
      const unavailable = ['access_unavailable', 'not_allowed', 'wrong_channel'].includes(code)
      finish(
        job.id,
        null,
        code === 'rate_limited'
          ? '送信回数が上限に達しました。少し待ってから送信してください。'
          : authFailed || unavailable
            ? '利用資格を確認できませんでした。再認証・利用確認後に送信してください。'
            : '判定できませんでした。入力は残しています。もう一度送信してください。',
      )
      if (authFailed || unavailable)
        invalidateAccess(authFailed ? 'auth_expired' : 'access_unavailable')
    } finally {
      clearTimeout(timeout)
    }
  }
  return { turns, draft, setDraft, pending, submit }
}

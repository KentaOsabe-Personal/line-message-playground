import { useMemo, useSyncExternalStore } from 'react'

import { LabHttpError, type LabHttpClient } from './textJudgmentLabApi'
import {
  applyJudgment,
  createConversationCore,
  currentQuestionId,
  interruptConversation,
  restartConversation,
  selectConversationChoice,
  type ConversationChoice,
  type ConversationCore,
} from './textJudgmentLabState'
import type { JudgmentRequest, JudgmentResponse, QuestionId } from './textJudgmentLabTypes'

export type LabDisplayMessage = Readonly<{
  id: string
  role: 'user'
  text: string
  source: 'text' | 'example' | 'choice'
  choiceQuestion?: QuestionId
  status: 'pending' | 'judged' | 'failed' | 'interrupted'
  judgment?: JudgmentResponse
  uiElapsedMs?: number
}>

export type PendingRequest = Readonly<{
  consultationId: string
  requestId: string
  revision: number
  startedAt: number
  deadlineAt: number
  snapshot: ConversationCore
  messageId: string
  text: string
}>

export type LabControllerState = Readonly<{
  core: ConversationCore
  messages: readonly LabDisplayMessage[]
  pending: PendingRequest | null
  draft: string
  failure: 'judgment_failed' | 'auth_expired' | 'access_unavailable' | null
  interactive: boolean
}>

type ControllerApi = Pick<LabHttpClient, 'judge'>
type ControllerRuntime = Readonly<{
  now?: () => number
  uuid?: () => string
  onAccessFailure?: (reason: 'auth_expired' | 'access_unavailable') => void
}>

export type TextJudgmentLabController = Readonly<{
  getState: () => LabControllerState
  subscribe: (listener: () => void) => () => void
  setDraft: (draft: string) => void
  setInteractive: (interactive: boolean, reason?: 'auth_expired' | 'access_unavailable') => void
  submit: (text: string, source: 'text' | 'example', idToken: string) => Promise<void>
  choose: (question: QuestionId, value: string, revision: number, authorized: boolean) => boolean
  interrupt: () => void
  restart: () => void
}>

const defaultUuid = () => crypto.randomUUID()

function choiceValue(question: QuestionId, value: string): ConversationChoice | null {
  if (question === 'topic' && (value === 'missing_notification' || value === 'notification_settings')) return { question, value }
  if (question === 'scope' && (value === 'all' || value === 'specific' || value === 'unknown')) return { question, value }
  if (question === 'workaround' && (value === 'can_read' || value === 'cannot_read' || value === 'unknown')) return { question, value }
  if (question === 'urgency' && (value === 'yes' || value === 'no')) return { question, value: value === 'yes' }
  if (question === 'result' && (value === 'done' || value === 'not_done' || value === 'not_tried' || value === 'cannot_check')) return { question, value }
  return null
}

export function createTextJudgmentLabController(api: ControllerApi, runtime: ControllerRuntime = {}): TextJudgmentLabController {
  const now = runtime.now ?? (() => performance.now())
  const uuid = runtime.uuid ?? defaultUuid
  let state: LabControllerState = {
    core: createConversationCore(uuid()), messages: [], pending: null, draft: '', failure: null, interactive: true,
  }
  let abortController: AbortController | null = null
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null
  const listeners = new Set<() => void>()
  const emit = (next: LabControllerState) => { state = next; listeners.forEach((listener) => listener()) }
  const updateMessage = (id: string, changes: Partial<LabDisplayMessage>, messages = state.messages) =>
    messages.map((message) => message.id === id ? { ...message, ...changes } : message)

  const fail = (pending: PendingRequest, reason: LabControllerState['failure']) => {
    if (state.pending?.requestId !== pending.requestId) return
    emit({
      ...state,
      core: pending.snapshot,
      pending: null,
      draft: pending.text,
      failure: reason,
      messages: updateMessage(pending.messageId, { status: 'failed' }),
    })
    if (reason === 'auth_expired' || reason === 'access_unavailable') runtime.onAccessFailure?.(reason)
  }

  const controller: TextJudgmentLabController = {
    getState: () => state,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    setDraft: (draft) => emit({ ...state, draft }),
    setInteractive: (interactive, reason) => {
      if (!interactive && state.pending !== null) {
        abortController?.abort()
        fail(state.pending, reason ?? 'auth_expired')
      }
      if (state.interactive !== interactive) emit({ ...state, interactive })
    },
    submit: async (rawText, source, idToken) => {
      const text = rawText.trim()
      if (!text || !state.interactive || state.pending !== null || state.core.stage.kind === 'ended') return
      const requestId = uuid()
      const messageId = uuid()
      const startedAt = now()
      const snapshot = state.core
      const pending: PendingRequest = {
        consultationId: snapshot.consultationId,
        requestId,
        revision: snapshot.revision,
        startedAt,
        deadlineAt: startedAt + 15_000,
        snapshot,
        messageId,
        text,
      }
      const recentUserTexts = state.messages.filter((message) => message.status === 'judged').slice(-2).map((message) => message.text)
      const request: JudgmentRequest = {
        contractVersion: 1,
        consultationId: snapshot.consultationId,
        requestId,
        revision: snapshot.revision,
        text,
        context: {
          question: currentQuestionId(snapshot),
          confirmed: snapshot.confirmed,
          recentUserTexts,
          impact: snapshot.impact,
        },
      }
      const requestAbortController = new AbortController()
      abortController = requestAbortController
      const requestDeadlineTimer = setTimeout(() => {
        if (state.pending?.requestId !== requestId) return
        requestAbortController.abort()
        fail(pending, 'judgment_failed')
      }, 15_000)
      deadlineTimer = requestDeadlineTimer
      emit({
        ...state,
        pending,
        draft: '',
        failure: null,
        messages: [...state.messages, { id: messageId, role: 'user', text, source, status: 'pending' }],
      })
      try {
        const result = await api.judge(idToken, request, requestAbortController.signal)
        const current = state.pending as PendingRequest | null
        if (current === null || current.requestId !== requestId) return
        const finishedAt = now()
        const valid = state.interactive && finishedAt < current.deadlineAt &&
          state.core.consultationId === result.consultationId &&
          current.consultationId === result.consultationId &&
          current.requestId === result.requestId &&
          current.revision === result.revision &&
          state.core.revision === current.revision
        if (!valid) { fail(current, state.interactive ? 'judgment_failed' : 'auth_expired'); return }
        const core = applyJudgment(current.snapshot, result.evidence)
        emit({
          ...state,
          core,
          pending: null,
          failure: null,
          messages: updateMessage(messageId, { status: 'judged', judgment: result, uiElapsedMs: finishedAt - current.startedAt }),
        })
      } catch (error) {
        const current = state.pending as PendingRequest | null
        if (current === null || current.requestId !== requestId) return
        const reason = error instanceof LabHttpError && error.code === 'reauthentication_required'
          ? 'auth_expired'
          : error instanceof LabHttpError && error.code === 'access_unavailable'
            ? 'access_unavailable'
            : 'judgment_failed'
        fail(current, reason)
      } finally {
        clearTimeout(requestDeadlineTimer)
        if (deadlineTimer === requestDeadlineTimer) deadlineTimer = null
        if (abortController === requestAbortController) abortController = null
      }
    },
    choose: (question, value, revision, authorized) => {
      if (!authorized || !state.interactive || state.pending !== null || state.core.stage.kind === 'ended' || revision !== state.core.revision) return false
      const choice = choiceValue(question, value)
      if (choice === null) return false
      const next = selectConversationChoice(state.core, choice)
      if (next === state.core) return false
      emit({
        ...state,
        core: next,
        failure: null,
        messages: [...state.messages, { id: uuid(), role: 'user', text: value, source: 'choice', choiceQuestion: question, status: 'judged' }],
      })
      return true
    },
    interrupt: () => {
      if (state.core.stage.kind === 'ended') return
      abortController?.abort()
      if (deadlineTimer !== null) clearTimeout(deadlineTimer)
      deadlineTimer = null
      const messages = state.pending === null ? state.messages : updateMessage(state.pending.messageId, { status: 'interrupted' })
      emit({ ...state, core: interruptConversation(state.core), messages, pending: null, draft: '', failure: null })
    },
    restart: () => {
      abortController?.abort()
      if (deadlineTimer !== null) clearTimeout(deadlineTimer)
      deadlineTimer = null
      emit({
        core: restartConversation(state.core, uuid()), messages: [], pending: null, draft: '', failure: null, interactive: state.interactive,
      })
    },
  }
  return controller
}

export function useTextJudgmentLabController(controller: TextJudgmentLabController): LabControllerState {
  return useSyncExternalStore(controller.subscribe, controller.getState, controller.getState)
}

export function useTextJudgmentLab(api: ControllerApi, onAccessFailure?: ControllerRuntime['onAccessFailure']): readonly [TextJudgmentLabController, LabControllerState] {
  const controller = useMemo(() => createTextJudgmentLabController(api, { onAccessFailure }), [api, onAccessFailure])
  return [controller, useTextJudgmentLabController(controller)] as const
}

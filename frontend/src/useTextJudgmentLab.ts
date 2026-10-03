import { useEffect, useMemo, useSyncExternalStore } from 'react'

import { LabHttpError, type LabHttpClient } from './textJudgmentLabApi'
import {
  applyJudgment,
  createConversationCore,
  conversationQuestion,
  currentQuestionId,
  interruptConversation,
  restartConversation,
  selectConversationChoice,
  type ConversationChoice,
  type ConversationCore,
} from './textJudgmentLabState'
import type { JudgmentRequest, QuestionId, TurnRecord } from './textJudgmentLabTypes'

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
  messages: readonly TurnRecord[]
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
  dispose: () => void
}>

function freezeValue<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freezeValue)
    Object.freeze(value)
  }
  return value
}

function snapshotValue<T>(value: T): T {
  return freezeValue(structuredClone(value))
}

const defaultUuid = () => crypto.randomUUID()

function choiceValue(question: QuestionId, value: string): ConversationChoice | null {
  if (
    question === 'topic' &&
    (value === 'missing_notification' || value === 'notification_settings')
  )
    return { question, value }
  if (question === 'scope' && (value === 'all' || value === 'specific' || value === 'unknown'))
    return { question, value }
  if (
    question === 'workaround' &&
    (value === 'can_read' || value === 'cannot_read' || value === 'unknown')
  )
    return { question, value }
  if (question === 'urgency' && (value === 'yes' || value === 'no'))
    return { question, value: value === 'yes' }
  if (
    question === 'result' &&
    (value === 'done' || value === 'not_done' || value === 'not_tried' || value === 'cannot_check')
  )
    return { question, value }
  return null
}

export function createTextJudgmentLabController(
  api: ControllerApi,
  runtime: ControllerRuntime = {},
): TextJudgmentLabController {
  const now = runtime.now ?? (() => performance.now())
  const uuid = runtime.uuid ?? defaultUuid
  let state: LabControllerState = {
    core: createConversationCore(uuid()),
    messages: [],
    pending: null,
    draft: '',
    failure: null,
    interactive: true,
  }
  let abortController: AbortController | null = null
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null
  const listeners = new Set<() => void>()
  const emit = (next: LabControllerState) => {
    state = freezeValue(next)
    listeners.forEach((listener) => listener())
  }
  state = freezeValue(state)
  const updateMessage = (id: string, replace: (record: TurnRecord) => TurnRecord) =>
    state.messages.map((message) => (message.id === id ? replace(message) : message))
  const nonApplied = (
    record: TurnRecord,
    kind: 'failed' | 'interrupted',
    failure?: NonNullable<LabControllerState['failure']>,
  ): TurnRecord => {
    if (record.kind !== 'pending') return record
    const { id, text, before, previousQuestion, source } = record
    const origin = { id, text, before, previousQuestion, source }
    return kind === 'failed'
      ? { ...origin, kind, failure: failure ?? 'judgment_failed' }
      : { ...origin, kind }
  }

  const fail = (pending: PendingRequest, reason: LabControllerState['failure']) => {
    if (state.pending?.requestId !== pending.requestId) return
    emit({
      ...state,
      core: pending.snapshot,
      pending: null,
      draft: pending.text,
      failure: reason,
      messages: updateMessage(pending.messageId, (record) =>
        nonApplied(record, 'failed', reason ?? 'judgment_failed'),
      ),
    })
    if (reason === 'auth_expired' || reason === 'access_unavailable')
      runtime.onAccessFailure?.(reason)
  }

  const controller: TextJudgmentLabController = {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
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
      if (
        !text ||
        [...text].length > 1000 ||
        !state.interactive ||
        state.pending !== null ||
        state.core.stage.kind === 'ended' ||
        conversationQuestion(state.core)?.inputMode === 'choices_only'
      )
        return
      const requestId = uuid()
      const messageId = uuid()
      const startedAt = now()
      const snapshot = snapshotValue(state.core)
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
      const recentUserTexts = state.messages
        .filter((message) => message.kind === 'judged' || message.kind === 'choice')
        .slice(-2)
        .map((message) => message.text)
      const request: JudgmentRequest = snapshotValue({
        contractVersion: 2,
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
      })
      const previousQuestion = snapshotValue(conversationQuestion(snapshot)!)
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
        messages: [
          ...state.messages,
          {
            id: messageId,
            text,
            source,
            kind: 'pending',
            before: snapshot,
            previousQuestion,
            request,
          },
        ],
      })
      try {
        const result = snapshotValue(
          await api.judge(idToken, request, requestAbortController.signal),
        )
        const current = state.pending as PendingRequest | null
        if (current === null || current.requestId !== requestId) return
        const finishedAt = now()
        const valid =
          state.interactive &&
          finishedAt < current.deadlineAt &&
          state.core.consultationId === result.consultationId &&
          current.consultationId === result.consultationId &&
          current.requestId === result.requestId &&
          current.revision === result.revision &&
          state.core.revision === current.revision
        if (!valid) {
          fail(current, state.interactive ? 'judgment_failed' : 'auth_expired')
          return
        }
        const transition = applyJudgment(current.snapshot, result)
        emit({
          ...state,
          core: transition.core,
          pending: null,
          failure: null,
          messages: updateMessage(messageId, (record) =>
            record.kind === 'pending'
              ? {
                  ...record,
                  kind: 'judged',
                  judgment: result,
                  application: snapshotValue(transition.application),
                  uiElapsedMs: finishedAt - current.startedAt,
                }
              : record,
          ),
        })
      } catch (error) {
        const current = state.pending as PendingRequest | null
        if (current === null || current.requestId !== requestId) return
        const reason =
          error instanceof LabHttpError && error.code === 'reauthentication_required'
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
      if (
        !authorized ||
        !state.interactive ||
        state.pending !== null ||
        state.core.stage.kind === 'ended' ||
        revision !== state.core.revision
      )
        return false
      const choice = choiceValue(question, value)
      if (choice === null) return false
      const before = snapshotValue(state.core)
      const previousQuestion = snapshotValue(conversationQuestion(before)!)
      const next = selectConversationChoice(before, choice)
      if (next === null) return false
      const label = previousQuestion.choices.find((candidate) => candidate.id === value)?.label
      if (label === undefined) return false
      emit({
        ...state,
        core: next.core,
        failure: null,
        messages: [
          ...state.messages,
          {
            id: uuid(),
            text: label,
            source: 'choice',
            kind: 'choice',
            before,
            previousQuestion,
            answer: snapshotValue(choice),
            application: snapshotValue(next.application),
          },
        ],
      })
      return true
    },
    interrupt: () => {
      if (state.core.stage.kind === 'ended') return
      abortController?.abort()
      if (deadlineTimer !== null) clearTimeout(deadlineTimer)
      deadlineTimer = null
      const messages =
        state.pending === null
          ? state.messages
          : updateMessage(state.pending.messageId, (record) => nonApplied(record, 'interrupted'))
      emit({
        ...state,
        core: interruptConversation(state.core),
        messages,
        pending: null,
        draft: '',
        failure: null,
      })
    },
    dispose: () => controller.restart(),
    restart: () => {
      abortController?.abort()
      if (deadlineTimer !== null) clearTimeout(deadlineTimer)
      deadlineTimer = null
      emit({
        core: restartConversation(state.core, uuid()),
        messages: [],
        pending: null,
        draft: '',
        failure: null,
        interactive: state.interactive,
      })
    },
  }
  return controller
}

export function useTextJudgmentLabController(
  controller: TextJudgmentLabController,
): LabControllerState {
  return useSyncExternalStore(controller.subscribe, controller.getState, controller.getState)
}

export function useTextJudgmentLab(
  api: ControllerApi,
  onAccessFailure?: ControllerRuntime['onAccessFailure'],
): readonly [TextJudgmentLabController, LabControllerState] {
  const controller = useMemo(
    () => createTextJudgmentLabController(api, { onAccessFailure }),
    [api, onAccessFailure],
  )
  useEffect(() => () => controller.dispose(), [controller])
  return [controller, useTextJudgmentLabController(controller)] as const
}

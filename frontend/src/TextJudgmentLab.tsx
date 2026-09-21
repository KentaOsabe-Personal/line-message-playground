import { useEffect, useRef, type FormEvent, type KeyboardEvent } from 'react'

import TextJudgmentDetails from './TextJudgmentDetails'
import { LAB_CONTENT, getGuide, getOfficialHelpLink, getQuestion, type LabQuestionContentId } from './textJudgmentLabContent'
import { currentQuestionId } from './textJudgmentLabState'
import type { LabAccessState, QuestionId } from './textJudgmentLabTypes'
import { useTextJudgmentLabController, type TextJudgmentLabController } from './useTextJudgmentLab'

function questionContentId(question: QuestionId, topic: string | null): LabQuestionContentId {
  if (question === 'topic' || question === 'workaround' || question === 'urgency') return question
  if (question === 'scope') return topic === 'notification_settings' ? 'settings_scope' : 'missing_scope'
  return topic === 'notification_settings' ? 'settings_result' : 'missing_result'
}

function choiceLabel(question: QuestionId, value: string, topic: string | null): string {
  return getQuestion(questionContentId(question, topic)).choices.find((choice) => choice.id === value)?.label ?? value
}

export default function TextJudgmentLab({ controller, access, getValidIdToken }: Readonly<{
  controller: TextJudgmentLabController
  access: LabAccessState
  getValidIdToken: () => string | null
}>) {
  const state = useTextJudgmentLabController(controller)
  const composing = useRef(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const authorized = access.kind === 'authorized'
  const ended = state.core.stage.kind === 'ended'
  const pending = state.pending !== null
  const questionId = state.core.stage.kind === 'start' ? 'topic' : currentQuestionId(state.core)
  const question = state.core.stage.kind === 'ended' ? null : getQuestion(questionContentId(questionId, state.core.confirmed.topic))
  const choicesOnly = state.core.clarification?.question === questionId && state.core.clarification.mode === 'choices_only'
  const inputEnabled = authorized && state.interactive && !pending && !ended && !choicesOnly

  useEffect(() => controller.setInteractive(authorized, access.kind === 'unavailable' ? 'access_unavailable' : 'auth_expired'), [access.kind, authorized, controller])
  useEffect(() => { if (inputEnabled) inputRef.current?.focus() }, [inputEnabled, state.core.revision])

  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    const token = getValidIdToken()
    if (token !== null) void controller.submit(state.draft, 'text', token)
  }

  const notice = state.core.notice === 'out_of_scope' ? LAB_CONTENT.messages.outOfScope
    : state.core.notice === 'mixed_scope' ? '対応できない部分を除き、通知の相談だけを続けます。'
      : state.core.notice === 'restart_required' ? LAB_CONTENT.messages.restartRequired : null

  return <section className="text-judgment-lab" aria-label="文章判定ラボ">
    <p className="lab-safety-notice">{LAB_CONTENT.safetyNotice}</p>
    <div role="log" aria-live="polite" aria-label="相談の会話">
      {state.messages.map((message) => <article key={message.id} className={`lab-message lab-message-${message.status}`}>
        <p><strong>あなた:</strong> {message.source === 'choice' ? `${choiceLabel(message.choiceQuestion ?? questionId, message.text, state.core.confirmed.topic)}（選択肢で回答）` : message.text}</p>
        {message.status === 'pending' && <p role="status">判定中です。</p>}
        {message.status === 'failed' && <p role="alert">判定できませんでした。入力欄から編集して再送信できます。</p>}
        {message.status === 'interrupted' && <p>中断のため判定を反映しませんでした。</p>}
        {message.judgment && <TextJudgmentDetails judgment={message.judgment} uiElapsedMs={message.uiElapsedMs ?? 0} />}
      </article>)}
      {notice && <p role="status">{notice}</p>}
      {(state.core.stage.kind === 'start' || state.core.stage.kind === 'question') && question && <p><strong>ラボ:</strong> {question.prompt}</p>}
      {state.core.stage.kind === 'guidance' && (() => {
        const guide = getGuide(state.core.stage.guideId)
        return <article className="lab-guide">
          <p><strong>ラボ:</strong> {guide.summary}</p>
          <details open={state.core.stage.presentation === 'details_open'}>
            <summary>詳しい手順</summary>
            <ol>{guide.steps.map((step) => <li key={step}>{step}</li>)}</ol>
            <a href={getOfficialHelpLink(guide.helpUrl)}>LINE公式案内</a>
            <p>確認日: {guide.checkedOn}</p>
          </details>
          <p>{question?.prompt}</p>
        </article>
      })()}
      {ended && <p role="status">{state.core.stage.kind === 'ended' && (
        state.core.stage.outcome === 'settings_completed' ? LAB_CONTENT.messages.settingsCompleted
          : state.core.stage.outcome === 'unresolved' ? (state.core.confirmed.topic === 'notification_settings' ? LAB_CONTENT.messages.settingsUnresolved : LAB_CONTENT.messages.missingUnresolved)
            : LAB_CONTENT.messages[state.core.stage.outcome]
      )}</p>}
    </div>

    {!ended && state.core.stage.kind === 'start' && <div aria-label="入力例">
      {LAB_CONTENT.examples.map((example) => <button key={example.id} type="button" disabled={!inputEnabled} onClick={() => {
        const token = getValidIdToken(); if (token !== null) void controller.submit(example.text, 'example', token)
      }}>{example.text}</button>)}
    </div>}

    {!ended && question && <div aria-label="現在の選択肢">
      {question.choices.map((choice) => <button key={`${state.core.revision}-${choice.id}`} type="button" disabled={!authorized || pending} onClick={() => controller.choose(questionId, choice.id, state.core.revision, authorized)}>{choice.label}</button>)}
    </div>}

    {!ended && <form onSubmit={submit}>
      <label htmlFor="lab-message-input">相談または回答</label>
      <textarea id="lab-message-input" ref={inputRef} value={state.draft} disabled={!inputEnabled} onChange={(event) => controller.setDraft(event.currentTarget.value)} onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }} onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing) { event.preventDefault(); submit() }
      }} />
      <button type="submit" disabled={!inputEnabled || state.draft.trim().length === 0}>送信</button>
      {choicesOnly && <p role="status">この質問は選択肢で回答してください。</p>}
    </form>}

    <div className="lab-actions">
      {!ended && <button type="button" onClick={controller.interrupt}>相談を終了する</button>}
      <button type="button" onClick={controller.restart}>新しい相談を始める</button>
    </div>
  </section>
}

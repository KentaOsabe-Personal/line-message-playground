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
  const chatScrollRef = useRef<HTMLDivElement>(null)
  const authorized = access.kind === 'authorized'
  const ended = state.core.stage.kind === 'ended'
  const pending = state.pending !== null
  const questionId = state.core.stage.kind === 'start' ? 'topic' : currentQuestionId(state.core)
  const question = state.core.stage.kind === 'ended' ? null : getQuestion(questionContentId(questionId, state.core.confirmed.topic))
  const choicesOnly = state.core.clarification?.question === questionId && state.core.clarification.mode === 'choices_only'
  const inputEnabled = authorized && state.interactive && !pending && !ended && !choicesOnly

  useEffect(() => controller.setInteractive(authorized, access.kind === 'unavailable' ? 'access_unavailable' : 'auth_expired'), [access.kind, authorized, controller])
  useEffect(() => { if (inputEnabled) inputRef.current?.focus() }, [inputEnabled, state.core.revision])
  useEffect(() => {
    const scrollArea = chatScrollRef.current
    if (scrollArea) scrollArea.scrollTop = state.messages.length === 0 ? 0 : scrollArea.scrollHeight
  }, [state.messages.length, state.core.revision])

  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    const token = getValidIdToken()
    if (token !== null) void controller.submit(state.draft, 'text', token)
  }

  const notice = state.core.notice === 'out_of_scope' ? LAB_CONTENT.messages.outOfScope
    : state.core.notice === 'mixed_scope' ? '対応できない部分を除き、通知の相談だけを続けます。'
      : state.core.notice === 'restart_required' ? LAB_CONTENT.messages.restartRequired : null

  return <section className="text-judgment-lab mx-auto max-w-3xl rounded-card bg-page p-4 shadow-card sm:p-5" aria-label="文章判定ラボ">
    <p className="lab-notice flex items-start gap-3 rounded-card border border-info bg-info-soft p-4 text-sm leading-relaxed text-info"><span className="grid size-5 shrink-0 place-items-center rounded-full bg-info text-xs font-black text-on-action" aria-hidden="true">i</span><span>{LAB_CONTENT.safetyNotice}</span></p>
    <div className="lab-chat-scroll" ref={chatScrollRef}>
    <div className="lab-conversation overflow-hidden rounded-card border border-border bg-page-deep shadow-card">
      <div className="lab-conversation-header flex items-center justify-between gap-2 border-b border-border bg-surface px-4 py-2 text-xs font-bold tracking-wide text-muted">
        <span className="flex items-center gap-2" aria-hidden="true"><span className="size-2 rounded-full bg-line shadow-selected" /><span className="lab-assistant-title">文章判定アシスタント</span></span>
        <div className="lab-actions lab-header-actions">
          {!ended && <button className="lab-action-danger bg-transparent text-xs font-semibold text-danger" type="button" onClick={controller.interrupt}>相談を終了する</button>}
          <button className="bg-transparent text-xs font-semibold text-muted underline underline-offset-4" type="button" onClick={controller.restart}>新しい相談を始める</button>
        </div>
      </div>
      <div className="grid min-h-36 gap-4 p-4" role="log" aria-live="polite" aria-label="相談の会話">
      {state.messages.map((message) => <article key={message.id} className={`lab-message-row lab-message-user lab-message-${message.status} flex max-w-[92%] items-end justify-self-end gap-2`} aria-label="あなたのメッセージ">
        <div className="grid min-w-0 justify-items-end">
          <div className="lab-bubble rounded-card rounded-br-sm bg-success-soft px-4 py-3 leading-relaxed shadow-card">
            <p>{message.source === 'choice' ? `${choiceLabel(message.choiceQuestion ?? questionId, message.text, state.core.confirmed.topic)}（選択肢で回答）` : message.text}</p>
            {message.status === 'pending' && <p className="mt-2 border-t border-border pt-2 text-xs text-muted" role="status"><span className="mr-2 inline-flex gap-1" aria-hidden="true"><i className="size-1 rounded-full bg-muted" /><i className="size-1 rounded-full bg-muted" /><i className="size-1 rounded-full bg-muted" /></span>判定中です。</p>}
            {message.status === 'failed' && <p className="mt-2 border-t border-danger pt-2 text-xs font-bold text-danger" role="alert">判定できませんでした。入力欄から編集して再送信できます。</p>}
            {message.status === 'interrupted' && <p className="mt-2 border-t border-border pt-2 text-xs text-muted">中断のため判定を反映しませんでした。</p>}
          </div>
          {message.judgment && <TextJudgmentDetails judgment={message.judgment} uiElapsedMs={message.uiElapsedMs ?? 0} />}
        </div>
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-text text-[0.58rem] font-black text-on-action" aria-hidden="true">YOU</span>
      </article>)}
      {notice && <div className="justify-self-center rounded-pill bg-surface px-3 py-2 text-center text-xs leading-relaxed text-muted" role="status">{notice}</div>}
      {(state.core.stage.kind === 'start' || state.core.stage.kind === 'question') && question && <article className="lab-message-row lab-message-assistant flex max-w-[92%] items-end gap-2" aria-label="ラボからのメッセージ">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-line text-xs font-black text-on-action shadow-action" aria-hidden="true">J</span>
        <div className="lab-bubble rounded-card rounded-bl-sm bg-surface px-4 py-3 leading-relaxed shadow-card"><p>{question.prompt}</p></div>
      </article>}
      {state.core.stage.kind === 'guidance' && (() => {
        const guide = getGuide(state.core.stage.guideId)
        return <div className="lab-message-row lab-message-assistant flex max-w-[92%] items-end gap-2" aria-label="ラボからのメッセージ">
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-line text-xs font-black text-on-action shadow-action" aria-hidden="true">J</span>
          <article className="lab-bubble lab-guide rounded-card rounded-bl-sm bg-surface px-4 py-3 leading-relaxed shadow-card">
            <p>{guide.summary}</p>
            <details className="mt-3 rounded-control bg-surface-soft p-3" open={state.core.stage.presentation === 'details_open'}>
              <summary>詳しい手順</summary>
              <ol>{guide.steps.map((step) => <li key={step}>{step}</li>)}</ol>
              <a href={getOfficialHelpLink(guide.helpUrl)} rel="noopener noreferrer">LINE公式案内</a>
              <p className="text-xs text-muted">確認日: {guide.checkedOn}</p>
            </details>
            <p>{question?.prompt}</p>
          </article>
        </div>
      })()}
      {ended && <div className="justify-self-center rounded-pill bg-success-soft px-3 py-2 text-center text-xs font-bold leading-relaxed text-line-strong" role="status">{state.core.stage.kind === 'ended' && (
        state.core.stage.outcome === 'settings_completed' ? LAB_CONTENT.messages.settingsCompleted
          : state.core.stage.outcome === 'unresolved' ? (state.core.confirmed.topic === 'notification_settings' ? LAB_CONTENT.messages.settingsUnresolved : LAB_CONTENT.messages.missingUnresolved)
            : LAB_CONTENT.messages[state.core.stage.outcome]
      )}</div>}
      {state.core.stage.kind === 'ended' && state.core.stage.outcome === 'unresolved' && state.core.confirmed.topic !== null && state.core.confirmed.scope !== null && (() => {
        const suffix = state.core.confirmed.scope === 'specific' ? 'specific' : 'all'
        const guide = getGuide(`${state.core.confirmed.topic === 'missing_notification' ? 'missing' : 'settings'}_${suffix}`)
        return <a className="justify-self-center font-bold text-line-strong" href={getOfficialHelpLink(guide.helpUrl)} rel="noopener noreferrer">LINE公式案内</a>
      })()}
      </div>
    </div>

    {!ended && question && <div className="lab-choice-panel rounded-card border border-border bg-surface p-4 shadow-card">
      <p className="lab-section-eyebrow mb-1 text-[0.66rem] font-black tracking-[0.14em] text-line">QUICK REPLY</p>
      <h2 className="lab-section-title mb-3 text-base font-extrabold">{state.core.stage.kind === 'start' ? '相談内容を選ぶ' : '回答を選ぶ'}</h2>
      <div className="lab-choice-grid" aria-label="現在の選択肢">
        {question.choices.map((choice) => <button className="lab-choice flex w-full items-center justify-between gap-3 rounded-control border border-line bg-success-soft px-4 py-3 text-left font-bold text-line-strong shadow-none after:text-2xl after:font-normal after:leading-none after:content-['›'] hover:shadow-selected disabled:cursor-not-allowed disabled:opacity-50" key={`${state.core.revision}-${choice.id}`} type="button" disabled={!authorized || pending} onClick={() => controller.choose(questionId, choice.id, state.core.revision, authorized)}>{choice.label}</button>)}
      </div>
    </div>}

    {!ended && state.core.stage.kind === 'start' && <details className="lab-examples overflow-hidden rounded-card border border-border bg-surface shadow-card">
      <summary className="cursor-pointer px-4 py-3 text-sm font-bold text-muted">文章を送って判定を試す</summary>
      <p className="px-4 pb-3 text-xs text-muted">例文をJevへ送り、文章判定から会話を始めます。</p>
      <div className="lab-example-grid" aria-label="入力例">
        {LAB_CONTENT.examples.map((example) => <button className="lab-example w-full rounded-control border border-border bg-surface-soft px-4 py-3 text-left text-sm font-semibold text-text disabled:cursor-not-allowed disabled:opacity-50" key={example.id} type="button" disabled={!inputEnabled} onClick={() => {
          const token = getValidIdToken(); if (token !== null) void controller.submit(example.text, 'example', token)
        }}>{example.text}</button>)}
      </div>
    </details>}
    </div>

    <div className="lab-controls">
    {!ended && <form className="lab-composer rounded-card border border-border bg-surface p-3 shadow-hero" onSubmit={submit}>
      <div className="flex items-baseline justify-between gap-4">
        <label className="text-sm font-extrabold" htmlFor="lab-message-input">メッセージ</label>
        <span className="hidden text-xs text-muted sm:inline">Enterで送信・Shift+Enterで改行</span>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2">
        <textarea className="w-full resize-y rounded-control border border-border bg-surface-soft px-3 py-3 leading-relaxed text-text placeholder:text-muted focus:border-line focus:outline-3 focus:outline-line" id="lab-message-input" ref={inputRef} value={state.draft} disabled={!inputEnabled} placeholder="相談内容を入力してください" onChange={(event) => controller.setDraft(event.currentTarget.value)} onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }} onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => {
          if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing) { event.preventDefault(); submit() }
        }} />
        <button className="lab-send min-w-20 rounded-control bg-line px-4 py-3 font-bold text-on-action shadow-action disabled:cursor-not-allowed disabled:opacity-50" type="submit" disabled={!inputEnabled || state.draft.trim().length === 0}>送信</button>
      </div>
      {choicesOnly && <p role="status">この質問は選択肢で回答してください。</p>}
    </form>}

    </div>
  </section>
}

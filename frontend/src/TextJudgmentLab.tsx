import { useEffect, useRef } from 'react'

import type { LabAuthContext } from './TextJudgmentLabAuthGate'
import type { LabHttpClient } from './textJudgmentLabApi'
import TextJudgmentResult from './TextJudgmentResult'
import { isValidDraft, type JudgmentSnapshot, type ResultRef } from './textJudgmentLabState'
import { useTextJudgmentLab } from './useTextJudgmentLab'

export default function TextJudgmentLab({
  context,
  api,
}: Readonly<{ context: LabAuthContext; api: LabHttpClient }>) {
  const authorized = context.access.kind === 'authorized'
  const { state, setDraft, pending, submit, selectSource, cancelComparison } = useTextJudgmentLab(
    api,
    context,
  )
  const { entries, composer } = state
  const draft = composer.kind === 'editing' ? composer.draft : composer.submission.draft
  const original = composer.kind === 'editing' ? composer.original : composer.submission.original
  const replacedSingles = new Set(
    entries.flatMap((entry) =>
      entry.kind === 'comparison' && entry.original.source?.side === 'single'
        ? [entry.original.source.entryId]
        : [],
    ),
  )
  if (original?.source?.side === 'single') replacedSingles.add(original.source.entryId)
  const end = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const count = [...draft].length
  const disabled = !authorized || pending
  const focusedSuccess = useRef(0)
  useEffect(() => {
    if (entries.length) end.current?.scrollIntoView?.({ block: 'nearest' })
  }, [entries, pending])
  useEffect(() => {
    const latest = entries.at(-1)
    if (
      latest &&
      (latest.kind === 'comparison' || latest.outcome.kind === 'succeeded') &&
      latest.id !== focusedSuccess.current &&
      composer.kind === 'editing' &&
      authorized
    ) {
      focusedSuccess.current = latest.id
      input.current?.focus()
    }
  }, [entries, composer, authorized])
  const choose = (source: ResultRef, origin: HTMLButtonElement) => {
    let outcome = selectSource(source)
    if (outcome === 'confirmation_required') {
      if (window.confirm('入力中の文章を選択した文章に置き換えますか？'))
        outcome = selectSource(source, true)
      else {
        origin.focus()
        return
      }
    }
    if (outcome === 'selected') input.current?.focus()
  }
  const selectionButton = (source: ResultRef, labelledBy: string) => (
    <button
      type="button"
      disabled={disabled}
      aria-describedby={labelledBy}
      onClick={(event) => choose(source, event.currentTarget)}
    >
      書き換えて試す
    </button>
  )
  const comparisonSide = (snapshot: JudgmentSnapshot, source: ResultRef, title: string) => {
    const heading = `judgment-${source.entryId}-${source.side}`
    return (
      <section
        className={`judgment-comparison-side judgment-comparison-${source.side}`}
        aria-labelledby={heading}
      >
        <h2 id={heading}>{title}</h2>
        <p className="judgment-comparison-text">{snapshot.text}</p>
        <TextJudgmentResult result={snapshot.result} labelledBy={heading} />
        {selectionButton(source, heading)}
      </section>
    )
  }
  const send = () => {
    void submit()
  }
  return (
    <section className="simple-judgment-chat" aria-label="Jevと文章を試す">
      <div
        className="judgment-chat-log"
        role="log"
        aria-label="文章と判定結果"
        aria-live="polite"
        aria-relevant="additions text"
      >
        <div className="judgment-assistant">
          <span className="judgment-speaker">Jev</span>
          <div className="judgment-bubble judgment-welcome">
            <p>好きな文章を送ってみてください。</p>
            <p>
              「分類」「感情」「急ぎの要望」を判定します。言い方を変えて、結果を見比べてみましょう。
            </p>
            <p className="judgment-hint">例：助かりました！急ぎではないので、来週で大丈夫です。</p>
          </div>
        </div>
        {entries.map((entry) =>
          entry.kind === 'comparison' ? (
            <article key={entry.id} className="judgment-comparison" aria-label="成功した比較結果">
              <header className="judgment-comparison-header">
                <h2>比較結果</h2>
                <span role="status">比較の判定が完了しました。</span>
              </header>
              <p className="judgment-hint judgment-comparison-note">
                元の判定を使い、書き換え後だけを判定しました。
              </p>
              <div className="judgment-comparison-columns">
                {comparisonSide(
                  entry.original,
                  { entryId: entry.id, side: 'original' },
                  '元の文章',
                )}
                {comparisonSide(
                  entry.rewritten,
                  { entryId: entry.id, side: 'rewritten' },
                  '書き換え後',
                )}
              </div>
            </article>
          ) : (
            <div key={entry.id} className="judgment-turn" hidden={replacedSingles.has(entry.id)}>
              <div className="judgment-user">
                <span className="judgment-speaker">あなた</span>
                <p className="judgment-bubble">{entry.text}</p>
              </div>
              <div className="judgment-assistant">
                <span className="judgment-speaker" id={`judgment-${entry.id}-single`}>
                  Jevの判定
                </span>
                <div className="judgment-bubble">
                  {entry.outcome.kind === 'succeeded' ? (
                    <>
                      <TextJudgmentResult
                        result={entry.outcome.result}
                        labelledBy={`judgment-${entry.id}-single`}
                      />
                      {selectionButton(
                        { entryId: entry.id, side: 'single' },
                        `judgment-${entry.id}-single`,
                      )}
                    </>
                  ) : entry.outcome.kind === 'failed' ? (
                    <p role="alert">{entry.outcome.message}</p>
                  ) : (
                    <p role="status" className="judgment-waiting">
                      判定しています…
                    </p>
                  )}
                </div>
              </div>
            </div>
          ),
        )}
      </div>
      {original && (
        <section
          className="judgment-source judgment-bubble"
          aria-labelledby="judgment-source-heading"
        >
          <h2 id="judgment-source-heading">比較元</h2>
          <p className="judgment-comparison-text">{original.text}</p>
          <TextJudgmentResult result={original.result} labelledBy="judgment-source-heading" />
        </section>
      )}
      <form
        className="judgment-composer"
        onSubmit={(event) => {
          event.preventDefault()
          send()
        }}
      >
        {original && (
          <p role="status">
            {pending ? '比較を判定中… 送信した文章は変更できません。' : '比較を編集中'}
          </p>
        )}
        {composer.kind === 'editing' && original && composer.error && (
          <p role="alert">{composer.error}</p>
        )}
        <label htmlFor="judgment-text">{original ? '書き換え後の文章' : '試したい文章'}</label>
        <div className="judgment-input-row">
          <textarea
            ref={input}
            id="judgment-text"
            value={draft}
            disabled={disabled}
            readOnly={disabled}
            rows={2}
            placeholder="テーマは自由。文章を入力…"
            aria-describedby="judgment-input-note"
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" disabled={disabled || !isValidDraft(draft)}>
            {pending ? '判定中…' : original ? '比較する' : '送信'}
          </button>
        </div>
        {original && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              cancelComparison()
              if (!disabled) input.current?.focus()
            }}
          >
            比較をやめる
          </button>
        )}
        <p
          id="judgment-input-note"
          className={count > 1000 ? 'judgment-input-error' : 'judgment-hint'}
        >
          {count > 1000
            ? `${count}文字 / 1,000文字以内で入力してください。`
            : '1回につき1,000文字まで。送信した文章だけを判定します。'}
        </p>
        <p className="judgment-footnote">
          入力は外部AIのJevに送信されます。個人情報・秘密情報は入力しないでください。数値はモデルの判定で、正しさの保証ではありません。
        </p>
      </form>
      <div ref={end} />
    </section>
  )
}

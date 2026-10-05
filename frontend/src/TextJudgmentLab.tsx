import { useEffect, useRef } from 'react'

import type { LabAuthContext } from './TextJudgmentLabAuthGate'
import type { LabHttpClient } from './textJudgmentLabApi'
import { intentLabels, type Intent, type JudgmentResponse } from './textJudgmentLabTypes'
import { useTextJudgmentLab } from './useTextJudgmentLab'

const percent = (value: number) => `${(value * 100).toFixed(1)}%`

function Judgment({ result }: Readonly<{ result: JudgmentResponse }>) {
  const { intent, sentiment, urgency } = result.answers
  return (
    <>
      <div className="judgment-row">
        <div className="judgment-label">
          <h2>文章の分類</h2>
          <span>Choice</span>
        </div>
        <strong className="judgment-value">{intentLabels[intent.choice]}</strong>
        <div className="judgment-probabilities">
          {(Object.keys(intentLabels) as Intent[]).map((key) => (
            <div className="judgment-probability" key={key}>
              <span>{intentLabels[key]}</span>
              <meter
                min="0"
                max="1"
                value={intent.probabilities[key]}
                aria-label={`${intentLabels[key]}の確率`}
              />
              <span>{percent(intent.probabilities[key])}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="judgment-row">
        <div className="judgment-label">
          <h2>文章の感情</h2>
          <span>Score</span>
        </div>
        <p className="judgment-value">
          {sentiment.score.toFixed(2)} <small>/ 2</small>
        </p>
        <meter
          className="judgment-scale"
          min="0"
          max="2"
          value={sentiment.score}
          aria-label="感情のスコア"
        />
        <div className="judgment-scale-labels">
          <span>0 否定的</span>
          <span>1 中立</span>
          <span>2 肯定的</span>
        </div>
      </div>
      <div className="judgment-row">
        <div className="judgment-label">
          <h2>急ぎの要望</h2>
          <span>Noul</span>
        </div>
        <p className="judgment-value">{percent(urgency.noul)}</p>
        <p className="judgment-hint">早い対応を求めている確率</p>
      </div>
      <p className="judgment-meta">
        Jev応答 {Math.round(result.elapsedMs)} ms · {result.model}
      </p>
      <details className="judgment-raw">
        <summary>返却値を見る</summary>
        <pre>{JSON.stringify(result.answers, null, 2)}</pre>
      </details>
    </>
  )
}

export default function TextJudgmentLab({
  context,
  api,
}: Readonly<{ context: LabAuthContext; api: LabHttpClient }>) {
  const authorized = context.access.kind === 'authorized'
  const { turns, draft, setDraft, pending, submit } = useTextJudgmentLab(api, context)
  const end = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const count = [...draft].length
  const disabled = !authorized || pending
  useEffect(() => {
    if (turns.length) end.current?.scrollIntoView?.({ block: 'nearest' })
  }, [turns, pending])
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
        {turns.map((turn) => (
          <div key={turn.id} className="judgment-turn">
            <div className="judgment-user">
              <span className="judgment-speaker">あなた</span>
              <p className="judgment-bubble">{turn.text}</p>
            </div>
            <div className="judgment-assistant">
              <span className="judgment-speaker">Jev</span>
              <div className="judgment-bubble">
                {turn.result ? (
                  <Judgment result={turn.result} />
                ) : turn.error ? (
                  <p role="alert">{turn.error}</p>
                ) : (
                  <p role="status" className="judgment-waiting">
                    判定しています…
                  </p>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      <form
        className="judgment-composer"
        onSubmit={(event) => {
          event.preventDefault()
          send()
        }}
      >
        <label htmlFor="judgment-text">試したい文章</label>
        <div className="judgment-input-row">
          <textarea
            ref={input}
            id="judgment-text"
            value={draft}
            disabled={disabled}
            rows={2}
            placeholder="テーマは自由。文章を入力…"
            aria-describedby="judgment-input-note"
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" disabled={disabled || !draft.trim() || count > 1000}>
            {pending ? '判定中…' : '送信'}
          </button>
        </div>
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

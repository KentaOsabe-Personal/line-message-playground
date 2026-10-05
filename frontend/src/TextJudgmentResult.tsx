import type { Immutable } from './textJudgmentLabState'
import { intentLabels, type Intent, type JudgmentResponse } from './textJudgmentLabTypes'

const percent = (value: number) => `${(value * 100).toFixed(1)}%`

export default function TextJudgmentResult({
  result,
  labelledBy,
}: Readonly<{ result: Immutable<JudgmentResponse>; labelledBy?: string }>) {
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
      <p className="judgment-hint">数値はモデルの判定です。</p>
      <p className="judgment-meta">
        Jev応答 {Math.round(result.elapsedMs)} ms · {result.model}
      </p>
      <details className="judgment-raw">
        <summary aria-describedby={labelledBy}>返却値を見る</summary>
        <pre tabIndex={0} aria-describedby={labelledBy} aria-label="未丸めの返却値">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </>
  )
}

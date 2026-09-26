import type { JudgmentResponse } from './textJudgmentLabTypes'

const choiceLabels: Record<string, string> = {
  topic: '相談の種類', relevance: '対象範囲', change: '変更希望', scope: '通知の範囲',
  workaround: '代替確認', result: '結果', impact_evidence: '支障の根拠',
}
const valueLabels: Record<string, string> = {
  missing_notification: '通知が届かない', notification_settings: '通知の設定方法を知りたい', both: '両方の相談',
  in_scope: '対象内', mixed: '対象内と対象外が混在', out_of_scope: '対象外',
  keep: '現在の相談を継続', restart: '相談のやり直し',
  all: '全体・すべてのトーク', specific: '特定のトーク', unknown: '分からない',
  can_read: 'LINEを開けば確認できる', cannot_read: 'LINEを開いても確認できない',
  done: '完了・解決', not_done: '未解決', not_tried: 'まだ試していない', cannot_check: '確認できない',
  present: '支障の記述あり', absent: '支障の記述なし', unmentioned: '未言及', unclear: '判定要確認',
}
const valueLabel = (value: string) => valueLabels[value] ?? value
const percent = (value: number) => `${(value * 100).toFixed(1)}%`

export default function TextJudgmentDetails({ judgment, uiElapsedMs }: Readonly<{ judgment: JudgmentResponse; uiElapsedMs: number }>) {
  return <details className="lab-judgment-details">
    <summary>判定の詳細</summary>
    <dl>
      <dt>model</dt><dd>{judgment.model}</dd>
      <dt>通信と本人確認を含む待ち時間: </dt><dd>{Math.round(uiElapsedMs)} ms</dd>
      <dt>Jev通信と応答検証の時間: </dt><dd>{Math.round(judgment.details.jevElapsedMs)} ms</dd>
    </dl>
    <section aria-labelledby={`choice-${judgment.requestId}`}>
      <h3 id={`choice-${judgment.requestId}`}>Choice</h3>
      {Object.entries(judgment.details.choices).map(([id, detail]) => <div key={id}>
        <h4>{choiceLabels[id] ?? id}: {valueLabel(detail.choice)}</h4>
        <p>confidence: {percent(detail.confidence)}</p>
        <ul>{Object.entries(detail.probabilities).map(([candidate, probability]) => <li key={candidate}>{valueLabel(candidate)}: {percent(probability)}</li>)}</ul>
      </div>)}
    </section>
    <section>
      <h3>Score: {judgment.details.score.score.toFixed(2)}</h3>
      <p>0: {judgment.details.score.legend['0']} / 1: {judgment.details.score.legend['1']} / 2: {judgment.details.score.legend['2']}</p>
      <p>confidence: {percent(judgment.details.score.confidence)}</p>
      <ul>{Object.entries(judgment.details.score.probabilities).map(([score, probability]) => <li key={score}>{score}: {percent(probability)}</li>)}</ul>
    </section>
    <section>
      <h3>Noul</h3>
      <p>急ぎの要望がある確率: {percent(judgment.details.noul.noul)}</p>
    </section>
    <p>確率とconfidenceは正答率の保証ではありません。分岐には丸め前の値を使います。</p>
  </details>
}

import {
  LAB_APPLICATION_REASONS,
  LAB_JUDGMENT_LABELS,
  LAB_NORMALIZATION_REASONS,
  LAB_PRIORITY_REASONS,
  labValueLabel,
} from './textJudgmentLabContent'
import type { ConversationCore } from './textJudgmentLabState'
import type {
  ConversationApplication,
  JudgmentId,
  JudgmentResponse,
  QuestionSnapshot,
} from './textJudgmentLabTypes'
import { TurnApplicationSummary } from './TextJudgmentTurnSummary'

const percent = (value: number) => `${(value * 100).toFixed(1)}%`
const operators = { gte: '≥', lte: '≤', eq: '=' } as const
const dispositionLabels = {
  applied: '採用',
  not_applied: '不採用',
  not_used: '今の場面では不使用',
} as const
const normalizationLabels = {
  eligible: '数値上は採用可能',
  unmentioned: '未言及',
  needs_review: '判定要確認',
} as const
const format = (value: unknown) => JSON.stringify(value, null, 2)

// 数値だけを閲覧する使い方も維持する。会話画面では、発言時の適用結果と文脈を渡す。
type Props = Readonly<{
  judgment: JudgmentResponse
  uiElapsedMs: number
  application?: ConversationApplication
  before?: Readonly<ConversationCore>
  previousQuestion?: QuestionSnapshot
}>
export default function TextJudgmentDetails({
  judgment,
  uiElapsedMs,
  application,
  before,
  previousQuestion,
}: Props) {
  const inspection = judgment.inspection
  return (
    <details className="lab-judgment-details min-w-0 break-words [overflow-wrap:anywhere]">
      <summary>判定の詳細</summary>
      <dl>
        <dt>model</dt>
        <dd>{judgment.model}</dd>
        <dt>通信と本人確認を含む待ち時間: </dt>
        <dd>{Math.round(uiElapsedMs)} ms</dd>
        <dt>Jev通信と応答検証の時間: </dt>
        <dd>{Math.round(judgment.details.jevElapsedMs)} ms</dd>
        {inspection && (
          <>
            <dt>判定質問の版</dt>
            <dd>{inspection.questionVersion}</dd>
            <dt>採用規則の版</dt>
            <dd>{inspection.policy.version}</dd>
          </>
        )}
        {application && (
          <>
            <dt>会話規則の版</dt>
            <dd>{application.ruleVersion}</dd>
          </>
        )}
      </dl>
      <section>
        <h3>Jevの出力</h3>
        <h4>Choice</h4>
        {Object.entries(judgment.details.choices).map(([id, detail]) => (
          <div key={id}>
            <h5>
              {LAB_JUDGMENT_LABELS[id as JudgmentId]}: {labValueLabel(detail.choice)} ({id})
            </h5>
            <p>confidence: {percent(detail.confidence)}</p>
            <ul>
              {Object.entries(detail.probabilities).map(([candidate, probability]) => (
                <li key={candidate}>
                  {labValueLabel(candidate)}: {percent(probability)}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <h4>Score (impact): {judgment.details.score.score.toFixed(2)}</h4>
        <p>0〜2の段階番号を確率で重み付けした平均値です。</p>
        <p>
          0: {judgment.details.score.legend['0']} / 1: {judgment.details.score.legend['1']} / 2:{' '}
          {judgment.details.score.legend['2']}
        </p>
        <p>confidence: {percent(judgment.details.score.confidence)}</p>
        <ul>
          {Object.entries(judgment.details.score.probabilities).map(([score, probability]) => (
            <li key={score}>
              {score}: {percent(probability)}
            </li>
          ))}
        </ul>
        <h4>Noul (urgency)</h4>
        <p>急ぎの要望がある確率: {percent(judgment.details.noul.noul)}</p>
        <p>「急ぎの要望があるか」に対するyesの確率です。急ぎの強さを表す値ではありません。</p>
        <p>Choice・Scoreのconfidenceは確率分布から計算される値で、候補の確率とは別です。</p>
        <p>確率とconfidenceは正答率の保証ではありません。分岐には丸め前の値を使います。</p>
      </section>
      {inspection && (
        <section>
          <h3>アプリの採用判断</h3>
          <p>
            閾値はアプリの初期方針で、正答を保証しません。数値上の採用可能性と、この場面で使うかは別に判断します。
          </p>
          <p>
            Choice: confidence ≥ {inspection.policy.choice.minConfidence}、最大候補確率 ≥{' '}
            {inspection.policy.choice.minProbability}
            、最大候補が一つの場合に採用できます。unclearは判定要確認、unmentionedは未言及です。
          </p>
          <p>
            Score: 支障の根拠ありを採用でき、confidence ≥ {inspection.policy.score.minConfidence}
            の場合に採用します。点数 ≥ {inspection.policy.score.highFrom}
            は支障大、それ未満は支障小です。未確定の回避策を確認するかに使い、設定相談では使いません。
          </p>
          <p>
            Noul: 値 ≥ {inspection.policy.noul.urgentFrom}は急ぎ、値 ≤{' '}
            {inspection.policy.noul.notUrgentThrough}
            は急ぎなし、中間は判定要確認です。確定済み回答は維持します。
          </p>
          {Object.entries(inspection.normalization).map(([id, decision]) => {
            const applied = application?.decisions.find((item) => item.judgmentId === id)
            return (
              <div key={id}>
                <h4>
                  {LAB_JUDGMENT_LABELS[id as JudgmentId]} ({id}):{' '}
                  {normalizationLabels[decision.status]}
                </h4>
                <p>
                  {decision.reasons.map((reason) => LAB_NORMALIZATION_REASONS[reason]).join('、')}
                </p>
                <ul>
                  {decision.checks.map((check, index) => (
                    <li key={index}>
                      {LAB_NORMALIZATION_REASONS[check.rule]}: {String(check.actual)}{' '}
                      {operators[check.operator]} {String(check.expected)}:{' '}
                      {check.passed ? '条件成立' : '条件未達'}
                    </li>
                  ))}
                </ul>
                {applied && (
                  <p>
                    {dispositionLabels[applied.disposition]}:{' '}
                    {LAB_APPLICATION_REASONS[applied.reason]}
                    {applied.priorityRule && `（${LAB_PRIORITY_REASONS[applied.priorityRule]}）`}
                  </p>
                )}
              </div>
            )
          })}
        </section>
      )}
      {application && before && previousQuestion && (
        <section>
          <h3>実際の動作</h3>
          <TurnApplicationSummary application={application} previousQuestion={previousQuestion} />
          <p>
            Noulによる表示変更は要点を先に示すことと手順の開閉です。案内の種類や実際の対応速度は変えません。
          </p>
        </section>
      )}
      {inspection && (
        <>
          <details>
            <summary>送信した文脈</summary>
            <p>Jevへ送信したstateの全項目</p>
            <pre className="whitespace-pre-wrap break-words">{format(inspection.state)}</pre>
            {previousQuestion && (
              <>
                <p>入力直前に実際に表示していた質問</p>
                <pre className="whitespace-pre-wrap break-words">{format(previousQuestion)}</pre>
              </>
            )}
            {before && (
              <>
                <p>Jevへ送信していない入力前の画面状態（before）</p>
                <pre className="whitespace-pre-wrap break-words">
                  {format({
                    stage: before.stage,
                    clarification: before.clarification,
                    topicPicker: before.topicPicker,
                    notice: before.notice,
                  })}
                </pre>
              </>
            )}
          </details>
          <details>
            <summary>判定質問の設計</summary>
            {Object.entries(inspection.questions).map(([id, question]) => (
              <section key={id} data-question-id={id}>
                <h4>
                  {LAB_JUDGMENT_LABELS[id as JudgmentId]} ({id}) / {question.type}
                </h4>
                <p>
                  対応する数値:{' '}
                  {id === 'impact'
                    ? String(judgment.details.score.score)
                    : id === 'urgency'
                      ? String(judgment.details.noul.noul)
                      : format(
                          judgment.details.choices[id as keyof typeof judgment.details.choices],
                        )}
                </p>
                <p>instructions: {question.instructions}</p>
                <p>
                  {question.type === 'noul'
                    ? 'criteriaは未定義'
                    : `criteria: ${format(question.criteria)}`}
                </p>
              </section>
            ))}
          </details>
        </>
      )}
    </details>
  )
}

import {
  LAB_CONFIRMATION_REASONS,
  LAB_END_LABELS,
  LAB_JUDGMENT_LABELS,
  LAB_SKIP_REASONS,
  getGuide,
  labValueLabel,
  LAB_CONTENT,
} from './textJudgmentLabContent'
import type { ConversationApplication, QuestionSnapshot, TurnRecord } from './textJudgmentLabTypes'

export default function TextJudgmentTurnSummary({ record }: Readonly<{ record: TurnRecord }>) {
  if (record.kind === 'pending') return <p role="status">判定中です。</p>
  if (record.kind === 'failed') return <p>判定できませんでした。結果は適用していません。</p>
  if (record.kind === 'interrupted') return <p>判定を中断しました。結果は適用していません。</p>
  return (
    <div className="lab-turn-summary min-w-0 break-words">
      {record.kind === 'choice' && <p>Jev呼び出しなし</p>}
      <TurnApplicationSummary
        application={record.application}
        previousQuestion={record.previousQuestion}
      />
    </div>
  )
}

export function TurnApplicationSummary({
  application,
  previousQuestion,
}: Readonly<{
  application: ConversationApplication
  previousQuestion: QuestionSnapshot
}>) {
  const next = application.next
  return (
    <div className="lab-turn-summary min-w-0 break-words">
      <dl>
        <dt>入力直前の質問</dt>
        <dd>{previousQuestion.prompt}</dd>
        <dt>今回確定したこと</dt>
        <dd>
          {application.newlyConfirmed.length === 0 && <p>新しく確定した回答はありません</p>}
          {application.newlyConfirmed.map((change) => (
            <p key={change.field}>
              {LAB_JUDGMENT_LABELS[change.field]}: {labValueLabel(change.value)}
            </p>
          ))}
          {Object.entries(application.preserved)
            .filter(([, value]) => value !== null)
            .map(([field, value]) => (
              <p key={field}>
                以前の回答を維持: {LAB_JUDGMENT_LABELS[field as keyof typeof application.preserved]}
                : {labValueLabel(value!)}
              </p>
            ))}
          {application.impactChange && (
            <p>
              支障の大きさ: {labValueLabel(application.impactChange.before)} →{' '}
              {labValueLabel(application.impactChange.after)}
            </p>
          )}
        </dd>
        <dt>会話への影響</dt>
        <dd>
          {application.skipped.map((skip) => (
            <p key={skip.question}>
              {LAB_JUDGMENT_LABELS[skip.question]}の確認を省略: {LAB_SKIP_REASONS[skip.reason]}
            </p>
          ))}
          {application.notice === 'restart_required' && (
            <p>{LAB_CONTENT.messages.restartRequired}</p>
          )}
          {application.notice === 'out_of_scope' && <p>{LAB_CONTENT.messages.outOfScope}</p>}
          {application.notice === 'mixed_scope' && <p>対象内の相談だけを扱います。</p>}
          {next.kind === 'start' && <p>相談開始の入力を待ちます。</p>}
          {application.nextQuestion && <p>次の質問: {application.nextQuestion.prompt}</p>}
          {next.kind === 'guidance' && (
            <>
              <p>案内: {getGuide(next.guideId).summary}</p>
              <p>
                {next.presentation === 'summary_first'
                  ? '要点を先に表示し、手順は閉じて表示'
                  : '手順を開いて表示'}
              </p>
            </>
          )}
          {next.kind === 'ended' && <p>{LAB_END_LABELS[next.outcome]}</p>}
        </dd>
        <dt>確認が必要なこと</dt>
        <dd>
          {application.needsConfirmation.length === 0 && <p>追加の確認はありません</p>}
          {application.needsConfirmation.map((item) => (
            <p key={item.question}>
              {LAB_JUDGMENT_LABELS[item.question]}: {LAB_CONFIRMATION_REASONS[item.reason]}。
              {item.mode === 'choices_only'
                ? '選択肢で回答してください'
                : '自由文または選択肢で回答してください'}
            </p>
          ))}
        </dd>
      </dl>
    </div>
  )
}

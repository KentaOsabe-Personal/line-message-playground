import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import TextJudgmentTurnSummary from '../src/TextJudgmentTurnSummary'
import { applyJudgment, conversationQuestion, createConversationCore, selectConversationChoice } from '../src/textJudgmentLabState'
import type { TurnRecord } from '../src/textJudgmentLabTypes'
import { v2Request, v2Response } from './textJudgmentLabV2Fixture'

export function judgedRecord(): Extract<TurnRecord, { kind: 'judged' }> {
  const before = createConversationCore('c')
  const judgment = v2Response()
  const application = applyJudgment(before, judgment).application
  return { id: 'turn', kind: 'judged', source: 'text', text: '通知が届きません', before,
    previousQuestion: conversationQuestion(before)!, request: v2Request(), judgment, application, uiElapsedMs: 456 }
}
const show = (record: TurnRecord) => renderToStaticMarkup(<TextJudgmentTurnSummary record={record} />)

describe('task 11.1 発言記録の通常表示', () => {
  // テストケース: 判定に成功した発言の記録に、以前の回答と今回確定した回答を含めて表示する。
  // 期待値: 通常表示の4項目を示す。今回確定した回答、維持した以前の回答、次の動作を区別して表示する。
  it('四項目と新しい回答・以前の回答を示す', () => {
    const original = judgedRecord()
    const record = { ...original, application: { ...original.application, preserved: { topic: 'missing_notification', scope: null, workaround: null, urgency: null },
      newlyConfirmed: [{ field: 'scope', value: 'unknown' }], needsConfirmation: [] } } satisfies Extract<TurnRecord, { kind: 'judged' }>
    const html = show(record)
    for (const label of ['入力直前の質問', '今回確定したこと', '会話への影響', '確認が必要なこと',
      '通知の範囲: 分からない', '以前の回答を維持: 相談の種類: 通知が届かない', '追加の確認はありません']) expect(html).toContain(label)
    expect(html).toContain(record.previousQuestion.prompt)
  })
  // テストケース: 新たに確定した回答がなく、判定が曖昧な通知の範囲を選択肢だけで確認する発言記録を表示する。
  // 期待値: 新たに確定した回答がないこと、確認が必要な理由、回答方法、記録された次の質問を表示する。
  it('空の確定と確認理由・回答方法を示す', () => {
    const original = judgedRecord()
    const record = { ...original, application: { ...original.application, newlyConfirmed: [], impactChange: null,
      needsConfirmation: [{ question: 'scope', reason: 'unclear', mode: 'choices_only' }] } } satisfies Extract<TurnRecord, { kind: 'judged' }>
    expect(show(record)).toContain('新しく確定した回答はありません')
    expect(show(record)).toContain('判定が曖昧なため')
    expect(show(record)).toContain('選択肢で回答してください')
  })
  // テストケース: 本人が相談の種類を選択する記録を表示する。
  // 期待値: Jevを呼び出さずに確定した回答と次の質問を表示し、判定数値は生成しない。
  it('選択肢はJev呼び出しなしと示す', () => {
    const record = judgedRecord()
    const answer = { question: 'topic', value: 'missing_notification' } as const
    const application = selectConversationChoice(record.before, answer)!.application
    const html = show({ ...record, kind: 'choice', source: 'choice', answer, application })
    expect(html).toContain('Jev呼び出しなし')
    expect(html).toContain('通知が届かない')
    expect(html).not.toContain('confidence')
  })
  // テストケース: 判定中、失敗、中断の記録を表示する。
  // 期待値: 判定中であること、または結果を適用していないことだけを表示する。確定した回答や判定数値は補わない。
  it.each(['pending', 'failed', 'interrupted'] as const)('%sに確定や数値を付けない', kind => {
    const record = judgedRecord()
    const html = show({ ...record, kind, failure: 'judgment_failed' } as TurnRecord)
    expect(html).toContain(kind === 'pending' ? '判定中' : '結果は適用していません')
    expect(html).not.toContain('今回確定したこと')
    expect(html).not.toContain('confidence')
  })
  // テストケース: 回避策の確認を回答済みのため省略し、急ぎの場合の案内を表示する発言記録を渡す。
  // 期待値: 確認を省略した理由として回答済みであることを示す。要点を先に表示して手順を閉じることを示し、確認の省略をScoreだけの効果とは説明しない。
  it('省略原因と案内の開閉を固定記録から示す', () => {
    const original = judgedRecord()
    const record = { ...original, application: { ...original.application, skipped: [{ question: 'workaround', reason: 'answered_before' }],
      next: { kind: 'guidance', guideId: 'missing_all', presentation: 'summary_first' }, nextQuestion: null } } satisfies Extract<TurnRecord, { kind: 'judged' }>
    expect(show(record)).toContain('代替確認の確認を省略: 以前に回答済み')
    expect(show(record)).toContain('要点を先に表示し、手順は閉じて表示')
  })
})

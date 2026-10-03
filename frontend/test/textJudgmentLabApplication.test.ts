import { describe, expect, it } from 'vitest'
import { applyJudgment, createConversationCore, conversationQuestion, selectConversationChoice, type ConversationCore } from '../src/textJudgmentLabState'
import type { ConversationTransition, JudgmentResponse } from '../src/textJudgmentLabTypes'
import { v2Response } from './textJudgmentLabV2Fixture'

const unknown = { kind: 'unmentioned' } as const
function judgment(overrides: Partial<JudgmentResponse['evidence']> = {}) {
  const value = v2Response()
  value.evidence = { topic: unknown, relevance: 'in_scope', change: 'keep', scope: unknown,
    workaround: unknown, result: unknown, impact: 'needs_review', urgency: unknown, ...overrides }
  return value
}
function allAnswers(topic: 'missing_notification' | 'notification_settings' = 'missing_notification') {
  return judgment({ topic: { kind: 'known', value: topic }, scope: { kind: 'known', value: 'specific' },
    workaround: { kind: 'known', value: 'can_read' }, impact: 'high', urgency: { kind: 'known', value: false } })
}
function consistent(transition: ConversationTransition, before: ConversationCore) {
  expect(transition.application.next).toEqual(transition.core.stage)
  expect(transition.application.nextQuestion).toEqual(conversationQuestion(transition.core))
  expect(transition.application.preserved).toEqual(before.confirmed)
  for (const field of ['topic', 'scope', 'workaround', 'urgency'] as const) {
    expect(transition.application.newlyConfirmed.find(change => change.field === field)?.value ?? null)
      .toEqual(before.confirmed[field] === null ? transition.core.confirmed[field] : null)
  }
  expect(transition.application.decisions).toHaveLength(9)
  expect(new Set(transition.application.decisions.map(d => d.judgmentId)).size).toBe(9)
}

describe('task 10.1 会話適用結果', () => {
  // テストケース: 一つの発言から、通知相談に必要な回答をすべて確定する。
  // 期待値: 新たに確定した回答、支障の大きさの変化、次の案内の記録が、実際の会話状態と一致する。
  it('一括確定の差分と案内を同時に返す', () => {
    const before = createConversationCore('c')
    const result = applyJudgment(before, allAnswers())
    consistent(result, before)
    expect(result.application.newlyConfirmed).toHaveLength(4)
    expect(result.application.impactChange).toEqual({ before: 'unassessed', after: 'high' })
    expect(result.core.stage).toEqual({ kind: 'guidance', guideId: 'missing_specific', presentation: 'details_open' })
    expect(result.application.needsConfirmation).toEqual([])
    expect(result.application.decisions.find(d => d.judgmentId === 'result')).toMatchObject({ reason: 'not_used_here' })
  })
  // テストケース: 案内を表示した後に、確定済みの範囲、支障の大きさ、急ぎの要望と矛盾する判定を受ける。
  // 期待値: 確定済みの回答と案内を維持し、新たな回答の確定や支障の大きさの変化を記録しない。
  it('矛盾する後続判定から回答と案内を守る', () => {
    const before = applyJudgment(createConversationCore('c'), allAnswers()).core
    const result = applyJudgment(before, judgment({ scope: { kind: 'known', value: 'all' }, impact: 'low', urgency: { kind: 'known', value: true } }))
    consistent(result, before)
    expect(result.core.confirmed).toEqual(before.confirmed)
    expect(result.core.stage).toEqual(before.stage)
    expect(result.application.impactChange).toBeNull()
    expect(result.application.decisions.find(d => d.judgmentId === 'scope')).toMatchObject({ reason: 'confirmed_preserved' })
    expect(result.application.decisions.find(d => d.judgmentId === 'urgency')).toMatchObject({ reason: 'confirmed_preserved' })
  })
  // テストケース: 本人の「分からない」という回答と、未言及、採用条件未達、判定が曖昧な場合をそれぞれ処理する。
  // 期待値: 「分からない」は回答として確定する。確認する理由と質問の順序は、実際の会話状態と一致する。
  it('分からないの確定と判定要確認を区別する', () => {
    const before = applyJudgment(createConversationCore('c'), judgment({ topic: { kind: 'known', value: 'notification_settings' } })).core
    const known = applyJudgment(before, judgment({ scope: { kind: 'known', value: 'unknown' } }))
    consistent(known, before)
    expect(known.application.newlyConfirmed).toContainEqual({ field: 'scope', value: 'unknown' })
    expect(known.application.needsConfirmation[0].question).toBe('urgency')
    const unclear = judgment({ scope: { kind: 'needs_review' } })
    unclear.inspection = { ...unclear.inspection, normalization: { ...unclear.inspection.normalization,
      scope: { status: 'needs_review', reasons: ['unclear'], checks: [] } } }
    const first = applyJudgment(before, unclear)
    expect(first.application.needsConfirmation).toEqual([{ question: 'scope', reason: 'unclear', mode: 'free_and_choices' }])
    const second = applyJudgment(first.core, judgment({ scope: { kind: 'needs_review' } }))
    expect(second.application.needsConfirmation).toEqual([{ question: 'scope', reason: 'conditions_not_met', mode: 'choices_only' }])
    const unmentioned = applyJudgment(before, judgment())
    expect(unmentioned.application.needsConfirmation[0].reason).toBe('unmentioned')
  })
  // テストケース: 通知設定の相談で、支障が大きいというScoreの判定と、案内前の結果判定を受ける。
  // 期待値: この場面ではScoreと結果判定を使わず、支障の大きさを確定しない。
  it('設定相談のScoreを会話へ適用しない', () => {
    const before = createConversationCore('c')
    const result = applyJudgment(before, allAnswers('notification_settings'))
    consistent(result, before)
    expect(result.core.impact).toBe('unassessed')
    expect(result.application.decisions.find(d => d.judgmentId === 'impact')).toMatchObject({ disposition: 'not_used', reason: 'not_used_here' })
  })
})

// テストケース: 通知不達の案内後、相談内容を選ぶ場面で、相談内容が未言及または要確認で、支障の大きさが異なる判定を受ける。
// 期待値: 保存済みの案内と支障の大きさを維持し、支障の大きさの変化を記録しない。
it.each(['unmentioned', 'needs_review'] as const)('通知不達の保存済み案内をpicker中も固定する %s', kind => {
  const before = applyJudgment(createConversationCore('c'), { ...allAnswers(), evidence: { ...allAnswers().evidence, impact: 'needs_review' } }).core
  const picker = applyJudgment(before, judgment({ topic: { kind: 'known', value: 'both' } })).core
  const result = applyJudgment(picker, judgment({ topic: { kind }, impact: 'high' }))
  expect(result.core.impact).toBe('needs_review')
  expect(result.application.impactChange).toBeNull()
  const restored = selectConversationChoice(result.core, { question: 'topic', value: 'missing_notification' })
  expect(restored?.core.stage).toEqual(before.stage)
})

describe('task 10.2 優先規則と省略', () => {
  // テストケース: 複数の優先規則の適用条件が同時に成立する。
  // 期待値: 最初に適用した規則だけを記録し、その規則により他の判定を会話に適用しない。
  it.each([
    ['restart', 'change', { change: 'restart', topic: { kind: 'known', value: 'both' }, relevance: 'out_of_scope' }],
    ['different_topic', 'topic', { topic: { kind: 'known', value: 'notification_settings' }, relevance: 'out_of_scope' }],
    ['multiple_topics', 'topic', { topic: { kind: 'known', value: 'both' }, relevance: 'out_of_scope' }],
    ['out_of_scope', 'relevance', { relevance: 'out_of_scope', change: 'needs_review' }],
    ['guard_needs_review', 'relevance', { relevance: 'needs_review', change: 'needs_review' }],
    ['guard_needs_review', 'change', { change: 'needs_review' }],
  ] as const)('%sの実行だけを記録する', (rule, id, overrides) => {
    const before = applyJudgment(createConversationCore('c'), judgment({ topic: { kind: 'known', value: 'missing_notification' } })).core
    const result = applyJudgment(before, judgment({ ...allAnswers().evidence, ...overrides }))
    consistent(result, before)
    expect(result.application.decisions.filter(d => d.disposition === 'applied')).toEqual([
      { judgmentId: id, disposition: 'applied', reason: 'priority_rule', priorityRule: rule },
    ])
    expect(result.application.decisions.every(d => d.priorityRule === rule)).toBe(true)
    expect(result.application.skipped).toEqual([])
    expect(result.core.confirmed).toEqual(before.confirmed)
  })
  // テストケース: 範囲が未回答の状態で、設定相談、支障が小さいこと、急ぎの要望に関する判定を受ける。
  // 期待値: まだ質問する段階に達していない回避策と急ぎの要望を、省略した質問として記録しない。
  it('未到達の質問は省略に含めない', () => {
    const result = applyJudgment(createConversationCore('c'), judgment({ topic: { kind: 'known', value: 'notification_settings' }, impact: 'low' }))
    expect(result.application.skipped).toEqual([{ question: 'topic', reason: 'answered_this_turn' }])
    expect(result.application.nextQuestion?.id).toBe('scope')
  })
  // テストケース: 回避策が回答済みの状態で、支障が小さいという判定を受ける。
  // 期待値: 動作が変わらないことと、回答済みのため回避策の質問を省略したことを記録する。省略をScoreだけの効果として説明しない。
  it('回答済みの回避策をScore省略と説明しない', () => {
    const before = applyJudgment(createConversationCore('c'), judgment({ topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' }, workaround: { kind: 'known', value: 'can_read' } })).core
    const result = applyJudgment(before, judgment({ impact: 'low', urgency: { kind: 'known', value: true } }))
    expect(result.application.decisions.find(d => d.judgmentId === 'impact')).toMatchObject({ disposition: 'applied', reason: 'applied_no_action_change' })
    expect(result.application.skipped).toContainEqual({ question: 'workaround', reason: 'answered_before' })
    const together = applyJudgment(createConversationCore('c'), allAnswers())
    expect(together.application.skipped).toContainEqual({ question: 'workaround', reason: 'answered_this_turn' })
  })
  // テストケース: 支障が小さい通知不達の相談と設定相談で、回避策が未回答のまま案内へ進む。
  // 期待値: 回避策の質問を省略する理由を相談ごとに区別する。確定済みの「支障が小さい」という判定も、今回のScoreと区別する。
  it('low・設定・既存impactを区別する', () => {
    const input = allAnswers()
    input.evidence.workaround = unknown
    input.evidence.impact = 'low'
    const low = applyJudgment(createConversationCore('c'), input)
    expect(low.application.skipped).toContainEqual({ question: 'workaround', reason: 'impact_low' })
    const settings = applyJudgment(createConversationCore('c'), allAnswers('notification_settings'))
    expect(settings.application.skipped).toContainEqual({ question: 'workaround', reason: 'settings_consultation' })
    const before = applyJudgment(createConversationCore('c'), judgment({ topic: { kind: 'known', value: 'missing_notification' }, impact: 'low' })).core
    const fixed = applyJudgment(before, judgment({ scope: { kind: 'known', value: 'all' }, impact: 'high' }))
    expect(fixed.application.decisions.find(d => d.judgmentId === 'impact')).toMatchObject({ reason: 'confirmed_preserved' })
    expect(fixed.application.skipped).toContainEqual({ question: 'workaround', reason: 'impact_low' })
  })
  // テストケース: LINEを開いてもメッセージを確認できないことと、急ぎの要望を同時に確定する。
  // 期待値: 未解決での終了と急ぎの確認を省略したことを記録する。案内の開閉は記録しない。
  it('cannot_read終了ではNoulの案内表示効果を作らない', () => {
    const input = allAnswers()
    input.evidence.workaround = { kind: 'known', value: 'cannot_read' }
    input.evidence.urgency = { kind: 'known', value: true }
    const before = createConversationCore('c')
    const result = applyJudgment(before, input)
    consistent(result, before)
    expect(result.application.next).toEqual({ kind: 'ended', outcome: 'unresolved' })
    expect(result.application.nextQuestion).toBeNull()
    expect(result.application.skipped).toContainEqual({ question: 'urgency', reason: 'cannot_read_termination' })
    expect(result.application.newlyConfirmed).toContainEqual({ field: 'urgency', value: true })
  })
  // テストケース: 対象内と対象外の内容が混在する発言から、対象内の候補を採用し、急ぎの要望を先に確定する。
  // 期待値: 内容が混在している旨の通知と回答の確定だけを記録する。範囲への回答後は、確定済みの急ぎの要望に従って案内を表示する。
  it('mixedと先行Noulを実動作から区別する', () => {
    const before = applyJudgment(createConversationCore('c'), judgment({ relevance: 'mixed', topic: { kind: 'known', value: 'notification_settings' }, urgency: { kind: 'known', value: true } }))
    expect(before.application.notice).toBe('mixed_scope')
    expect(before.application.next).toEqual({ kind: 'question', question: 'scope' })
    const next = applyJudgment(before.core, judgment({ scope: { kind: 'known', value: 'all' }, urgency: { kind: 'known', value: false } }))
    expect(next.application.next).toEqual({ kind: 'guidance', guideId: 'settings_all', presentation: 'summary_first' })
    expect(next.application.decisions.find(d => d.judgmentId === 'urgency')).toMatchObject({ reason: 'confirmed_preserved' })
  })
})

describe('task 10.3 選択肢の適用結果', () => {
  // テストケース: 相談内容、範囲、回避策、急ぎの要望を順に選択する。
  // 期待値: 選択した回答だけを一度確定し、Jev判定の適用記録は空にする。最後の回答後に案内へ進む。
  it('選択だけで回答を一回確定し、Jev理由を作らない', () => {
    let core = createConversationCore('c')
    for (const choice of [
      { question: 'topic', value: 'missing_notification' },
      { question: 'scope', value: 'unknown' },
      { question: 'workaround', value: 'unknown' },
      { question: 'urgency', value: false },
    ] as const) {
      const next = selectConversationChoice(core, choice)!
      expect(next.core.revision).toBe(core.revision + 1)
      expect(next.application.newlyConfirmed).toEqual([{ field: choice.question, value: choice.value }])
      expect(next.application.decisions).toEqual([])
      expect(next.application.next).toEqual(next.core.stage)
      expect(next.application.nextQuestion).toEqual(conversationQuestion(next.core))
      core = next.core
    }
    expect(core.stage).toEqual({ kind: 'guidance', guideId: 'missing_all', presentation: 'details_open' })
  })
  // テストケース: 二種類の相談それぞれで、案内後の結果をすべて選択する。
  // 期待値: 完了時の終了理由は相談ごとに区別する。未完了は未解決として終了し、未試行または確認不能では案内を維持する。
  it.each(['missing_notification', 'notification_settings'] as const)('%sの結果を本人回答だけで確定する', topic => {
    const before = applyJudgment(createConversationCore('c'), allAnswers(topic)).core
    for (const value of ['done', 'not_done', 'not_tried', 'cannot_check'] as const) {
      const result = selectConversationChoice(before, { question: 'result', value })!
      expect(result.application.decisions).toEqual([])
      expect(result.application.newlyConfirmed).toEqual([])
      if (value === 'not_tried' || value === 'cannot_check') expect(result.application.next).toEqual(before.stage)
      else expect(result.application.next).toEqual({ kind: 'ended', outcome: value === 'not_done' ? 'unresolved' : topic === 'missing_notification' ? 'resolved' : 'settings_completed' })
    }
  })
  // テストケース: 現在の質問以外への回答、許可されていない値、相談終了後の回答を渡す。
  // 期待値: nullを返し、会話状態と適用記録を作らない。
  it('許可値外・現在質問外・終了後を拒否する', () => {
    const before = createConversationCore('c')
    expect(selectConversationChoice(before, { question: 'scope', value: 'all' })).toBeNull()
    expect(selectConversationChoice(before, { question: 'topic', value: 'bad' } as never)).toBeNull()
    const ended = { ...before, stage: { kind: 'ended', outcome: 'interrupted' } } as const
    expect(selectConversationChoice(ended, { question: 'topic', value: 'missing_notification' })).toBeNull()
    const urgency = applyJudgment(before, judgment({ topic: { kind: 'known', value: 'notification_settings' }, scope: { kind: 'known', value: 'all' } })).core
    expect(selectConversationChoice(urgency, { question: 'urgency', value: 'yes' } as never)).toBeNull()
  })
  // テストケース: 範囲への回答が二度曖昧だった後に、複数の相談から選ぶ画面で現在の相談を選択する。
  // 期待値: 元の質問と選択肢のみで回答する状態に戻す。範囲を選択した後は自由入力を再開する。
  it('pickerから質問とclarificationを復元する', () => {
    let core = selectConversationChoice(createConversationCore('c'), { question: 'topic', value: 'notification_settings' })!.core
    core = applyJudgment(core, judgment({ scope: { kind: 'needs_review' } })).core
    core = applyJudgment(core, judgment({ scope: { kind: 'needs_review' } })).core
    const picker = applyJudgment(core, judgment({ topic: { kind: 'known', value: 'both' } })).core
    const restored = selectConversationChoice(picker, { question: 'topic', value: 'notification_settings' })!
    expect(restored.core.stage).toEqual(core.stage)
    expect(restored.core.clarification).toEqual(core.clarification)
    expect(restored.application.nextQuestion?.inputMode).toBe('choices_only')
    expect(restored.application.decisions).toEqual([])
    const scope = selectConversationChoice(restored.core, { question: 'scope', value: 'all' })!
    expect(scope.application.newlyConfirmed).toEqual([{ field: 'scope', value: 'all' }])
    expect(scope.application.nextQuestion?.inputMode).toBe('free_and_choices')
  })
})

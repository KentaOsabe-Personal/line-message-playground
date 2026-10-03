import { describe, expect, test } from 'vitest'

import {
  applyJudgment as transitionJudgment,
  createConversationCore,
  currentQuestionId,
  interruptConversation,
  restartConversation,
  selectConversationChoice as transitionChoice,
  type ConversationCore,
  type JudgmentEvidence,
} from '../src/textJudgmentLabState'

import { v2Response } from './textJudgmentLabV2Fixture'

function selectConversationChoice(core: ConversationCore, choice: Parameters<typeof transitionChoice>[1]) {
  return transitionChoice(core, choice)?.core ?? core
}

function applyJudgment(core: ConversationCore, evidence: JudgmentEvidence) {
  return transitionJudgment(core, { ...v2Response(), evidence }).core
}

const unmentioned = { kind: 'unmentioned' } as const
const needsReview = { kind: 'needs_review' } as const

function evidence(overrides: Partial<JudgmentEvidence> = {}): JudgmentEvidence {
  return {
    topic: unmentioned,
    relevance: 'in_scope',
    change: 'keep',
    scope: unmentioned,
    workaround: unmentioned,
    result: unmentioned,
    impact: 'needs_review',
    urgency: unmentioned,
    ...overrides,
  }
}

function choose(core: ConversationCore, question: 'topic' | 'scope' | 'workaround' | 'urgency', value: string | boolean) {
  return selectConversationChoice(core, { question, value } as Parameters<typeof selectConversationChoice>[1])
}

describe('text judgment lab conversation state', () => {
  // テストケース: 新しい相談を作る。
  // 期待値: 回答を持たない送信前状態となり、外部状態や過去相談を引き継がない。
  test('creates an isolated immutable conversation core', () => {
    const core = createConversationCore('consultation-1')
    expect(core).toEqual({
      consultationId: 'consultation-1',
      revision: 0,
      stage: { kind: 'start' },
      confirmed: { topic: null, scope: null, workaround: null, urgency: null },
      impact: 'unassessed',
      clarification: null,
      topicPicker: null,
      notice: null,
    })
    expect(currentQuestionId(core)).toBe('start')
  })

  // テストケース: 一発言から相談、範囲、回避策、急ぎを一括確定する。
  // 期待値: 未確定knownだけを取り込み、通知不達の特定トーク案内へ進む。
  test('confirms all known answers from one judgment', () => {
    const initial = createConversationCore('consultation-1')
    const next = applyJudgment(initial, evidence({
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'specific' },
      workaround: { kind: 'known', value: 'can_read' },
      impact: 'high',
      urgency: { kind: 'known', value: false },
    }))

    expect(next.confirmed).toEqual({
      topic: 'missing_notification', scope: 'specific', workaround: 'can_read', urgency: false,
    })
    expect(next.stage).toEqual({ kind: 'guidance', guideId: 'missing_specific', presentation: 'details_open' })
    expect(initial.confirmed.topic).toBeNull()
  })

  // テストケース: 後続判定が確定済み回答と矛盾する。
  // 期待値: 相談、範囲、回避策、急ぎ、impactを上書きしない。
  test('never overwrites confirmed answers with later contradictory evidence', () => {
    const fixed = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
      workaround: { kind: 'known', value: 'can_read' },
      impact: 'high',
      urgency: { kind: 'known', value: true },
    }))
    const contradicted = applyJudgment(fixed, evidence({
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'specific' },
      workaround: { kind: 'known', value: 'cannot_read' },
      impact: 'low',
      urgency: { kind: 'known', value: false },
    }))

    expect(contradicted.confirmed).toEqual(fixed.confirmed)
    expect(contradicted.impact).toBe('high')
    expect(contradicted.notice).toBe('restart_required')
  })

  const guidanceCases = [
    {
      name: '通知不達・全体・lowは回避策を省略する',
      topic: 'missing_notification', scope: 'all', impact: 'low', urgency: false,
      guideId: 'missing_all', presentation: 'details_open',
    },
    {
      name: '通知不達・分からない・high・確認可能・急ぎは全体要点を示す',
      topic: 'missing_notification', scope: 'unknown', impact: 'high', workaround: 'can_read', urgency: true,
      guideId: 'missing_all', presentation: 'summary_first',
    },
    {
      name: '設定・特定・highでも回避策を質問しない',
      topic: 'notification_settings', scope: 'specific', impact: 'high', urgency: false,
      guideId: 'settings_specific', presentation: 'details_open',
    },
    {
      name: '設定・分からない・急ぎは全体要点を示す',
      topic: 'notification_settings', scope: 'unknown', impact: 'low', urgency: true,
      guideId: 'settings_all', presentation: 'summary_first',
    },
  ] as const

  test.each(guidanceCases)('$name', (row) => {
    const { topic, scope, impact, urgency, guideId, presentation } = row
    const workaround = 'workaround' in row ? row.workaround : undefined
    const next = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: topic },
      scope: { kind: 'known', value: scope },
      workaround: workaround ? { kind: 'known', value: workaround } : unmentioned,
      impact,
      urgency: { kind: 'known', value: urgency },
    }))
    expect(next.stage).toEqual({ kind: 'guidance', guideId, presentation })
    if (topic === 'notification_settings' || impact === 'low') expect(next.confirmed.workaround).toBeNull()
  })

  // テストケース: 通知不達でLINEを開いても確認できない。
  // 期待値: 急ぎを質問せず公式ヘルプへつなぐ未解決終了となる。
  test('ends missing-notification consultation unresolved when messages cannot be read', () => {
    const next = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
      workaround: { kind: 'known', value: 'cannot_read' },
      impact: 'low',
      urgency: needsReview,
    }))
    expect(next.stage).toEqual({ kind: 'ended', outcome: 'unresolved' })
    expect(next.confirmed.urgency).toBeNull()
  })

  // テストケース: high、要確認、未評価の通知不達相談を進める。
  // 期待値: scopeの後に回避策だけを質問し、lowだけは急ぎへ進む。
  test.each([
    ['high', 'workaround'], ['needs_review', 'workaround'], ['unassessed', 'workaround'], ['low', 'urgency'],
  ] as const)('selects one next question for impact %s', (impact, question) => {
    let core = createConversationCore('consultation-1')
    core = choose(core, 'topic', 'missing_notification')
    core = { ...core, impact }
    core = choose(core, 'scope', 'all')
    expect(core.stage).toEqual({ kind: 'question', question })
  })

  // テストケース: 同じ質問へ自由文で二度曖昧に答え、その後選択肢で確定する。
  // 期待値: open、choices_onlyの順に変わり、次の質問で自由入力を復帰する。
  test('limits only a repeatedly ambiguous question to choices', () => {
    let core = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    core = applyJudgment(core, evidence({ scope: needsReview }))
    expect(core.clarification).toEqual({ question: 'scope', mode: 'open' })
    core = applyJudgment(core, evidence({ scope: unmentioned }))
    expect(core.clarification).toEqual({ question: 'scope', mode: 'choices_only' })
    core = choose(core, 'scope', 'all')
    expect(core.clarification).toBeNull()
    expect(core.stage).toEqual({ kind: 'question', question: 'workaround' })
  })

  // テストケース: 判定要確認中に対象外入力が来る。
  // 期待値: 確定値と曖昧回答回数を維持し、同じ質問へ戻す。
  test('keeps state and clarification attempts for out-of-scope input', () => {
    let core = choose(createConversationCore('consultation-1'), 'topic', 'notification_settings')
    core = applyJudgment(core, evidence({ scope: needsReview }))
    const next = applyJudgment(core, evidence({ relevance: 'out_of_scope', scope: { kind: 'known', value: 'specific' } }))
    expect(next.confirmed).toEqual(core.confirmed)
    expect(next.clarification).toEqual(core.clarification)
    expect(next.stage).toEqual(core.stage)
    expect(next.notice).toBe('out_of_scope')
  })

  // テストケース: 対象内と対象外が混在する入力を受ける。
  // 期待値: 対象外部分を知らせ、対象内の未確定回答だけを採用する。
  test('accepts in-scope evidence from mixed input without losing context', () => {
    const core = choose(createConversationCore('consultation-1'), 'topic', 'notification_settings')
    const next = applyJudgment(core, evidence({
      relevance: 'mixed',
      scope: { kind: 'known', value: 'specific' },
      urgency: { kind: 'known', value: false },
      impact: 'high',
    }))
    expect(next.notice).toBe('mixed_scope')
    expect(next.confirmed.scope).toBe('specific')
    expect(next.stage).toMatchObject({ kind: 'guidance', guideId: 'settings_specific' })
  })

  // テストケース: 二相談を同時に検出し、現在相談か別相談を選ぶ。
  // 期待値: 現相談なら保存状態へ戻り、別相談なら自動切替せず新規開始を案内する。
  test('preserves the current consultation through the topic picker', () => {
    let core = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    core = applyJudgment(core, evidence({ topic: { kind: 'known', value: 'both' } }))
    expect(core.topicPicker).not.toBeNull()
    expect(core.stage).toEqual({ kind: 'question', question: 'topic' })

    const restored = choose(core, 'topic', 'missing_notification')
    expect(restored.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(restored.topicPicker).toBeNull()

    const asksRestart = choose(core, 'topic', 'notification_settings')
    expect(asksRestart.confirmed.topic).toBe('missing_notification')
    expect(asksRestart.notice).toBe('restart_required')
  })

  // テストケース: 複数相談picker表示中に、自由文が再び二相談と判定される。
  // 期待値: 最初に保存したstageを上書きせず、現在相談の選択で元の質問へ一度で戻る。
  test('keeps the original stage when both topics are detected repeatedly', () => {
    let core = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    core = applyJudgment(core, evidence({ topic: { kind: 'known', value: 'both' } }))
    core = applyJudgment(core, evidence({ topic: { kind: 'known', value: 'both' } }))

    const restored = choose(core, 'topic', 'missing_notification')
    expect(restored.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(restored.topicPicker).toBeNull()
  })

  // テストケース: 初回の複数相談pickerへ自由文で一つの相談を答える。
  // 期待値: 選んだ相談だけを確定し、pickerを解除して範囲質問へ進む。
  test('starts the freely selected topic from the initial topic picker', () => {
    let core = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'both' },
    }))
    core = applyJudgment(core, evidence({
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'specific' },
    }))

    expect(core.confirmed.topic).toBe('notification_settings')
    expect(core.confirmed.scope).toBeNull()
    expect(core.topicPicker).toBeNull()
    expect(core.stage).toEqual({ kind: 'question', question: 'scope' })
  })

  // テストケース: 初回の複数相談pickerへ、対象外内容を含む自由文で一つの相談を答える。
  // 期待値: 選択した相談だけを確定し、他の候補を採用せず、対象外部分があることを通知する。
  test('keeps the mixed-scope notice when starting a freely selected topic from the initial picker', () => {
    let core = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'both' },
    }))
    core = applyJudgment(core, evidence({
      relevance: 'mixed',
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'specific' },
    }))

    expect(core.confirmed.topic).toBe('notification_settings')
    expect(core.confirmed.scope).toBeNull()
    expect(core.topicPicker).toBeNull()
    expect(core.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(core.notice).toBe('mixed_scope')
  })

  // テストケース: 進行中の複数相談pickerへ自由文で現在相談を答える。
  // 期待値: 新しい候補を混ぜず、保存したstageとclarificationへ復帰する。
  test('restores the current consultation selected freely from the topic picker', () => {
    let core = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    core = applyJudgment(core, evidence({ scope: needsReview }))
    const savedClarification = core.clarification
    core = applyJudgment(core, evidence({ topic: { kind: 'known', value: 'both' } }))
    core = applyJudgment(core, evidence({
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
    }))

    expect(core.confirmed.scope).toBeNull()
    expect(core.topicPicker).toBeNull()
    expect(core.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(core.clarification).toEqual(savedClarification)
  })

  // テストケース: 進行中の複数相談pickerへ、対象外内容を含む自由文で現在相談を答える。
  // 期待値: 新しい候補を混ぜず、保存した状態へ復帰し、対象外部分があることを通知する。
  test('keeps the mixed-scope notice when restoring the current topic from the picker', () => {
    let core = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    core = applyJudgment(core, evidence({ scope: needsReview }))
    const savedClarification = core.clarification
    core = applyJudgment(core, evidence({ topic: { kind: 'known', value: 'both' } }))
    core = applyJudgment(core, evidence({
      relevance: 'mixed',
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
    }))

    expect(core.confirmed.scope).toBeNull()
    expect(core.topicPicker).toBeNull()
    expect(core.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(core.clarification).toEqual(savedClarification)
    expect(core.notice).toBe('mixed_scope')
  })

  // テストケース: 訂正希望を検出する。
  // 期待値: 確定回答とstageを変えず、新しい相談の開始だけを案内する。
  test('does not partially correct confirmed answers', () => {
    const core = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    const next = applyJudgment(core, evidence({ change: 'restart', scope: { kind: 'known', value: 'all' } }))
    expect(next.confirmed).toEqual(core.confirmed)
    expect(next.stage).toEqual(core.stage)
    expect(next.notice).toBe('restart_required')
  })

  // テストケース: 初回入力と例文へ同じ判定結果を適用する。
  // 期待値: 入力元によらず同一の確定状態と分岐になる。
  test('applies the same judgment path to free text and starter examples', () => {
    const judged = evidence({
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'all' },
      urgency: { kind: 'known', value: false },
      impact: 'low',
    })
    expect(applyJudgment(createConversationCore('free'), judged).stage)
      .toEqual(applyJudgment(createConversationCore('example'), judged).stage)
  })

  // テストケース: 案内表示後の結果回答判定に別のimpactが含まれる。
  // 期待値: 設定相談では支障の大きさを未評価のまま保ち、案内と表示方法も変更しない。
  test('freezes impact after guidance is shown', () => {
    let core = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'all' },
      impact: 'needs_review',
      urgency: { kind: 'known', value: false },
    }))
    expect(core.stage.kind).toBe('guidance')
    expect(core.impact).toBe('unassessed')

    core = applyJudgment(core, evidence({ result: { kind: 'known', value: 'not_tried' }, impact: 'high' }))
    expect(core.impact).toBe('unassessed')
    expect(core.stage.kind).toBe('guidance')
  })

  // テストケース: 案内後の複数相談pickerで、現在の相談を特定できない自由文に別のimpactが含まれる。
  // 期待値: 現在の相談に戻っても、支障の大きさは未評価のままとし、案内と表示方法を変更しない。
  test.each([
    ['needs_review', needsReview],
    ['unmentioned', unmentioned],
  ] as const)('freezes guidance through the topic picker when topic is %s', (_name, topic) => {
    let core = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'all' },
      impact: 'needs_review',
      urgency: { kind: 'known', value: false },
    }))
    const guidance = core.stage
    expect(guidance).toEqual({
      kind: 'guidance', guideId: 'settings_all', presentation: 'details_open',
    })

    core = applyJudgment(core, evidence({ topic: { kind: 'known', value: 'both' } }))
    core = applyJudgment(core, evidence({ topic, impact: 'high' }))
    core = choose(core, 'topic', 'notification_settings')

    expect(core.impact).toBe('unassessed')
    expect(core.stage).toEqual(guidance)
  })

  // テストケース: 案内後に各結果を選ぶ。
  // 期待値: topic別の成功、共通の未解決、未試行維持を区別する。
  test.each([
    ['missing_notification', 'done', 'resolved'],
    ['notification_settings', 'done', 'settings_completed'],
    ['missing_notification', 'not_done', 'unresolved'],
    ['notification_settings', 'not_done', 'unresolved'],
  ] as const)('maps %s result %s to %s', (topic, result, outcome) => {
    let core = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: topic },
      scope: { kind: 'known', value: 'all' },
      impact: 'low',
      urgency: { kind: 'known', value: false },
    }))
    core = selectConversationChoice(core, { question: 'result', value: result })
    expect(core.stage).toEqual({ kind: 'ended', outcome })
  })

  test.each(['not_tried', 'cannot_check'] as const)('keeps guidance active for result %s', (result) => {
    let core = applyJudgment(createConversationCore('consultation-1'), evidence({
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
      impact: 'low',
      urgency: { kind: 'known', value: false },
    }))
    const guidance = core.stage
    core = selectConversationChoice(core, { question: 'result', value: result })
    expect(core.stage).toEqual(guidance)
    expect(currentQuestionId(core)).toBe('result')
  })

  // テストケース: 進行中相談を中断し、新しい相談を始める。
  // 期待値: 中断理由を区別し、新相談は前の回答や判定状態を一切持たない。
  test('interrupts and restarts without restoring the previous conversation', () => {
    const active = choose(createConversationCore('consultation-1'), 'topic', 'missing_notification')
    const ended = interruptConversation(active)
    expect(ended.stage).toEqual({ kind: 'ended', outcome: 'interrupted' })
    expect(interruptConversation(ended)).toBe(ended)

    const restarted = restartConversation(ended, 'consultation-2')
    expect(restarted).toEqual(createConversationCore('consultation-2'))
  })
})

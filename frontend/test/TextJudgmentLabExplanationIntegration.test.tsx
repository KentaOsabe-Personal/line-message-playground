import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test } from 'vitest'
import TextJudgmentLab from '../src/TextJudgmentLab'
import {
  LAB_NORMALIZATION_REASONS,
  LAB_JUDGMENT_LABELS,
  LAB_APPLICATION_REASONS,
  LAB_SKIP_REASONS,
} from '../src/textJudgmentLabContent'
import { conversationQuestion } from '../src/textJudgmentLabState'
import { createTextJudgmentLabController } from '../src/useTextJudgmentLab'
import type { JudgmentId } from '../src/textJudgmentLabTypes'
import { boundaryFixture, boundaryResponse } from './textJudgmentLabBoundaryFixture'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
let container: HTMLDivElement
let root: Root
beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

// テストケース: Backendと共有する固定応答を会話画面に渡す。閾値の前後・同率最大・不明・未言及・支障根拠について確認する。
// 期待値: 同じ発言について、丸め前の値による採用理由と会話への適用結果が、返された会話状態・通常表示・詳細・次の質問と一致する。
test.each(
  boundaryFixture.cases.filter(
    (c) =>
      !c.id.startsWith('topic-') && !c.id.startsWith('workaround-') && !c.id.startsWith('result-'),
  ),
)('$idの説明と実動作が一致する', async (scenario) => {
  let id = 0
  const controller = createTextJudgmentLabController(
    { judge: async (_token, request) => boundaryResponse(request, scenario.id) },
    { now: () => 1, uuid: () => `00000000-0000-4000-8000-${String(++id).padStart(12, '0')}` },
  )
  await act(async () =>
    root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }}
        getValidIdToken={() => 'safe-token'}
      />,
    ),
  )
  await act(async () => controller.submit('架空の通知相談です', 'text', 'safe-token'))
  const state = controller.getState()
  const record = state.messages[0]
  if (record.kind !== 'judged') throw new Error('成功記録がありません')
  const field = scenario.field as JudgmentId
  const normalized = record.judgment.inspection.normalization[field]
  expect(normalized.status).toBe(scenario.status)
  expect(normalized.reasons).toEqual(scenario.reasons)
  expect(record.application.next).toEqual(state.core.stage)
  expect(record.application.nextQuestion).toEqual(conversationQuestion(state.core))
  const row = container.querySelector('[aria-label="あなたのメッセージ"]')!
  const summary = row.querySelector('.lab-turn-summary')!
  const details = row.querySelector<HTMLDetailsElement>('.lab-judgment-details')!
  expect(details.open).toBe(false)
  const adoption = [...details.querySelectorAll('h4')].find((h) =>
    h.textContent?.startsWith(`${LAB_JUDGMENT_LABELS[field]} (${field}):`),
  )!.parentElement!
  for (const reason of normalized.reasons)
    expect(adoption.textContent).toContain(LAB_NORMALIZATION_REASONS[reason])
  for (const check of normalized.checks) {
    const op = { gte: '≥', lte: '≤', eq: '=' }[check.operator]
    expect(adoption.textContent).toContain(
      `${check.actual} ${op} ${check.expected}: ${check.passed ? '条件成立' : '条件未達'}`,
    )
  }
  const applied = record.application.decisions.find((item) => item.judgmentId === field)!
  expect(adoption.textContent).toContain(LAB_APPLICATION_REASONS[applied.reason])
  for (const skipped of record.application.skipped)
    expect(summary.textContent).toContain(LAB_SKIP_REASONS[skipped.reason])
  const question = record.application.nextQuestion!
  expect(summary.textContent).toContain(question.prompt)
  expect(container.querySelector('[aria-label="ラボからのメッセージ"]')?.textContent).toContain(
    question.prompt,
  )
  // 次に表示する質問の期待値は、テストデータの説明に依存せず、閾値と質問の順序から決める。
  const expected =
    record.judgment.evidence.scope.kind !== 'known'
      ? 'scope'
      : record.judgment.evidence.impact !== 'low'
        ? 'workaround'
        : 'urgency'
  expect(state.core.stage).toEqual({ kind: 'question', question: expected })
  if (scenario.id.startsWith('score-')) {
    expect(state.core.impact).toBe(scenario.id === 'score-1.4999' ? 'low' : 'high')
    expect(details.textContent).toContain('Score (impact): 1.50')
  }
  if (scenario.id === 'scope-confidence-0.6999' || scenario.id === 'scope-confidence-0.7')
    expect(details.textContent).toContain('confidence: 70.0%')
  expect(details.textContent).toContain('正答率の保証ではありません')
  await act(async () => controller.dispose())
})

// テストケース: 回避策への回答が確定済みの通知相談と設定相談に、採用条件を満たすScoreを返す。
// 期待値: Scoreを採用しても動作が変わらない場合と、この場面では使わない場合を区別する。画面には、回答済みのため省略した場合と設定相談のため省略した場合を分けて表示する。
test.each(['missing_notification', 'notification_settings'] as const)(
  '%sのScore使用と省略原因を区別する',
  async (topic) => {
    let id = 0
    const controller = createTextJudgmentLabController(
      {
        judge: async (_token, request) => {
          const patch =
            topic === 'notification_settings'
              ? boundaryFixture.cases.find((c) => c.id === 'topic-notification_settings')!
                  .responsePatch
              : {}
          return boundaryResponse(request, 'score-1.4999', patch)
        },
      },
      { now: () => 1, uuid: () => `00000000-0000-4000-8000-${String(++id).padStart(12, '0')}` },
    )
    const choose = (question: 'topic' | 'scope' | 'workaround', value: string) =>
      expect(controller.choose(question, value, controller.getState().core.revision, true)).toBe(
        true,
      )
    choose('topic', topic)
    choose('scope', 'specific')
    if (topic === 'missing_notification') choose('workaround', 'can_read')
    await act(async () =>
      root.render(
        <TextJudgmentLab
          controller={controller}
          access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }}
          getValidIdToken={() => 'safe-token'}
        />,
      ),
    )
    await act(async () => controller.submit('支障は小さいです', 'text', 'safe-token'))
    const record = controller.getState().messages.at(-1)!
    if (record.kind !== 'judged') throw new Error('成功記録がありません')
    expect(record.judgment.inspection.normalization.impact.status).toBe('eligible')
    const reason = topic === 'missing_notification' ? 'applied_no_action_change' : 'not_used_here'
    expect(record.application.decisions.find((d) => d.judgmentId === 'impact')?.reason).toBe(reason)
    const skipReason =
      topic === 'missing_notification' ? 'answered_before' : 'settings_consultation'
    expect(record.application.skipped).toContainEqual({
      question: 'workaround',
      reason: skipReason,
    })
    expect(container.querySelectorAll('.lab-judgment-details')[0].textContent).toContain(
      LAB_APPLICATION_REASONS[reason],
    )
    const last = [...container.querySelectorAll('[aria-label="あなたのメッセージ"]')].at(-1)!
    expect(last.querySelector('.lab-turn-summary')?.textContent).toContain(
      LAB_SKIP_REASONS[skipReason],
    )
    expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'urgency' })
    await act(async () => controller.dispose())
  },
)

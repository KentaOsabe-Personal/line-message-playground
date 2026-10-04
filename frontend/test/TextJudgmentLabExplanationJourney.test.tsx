import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import TextJudgmentLab from '../src/TextJudgmentLab'
import { LAB_THEMES } from '../src/textJudgmentLabThemes'
import { LAB_END_LABELS, LAB_SKIP_REASONS, getGuide } from '../src/textJudgmentLabContent'
import { conversationQuestion } from '../src/textJudgmentLabState'
import {
  createTextJudgmentLabController,
  type TextJudgmentLabController,
} from '../src/useTextJudgmentLab'
import type { JudgmentRequest, TurnRecord } from '../src/textJudgmentLabTypes'
import { boundaryFixture, boundaryResponse, mergePatch } from './textJudgmentLabBoundaryFixture'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
let container: HTMLDivElement
let root: Root
let controller: TextJudgmentLabController
beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => {
    controller?.dispose()
    root.unmount()
  })
  container.remove()
})
function responseFor(request: JudgmentRequest, ...ids: string[]) {
  const patch: Record<string, unknown> = {}
  for (const id of ids)
    mergePatch(patch, boundaryFixture.cases.find((item) => item.id === id)!.responsePatch)
  return boundaryResponse(request, undefined, patch)
}
async function mount(
  judge: (token: string, request: JudgmentRequest) => Promise<ReturnType<typeof boundaryResponse>>,
) {
  let id = 0
  controller = createTextJudgmentLabController(
    { judge },
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
}
function explanation(): TurnRecord {
  const state = controller.getState()
  const record = state.messages.at(-1)!
  if (record.kind !== 'choice' && record.kind !== 'judged') throw new Error('適用記録がありません')
  expect(record.application.next).toEqual(state.core.stage)
  expect(record.application.nextQuestion).toEqual(conversationQuestion(state.core))
  const row = [...container.querySelectorAll('[aria-label="あなたのメッセージ"]')].at(-1)!
  const summary = row.querySelector('.lab-turn-summary')!
  for (const skip of record.application.skipped)
    expect(summary.textContent).toContain(LAB_SKIP_REASONS[skip.reason])
  if (record.application.nextQuestion) {
    expect(summary.textContent).toContain(record.application.nextQuestion.prompt)
    expect(container.querySelector('[aria-label="ラボからのメッセージ"]')?.textContent).toContain(
      record.application.nextQuestion.prompt,
    )
  }
  const next = record.application.next
  if (next.kind === 'guidance') {
    expect(summary.textContent).toContain(getGuide(next.guideId).summary)
    expect(container.querySelector('.lab-guide')?.textContent).toContain(
      getGuide(next.guideId).summary,
    )
    expect(container.querySelector<HTMLDetailsElement>('.lab-guide details')?.open).toBe(
      next.presentation === 'details_open',
    )
  }
  if (next.kind === 'ended') expect(summary.textContent).toContain(LAB_END_LABELS[next.outcome])
  return record
}
async function click(label: string) {
  const button = [...container.querySelectorAll('button')].find((b) => b.textContent === label)!
  expect(button).toBeDefined()
  await act(async () => button.click())
  return explanation()
}

// テストケース: 正規化済みの固定応答を使い、通知が届かない相談を自由文で始める。回避策を選択肢で回答し、急ぎの要望と結果を自由文で答える。
// 期待値: 各発言の説明が、次の質問・案内・解決の状態と一致する。終了後も過去の説明を変更せず、読み取り専用で保持する。
test('通知不達の自由文・選択肢・急ぎ・解決を正規化から画面まで照合する', async () => {
  let call = 0
  const judge = vi.fn(async (_token: string, request: JudgmentRequest) =>
    responseFor(request, ...(++call === 1 ? [] : call === 2 ? ['noul-0.8'] : ['result-done'])),
  )
  await mount(judge)
  await act(async () => controller.submit('特定トークの通知が届きません', 'text', 'safe-token'))
  explanation()
  expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'workaround' })
  await click('確認できる')
  expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'urgency' })
  await act(async () => controller.submit('急いでいます', 'text', 'safe-token'))
  explanation()
  expect(controller.getState().core.stage).toEqual({
    kind: 'guidance',
    guideId: 'missing_specific',
    presentation: 'summary_first',
  })
  const old = controller.getState().messages.map((m) => JSON.stringify(m))
  await act(async () => controller.submit('通知が届くようになりました', 'text', 'safe-token'))
  explanation()
  expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'resolved' })
  expect(
    controller
      .getState()
      .messages.slice(0, old.length)
      .map((m) => JSON.stringify(m)),
  ).toEqual(old)
  expect(container.querySelector('textarea')).toBeNull()
  expect(judge).toHaveBeenCalledTimes(3)
})

// テストケース: 支障が大きい通知不達の相談で、メッセージを確認できない回答であるcannot_readを選ぶ。
// 期待値: 急ぎの確認を省略した理由が、未解決で終了する動作と一致する。公式ヘルプへのHTTPSリンクを表示する。
test('cannot_readの省略と未解決終了を表示する', async () => {
  await mount(async (_token, request) => boundaryResponse(request))
  await act(async () => controller.submit('通知が届きません', 'text', 'safe-token'))
  explanation()
  const record = await click('確認できない')
  if (record.kind !== 'choice') throw new Error('選択記録がありません')
  expect(record.application.skipped).toContainEqual({
    question: 'urgency',
    reason: 'cannot_read_termination',
  })
  expect(record.application.nextQuestion).toBeNull()
  expect(container.querySelector<HTMLAnchorElement>('a[href^="https://"]')?.href).toMatch(
    /^https:\/\/(help|guide)\.line\.me\//,
  )
})

// テストケース: Scoreがhighの設定相談に、急ぎあり・なしの判定を返す。未試行・確認不能を回答した後、設定完了または未解決を選ぶ。
// 期待値: この場面ではScoreを使わず、設定相談のため回避策の確認を省略する。Noulによる違いは手順の開閉だけで、案内の種類は変わらない。
test.each([true, false])('設定相談の急ぎ=%sで案内と結果の説明を照合する', async (urgent) => {
  const judge = vi.fn(async (_token: string, request: JudgmentRequest) =>
    responseFor(request, 'topic-notification_settings', urgent ? 'noul-0.8' : 'noul-0.2'),
  )
  await mount(judge)
  await act(async () =>
    controller.submit('特定トークの通知設定を知りたいです', 'example', 'safe-token'),
  )
  const first = explanation()
  if (first.kind !== 'judged') throw new Error('判定記録がありません')
  expect(first.judgment.evidence.impact).toBe('high')
  expect(first.application.decisions.find((d) => d.judgmentId === 'impact')).toMatchObject({
    disposition: 'not_used',
    reason: 'not_used_here',
  })
  expect(first.application.skipped).toContainEqual({
    question: 'workaround',
    reason: 'settings_consultation',
  })
  expect(controller.getState().core.stage).toEqual({
    kind: 'guidance',
    guideId: 'settings_specific',
    presentation: urgent ? 'summary_first' : 'details_open',
  })
  const fixed = JSON.stringify(first)
  await click('まだ試していない')
  await click('確認できない')
  expect(controller.getState().core.stage.kind).toBe('guidance')
  await click(urgent ? '設定できた' : '設定できない')
  expect(controller.getState().core.stage).toEqual({
    kind: 'ended',
    outcome: urgent ? 'settings_completed' : 'unresolved',
  })
  expect(JSON.stringify(controller.getState().messages[0])).toBe(fixed)
  expect(judge).toHaveBeenCalledTimes(1)
})

// テストケース: 全7テーマの前提と期待する結果を閲覧する。テーマを選んだ後、例文の送信ボタンを続けて押す。
// 期待値: テーマを選んでも会話状態・判定質問・採用規則は変わらない。例文は通常の判定処理に一回だけ送り、実測結果と期待する結果を区別して表示する。
test('全7テーマの選択と一回の例文送信を会話と分離する', async () => {
  const judge = vi.fn(async (_token: string, request: JudgmentRequest) => boundaryResponse(request))
  await mount(judge)
  const before = JSON.stringify(controller.getState())
  const select = container.querySelector<HTMLSelectElement>('.lab-theme-panel select')!
  expect(select.options).toHaveLength(7)
  for (const theme of LAB_THEMES) {
    await act(async () => {
      select.value = theme.id
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(JSON.stringify(controller.getState())).toBe(before)
    expect(judge).not.toHaveBeenCalled()
    const panel = container.querySelector('.lab-theme-panel')!
    for (const text of [theme.consultation, theme.question, theme.expectation, theme.observation])
      expect(panel.textContent).toContain(text)
    expect(panel.textContent).toContain('期待は実測結果ではありません')
  }
  const expectedText = LAB_THEMES.at(-1)!.examples[0]
  const button = container.querySelector<HTMLButtonElement>('.lab-theme-panel button')!
  await act(async () => {
    button.click()
    button.click()
  })
  expect(judge).toHaveBeenCalledTimes(1)
  const record = explanation()
  expect(record).toMatchObject({ kind: 'judged', source: 'example', text: expectedText })
  expect(judge.mock.calls[0][1].context).toEqual({
    question: 'start',
    confirmed: { topic: null, scope: null, workaround: null, urgency: null },
    impact: 'unassessed',
    recentUserTexts: [],
  })
  expect(JSON.stringify(judge.mock.calls[0][1])).not.toContain('expectation')
  if (record.kind === 'judged')
    expect(record.judgment.inspection.policy).toEqual(boundaryFixture.response.inspection.policy)
})

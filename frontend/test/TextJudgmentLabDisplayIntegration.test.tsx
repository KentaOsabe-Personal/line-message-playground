import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import TextJudgmentLab from '../src/TextJudgmentLab'
import { createTextJudgmentLabController } from '../src/useTextJudgmentLab'
import type { JudgmentResponse, JudgmentRequest } from '../src/textJudgmentLabTypes'
import { v2Response } from './textJudgmentLabV2Fixture'

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
const button = (label: string) =>
  [...container.querySelectorAll('button')].find((item) => item.textContent === label)!

// テストケース: 判定に成功した発言と選択肢による回答を同じ会話画面に表示し、相談を続ける。
// 期待値: 発言時の通常表示と判定詳細を維持する。次の質問は説明と一致し、選択肢による回答には判定詳細を表示しない。
test('発言記録から通常表示と詳細を接続する', async () => {
  let id = 0
  const judge = vi.fn(async (_token: string, request: JudgmentRequest) => v2Response(request))
  const controller = createTextJudgmentLabController(
    { judge },
    { now: () => 1, uuid: () => `id-${++id}` },
  )
  await act(async () =>
    root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }}
        getValidIdToken={() => 'token'}
      />,
    ),
  )
  await act(async () => controller.submit('通知が届きません', 'text', 'token'))
  const first = container.querySelector('[aria-label="あなたのメッセージ"]')!
  expect(first.querySelector('.lab-turn-summary')?.textContent).toContain('今回確定したこと')
  expect(first.textContent).toContain('入力直前の質問')
  expect(first.textContent).toContain('どちらについて相談しますか？')
  const details = first.querySelector<HTMLDetailsElement>('.lab-judgment-details')!
  expect(details.open).toBe(false)
  expect(details.textContent).toContain('実際の動作')
  expect(details.textContent).toContain('送信した文脈')
  const original = first.textContent
  await act(async () => button('すべてのトーク').click())
  expect(first.textContent).toBe(original)
  const rows = container.querySelectorAll('[aria-label="あなたのメッセージ"]')
  expect(rows[1].textContent).toContain('Jev呼び出しなし')
  expect(rows[1].querySelector('.lab-judgment-details')).toBeNull()
  const choice = controller.getState().messages[1]
  if (choice.kind !== 'choice') throw new Error('選択肢記録がありません')
  expect(container.querySelector('[aria-label="ラボからのメッセージ"]')?.textContent).toContain(
    choice.application.nextQuestion?.prompt,
  )
  await act(async () => controller.dispose())
})

// テストケース: テーマを切り替えて例文の送信ボタンを続けて押し、判定中にテーマを閲覧して相談を中断する。
// 期待値: テーマを選んでも会話の状態は変わらない。例文は通常の判定処理へ一回だけ送信し、中断・終了後もテーマを閲覧できる。
test('テーマ閲覧と例文送信の許可を分ける', async () => {
  let id = 0
  const judge = vi.fn(() => new Promise<JudgmentResponse>(() => undefined))
  const controller = createTextJudgmentLabController(
    { judge },
    { now: () => 1, uuid: () => `id-${++id}` },
  )
  await act(async () =>
    root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }}
        getValidIdToken={() => 'token'}
      />,
    ),
  )
  const before = controller.getState().core
  const select = container.querySelector<HTMLSelectElement>('.lab-theme-panel select')!
  expect(select.options).toHaveLength(7)
  expect(container.querySelector('.lab-chat-scroll')?.contains(select)).toBe(true)
  expect(
    container.querySelector('.lab-chat-scroll')?.contains(container.querySelector('.lab-composer')),
  ).toBe(false)
  await act(async () => {
    select.value = 'negation'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  expect(controller.getState().core).toBe(before)
  expect(judge).not.toHaveBeenCalled()
  const example = container.querySelector<HTMLButtonElement>('.lab-theme-panel button')!
  await act(async () => {
    example.click()
    example.click()
  })
  expect(judge).toHaveBeenCalledTimes(1)
  expect(controller.getState().messages[0]).toMatchObject({ source: 'example', kind: 'pending' })
  expect(select.disabled).toBe(false)
  expect(example.disabled).toBe(true)
  await act(async () => button('相談を終了する').click())
  expect(container.querySelector('.lab-theme-panel')).not.toBeNull()
  expect(container.querySelector('.lab-judgment-details')).toBeNull()
  expect(container.textContent).toContain('結果は適用していません')
  await act(async () => button('新しい相談を始める').click())
  expect(controller.getState().messages).toHaveLength(0)
  expect(container.querySelector<HTMLButtonElement>('.lab-theme-panel button')?.disabled).toBe(
    false,
  )
  await act(async () => controller.dispose())
})

// テストケース: 急ぎの要望がある場合とない場合に、選択肢で回答して案内へ進み、「解決した」と回答する。
// 期待値: 通常表示に示す案内、手順の開閉状態、相談の終了が、適用結果と実際の画面表示に一致する。
test.each([true, false])('急ぎ=%sの案内・手順開閉・終了の説明が画面と一致する', async (urgent) => {
  let id = 0
  const judge = vi.fn(async (_token: string, request: JudgmentRequest) => {
    const response = v2Response(request)
    return {
      ...response,
      evidence: { ...response.evidence, urgency: { kind: 'known' as const, value: urgent } },
      details: { ...response.details, noul: { type: 'noul' as const, noul: urgent ? 1 : 0 } },
    }
  })
  const controller = createTextJudgmentLabController(
    { judge },
    { now: () => 1, uuid: () => `id-${++id}` },
  )
  await act(async () =>
    root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }}
        getValidIdToken={() => 'token'}
      />,
    ),
  )
  await act(async () => controller.submit('通知が届きません', 'text', 'token'))
  await act(async () => button('すべてのトーク').click())
  await act(async () => button('確認できる').click())
  const rows = container.querySelectorAll('[aria-label="あなたのメッセージ"]')
  const guideSummary = rows[2].querySelector('.lab-turn-summary')!
  const guide = container.querySelector('.lab-guide')!
  const summaryText = guide.querySelector('p')!.textContent
  expect(guideSummary.textContent).toContain(`案内: ${summaryText}`)
  expect(guideSummary.textContent).toContain(
    urgent ? '要点を先に表示し、手順は閉じて表示' : '手順を開いて表示',
  )
  const steps = guide.querySelector<HTMLDetailsElement>('details')!
  expect(steps.open).toBe(!urgent)
  expect(controller.getState().core.stage).toMatchObject({
    kind: 'guidance',
    guideId: 'missing_all',
    presentation: urgent ? 'summary_first' : 'details_open',
  })
  const before = guideSummary.textContent
  await act(async () => {
    steps.open = !steps.open
    steps.dispatchEvent(new Event('toggle'))
  })
  expect(guideSummary.textContent).toBe(before)
  await act(async () => button('解決した').click())
  const last = container.querySelectorAll('[aria-label="あなたのメッセージ"]')[3]
  expect(last.querySelector('.lab-turn-summary')?.textContent).toContain('通知相談の解決')
  expect(last.textContent).toContain('Jev呼び出しなし')
  expect(last.querySelector('.lab-judgment-details')).toBeNull()
  expect(container.querySelector('[role="status"]')?.textContent).toContain(
    '通知が届くようになりました。相談を終了します。',
  )
  expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'resolved' })
  expect(guideSummary.textContent).toBe(before)
  expect(judge).toHaveBeenCalledTimes(1)
  await act(async () => controller.dispose())
})

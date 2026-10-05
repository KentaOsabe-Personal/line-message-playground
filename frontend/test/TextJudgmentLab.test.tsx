import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import TextJudgmentLab from '../src/TextJudgmentLab'
import type { LabAuthContext } from '../src/TextJudgmentLabAuthGate'
import { LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import type { JudgmentResponse } from '../src/textJudgmentLabTypes'
import fixture from './fixtures/text-judgment-lab-v3.json'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
const parsed = parseJudgmentResponse(fixture)
if (!parsed.ok) throw new Error('invalid fixture')
const result = parsed.value

describe('自由文の文章判定ラボ', () => {
  let container: HTMLDivElement
  let root: Root
  let api: LabHttpClient
  let context: LabAuthContext
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    api = { checkAccess: vi.fn(), judge: vi.fn().mockResolvedValue(result) }
    context = {
      access: { kind: 'authorized', expiresAt: '2099-01-01T00:00:00Z', remainingMs: 100000 },
      getValidIdToken: vi.fn().mockReturnValue('test-token'),
      recheckAccess: vi.fn(),
      reauthenticate: vi.fn(),
      invalidateAccess: vi.fn(),
    }
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })
  const render = async () =>
    act(async () => root.render(<TextJudgmentLab api={api} context={context} />))
  const type = async (text: string) =>
    act(async () => {
      const input = container.querySelector('textarea')!
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
        input,
        text,
      )
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  const send = async () =>
    act(async () => {
      container
        .querySelector('form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

  // テストケース: 自由文を続けて送信し、3種類の判定を受け取る。
  // 期待値: 各入力だけを送信し、未加工の数値と応答時間をチャットへ残す。
  test('shows three readable judgments and sends no conversation history', async () => {
    await render()
    expect(container.querySelectorAll('textarea')).toHaveLength(1)
    expect(container.querySelector('button')?.disabled).toBe(true)
    await type('明日までに送ってほしい')
    await send()
    expect(api.judge).toHaveBeenCalledWith(
      'test-token',
      { contractVersion: 3, text: '明日までに送ってほしい' },
      expect.any(AbortSignal),
    )
    expect(container.textContent).toContain('文章の分類')
    expect(container.textContent).toContain('70.0%')
    expect(container.textContent).toContain('1.10')
    expect(container.textContent).toContain('69.0%')
    expect(container.textContent).toContain('Jev応答 125 ms')
    expect(container.textContent).not.toContain('採用')
    await type('今日は嬉しい')
    await send()
    expect(api.judge).toHaveBeenLastCalledWith(
      'test-token',
      { contractVersion: 3, text: '今日は嬉しい' },
      expect.any(AbortSignal),
    )
    expect(container.querySelectorAll('.judgment-turn')).toHaveLength(2)
  })

  // テストケース: 日本語入力のEnter、空白だけ、上限超過、送信中の連打を試す。
  // 期待値: 意図しない送信や重複呼出しをしない。
  test('blocks empty, oversized, composition enter, and duplicate sends', async () => {
    api.judge = vi.fn().mockReturnValue(new Promise(() => {}))
    await render()
    await type('   ')
    await send()
    await type('あ'.repeat(1001))
    await send()
    expect(api.judge).not.toHaveBeenCalled()
    await type('質問です')
    await act(async () =>
      container
        .querySelector('textarea')!
        .dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }),
        ),
    )
    expect(api.judge).not.toHaveBeenCalled()
    await send()
    await send()
    expect(api.judge).toHaveBeenCalledTimes(1)
    expect(container.querySelector('textarea')?.disabled).toBe(true)
    expect(container.textContent).toContain('判定しています')
  })

  // テストケース: 通信失敗後に本人がもう一度送信する。
  // 期待値: 下書きを復元し、エラーを表示し、自動再送しない。
  test('preserves failed input and retries only on explicit send', async () => {
    api.judge = vi
      .fn()
      .mockRejectedValueOnce(new LabHttpError('judgment_failed'))
      .mockResolvedValueOnce(result)
    await render()
    await type('再度試したい文章')
    await send()
    expect(container.querySelector('textarea')?.value).toBe('再度試したい文章')
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('判定できませんでした')
    expect(api.judge).toHaveBeenCalledTimes(1)
    await send()
    expect(api.judge).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('文章の分類')
  })

  // テストケース: 15秒を超えてから成功応答が到着する。
  // 期待値: タイムアウトとして入力を復元し、後着した結果は表示しない。
  test('discards late completion after timeout', async () => {
    vi.useFakeTimers()
    let resolve!: (value: JudgmentResponse) => void
    api.judge = vi.fn().mockReturnValue(
      new Promise<JudgmentResponse>((r) => {
        resolve = r
      }),
    )
    await render()
    await type('時間がかかる')
    await send()
    await act(async () => vi.advanceTimersByTime(15000))
    expect(container.textContent).toContain('時間内に判定できませんでした')
    await act(async () => resolve(result))
    expect(container.textContent).not.toContain('文章の分類')
    expect(container.querySelector('textarea')?.value).toBe('時間がかかる')
  })

  // テストケース: 判定中に認証が失効してから応答が戻る。
  // 期待値: 応答を破棄し、読取専用にし、認証回復後も自動再送しない。
  test('aborts on auth loss and does not resend when authorization recovers', async () => {
    let resolve!: (value: JudgmentResponse) => void
    api.judge = vi.fn().mockReturnValue(
      new Promise<JudgmentResponse>((r) => {
        resolve = r
      }),
    )
    await render()
    await type('判定中の文章')
    await send()
    context = { ...context, access: { kind: 'reauthentication_required' } }
    await render()
    await act(async () => resolve(result))
    expect(container.querySelector('textarea')?.disabled).toBe(true)
    expect(container.textContent).not.toContain('文章の分類')
    context = {
      ...context,
      access: { kind: 'authorized', expiresAt: '2099-01-01T00:00:00Z', remainingMs: 100000 },
    }
    await render()
    expect(api.judge).toHaveBeenCalledTimes(1)
    expect(container.querySelector('textarea')?.value).toBe('判定中の文章')
  })

  // テストケース: APIが認証失効または回数制限を返す。
  // 期待値: 再認証と待機を区別し、判定数値を捏造しない。
  test('handles expiry and rate limiting distinctly', async () => {
    api.judge = vi
      .fn()
      .mockRejectedValueOnce(new LabHttpError('reauthentication_required'))
      .mockRejectedValueOnce(new LabHttpError('rate_limited'))
    await render()
    await type('試す')
    await send()
    expect(context.invalidateAccess).toHaveBeenCalledWith('auth_expired')
    await send()
    expect(container.textContent).toContain('送信回数が上限に達しました')
    expect(container.textContent).not.toContain('文章の分類')
  })
  const button = (label: string) => {
    const found = [...container.querySelectorAll('button')].find(
      (item) => item.textContent === label,
    )
    if (!found) throw new Error(`missing button: ${label}`)
    return found
  }
  const click = async (label: string) => act(async () => button(label).click())

  // テストケース: 通常成功から入力置換の拒否・承認、中止、比較成功と両側の再選択を行う。
  // 期待値: 入力・比較元・focusを保持または更新し、成功した比較を一組だけ追加する。
  test('selects, confirms, cancels, compares, and reuses either result', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await render()
    await type('元の文章')
    await send()
    await click('書き換えて試す')
    expect(container.querySelector('textarea')?.value).toBe('元の文章')
    expect(document.activeElement).toBe(container.querySelector('textarea'))
    expect(container.querySelector('.judgment-source')?.textContent).toContain('元の文章')
    await type('  \n')
    const origin = button('書き換えて試す')
    await click('書き換えて試す')
    expect(confirm).toHaveBeenCalledWith('入力中の文章を選択した文章に置き換えますか？')
    expect(container.querySelector('textarea')?.value).toBe('  \n')
    expect(document.activeElement).toBe(origin)
    confirm.mockReturnValue(true)
    await click('書き換えて試す')
    expect(container.querySelector('textarea')?.value).toBe('元の文章')
    await type('編集を残す')
    await click('比較をやめる')
    expect(container.querySelector('.judgment-source')).toBeNull()
    expect(container.querySelector('textarea')?.value).toBe('編集を残す')
    expect(document.activeElement).toBe(container.querySelector('textarea'))
    await click('書き換えて試す')
    await type('書き換えた文章')
    await send()
    expect(api.judge).toHaveBeenCalledTimes(2)
    expect(api.judge).toHaveBeenLastCalledWith(
      'test-token',
      { contractVersion: 3, text: '書き換えた文章' },
      expect.any(AbortSignal),
    )
    expect(container.querySelectorAll('.judgment-turn')).toHaveLength(1)
    expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(1)
    expect(container.querySelector('textarea')?.value).toBe('')
    expect(container.querySelector('.judgment-source')).toBeNull()
    expect(document.activeElement).toBe(container.querySelector('textarea'))
    const sides = container.querySelectorAll('.judgment-comparison section')
    expect(sides[0].textContent).toContain('元の文章')
    expect(sides[1].textContent).toContain('書き換えた文章')
    await act(async () => sides[1].querySelector('button')!.click())
    expect(container.querySelector('textarea')?.value).toBe('書き換えた文章')
    await click('比較をやめる')
    await act(async () => sides[0].querySelector('button')!.click())
    expect(container.querySelector('textarea')?.value).toBe('元の文章')
    expect(container.querySelector('.judgment-footnote')?.textContent).toContain(
      '個人情報・秘密情報',
    )
  })

  // テストケース: 比較の判定待ちと利用資格喪失を画面へ反映する。
  // 期待値: 送信時の入力と比較元を表示し、変更操作をすべて止め、失敗を案内する。
  test('keeps pending comparison readable and disables all mutations without access', async () => {
    await render()
    await type('元です')
    await send()
    await click('書き換えて試す')
    await type('  書き換え\n本文  ')
    api.judge = vi.fn().mockReturnValue(new Promise(() => {}))
    await send()
    expect(container.querySelector('textarea')?.value).toBe('  書き換え\n本文  ')
    expect(container.querySelector('textarea')?.readOnly).toBe(true)
    expect([...container.querySelectorAll('button')].every((item) => item.disabled)).toBe(true)
    expect(container.querySelector('[role="status"]')?.textContent).toContain('判定中')
    context = { ...context, access: { kind: 'unavailable' } }
    await render()
    expect(container.querySelector('.judgment-source')?.textContent).toContain('元です')
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('中断')
    expect([...container.querySelectorAll('button')].every((item) => item.disabled)).toBe(true)
    expect(container.querySelector('textarea')?.disabled).toBe(true)
  })
  // テストケース: 比較の各側の見出しと、詳細をキーボードで操作する要素を関連付ける。
  // 期待値: 独立article内で元・書き換え後のsectionを順に並べ、見出しとの関連とJSONへのfocus経路を持つ。
  test('provides full-width comparison sections and keyboard-accessible raw responses', async () => {
    await render()
    await type('元の文章\n二行目')
    await send()
    await click('書き換えて試す')
    await type('新しい文章\n二行目')
    await send()
    const article = container.querySelector('article.judgment-comparison')!
    expect(article.parentElement?.getAttribute('role')).toBe('log')
    expect(article.closest('.judgment-assistant, .judgment-user')).toBeNull()
    const sides = article.querySelectorAll('section')
    expect([...sides].map((side) => side.querySelector('h2')?.textContent)).toEqual([
      '元の文章',
      '書き換え後',
    ])
    for (const side of sides) {
      const heading = side.querySelector('h2')!.id
      expect(side.getAttribute('aria-labelledby')).toBe(heading)
      expect(side.querySelector('button')?.getAttribute('aria-describedby')).toBe(heading)
      expect(side.querySelector('summary')?.getAttribute('aria-describedby')).toBe(heading)
      const raw = side.querySelector('pre')!
      expect(raw.tabIndex).toBe(0)
      expect(raw.getAttribute('aria-describedby')).toBe(heading)
      side.querySelector('details')!.open = true
      raw.focus()
      expect(document.activeElement).toBe(raw)
      expect(JSON.parse(raw.textContent)).toEqual(result)
    }
  })
})

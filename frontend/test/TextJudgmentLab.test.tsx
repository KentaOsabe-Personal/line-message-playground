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
})

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import TextJudgmentLab from '../src/TextJudgmentLab'
import type { LabAuthContext } from '../src/TextJudgmentLabAuthGate'
import { createLabHttpClient, LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'
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

  // テストケース: 比較を実HTTP adapter経由で送り、両側に異なる判定を返す。
  // 期待値: v3本文だけを一回送り、各側の値・順序・未丸めJSONと履歴の対応を保持する。
  test('integrates comparison with the exact HTTP contract and distinct results', async () => {
    const rewritten: JudgmentResponse = {
      ...result,
      model: 'comparison-model',
      elapsedMs: 987.6,
      answers: {
        ...result.answers,
        intent: {
          ...result.answers.intent,
          choice: 'question',
          probabilities: {
            question: 0.71234,
            request: 0.12345,
            report: 0.1,
            other: 0.06421,
          },
        },
        sentiment: { ...result.answers.sentiment, score: 1.23456 },
        urgency: { type: 'noul', noul: 0.12345 },
      },
    }
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(result)))
      .mockResolvedValueOnce(new Response(JSON.stringify(rewritten)))
    api = createLabHttpClient(fetcher)
    await render()
    await type('元の文章')
    await send()
    await click('書き換えて試す')
    await type('  新しい文章\n二行目  ')
    await send()
    expect(fetcher).toHaveBeenCalledTimes(2)
    const signal = fetcher.mock.calls[1]?.[1]?.signal
    expect(signal).toBeInstanceOf(AbortSignal)
    expect(fetcher.mock.calls[1]).toEqual([
      '/api/labs/text-judgment/judgments',
      {
        method: 'POST',
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
        headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' },
        body: JSON.stringify({ contractVersion: 3, text: '新しい文章\n二行目' }),
        signal,
      },
    ])
    const sides = container.querySelectorAll('.judgment-comparison section')
    for (const [index, expected] of [result, rewritten].entries()) {
      const side = sides[index]
      expect(side.querySelector('h2')?.textContent).toBe(index === 0 ? '元の文章' : '書き換え後')
      expect(side.querySelector('.judgment-row .judgment-value')?.textContent).toBe(
        index === 0 ? '依頼' : '質問',
      )
      expect(side.textContent).toContain(expected.model)
      expect(side.textContent).toContain(`Jev応答 ${Math.round(expected.elapsedMs)} ms`)
      const content = side.textContent
      expect(content.indexOf('文章の分類')).toBeLessThan(content.indexOf('感情'))
      expect(content.indexOf('感情')).toBeLessThan(content.indexOf('急ぎ'))
      for (const value of Object.values(expected.answers.intent.probabilities))
        expect(content).toContain(`${(value * 100).toFixed(1)}%`)
      expect(content).toContain(expected.answers.sentiment.score.toFixed(2))
      expect(content).toContain(`${(expected.answers.urgency.noul * 100).toFixed(1)}%`)
      const sentimentMeter = side.querySelector<HTMLMeterElement>(
        'meter[aria-label="感情のスコア"]',
      )!
      expect(sentimentMeter.min).toBe(0)
      expect(sentimentMeter.max).toBe(2)
      expect(sentimentMeter.value).toBe(expected.answers.sentiment.score)
      const intentMeters = side.querySelectorAll<HTMLMeterElement>('.judgment-probability meter')
      expect(intentMeters).toHaveLength(4)
      for (const [candidateIndex, probability] of Object.values(
        expected.answers.intent.probabilities,
      ).entries()) {
        expect(intentMeters[candidateIndex].min).toBe(0)
        expect(intentMeters[candidateIndex].max).toBe(1)
        expect(intentMeters[candidateIndex].value).toBe(probability)
      }
      expect(JSON.parse(side.querySelector('pre')!.textContent)).toEqual(expected)
      expect(content).not.toMatch(/正解率|優劣|判定理由/)
    }
    expect(container.querySelectorAll('.judgment-turn')).toHaveLength(1)
    expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(1)
    expect(container.querySelector('textarea')?.value).toBe('')
    expect(container.querySelector('.judgment-source')).toBeNull()
    expect(document.activeElement).toBe(container.querySelector('textarea'))
    expect(container.querySelector('.judgment-footnote')?.textContent).toContain('外部AIのJev')
  })

  // テストケース: 空・空白・code point境界を通常入力と比較入力から送信する。
  // 期待値: 0・1,001文字は通信せず、1・1,000文字と補助平面文字はtrimして送信する。
  test.each([false, true])(
    'validates all input boundaries from the screen (comparison=%s)',
    async (comparison) => {
      const judge = vi.spyOn(api, 'judge')
      await render()
      if (comparison) {
        await type('元')
        await send()
        await click('書き換えて試す')
      }
      const before = judge.mock.calls.length
      for (const draft of [
        '',
        ' \n ',
        'あ'.repeat(1001),
        '😀'.repeat(1001),
        ` ${'あ'.repeat(1000)}`,
      ]) {
        await type(draft)
        expect(container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(
          true,
        )
        await send()
      }
      expect(api.judge).toHaveBeenCalledTimes(before)
      expect(container.querySelector('#judgment-input-note')?.textContent).toBe(
        '1001文字 / 1,000文字以内で入力してください。',
      )
      for (const draft of ['あ', 'あ'.repeat(1000), '😀'.repeat(1000), ' \n あ\nい \n ']) {
        await type(draft)
        expect(container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(
          false,
        )
        await send()
        expect(api.judge).toHaveBeenLastCalledWith(
          'test-token',
          { contractVersion: 3, text: draft.trim() },
          expect.any(AbortSignal),
        )
        if (comparison) {
          await act(async () =>
            container.querySelector<HTMLButtonElement>('.judgment-comparison button')!.click(),
          )
        }
      }
      expect(api.judge).toHaveBeenCalledTimes(before + 4)
    },
  )

  // テストケース: 通常・比較の送信を同一イベント内で連打し、待機中に編集・選択・中止を試す。
  // 期待値: 要求は一回で、入力・比較元を変更せず全変更操作を無効にする。
  test.each([false, true])(
    'guards same-event sends and pending mutations (comparison=%s)',
    async (comparison) => {
      await render()
      await type('元')
      await send()
      if (comparison) await click('書き換えて試す')
      await type('送信時\n入力')
      const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(new Promise(() => {}))
      api.judge = judge
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
      await act(async () => {
        const form = container.querySelector('form')!
        for (let i = 0; i < 2; i++)
          form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        container.querySelector<HTMLButtonElement>('.judgment-turn button')!.click()
        if (comparison) button('比較をやめる').click()
      })
      await type('変更しようとした入力')
      expect(judge).toHaveBeenCalledExactlyOnceWith(
        'test-token',
        { contractVersion: 3, text: '送信時\n入力' },
        expect.any(AbortSignal),
      )
      expect(container.querySelector('textarea')?.value).toBe('送信時\n入力')
      expect(container.querySelector('textarea')?.readOnly).toBe(true)
      expect([...container.querySelectorAll('button')].every((item) => item.disabled)).toBe(true)
      expect(container.querySelector('.judgment-source') !== null).toBe(comparison)
      expect(confirm).not.toHaveBeenCalled()
      expect(container.querySelector('.judgment-footnote')?.textContent).toContain(
        '個人情報・秘密情報',
      )
    },
  )

  // テストケース: 比較元を選び直す際に置換を拒否・抑止・承認し、中止後に通常送信する。
  // 期待値: 拒否と抑止で入力と旧比較元を保持し、承認だけで置換、中止後は通常履歴へ追加する。
  test('preserves both values on suppressed replacement and sends normally after cancellation', async () => {
    await render()
    for (const text of ['第一の元', '第二の元']) {
      await type(text)
      await send()
    }
    const sources = container.querySelectorAll<HTMLButtonElement>('.judgment-turn button')
    await act(async () => sources[0].click())
    await type(' \n 編集中 \n ')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    for (const suppressed of [false, true]) {
      if (suppressed) confirm.mockImplementation(() => undefined as unknown as boolean)
      await act(async () => sources[1].click())
      expect(container.querySelector('textarea')?.value).toBe(' \n 編集中 \n ')
      expect(
        container.querySelector('.judgment-source .judgment-comparison-text')?.textContent,
      ).toBe('第一の元')
      expect(document.activeElement).toBe(sources[1])
    }
    expect(confirm).toHaveBeenCalledWith('入力中の文章を選択した文章に置き換えますか？')
    confirm.mockReturnValue(true)
    await act(async () => sources[1].click())
    expect(container.querySelector('.judgment-source .judgment-comparison-text')?.textContent).toBe(
      '第二の元',
    )
    expect(container.querySelector('textarea')?.value).toBe('第二の元')
    await click('比較をやめる')
    expect(document.activeElement).toBe(container.querySelector('textarea'))
    await send()
    expect(container.querySelectorAll('.judgment-turn')).toHaveLength(3)
    expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(0)
    await act(async () => sources[0].click())
    await send()
    expect(api.judge).toHaveBeenLastCalledWith(
      'test-token',
      { contractVersion: 3, text: '第一の元' },
      expect.any(AbortSignal),
    )
    expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(1)
  })

  // テストケース: 通常・比較入力でEnterとIME確定を行い、改行を含む文章を明示送信する。
  // 期待値: キー操作だけでは送信せず、改行を保った文章だけを判定する。
  test.each([false, true])(
    'does not submit on Enter or IME (comparison=%s)',
    async (comparison) => {
      const judge = vi.spyOn(api, 'judge')
      await render()
      if (comparison) {
        await type('元')
        await send()
        await click('書き換えて試す')
      }
      const before = judge.mock.calls.length
      await type('一行目')
      await act(async () => {
        const input = container.querySelector('textarea')!
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
        input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
        input.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }),
        )
        input.dispatchEvent(
          new CompositionEvent('compositionend', { bubbles: true, data: '二行目' }),
        )
      })
      expect(api.judge).toHaveBeenCalledTimes(before)
      // jsdomはtextareaの既定改行動作を実行しないため、ブラウザ入力後の値を通知する。
      await type('一行目\n二行目')
      await send()
      expect(api.judge).toHaveBeenLastCalledWith(
        'test-token',
        { contractVersion: 3, text: '一行目\n二行目' },
        expect.any(AbortSignal),
      )
    },
  )

  const deferred = () => {
    let resolve!: (value: JudgmentResponse) => void
    let reject!: (reason: unknown) => void
    const promise = new Promise<JudgmentResponse>((yes, no) => {
      resolve = yes
      reject = no
    })
    return { promise, resolve, reject }
  }
  const trackJobTimer = () => {
    const setTimeout = vi.spyOn(globalThis, 'setTimeout')
    const clearTimeout = vi.spyOn(globalThis, 'clearTimeout')
    return {
      clearTimeout,
      latestHandle: () => {
        const index = setTimeout.mock.calls.map((call) => call[1]).lastIndexOf(15000)
        expect(index).toBeGreaterThanOrEqual(0)
        return setTimeout.mock.results[index].value as ReturnType<typeof globalThis.setTimeout>
      },
    }
  }
  const prepareComparison = async () => {
    await render()
    await type('比較の元')
    await send()
    await click('書き換えて試す')
    await type(' \n 書き換え後\n本文 \n ')
  }
  const expectPreservedComparison = () => {
    expect(container.querySelector('textarea')?.value).toBe(' \n 書き換え後\n本文 \n ')
    expect(container.querySelector('.judgment-source .judgment-comparison-text')?.textContent).toBe(
      '比較の元',
    )
    expect(container.querySelectorAll('.judgment-turn')).toHaveLength(1)
    expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(0)
  }

  // テストケース: 実HTTP adapterでnetwork・429・不正応答・5xx・401・403・503を再現する。
  // 期待値: 安全な案内と元・空白改行入力を保持し、失敗や再許可だけでは再送しない。
  test.each([
    ['network', null, null, '判定できませんでした'],
    ['429', 429, null, '送信回数が上限'],
    ['invalid response', 200, null, '判定できませんでした'],
    ['500', 500, null, '判定できませんでした'],
    ['502', 502, null, '判定できませんでした'],
    ['504', 504, null, '判定できませんでした'],
    ['401', 401, 'auth_expired', '利用資格'],
    ['403', 403, 'access_unavailable', '利用資格'],
    ['503', 503, 'access_unavailable', '利用資格'],
  ] as const)('integrates safe recovery for %s', async (_label, status, reason, message) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(result)))
    if (status === null) fetcher.mockRejectedValueOnce(new Error('private upstream text'))
    else
      fetcher.mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: { code: 'not_allowed', detail: 'private upstream text' } }),
          { status },
        ),
      )
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(result)))
    api = createLabHttpClient(fetcher)
    context = {
      ...context,
      invalidateAccess: vi.fn((failure) => {
        context = {
          ...context,
          access:
            failure === 'auth_expired'
              ? { kind: 'reauthentication_required' }
              : { kind: 'unavailable' },
        }
      }),
    }
    await prepareComparison()
    await send()
    await render()
    expectPreservedComparison()
    expect(container.querySelector('form [role="alert"]')?.textContent).toContain(message)
    expect(container.textContent).not.toContain('private upstream text')
    expect(fetcher).toHaveBeenCalledTimes(2)
    if (reason) {
      expect(context.invalidateAccess).toHaveBeenCalledWith(reason)
      expect(container.querySelector('textarea')?.disabled).toBe(true)
      expect([...container.querySelectorAll('button')].every((item) => item.disabled)).toBe(true)
      await send()
      context = {
        ...context,
        access: { kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 100000 },
      }
      await render()
    } else expect(context.invalidateAccess).not.toHaveBeenCalled()
    expect(fetcher).toHaveBeenCalledTimes(2)
    await type(' \n 手動で修正 \n ')
    await send()
    expect(fetcher).toHaveBeenCalledTimes(3)
    expect(fetcher.mock.calls[2][1]?.body).toBe(
      JSON.stringify({ contractVersion: 3, text: '手動で修正' }),
    )
    const sides = container.querySelectorAll(
      '.judgment-comparison section .judgment-comparison-text',
    )
    expect([...sides].map((side) => side.textContent)).toEqual(['比較の元', '手動で修正'])
    expect(container.querySelector('textarea')?.value).toBe('')
  })

  // テストケース: 比較を15秒で中断し、別の元で新しい比較を開始してから旧成功・失敗が届く。
  // 期待値: timer解除・abort後に旧応答は入力・元・履歴・通信・認証を変えず、新要求だけを完了する。
  test.each(['resolve', 'reject'] as const)(
    'ignores stale comparison %s after a new comparison starts',
    async (completion) => {
      vi.useFakeTimers()
      const timer = trackJobTimer()
      await render()
      for (const text of ['比較の元', '別の元']) {
        await type(text)
        await send()
      }
      await act(async () =>
        container.querySelector<HTMLButtonElement>('.judgment-turn button')!.click(),
      )
      await type(' \n 書き換え後\n本文 \n ')
      const old = deferred()
      const next = deferred()
      const judge = vi
        .fn<LabHttpClient['judge']>()
        .mockReturnValueOnce(old.promise)
        .mockReturnValueOnce(next.promise)
      api.judge = judge
      await send()
      const oldHandle = timer.latestHandle()
      expect(timer.clearTimeout).not.toHaveBeenCalledWith(oldHandle)
      await act(async () => vi.advanceTimersByTime(15000))
      expect(judge.mock.calls[0][2]?.aborted).toBe(true)
      expect(timer.clearTimeout).toHaveBeenCalledWith(oldHandle)
      expect(container.querySelector('form [role="alert"]')?.textContent).toContain('時間内')
      expect(container.querySelector('textarea')?.value).toBe(' \n 書き換え後\n本文 \n ')
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      await act(async () =>
        container.querySelectorAll<HTMLButtonElement>('.judgment-turn button')[1].click(),
      )
      expect(container.querySelector('form [role="alert"]')).toBeNull()
      await type('新しい比較の入力')
      await send()
      const nextHandle = timer.latestHandle()
      expect(nextHandle).not.toBe(oldHandle)
      const saved = container.innerHTML
      await act(async () => {
        if (completion === 'resolve') old.resolve(result)
        else old.reject(new LabHttpError('reauthentication_required'))
      })
      expect(container.innerHTML).toBe(saved)
      expect(context.invalidateAccess).not.toHaveBeenCalled()
      expect(judge).toHaveBeenCalledTimes(2)
      expect(judge.mock.calls[1][2]?.aborted).toBe(false)
      expect(timer.clearTimeout).not.toHaveBeenCalledWith(nextHandle)
      await act(async () => next.resolve(result))
      expect(timer.clearTimeout).toHaveBeenCalledWith(nextHandle)
      expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(1)
      expect(
        [...container.querySelectorAll('.judgment-comparison .judgment-comparison-text')].map(
          (p) => p.textContent,
        ),
      ).toEqual(['別の元', '新しい比較の入力'])
      expect(container.querySelectorAll('.judgment-turn')).toHaveLength(2)
    },
  )

  // テストケース: timer通知前に15秒経過した比較の成功・失敗が届く。
  // 期待値: timeout扱いで比較元と送信入力を復元し、timer解除・abort後に成功履歴を作らない。
  test.each(['resolve', 'reject'] as const)(
    'checks the elapsed deadline before timer notification (%s)',
    async (completion) => {
      vi.useFakeTimers()
      const timer = trackJobTimer()
      await prepareComparison()
      const delayed = deferred()
      const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(delayed.promise)
      api.judge = judge
      await send()
      const handle = timer.latestHandle()
      expect(timer.clearTimeout).not.toHaveBeenCalledWith(handle)
      vi.setSystemTime(Date.now() + 15000)
      await act(async () => {
        if (completion === 'resolve') delayed.resolve(result)
        else delayed.reject(new Error('private late failure'))
      })
      expectPreservedComparison()
      expect(container.querySelector('form [role="alert"]')?.textContent).toContain('時間内')
      expect(judge.mock.calls[0][2]?.aborted).toBe(true)
      expect(timer.clearTimeout).toHaveBeenCalledWith(handle)
    },
  )

  // テストケース: 比較完了直前のtoken失効または資格喪失後に成功が届く。
  // 期待値: abortとtimer解除を確定し、元と入力を読取専用で保持し、再許可でも自動送信しない。
  test.each(['token', 'access'] as const)(
    'rejects completion after current %s loss',
    async (loss) => {
      vi.useFakeTimers()
      const timer = trackJobTimer()
      await prepareComparison()
      const delayed = deferred()
      const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(delayed.promise)
      api.judge = judge
      await send()
      const handle = timer.latestHandle()
      expect(timer.clearTimeout).not.toHaveBeenCalledWith(handle)
      if (loss === 'token')
        context = {
          ...context,
          getValidIdToken: vi.fn().mockReturnValue(null),
          invalidateAccess: vi.fn(() => {
            expect(judge.mock.calls[0][2]?.aborted).toBe(true)
            expect(timer.clearTimeout).toHaveBeenCalledWith(handle)
            context = { ...context, access: { kind: 'reauthentication_required' } }
          }),
        }
      else context = { ...context, access: { kind: 'unavailable' } }
      await render()
      await act(async () => delayed.resolve(result))
      await render()
      expectPreservedComparison()
      expect(container.querySelector('textarea')?.disabled).toBe(true)
      expect([...container.querySelectorAll('button')].every((item) => item.disabled)).toBe(true)
      expect(judge.mock.calls[0][2]?.aborted).toBe(true)
      expect(timer.clearTimeout).toHaveBeenCalledWith(handle)
      context = {
        ...context,
        access: { kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 100000 },
        getValidIdToken: vi.fn().mockReturnValue('test-token'),
      }
      await render()
      expect(judge).toHaveBeenCalledTimes(1)
      expectPreservedComparison()
    },
  )
})

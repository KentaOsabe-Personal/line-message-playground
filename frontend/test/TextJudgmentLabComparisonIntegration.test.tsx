import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import TextJudgmentLabPage from '../src/TextJudgmentLabPage'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import { LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import type { JudgmentResponse, LabAccessResponse } from '../src/textJudgmentLabTypes'
import fixture from './fixtures/text-judgment-lab-v3.json'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
const parsed = parseJudgmentResponse(fixture)
if (!parsed.ok) throw new Error('invalid fixture')
const result = parsed.value
const config = {
  liffId: '123-comparison',
  liffUrl: 'https://liff.line.me/123-comparison/labs/text-judgment' as const,
  entryUrl: 'https://lab.example.test/liff/labs/text-judgment' as const,
}
const authorized: LabAccessResponse = {
  status: 'authorized',
  serverTime: '2026-10-05T00:00:00Z',
  expiresAt: '2026-10-05T00:01:00Z',
}
const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

describe('比較ラボのPage・実認証ゲート・通信寿命', () => {
  let root: Root
  let container: HTMLDivElement
  let liff: LinePlatformLiffAdapter
  let api: LabHttpClient
  let judge: ReturnType<typeof vi.fn<LabHttpClient['judge']>>
  let checkAccess: ReturnType<typeof vi.fn<LabHttpClient['checkAccess']>>
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(authorized.serverTime))
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    judge = vi.fn<LabHttpClient['judge']>().mockResolvedValue(result)
    checkAccess = vi.fn<LabHttpClient['checkAccess']>().mockResolvedValue(authorized)
    api = { checkAccess, judge }
    liff = {
      initialize: vi.fn().mockResolvedValue('liff_browser'),
      ensureProfilePermission: vi.fn(),
      isLoggedIn: vi.fn().mockReturnValue(true),
      login: vi.fn(),
      logout: vi.fn(),
      reauthenticate: vi.fn(),
      getIdToken: vi.fn().mockReturnValue('fixture-id-token'),
      getAccessToken: vi.fn().mockReturnValue(null),
    }
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })
  const render = () =>
    act(async () =>
      root.render(<TextJudgmentLabPage api={api} authGateProps={{ config, liffAdapter: liff }} />),
    )
  const input = () => container.querySelector('textarea')!
  const type = (draft: string) =>
    act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
        input(),
        draft,
      )
      input().dispatchEvent(new Event('input', { bubbles: true }))
    })
  const send = () =>
    act(async () => {
      container
        .querySelector('form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
  const click = (label: string) =>
    act(async () => {
      const button = [...container.querySelectorAll('button')].find(
        (item) => item.textContent === label,
      )
      if (!button) throw new Error(`missing button: ${label}`)
      button.click()
    })
  const prepare = async () => {
    await render()
    await type('元の文章')
    await send()
    await click('書き換えて試す')
    await type('比較済みの文章')
    await send()
    await act(async () =>
      container.querySelectorAll<HTMLButtonElement>('.judgment-comparison button')[1].click(),
    )
    await type(' \n 次の編集\n本文 \n ')
  }
  const preserved = () => {
    const textarea = input()
    const source = container.querySelector('.judgment-source')!
    const comparison = container.querySelector('.judgment-comparison')!
    const log = container.querySelector('[role="log"]')!
    const raw = comparison.querySelector('pre')!.textContent
    return (readonly: boolean) => {
      expect(input()).toBe(textarea)
      expect(input().value).toBe(' \n 次の編集\n本文 \n ')
      expect(input().disabled).toBe(readonly)
      expect(input().readOnly).toBe(readonly)
      expect(container.querySelector('.judgment-source')).toBe(source)
      expect(source.querySelector('.judgment-comparison-text')?.textContent).toBe('比較済みの文章')
      expect(container.querySelector('.judgment-comparison')).toBe(comparison)
      expect(comparison.querySelector('pre')?.textContent).toBe(raw)
      expect(container.querySelector('[role="log"]')).toBe(log)
      if (readonly) {
        expect(
          [...container.querySelectorAll<HTMLButtonElement>('.simple-judgment-chat button')].every(
            (button) => button.disabled,
          ),
        ).toBe(true)
        expect(container.textContent).toContain('読取専用')
      }
    }
  }
  const visible = () =>
    act(async () => {
      vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
      document.dispatchEvent(new Event('visibilitychange'))
    })
  const timers = () => {
    const schedule = vi.spyOn(globalThis, 'setTimeout')
    const clear = vi.spyOn(globalThis, 'clearTimeout')
    return {
      clear,
      handle: () => {
        const index = schedule.mock.calls.map((call) => call[1]).lastIndexOf(15000)
        expect(index).toBeGreaterThanOrEqual(0)
        return schedule.mock.results[index].value as ReturnType<typeof globalThis.setTimeout>
      },
    }
  }

  // テストケース: 実Pageでvisible復帰の再確認中に比較を中断し、確認不可・再許可を経る。
  // 期待値: 同じ入力・比較元・表示済み結果を保持し、非許可中の変更と通信を止め、手動送信を待つ。
  test('keeps page data through visible rechecking, outage, and reauthorization', async () => {
    await prepare()
    const assertPreserved = preserved()
    const pending = deferred<JudgmentResponse>()
    judge.mockReturnValueOnce(pending.promise)
    const timer = timers()
    await send()
    const handle = timer.handle()
    const recheck = deferred<LabAccessResponse>()
    checkAccess.mockReturnValueOnce(recheck.promise)
    await visible()
    assertPreserved(true)
    expect(container.textContent).toContain('利用資格を確認しています')
    expect(judge.mock.calls[2][2]?.aborted).toBe(true)
    expect(timer.clear).toHaveBeenCalledWith(handle)
    await type('禁止した変更')
    await send()
    await click('比較をやめる')
    assertPreserved(true)
    expect(judge).toHaveBeenCalledTimes(3)
    await act(async () => recheck.reject(new LabHttpError('access_unavailable')))
    assertPreserved(true)
    expect(container.textContent).toContain('利用資格を確認できませんでした')
    await click('利用確認を再試行')
    assertPreserved(false)
    expect(checkAccess).toHaveBeenCalledTimes(3)
    expect(checkAccess).toHaveBeenLastCalledWith('fixture-id-token')
    expect(judge).toHaveBeenCalledTimes(3)
    const saved = container.innerHTML
    await act(async () => pending.resolve(result))
    expect(container.innerHTML).toBe(saved)
    await send()
    expect(judge).toHaveBeenCalledTimes(4)
    expect(judge).toHaveBeenLastCalledWith(
      'fixture-id-token',
      { contractVersion: 3, text: '次の編集\n本文' },
      expect.any(AbortSignal),
    )
    expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(2)
    expect(input().value).toBe('')
  })

  // テストケース: 許可後のvisible再確認で本人またはチャネルを拒否する。
  // 期待値: Pageの同じ子を読取専用で保持し、編集・選択・中止・送信と自動判定を止める。
  test.each(['not_allowed', 'wrong_channel'] as const)(
    'retains comparison data after authorized access becomes %s',
    async (reason) => {
      await prepare()
      const assertPreserved = preserved()
      checkAccess.mockRejectedValueOnce(new LabHttpError(reason))
      await visible()
      assertPreserved(true)
      await type('変更不可')
      await send()
      await click('比較をやめる')
      await click('書き換えて試す')
      assertPreserved(true)
      expect(judge).toHaveBeenCalledTimes(2)
      expect(container.textContent).toContain(
        reason === 'wrong_channel' ? '対応するLINEミニアプリ' : 'このラボは利用できません',
      )
      expect(liff.reauthenticate).not.toHaveBeenCalled()
    },
  )

  // テストケース: 初回の利用確認で本人またはチャネルを拒否する。
  // 期待値: 認証案内のみでラボ内容をmountせず、判定を一度も送らない。
  test.each(['not_allowed', 'wrong_channel'] as const)(
    'does not mount lab content on initial %s',
    async (reason) => {
      checkAccess.mockRejectedValueOnce(new LabHttpError(reason))
      await render()
      expect(container.querySelector('textarea')).toBeNull()
      expect(container.querySelector('[role="log"]')).toBeNull()
      expect(container.querySelector('.simple-judgment-chat')).toBeNull()
      expect(judge).not.toHaveBeenCalled()
      expect(container.querySelector('[role="alert"]')).not.toBeNull()
    },
  )

  // テストケース: 実ゲートの期限が判定待ち中に到達するか、timer通知前の完了時に失効が判明する。
  // 期待値: 成功を採用せず、判定timer解除・abort後に同じデータを読取専用で残す。
  test.each(['expiry notification', 'completion check'] as const)(
    'rejects success on actual token expiry: %s',
    async (mode) => {
      await prepare()
      const assertPreserved = preserved()
      await act(async () => vi.advanceTimersByTime(55000))
      const pending = deferred<JudgmentResponse>()
      judge.mockReturnValueOnce(pending.promise)
      const timer = timers()
      await send()
      const handle = timer.handle()
      if (mode === 'expiry notification') await act(async () => vi.advanceTimersByTime(5000))
      else vi.setSystemTime(new Date(authorized.expiresAt))
      await act(async () => pending.resolve(result))
      assertPreserved(true)
      expect(container.textContent).toContain('再認証が必要')
      expect(judge.mock.calls[2][2]?.aborted).toBe(true)
      expect(timer.clear).toHaveBeenCalledWith(handle)
      expect(judge).toHaveBeenCalledTimes(3)
      expect(container.querySelectorAll('.judgment-comparison')).toHaveLength(1)
      await send()
      expect(judge).toHaveBeenCalledTimes(3)
    },
  )

  // テストケース: 比較判定待ちでPageを離脱または文書遷移による再認証を行い、旧応答を受けて再mountする。
  // 期待値: timer解除・abort・旧応答破棄を行い、URL・storageを読まず空の通常画面から開始する。
  test.each(['page departure', 'reauthentication navigation'] as const)(
    'discards all local data on %s',
    async (mode) => {
      await prepare()
      const pending = deferred<JudgmentResponse>()
      judge.mockReturnValueOnce(pending.promise)
      const timer = timers()
      await send()
      const handle = timer.handle()
      if (mode === 'reauthentication navigation') {
        await act(async () => vi.advanceTimersByTime(60000))
        liff.reauthenticate = vi.fn(() => {
          root.render(null)
        })
        await click('再認証')
        expect(liff.reauthenticate).toHaveBeenCalledWith(config.entryUrl)
      }
      await act(async () => root.unmount())
      expect(judge.mock.calls[2][2]?.aborted).toBe(true)
      expect(timer.clear).toHaveBeenCalledWith(handle)
      expect(container.childElementCount).toBe(0)
      await act(async () => pending.resolve(result))
      expect(container.childElementCount).toBe(0)
      const getItem = vi.spyOn(Storage.prototype, 'getItem')
      const setItem = vi.spyOn(Storage.prototype, 'setItem')
      const previousUrl = window.location.href
      window.history.replaceState(null, '', '/liff/labs/text-judgment?draft=stale#comparison')
      try {
        root = createRoot(container)
        await render()
        expect(input().value).toBe('')
        expect(container.querySelector('.judgment-source')).toBeNull()
        expect(container.querySelectorAll('.judgment-turn, .judgment-comparison')).toHaveLength(0)
        expect(container.querySelector('label')?.textContent).toBe('試したい文章')
        expect(judge).toHaveBeenCalledTimes(3)
        expect(getItem).not.toHaveBeenCalled()
        expect(setItem).not.toHaveBeenCalled()
      } finally {
        window.history.replaceState(null, '', previousUrl)
      }
    },
  )
})

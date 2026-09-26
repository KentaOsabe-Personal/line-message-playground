import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import TextJudgmentLabAuthGate, { conservativeRemainingMs } from '../src/TextJudgmentLabAuthGate'
import { LabHttpError } from '../src/textJudgmentLabApi'
import type { LabHttpClient } from '../src/textJudgmentLabApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'


(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const config = {
  liffId: '123-lab',
  liffUrl: 'https://liff.line.me/123-lab/labs/text-judgment' as const,
  entryUrl: 'https://lab.example.test/liff/labs/text-judgment' as const,
}

const adapter = (overrides: Partial<LinePlatformLiffAdapter> = {}): LinePlatformLiffAdapter => ({
  initialize: vi.fn().mockResolvedValue('liff_browser'),
  ensureProfilePermission: vi.fn().mockRejectedValue(new Error('must not call profile')),
  isLoggedIn: vi.fn().mockReturnValue(true),
  login: vi.fn(), reauthenticate: vi.fn(), logout: vi.fn(),
  getIdToken: vi.fn().mockReturnValue('raw-id-token'),
  getAccessToken: vi.fn().mockReturnValue(null),
  ...overrides,
})

const api = (overrides: Partial<LabHttpClient> = {}): LabHttpClient => ({
  checkAccess: vi.fn().mockResolvedValue({
    status: 'authorized', expiresAt: '2026-09-21T00:01:00Z', serverTime: '2026-09-21T00:00:00Z',
  }),
  judge: vi.fn(),
  ...overrides,
})

describe('TextJudgmentLabAuthGate', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-21T00:00:00Z'))
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  // テストケース: 開発用LIFFを初期化し、profile取得なしでID tokenを利用確認する
  // 期待値: serverTimeと往復時間から期限を保守的に保持し、許可後だけ操作可能にする
  test('authorizes with the dedicated LIFF ID token without profile fallback', async () => {
    const liff = adapter()
    const client = api()
    await act(async () => root.render(
      <TextJudgmentLabAuthGate config={config} liffAdapter={liff} api={client}>
        {({ access }) => <p>相談:{access.kind}</p>}
      </TextJudgmentLabAuthGate>,
    ))

    expect(liff.initialize).toHaveBeenCalledWith('123-lab')
    expect(liff.ensureProfilePermission).not.toHaveBeenCalled()
    expect(client.checkAccess).toHaveBeenCalledWith('raw-id-token')
    expect(container.textContent).toContain('相談:authorized')
  })

  // テストケース: 許可後に利用確認が一時失敗し、本人操作で再試行する
  // 期待値: 会話を読取専用で保持し、空のaccess確認だけを再実行する
  test('preserves mounted conversation read-only during access outage and retries access only', async () => {
    const checkAccess = vi.fn()
      .mockResolvedValueOnce({ status: 'authorized', expiresAt: '2026-09-21T00:01:00Z', serverTime: '2026-09-21T00:00:00Z' })
      .mockRejectedValueOnce(new LabHttpError('access_unavailable'))
      .mockResolvedValueOnce({ status: 'authorized', expiresAt: '2026-09-21T00:02:00Z', serverTime: '2026-09-21T00:00:10Z' })
    let mounts = 0
    const Conversation = ({ kind }: { kind: string }) => { mounts += 1; return <p>会話:{kind}</p> }
    await act(async () => root.render(
      <TextJudgmentLabAuthGate config={config} liffAdapter={adapter()} api={api({ checkAccess })}>
        {({ access }) => <Conversation kind={access.kind} />}
      </TextJudgmentLabAuthGate>,
    ))

    await act(async () => window.dispatchEvent(new PageTransitionEvent('pageshow')))
    expect(container.textContent).toContain('会話:unavailable')
    expect(container.textContent).toContain('利用確認を再試行')
    expect(mounts).toBeGreaterThan(0)
    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === '利用確認を再試行')!
    await act(async () => retry.click())
    expect(checkAccess).toHaveBeenCalledTimes(3)
    expect(container.textContent).toContain('会話:authorized')
  })

  // テストケース: token期限到達と本人拒否を処理する
  // 期待値: 期限後は再認証まで操作を止め、初回拒否では相談をmountしない
  test('expires conservatively and hides conversation on initial denial', async () => {
    const liff = adapter()
    await act(async () => root.render(
      <TextJudgmentLabAuthGate config={config} liffAdapter={liff} api={api()}>
        {({ access }) => <p>会話:{access.kind}</p>}
      </TextJudgmentLabAuthGate>,
    ))
    await act(async () => vi.advanceTimersByTimeAsync(60_000))
    expect(container.textContent).toContain('会話:reauthentication_required')
    const button = [...container.querySelectorAll('button')].find((candidate) => candidate.textContent === '再認証')!
    await act(async () => button.click())
    expect(liff.reauthenticate).toHaveBeenCalledWith(config.entryUrl)

    await act(async () => root.unmount())
    root = createRoot(container)
    await act(async () => root.render(
      <TextJudgmentLabAuthGate config={config} liffAdapter={adapter()} api={api({
        checkAccess: vi.fn().mockRejectedValue(new LabHttpError('not_allowed')),
      })}>
        <p>秘密の相談</p>
      </TextJudgmentLabAuthGate>,
    ))
    expect(container.textContent).toContain('利用できません')
    expect(container.textContent).not.toContain('秘密の相談')
  })

  // テストケース: wall-clockが後退する場合とmonotonicより大きく進む場合を計算する
  // 期待値: 時計後退で期限を延長せず、二つの経過時間の大きい方を控除する
  test('uses the greater monotonic or wall-clock elapsed time without extending on rollback', () => {
    expect(conservativeRemainingMs(1_000, 100, 10_000, 400, 9_000)).toBe(700)
    expect(conservativeRemainingMs(1_000, 100, 10_000, 200, 10_800)).toBe(200)
  })

  // テストケース: access確認に10秒かかり、その後visible復帰する
  // 期待値: 往復時間を期限から控除し、visibilitychangeで空のaccess確認を再実行する
  test('subtracts round-trip time and rechecks access on visibility return', async () => {
    let resolveAccess: ((value: { status: 'authorized'; expiresAt: string; serverTime: string }) => void) | undefined
    const checkAccess = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { resolveAccess = resolve }))
      .mockResolvedValue({
        status: 'authorized', expiresAt: '2026-09-21T00:02:00Z', serverTime: '2026-09-21T00:01:00Z',
      })
    await act(async () => root.render(
      <TextJudgmentLabAuthGate config={config} liffAdapter={adapter()} api={api({ checkAccess })}>
        {({ access }) => <p>残り:{access.kind === 'authorized' ? access.remainingMs : access.kind}</p>}
      </TextJudgmentLabAuthGate>,
    ))
    await act(async () => vi.advanceTimersByTimeAsync(10_000))
    await act(async () => resolveAccess?.({
      status: 'authorized', expiresAt: '2026-09-21T00:01:00Z', serverTime: '2026-09-21T00:00:00Z',
    }))
    expect(container.textContent).toContain('残り:50000')

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(checkAccess).toHaveBeenCalledTimes(2)
  })
})

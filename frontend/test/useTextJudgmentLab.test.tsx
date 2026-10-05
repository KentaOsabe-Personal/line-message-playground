import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import type { LabAuthContext } from '../src/TextJudgmentLabAuthGate'
import { LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import { useTextJudgmentLab } from '../src/useTextJudgmentLab'
import fixture from './fixtures/text-judgment-lab-v3.json'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
const parsed = parseJudgmentResponse(fixture)
if (!parsed.ok) throw new Error('invalid fixture')
const result = parsed.value

describe('文章判定hookの状態と送信制御', () => {
  let root: Root
  let container: HTMLDivElement
  let api: LabHttpClient
  let context: LabAuthContext
  let controller: ReturnType<typeof useTextJudgmentLab>
  function Harness() {
    controller = useTextJudgmentLab(api, context)
    return null
  }
  const render = () => act(async () => root.render(<Harness />))
  const edit = (draft: string) => act(async () => controller.setDraft(draft))
  const send = () =>
    act(async () => {
      void controller.submit()
    })
  beforeEach(async () => {
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
    await render()
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })
  const prepareComparison = async () => {
    await edit('元の文章')
    await send()
    await act(async () => {
      expect(controller.selectSource({ entryId: 1, side: 'single' })).toBe('selected')
    })
    await edit(' \n 書き換え後 \n ')
  }

  // テストケース: 通常成功を比較元に選び、同じイベント内で二回送信する。
  // 期待値: 元を固定し、書き換え後のv3要求だけを一回送る。
  test('sends only the frozen rewritten text once before rerender', async () => {
    await prepareComparison()
    api.judge = vi.fn().mockReturnValue(new Promise(() => {}))
    await act(async () => {
      void controller.submit()
      void controller.submit()
      controller.setDraft('改変')
      controller.cancelComparison()
      expect(controller.selectSource({ entryId: 1, side: 'single' }, true)).toBe('ignored')
    })
    expect(api.judge).toHaveBeenCalledExactlyOnceWith(
      'test-token',
      { contractVersion: 3, text: '書き換え後' },
      expect.any(AbortSignal),
    )
    expect(controller.state.composer).toMatchObject({
      kind: 'pending',
      submission: {
        id: 2,
        draft: ' \n 書き換え後 \n ',
        text: '書き換え後',
        original: { text: '元の文章', result },
      },
    })
    expect(controller.state.entries).toHaveLength(1)
  })

  // テストケース: 無効入力、許可なし、tokenなしで送信する。
  // 期待値: 通信せず、非許可中の編集・選択・中止も状態を変えない。
  test('rejects invalid drafts and unauthorized operations', async () => {
    for (const draft of ['', ' \n ', 'あ'.repeat(1001), ` ${'あ'.repeat(1000)}`]) {
      await edit(draft)
      await send()
    }
    expect(api.judge).not.toHaveBeenCalled()
    await prepareComparison()
    const saved = controller.state
    context = { ...context, access: { kind: 'denied', reason: 'not_allowed' } }
    await render()
    await act(async () => {
      controller.setDraft('変更')
      controller.cancelComparison()
      expect(controller.selectSource({ entryId: 1, side: 'single' }, true)).toBe('ignored')
      await controller.submit()
    })
    expect(controller.state).toBe(saved)
    expect(api.judge).toHaveBeenCalledTimes(1)
    context = {
      ...context,
      access: { kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 },
      getValidIdToken: vi.fn().mockReturnValue(null),
    }
    await render()
    await send()
    expect(api.judge).toHaveBeenCalledTimes(1)
  })

  // テストケース: 入力置換を拒否してから承認し、比較を中止する。
  // 期待値: 拒否中は入力と比較元を保持し、承認で置換、中止で入力だけを残す。
  test('requires confirmation and cancels without losing edits', async () => {
    await edit('元の文章')
    await send()
    await edit(' ')
    const saved = controller.state
    await act(async () => {
      expect(controller.selectSource({ entryId: 1, side: 'single' })).toBe('confirmation_required')
      expect(controller.selectSource({ entryId: 999, side: 'single' }, true)).toBe('ignored')
    })
    expect(controller.state).toBe(saved)
    await act(async () => {
      expect(controller.selectSource({ entryId: 1, side: 'single' }, true)).toBe('selected')
    })
    await edit('編集済み')
    await act(async () => controller.cancelComparison())
    expect(controller.state.composer).toEqual({
      kind: 'editing',
      draft: '編集済み',
      original: null,
      error: null,
    })
  })

  // テストケース: 補助平面文字1,000個を同じイベント内で編集して送信する。
  // 期待値: 古いrenderの下書きを使わず、Unicode code point上限内の文章を送る。
  test('uses synchronous state for same-event edits and the code point limit', async () => {
    await act(async () => {
      controller.setDraft('😀'.repeat(1000))
      await controller.submit()
    })
    expect(api.judge).toHaveBeenCalledExactlyOnceWith(
      'test-token',
      { contractVersion: 3, text: '😀'.repeat(1000) },
      expect.any(AbortSignal),
    )
    expect(controller.state.entries).toHaveLength(1)
    expect(controller.state.composer).toMatchObject({ kind: 'editing', draft: '', original: null })
  })
  const defer = () => {
    let resolve!: (value: typeof result) => void
    let reject!: (reason: unknown) => void
    const promise = new Promise<typeof result>((yes, no) => {
      resolve = yes
      reject = no
    })
    return { promise, resolve, reject }
  }

  // テストケース: 比較要求が安全なエラー分類で失敗する。
  // 期待値: 比較元と空白・改行を復元し、認証案内と回数制限を分け、自動再送しない。
  test.each([
    ['network', new Error('private error'), null, '判定できませんでした'],
    ['protocol_error', new LabHttpError('protocol_error'), null, '判定できませんでした'],
    ['5xx', new LabHttpError('judgment_failed'), null, '判定できませんでした'],
    ['429', new LabHttpError('rate_limited'), null, '送信回数が上限'],
    ['401', new LabHttpError('reauthentication_required'), 'auth_expired', '利用資格'],
    ['403 not_allowed', new LabHttpError('not_allowed'), 'access_unavailable', '利用資格'],
    ['403 wrong_channel', new LabHttpError('wrong_channel'), 'access_unavailable', '利用資格'],
    ['503', new LabHttpError('access_unavailable'), 'access_unavailable', '利用資格'],
  ])('restores comparison after %s', async (_label, failure, reason, message) => {
    await prepareComparison()
    const saved = controller.state.composer
    api.judge = vi.fn().mockRejectedValue(failure)
    await send()
    const composer = controller.state.composer
    expect(composer.kind).toBe('editing')
    if (composer.kind !== 'editing') throw new Error('expected editing composer')
    expect(composer).toMatchObject({ ...saved, error: composer.error })
    expect(composer.error).toContain(message)
    expect(controller.state.entries).toHaveLength(1)
    expect(api.judge).toHaveBeenCalledTimes(1)
    if (reason) expect(context.invalidateAccess).toHaveBeenCalledWith(reason)
    else expect(context.invalidateAccess).not.toHaveBeenCalled()
    expect(JSON.stringify(controller.state)).not.toContain('private error')
  })

  // テストケース: timeout後に新しい比較を送信し、abortを無視する旧成功・失敗が届く。
  // 期待値: timer解除とabortを済ませ、旧完了は新jobと履歴を変更しない。
  test.each(['resolve', 'reject'] as const)(
    'discards old %s after timeout and manual retry',
    async (completion) => {
      vi.useFakeTimers()
      await prepareComparison()
      const old = defer()
      const judge = vi.fn<LabHttpClient['judge']>().mockReturnValueOnce(old.promise)
      api.judge = judge
      await send()
      const signal = judge.mock.calls[0][2]!
      await act(async () => vi.advanceTimersByTime(15000))
      expect(signal.aborted).toBe(true)
      expect(vi.getTimerCount()).toBe(0)
      const composer = controller.state.composer
      expect(composer.kind).toBe('editing')
      if (composer.kind !== 'editing') throw new Error('expected editing composer')
      expect(composer).toMatchObject({
        kind: 'editing',
        draft: ' \n 書き換え後 \n ',
        original: { text: '元の文章' },
      })
      expect(composer.error).toContain('時間内')
      const next = defer()
      judge.mockReturnValueOnce(next.promise)
      await edit('手動で修正')
      await send()
      const saved = controller.state
      await act(async () => {
        if (completion === 'resolve') old.resolve(result)
        else old.reject(new LabHttpError('reauthentication_required'))
      })
      expect(controller.state).toBe(saved)
      expect(context.invalidateAccess).not.toHaveBeenCalled()
      expect(vi.getTimerCount()).toBe(1)
      await act(async () => next.resolve(result))
      expect(controller.state.entries).toHaveLength(2)
      expect(controller.state.entries[1]).toMatchObject({
        kind: 'comparison',
        original: { text: '元の文章' },
        rewritten: { text: '手動で修正' },
      })
      expect(api.judge).toHaveBeenCalledTimes(2)
      expect(vi.getTimerCount()).toBe(0)
    },
  )

  // テストケース: timerを実行せず開始から15秒経過した時点で通信が完了する。
  // 期待値: 成功も失敗もtimeoutとして扱い、後着を採用しない。
  test.each(['resolve', 'reject'] as const)(
    'enforces elapsed deadline before timer notification on %s',
    async (completion) => {
      vi.useFakeTimers()
      await prepareComparison()
      const delayed = defer()
      const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(delayed.promise)
      api.judge = judge
      await send()
      vi.setSystemTime(Date.now() + 15000)
      await act(async () => {
        if (completion === 'resolve') delayed.resolve(result)
        else delayed.reject(new Error('late error'))
      })
      expect(controller.state.entries).toHaveLength(1)
      const composer = controller.state.composer
      expect(composer.kind).toBe('editing')
      if (composer.kind !== 'editing') throw new Error('expected editing composer')
      expect(composer.error).toContain('時間内')
      expect(judge.mock.calls[0][2]?.aborted).toBe(true)
      expect(vi.getTimerCount()).toBe(0)
    },
  )

  // テストケース: 完了直前にtokenが失効する。
  // 期待値: 成功を破棄し、無効化・timer解除・abort後に認証案内へ接続する。
  test('checks current token on completion and invalidates before auth notification', async () => {
    vi.useFakeTimers()
    await prepareComparison()
    const delayed = defer()
    const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(delayed.promise)
    api.judge = judge
    await send()
    context = {
      ...context,
      getValidIdToken: vi.fn().mockReturnValue(null),
      invalidateAccess: vi.fn(() => {
        expect(judge.mock.calls[0][2]?.aborted).toBe(true)
        expect(vi.getTimerCount()).toBe(0)
      }),
    }
    await render()
    await act(async () => delayed.resolve(result))
    expect(context.invalidateAccess).toHaveBeenCalledWith('auth_expired')
    expect(controller.state.entries).toHaveLength(1)
    expect(controller.state.composer).toMatchObject({
      kind: 'editing',
      original: { text: '元の文章' },
      draft: ' \n 書き換え後 \n ',
    })
  })

  // テストケース: 比較待機中に利用資格を失い、再許可後に古い成功が届く。
  // 期待値: abort・timer解除と入力復元を行い、再許可だけでは再送せず旧応答を無視する。
  test.each(['initializing', 'denied', 'unavailable', 'reauthentication_required'] as const)(
    'cleans up on %s and waits for explicit resend',
    async (kind) => {
      vi.useFakeTimers()
      await prepareComparison()
      const delayed = defer()
      const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(delayed.promise)
      api.judge = judge
      await send()
      context = {
        ...context,
        access: kind === 'denied' ? { kind, reason: 'not_allowed' } : { kind },
      }
      await render()
      expect(judge.mock.calls[0][2]?.aborted).toBe(true)
      expect(vi.getTimerCount()).toBe(0)
      expect(controller.state.composer).toMatchObject({
        kind: 'editing',
        draft: ' \n 書き換え後 \n ',
        original: { text: '元の文章' },
      })
      context = {
        ...context,
        access: { kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 100000 },
      }
      await render()
      const saved = controller.state
      await act(async () => delayed.resolve(result))
      expect(controller.state).toBe(saved)
      expect(api.judge).toHaveBeenCalledTimes(1)
    },
  )

  // テストケース: 待機中にunmountし、旧controllerの操作と遅延成功を試す。
  // 期待値: timer解除・abort後に状態更新も通信もせず、再mountは空状態になる。
  test('cleans up without updates on unmount and starts empty on remount', async () => {
    vi.useFakeTimers()
    await prepareComparison()
    const delayed = defer()
    const judge = vi.fn<LabHttpClient['judge']>().mockReturnValue(delayed.promise)
    api.judge = judge
    await send()
    const oldController = controller
    await act(async () => root.unmount())
    expect(judge.mock.calls[0][2]?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    await act(async () => {
      oldController.setDraft('旧画面')
      await oldController.submit()
      delayed.resolve(result)
    })
    expect(api.judge).toHaveBeenCalledTimes(1)
    root = createRoot(container)
    await render()
    expect(controller.state.entries).toEqual([])
    expect(controller.state.composer).toEqual({
      kind: 'editing',
      draft: '',
      original: null,
      error: null,
    })
  })
})

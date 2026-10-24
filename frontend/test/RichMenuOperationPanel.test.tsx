import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import RichMenuOperationPanel from '../src/RichMenuOperationPanel'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
const now = '2026-08-03T10:00:00+09:00'
const preview = {
  channelId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  channelLabel: '通知',
  templateId: 'jp-link-one',
  templateVersion: 1,
  fields: [{ displayName: '案内', uri: 'https://example.com' }],
  image: {
    contentType: 'image/png' as const,
    width: 2500,
    height: 843,
    digest: 'a'.repeat(64),
    base64: 'aA==',
  },
  observation: {
    kind: 'external_default' as const,
    observedAt: now,
    fingerprint: 'b'.repeat(64),
    managedResourceId: null,
  },
  warnings: ['external_default_replaced' as const],
  confirmationToken: 'opaque',
  expiresAt: now,
}
const result = {
  operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  kind: 'apply' as const,
  status: 'succeeded' as const,
  stage: null,
  result: 'succeeded' as const,
  subjectOperationId: null,
  targetResourceId: null,
  acceptedAt: now,
  completedAt: now,
  nextAllowedActions: [],
}
let container: HTMLDivElement
let root: Root
describe('RichMenuOperationPanel', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.useRealTimers()
  })
  // テストケース: apply確認を表示し、再描画前に実行ボタンを二重clickする。
  // 期待値: 対象と置換影響を表示し、同じoperationのPOSTを一件だけ開始する。
  test('confirms the apply impact and prevents duplicate starts', async () => {
    const onApply = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          now="2026-08-03T09:59:00+09:00"
          channelLabel="通知"
          preview={preview}
          currentDefault="アプリ外の既定"
          busy={false}
          result={null}
          onApply={onApply}
        />,
      ),
    )
    expect(container.textContent).toContain('LINEに反映')
    expect(container.textContent).toContain('アプリ外資源自体は削除しません')
    const button = container.querySelector('button')!
    await act(async () => {
      button.click()
      button.click()
    })
    expect(onApply).toHaveBeenCalledTimes(1)
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          now="2026-08-03T09:59:00+09:00"
          channelLabel="通知"
          preview={preview}
          currentDefault="アプリ外の既定"
          busy={false}
          result={result}
          onApply={onApply}
        />,
      ),
    )
    expect(container.textContent).toContain('直前の操作: 完了')
    expect(container.textContent).not.toContain(result.operationId)
    expect(container.querySelector('button')?.disabled).toBe(true)
  })
  // テストケース: 確定済みの直前結果を保持したまま新しいプレビューを反映する。
  // 期待値: 成功・失敗・cleanup成功後に次の確認を一回だけ実行し、同じ確認は再送しない。
  test.each([
    ['apply', 'succeeded'],
    ['apply', 'failed'],
    ['cleanup', 'succeeded'],
  ] as const)('allows a fresh confirmation after %s %s', async (kind, status) => {
    const onApply = vi.fn()
    const settled = { ...result, kind, status }
    const renderPreview = async (confirmationToken: string) => {
      await act(async () =>
        root.render(
          <RichMenuOperationPanel
            channelLabel="通知"
            preview={{ ...preview, confirmationToken }}
            now="2026-08-03T09:59:00+09:00"
            busy={false}
            result={settled}
            onApply={onApply}
          />,
        ),
      )
    }
    await renderPreview('new-confirmation')
    expect(container.textContent).toContain('直前の操作:')
    expect(container.querySelector('button')?.disabled).toBe(false)
    await act(async () => {
      container.querySelector('button')?.click()
      container.querySelector('button')?.click()
    })
    expect(onApply).toHaveBeenCalledTimes(1)
    await renderPreview('new-confirmation')
    expect(container.querySelector('button')?.disabled).toBe(true)
    await renderPreview('next-confirmation')
    expect(container.querySelector('button')?.disabled).toBe(false)
    await act(async () => container.querySelector('button')?.click())
    expect(onApply).toHaveBeenCalledTimes(2)
    await renderPreview('new-confirmation')
    expect(container.querySelector('button')?.disabled).toBe(true)
  })

  // テストケース: 未確定・回復中の直前結果に新しいプレビューを渡す。
  // 期待値: 結果が成功か失敗に確定するまで新規適用を開始しない。
  test.each(['accepted', 'processing', 'unknown', 'cleanup_required', 'recovery_active'] as const)(
    'blocks a fresh confirmation while the prior result is %s',
    async (status) => {
      const onApply = vi.fn()
      await act(async () =>
        root.render(
          <RichMenuOperationPanel
            channelLabel="通知"
            preview={preview}
            now="2026-08-03T09:59:00+09:00"
            busy={false}
            result={{ ...result, status }}
            onApply={onApply}
          />,
        ),
      )
      expect(container.querySelector('button')?.disabled).toBe(true)
      await act(async () => container.querySelector('button')?.click())
      expect(onApply).not.toHaveBeenCalled()
    },
  )
  // テストケース: 確定結果があっても失効・無効確認・観測不明・処理中・通信結果不明を渡す。
  // 期待値: 次のプレビューでも安全条件を満たさない場合は適用を開始しない。
  test.each([
    { valid: false },
    { busy: true },
    { now: '2026-08-03T10:01:00+09:00' },
    { preview: { ...preview, observation: { ...preview.observation, kind: 'unknown' as const } } },
    { operation: { state: 'tracking' as const, operationId: result.operationId } },
    {
      operation: {
        state: 'transport_unknown' as const,
        operationId: result.operationId,
        error: { code: 'network_error', summary: '切断' },
      },
    },
  ])('retains confirmation safety gates with a settled result %j', async (patch) => {
    const onApply = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          channelLabel="通知"
          preview={preview}
          now="2026-08-03T09:59:00+09:00"
          busy={false}
          result={result}
          onApply={onApply}
          {...patch}
        />,
      ),
    )
    const apply = [...container.querySelectorAll('button')].find(
      (button) => button.textContent === 'LINEに反映',
    )!
    expect(apply.disabled).toBe(true)
    await act(async () => apply.click())
    expect(onApply).not.toHaveBeenCalled()
  })

  // テストケース: 失効した確認を渡して適用を選ぶ。
  // 期待値: ボタンを無効にし、適用を開始しない。
  test('prevents apply for expired or invalid confirmation', async () => {
    const apply = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          channelLabel="通知"
          preview={preview}
          currentDefault="既定"
          now="2026-08-03T10:01:00+09:00"
          busy={false}
          result={null}
          onApply={apply}
        />,
      ),
    )
    expect(container.querySelector('button')?.disabled).toBe(true)
    await act(async () => container.querySelector('button')?.click())
    expect(apply).not.toHaveBeenCalled()
  })

  // テストケース: 適用完了後の現在表示再取得が失敗し、通信結果不明へ切り替わる。
  // 期待値: 直前結果と再取得失敗を分け、プレビュー画像を流用せず同一操作確認へ進める。
  test('retains settled results separately from refresh failure and offers same-operation tracking', async () => {
    const track = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          channelLabel="通知"
          busy={false}
          result={result}
          refreshFailed
          onApply={vi.fn()}
        />,
      ),
    )
    expect(container.textContent).toContain('直前の操作: 完了')
    expect(container.textContent).toContain('現在表示の再取得に失敗')
    expect(container.querySelector('img')).toBeNull()
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          channelLabel="通知"
          busy={false}
          result={null}
          operation={{
            state: 'transport_unknown',
            operationId: result.operationId,
            error: { code: 'network_error', summary: '切断' },
          }}
          onTrack={track}
          onApply={vi.fn()}
        />,
      ),
    )
    await act(async () => container.querySelector('button')?.click())
    expect(track).toHaveBeenCalledOnce()
    expect(container.textContent).not.toContain('完了')
  })
  // テストケース: 有効期限の到達、確認失効、処理中、結果不明を順に表示する。
  // 期待値: どの場合も適用を無効にし、新規の外部作用を開始しない。
  test('closes apply when time expires or confirmation and operation state prohibit it', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-03T09:59:59+09:00'))
    const apply = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuOperationPanel
          channelLabel="通知"
          preview={preview}
          busy={false}
          result={null}
          onApply={apply}
        />,
      ),
    )
    expect(container.querySelector('button')?.disabled).toBe(false)
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    expect(container.querySelector('button')?.disabled).toBe(true)
    for (const patch of [
      { valid: false },
      { busy: true },
      { operation: { state: 'tracking' as const, operationId: result.operationId } },
      {
        operation: {
          state: 'transport_unknown' as const,
          operationId: result.operationId,
          error: { code: 'network_error', summary: '切断' },
        },
      },
    ]) {
      await act(async () =>
        root.render(
          <RichMenuOperationPanel
            channelLabel="通知"
            preview={preview}
            now="2026-08-03T09:59:00+09:00"
            busy={false}
            result={null}
            onApply={apply}
            {...patch}
          />,
        ),
      )
      expect(container.querySelector('button')?.disabled).toBe(true)
      await act(async () => container.querySelector('button')?.click())
    }
    expect(apply).not.toHaveBeenCalled()
  })
})

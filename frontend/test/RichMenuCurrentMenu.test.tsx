import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, test, vi } from 'vitest'
import RichMenuCurrentMenu from '../src/RichMenuCurrentMenu'
import type { CurrentReadState } from '../src/richMenuAdminState'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
// テストケース: 更新開始と取得失敗を順に表示する。
// 期待値: 旧画像を出さず、読み込み通知と明示更新を提供する。
test('separates loading and failure without reusing an old image', async () => {
  const container = document.createElement('div')
  const root = createRoot(container)
  const refresh = vi.fn()
  const render = async (current: CurrentReadState) => {
    await act(async () =>
      root.render(
        <RichMenuCurrentMenu
          channelLabel="通知"
          current={current}
          onRefresh={refresh}
          onImageError={vi.fn()}
          collapsed
        />,
      ),
    )
  }
  await render({ state: 'loading', generation: 1 })
  expect(container.querySelector('[role="status"]')?.textContent).toContain('読み込み中')
  expect(container.querySelector('details')?.open).toBe(false)
  await render({ state: 'failed', error: { code: 'network_error', summary: '取得失敗' } })
  expect(container.querySelector('img')).toBeNull()
  await act(async () => container.querySelector('button')?.click())
  expect(refresh).toHaveBeenCalledOnce()
  await act(async () => root.unmount())
})

// テストケース: 管理画像の実寸と各代替状態を順に渡す。
// 期待値: 全体画像は元比率で表示し、画像失敗・未設定・外部・要確認・停止を区別する。
test('renders the original image and distinct safe alternatives', async () => {
  const { default: fixtures } = await import('./fixtures/rich-menu-current.json')
  const { parseCurrentMenu } = await import('../src/richMenuAdminDto')
  const parsed = parseCurrentMenu(
    fixtures.managed,
    fixtures.managed.channelId,
    fixtures.managed.channelRevision,
  )
  if (!parsed.ok || parsed.value.current.kind !== 'managed') throw new Error('fixture')
  const base = parsed.value
  const container = document.createElement('div')
  const root = createRoot(container)
  const failed = vi.fn()
  const render = async (
    current: Extract<CurrentReadState, { state: 'ready' }>['value']['current'],
  ) => {
    await act(async () =>
      root.render(
        <RichMenuCurrentMenu
          channelLabel="通知"
          current={{ state: 'ready', value: { ...base, current } }}
          onRefresh={vi.fn()}
          onImageError={failed}
        />,
      ),
    )
  }
  const config = parsed.value.current.configuration
  for (const height of [843, 1686]) {
    await render({
      kind: 'managed',
      resourceId: 'resource',
      configuration: config,
      image: {
        status: 'available',
        contentType: 'image/png',
        width: 2500,
        height,
        digest: 'a'.repeat(64),
        imageUrl: 'blob:current',
      },
    })
    const img = container.querySelector('img')!
    expect(img.getAttribute('height')).toBe(String(height))
    expect(img.style.height).toBe('auto')
    expect(img.alt).toBe('通知 の現在のメニュー')
    await act(async () => img.dispatchEvent(new Event('error')))
  }
  expect(failed).toHaveBeenCalledTimes(2)
  await render({
    kind: 'managed',
    resourceId: 'resource',
    configuration: config,
    image: { status: 'unavailable', reason: 'fetch_failed' },
  })
  expect(container.textContent).toContain('許可された操作は引き続き利用できます')
  for (const [kind, label] of [
    ['unset', '未設定'],
    ['external', 'アプリ外'],
    ['attention', '要確認'],
    ['inactive', '停止中'],
  ] as const) {
    await render({ kind })
    expect(container.textContent).toContain(label)
    expect(container.querySelector('img')).toBeNull()
  }
  await act(async () => root.unmount())
})

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import RichMenuRecoveryPanel from '../src/RichMenuRecoveryPanel'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
let container: HTMLDivElement
let root: Root
const currentResource = {
  resourceId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  originOperationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  lifecycle: 'applied' as const,
  imageDigest: 'a'.repeat(64),
}
const subjectOperation = {
  operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  kind: 'unlink' as const,
  status: 'unknown' as const,
  stage: 'verifying' as const,
  result: 'timeout_unknown' as const,
  subjectOperationId: null,
  targetResourceId: currentResource.resourceId,
  acceptedAt: '2026-08-03T10:00:00+09:00',
  completedAt: null,
  nextAllowedActions: ['recheck' as const],
}
const observation = {
  kind: 'managed_default' as const,
  observedAt: '2026-08-03T10:00:00+09:00',
  fingerprint: 'b'.repeat(64),
  managedResourceId: currentResource.resourceId,
}
const common = {
  channelLabel: '通知',
  currentResource,
  cleanupResources: [currentResource],
  subjectOperation,
  observation,
  busy: false,
  result: null,
}
describe('RichMenuRecoveryPanel', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
  })
  // テストケース: 適用解除と管理終了の確認をそれぞれ表示してownerが選択する。
  // 期待値: 外部効果の違いを表示し、選択した一件の操作だけを開始する。
  test('distinguishes unlink from local management release', async () => {
    const onStart = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuRecoveryPanel {...common} actions={['unlink', 'release']} onStart={onStart} />,
      ),
    )
    expect(container.textContent).toContain('LINE既定を外します')
    expect(container.textContent).toContain('LINE既定を維持します')
    const buttons = [...container.querySelectorAll('button')]
    await act(async () => {
      buttons[0].click()
      buttons[0].click()
    })
    expect(onStart).toHaveBeenCalledTimes(1)
    expect(onStart).toHaveBeenCalledWith('unlink', undefined)
  })
  // テストケース: 結果不明operationとcleanup対象を回復panelへ渡す。
  // 期待値: 保存subjectに限定した明示recheckと一件のcleanupだけを開始できる。
  test('requires explicit recheck and cleanup for the saved subjects', async () => {
    const onStart = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuRecoveryPanel {...common} actions={['recheck', 'cleanup']} onStart={onStart} />,
      ),
    )
    expect(container.textContent).toContain('元の操作だけを再確認')
    expect(container.textContent).toContain('一件の管理対象資源だけを削除')
    expect(container.textContent).toContain(subjectOperation.operationId)
    expect(container.textContent).toContain('verifying')
    const buttons = [...container.querySelectorAll('button')]
    await act(async () => buttons[1].click())
    expect(onStart).toHaveBeenCalledWith('cleanup', currentResource.resourceId)
  })
})

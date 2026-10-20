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
    expect(container.querySelector('details')).not.toBeNull()
    const buttons = [...container.querySelectorAll('button')]
    await act(async () => {
      buttons[0].click()
      buttons[0].click()
    })
    expect(onStart).not.toHaveBeenCalled()
    expect(container.textContent).toContain('LINE既定を外します')
    await act(async () =>
      [...container.querySelectorAll('button')]
        .find((b) => b.textContent === '適用解除を確定')
        ?.click(),
    )
    expect(onStart).toHaveBeenCalledTimes(1)
    expect(onStart).toHaveBeenCalledWith('unlink', currentResource.resourceId)
  })
  // テストケース: 結果不明operationとcleanup対象を回復panelへ渡す。
  // 期待値: 保存subjectに限定した明示recheckと一件のcleanupだけを開始できる。
  test('requires explicit recheck and cleanup for the saved subjects', async () => {
    const onStart = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuRecoveryPanel
          {...common}
          currentResource={null}
          recoveryTargets={[
            {
              kind: 'cleanup',
              subjectOperationId: subjectOperation.operationId,
              targetResourceId: currentResource.resourceId,
            },
          ]}
          actions={['recheck', 'cleanup']}
          onStart={onStart}
        />,
      ),
    )
    expect(container.textContent).toContain('元の操作だけを再確認')
    expect(container.textContent).toContain('一件の管理対象資源だけを削除')
    expect(container.textContent).not.toContain('verifying')
    const buttons = [...container.querySelectorAll('button')]
    await act(async () => buttons[1].click())
    expect(onStart).toHaveBeenCalledWith('cleanup', currentResource.resourceId)
  })
  // テストケース: 適用解除を選び、確認を取り消してから再度確定する。
  // 期待値: 対象と削除影響を示し、確定するまで操作を開始しない。
  test('requires cancellable inline confirmation before unlink', async () => {
    const start = vi.fn()
    await act(async () =>
      root.render(<RichMenuRecoveryPanel {...common} actions={['unlink']} onStart={start} />),
    )
    await act(async () => container.querySelector('button')?.click())
    expect(start).not.toHaveBeenCalled()
    expect(container.textContent).toContain('アプリ外メニューの表示が再開')
    await act(async () =>
      [...container.querySelectorAll('button')].find((b) => b.textContent === '取消')?.click(),
    )
    expect(start).not.toHaveBeenCalled()
    await act(async () => container.querySelector('button')?.click())
    await act(async () =>
      [...container.querySelectorAll('button')]
        .find((b) => b.textContent === '適用解除を確定')
        ?.click(),
    )
    expect(start).toHaveBeenCalledWith('unlink', currentResource.resourceId)
  })

  // テストケース: 通常適用中の管理終了を確認し、停止・禁止modeへ切り替える。
  // 期待値: 対象と追跡終了の影響を確認後だけ渡し、禁止状態では操作を出さない。
  test('confirms release and hides forbidden or inactive operations', async () => {
    const recover = vi.fn()
    await act(async () =>
      root.render(<RichMenuRecoveryPanel {...common} actions={['release']} onRecovery={recover} />),
    )
    await act(async () => container.querySelector('button')?.click())
    for (const text of ['変更・削除しません', '自動的に管理対象へ戻りません', '履歴は保持'])
      expect(container.textContent).toContain(text)
    await act(async () =>
      [...container.querySelectorAll('button')]
        .find((b) => b.textContent === '管理終了を確定')
        ?.click(),
    )
    expect(recover).toHaveBeenCalledWith({
      kind: 'release',
      targetResourceId: currentResource.resourceId,
    })
    for (const props of [
      { active: false },
      { mode: 'read_only' as const },
      { mode: 'unavailable' as const },
      { mode: 'recovery_only' as const },
      { deactivationPending: true },
    ]) {
      await act(async () =>
        root.render(
          <RichMenuRecoveryPanel
            {...common}
            {...props}
            actions={['release', 'unlink']}
            onRecovery={recover}
          />,
        ),
      )
      expect(container.querySelector('button')).toBeNull()
    }
  })
  // テストケース: 有効なcleanupの組と関係不明の資源を混ぜて渡す。
  // 期待値: 有効な組だけをtyped操作へ渡し、組がなければ案内だけを残す。
  test('only sends an explicitly permitted cleanup pair', async () => {
    const recover = vi.fn()
    const target = {
      kind: 'cleanup' as const,
      subjectOperationId: subjectOperation.operationId,
      targetResourceId: currentResource.resourceId,
    }
    await act(async () =>
      root.render(
        <RichMenuRecoveryPanel
          {...common}
          currentResource={null}
          actions={['cleanup']}
          recoveryTargets={[target, { ...target, targetResourceId: 'unrelated' }]}
          onRecovery={recover}
        />,
      ),
    )
    expect(container.querySelectorAll('button')).toHaveLength(1)
    await act(async () => container.querySelector('button')?.click())
    expect(recover).toHaveBeenCalledWith(target)
    await act(async () =>
      root.render(
        <RichMenuRecoveryPanel
          {...common}
          actions={['cleanup']}
          recoveryTargets={[]}
          onRecovery={recover}
        />,
      ),
    )
    expect(container.querySelector('button')).toBeNull()
    expect(container.textContent).toContain('関係を確認できません')
  })
})

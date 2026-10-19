import { describe, expect, test } from 'vitest'

import { parseDeactivation, parseRichMenuState } from '../src/richMenuAdminDto'
import fixtures from './fixtures/rich-menu-current.json'
import errors from './fixtures/rich-menu-current-errors.json'

describe('現在閲覧の共通HTTP fixture', () => {
  // テストケース: 各状態の共通fixtureをjsdom環境で既存DTOと合成する。
  // 期待値: 全tag・部分失敗・停止情報が利用でき、適用確認値や期限を含まない。
  test('composes every current state with existing DTOs', () => {
    expect(new Set(Object.values(fixtures).map((view) => view.current.kind))).toEqual(
      new Set(['managed', 'unset', 'external', 'attention', 'inactive']),
    )
    for (const view of Object.values(fixtures)) {
      expect(parseRichMenuState(view.rich).ok).toBe(true)
      if (view.deactivation !== null) expect(parseDeactivation(view.deactivation).ok).toBe(true)
      expect(view).not.toHaveProperty('confirmationToken')
      expect(view).not.toHaveProperty('expiresAt')
      expect(view.checkedAt === null).toBe(view.current.kind === 'inactive')
    }
  })

  // テストケース: 現在画像fixtureのbytesと完全URLを読み取る。
  // 期待値: compact/largeが別寸法を持ち、元URLを短縮せず後続テストへ渡せる。
  test('provides real PNG bytes and complete configuration', () => {
    for (const view of [fixtures.managed, fixtures.managedLarge]) {
      const bytes = atob(view.current.image.base64)
      expect(Array.from(bytes.slice(0, 8), (value) => value.charCodeAt(0))).toEqual([
        137, 80, 78, 71, 13, 10, 26, 10,
      ])
      const buffer = Uint8Array.from(bytes, (value) => value.charCodeAt(0))
      const data = new DataView(buffer.buffer)
      expect(data.getUint32(16)).toBe(view.current.image.width)
      expect(data.getUint32(20)).toBe(view.current.image.height)
    }
    expect(fixtures.managed.current.configuration.fields[0].uri).toBe(
      'https://example.com/menu?source=fixture&value=1#details',
    )
    expect(fixtures.cleanup.recoveryTargets[0].subjectOperationId).toBe(
      fixtures.cleanup.rich.blockingOperation.operationId,
    )
  })

  // テストケース: 外部既定へ変わった管理対象の操作候補を確認する。
  // 期待値: 作り直しと管理終了を許可し、現在既定の適用解除を提示しない。
  test('limits external-change actions to the approved candidates', () => {
    const expected = ['new_preview', 'apply', 'get_state', 'view_history', 'release']
    expect(fixtures.attention.rich.nextAllowedActions).toEqual(expected)
    expect(fixtures.attention.rich.effectiveActions).toEqual(expected)
  })

  // テストケース: HTTP失敗fixtureのstatusと安全なcodeを確認する。
  // 期待値: 閲覧専用競合と認証・認可・入力・基盤失敗を区別できる。
  test('provides safe error contracts', () => {
    expect(new Set(errors.map((error) => error.status))).toEqual(
      new Set([400, 401, 403, 404, 409, 503]),
    )
    expect(errors).toContainEqual({
      status: 409,
      body: { error: { code: 'current_changed', nextAllowedActions: [] } },
    })
  })
})

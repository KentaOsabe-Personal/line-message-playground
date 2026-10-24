import { describe, expect, test } from 'vitest'

import {
  parseCurrentMenu,
  parseDeactivation,
  parseHistoryPage,
  parseOperation,
  parsePreview,
  parseRichMenuState,
  parseTemplates,
} from '../src/richMenuAdminDto'

import currentFixtures from './fixtures/rich-menu-current.json'

const id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const id2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const now = '2026-08-03T10:00:00+09:00'
const operation = {
  operationId: id,
  kind: 'apply',
  status: 'succeeded',
  stage: null,
  result: 'succeeded',
  subjectOperationId: null,
  targetResourceId: null,
  acceptedAt: now,
  completedAt: now,
  nextAllowedActions: ['view_history'],
}
const observation = {
  kind: 'default_none',
  observedAt: now,
  fingerprint: 'a'.repeat(64),
  managedResourceId: null,
}

describe('rich menu admin DTO', () => {
  // テストケース: template・state・operation・history・deactivationの正規応答を解析する。
  // 期待値: exactなclosed DTOとして全variantを受理する。
  test('accepts exact owner API projections', () => {
    expect(
      parseTemplates({
        items: [
          {
            templateId: 'jp-link-one',
            version: 1,
            displayName: '1リンク',
            canvas: { width: 2500, height: 843 },
            areas: [
              {
                field: 'area1',
                description: '全面',
                bounds: { x: 0, y: 0, width: 2500, height: 843 },
              },
            ],
            requiredFields: ['area1'],
            limits: { displayName: 20, uri: 1000 },
          },
        ],
      }),
    ).toMatchObject({ ok: true })
    expect(
      parseRichMenuState({
        channelId: id,
        currentResource: null,
        blockingOperation: null,
        activeOperation: null,
        cleanupResources: [],
        latestObservation: observation,
        historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null },
        nextAllowedActions: ['new_preview', 'view_history'],
        mode: 'enabled',
        effectiveActions: ['new_preview', 'view_history'],
        unavailableReason: null,
      }),
    ).toMatchObject({ ok: true })
    expect(parseOperation(operation)).toMatchObject({ ok: true })
    expect(
      parseHistoryPage({
        items: [
          {
            operation,
            channelId: id,
            channelLabel: '通知',
            configuration: null,
            transitions: ['accepted', 'succeeded'],
            defaultRelation: 'became_default',
            cleanupRelation: 'not_required',
          },
        ],
        nextCursor: 'opaque-cursor',
        hasMore: true,
      }),
    ).toMatchObject({ ok: true })
    expect(
      parseDeactivation({
        channelId: id,
        channelActive: true,
        channelUpdatedAt: now,
        operationId: id2,
        status: 'confirmation_required',
        reason: 'external_default',
        subjectOperationId: null,
        recoveryOperationId: null,
        nextAction: 'resolve_external_default_then_recheck',
        acceptedAt: now,
        updatedAt: now,
        completedAt: null,
      }),
    ).toMatchObject({ ok: true })
    expect(parseDeactivation(null)).toEqual({ ok: true, value: null })
  })

  // テストケース: unknown key・unknown enum・非canonical UUID・naive datetimeを解析する。
  // 期待値: 以前のprojectionに利用できないprotocol_errorとして拒否する。
  test('rejects non-exact and malformed projections', () => {
    for (const invalid of [
      { ...operation, rawResponse: {} },
      { ...operation, status: 'maybe' },
      { ...operation, operationId: id.toUpperCase() },
      { ...operation, acceptedAt: '2026-08-03T10:00:00' },
    ])
      expect(parseOperation(invalid)).toMatchObject({
        ok: false,
        error: { code: 'protocol_error' },
      })
  })

  // テストケース: preview外の応答へ確認値・URL・画像・秘密様keyを混入する。
  // 期待値: 入れ子を含め全てprotocol errorとして拒否し、previewだけexact shapeで受理する。
  test('rejects sensitive fields outside preview', () => {
    expect(parseRichMenuState({ confirmationToken: 'leak' })).toMatchObject({ ok: false })
    expect(
      parseHistoryPage({ items: [], nextCursor: null, hasMore: false, image: 'leak' }),
    ).toMatchObject({ ok: false })
    expect(parseTemplates({ items: [], accessToken: 'leak' })).toMatchObject({ ok: false })
    expect(
      parsePreview({
        channelId: id,
        channelLabel: '通知',
        templateId: 'jp-link-one',
        templateVersion: 1,
        fields: [{ displayName: '案内', uri: 'https://example.com/path' }],
        image: {
          contentType: 'image/png',
          width: 2500,
          height: 843,
          digest: 'b'.repeat(64),
          base64: 'aGVsbG8=',
        },
        observation,
        warnings: ['url_history_persisted'],
        confirmationToken: 'opaque',
        expiresAt: now,
      }),
    ).toMatchObject({ ok: true })
  })
})

const parseCurrent = (value: unknown) =>
  parseCurrentMenu(
    value,
    currentFixtures.managed.channelId,
    currentFixtures.managed.channelRevision,
  )
const changedCurrent = (
  path: string,
  replacement: unknown,
  source: unknown = currentFixtures.managed,
) => {
  const value = structuredClone(source) as Record<string, unknown>
  const keys = path.split('.')
  let parent = value
  for (const key of keys.slice(0, -1)) parent = parent[key] as Record<string, unknown>
  parent[keys.at(-1)!] = replacement
  return value
}

describe('現在閲覧DTO', () => {
  // テストケース: 全tag・部分失敗・廃止版・停止・後片付けの共通fixtureを解析する。
  // 期待値: 既存DTOを維持し、完全URLと元の画像を変更せず返す。
  test('accepts every current fixture without changing saved values', () => {
    for (const value of Object.values(currentFixtures)) {
      expect(parseCurrent(value), value.current.kind).toEqual({ ok: true, value })
    }
  })

  // テストケース: path、revision、停止情報、資源、観測、回復対象の対応やtagを壊す。
  // 期待値: 画像だけの失敗へ縮退せず応答全体を拒否する。
  test.each([
    ['channelId', id2],
    ['channelRevision', now],
    ['rich.channelId', id2],
    ['checkedAt', null],
    ['current.kind', 'future'],
    ['current.resourceId', id],
    ['rich.currentResource', null],
    ['rich.currentResource.lifecycle', 'released'],
    ['rich.latestObservation.managedResourceId', id],
    ['rich.latestObservation.kind', 'unknown'],
    ['rich.effectiveActions', ['future']],
    ['confirmationToken', 'secret'],
    ['recoveryTargets', [{ kind: 'cleanup', subjectOperationId: 'bad', targetResourceId: id2 }]],
  ])('rejects inconsistent envelope %s', (path, replacement) => {
    expect(parseCurrent(changedCurrent(path, replacement)).ok).toBe(false)
  })

  // テストケース: 停止情報のchannel・revision・active状態またはtag必須項目を不正にする。
  // 期待値: 状態の取り違えをprotocol errorとして拒否する。
  test('rejects deactivation mismatches and forbidden content', () => {
    for (const [path, replacement] of [
      ['deactivation.channelId', id2],
      ['deactivation.channelUpdatedAt', now],
      ['deactivation.channelActive', true],
      ['checkedAt', now],
    ] as const)
      expect(parseCurrent(changedCurrent(path, replacement, currentFixtures.inactive)).ok).toBe(
        false,
      )
    expect(parseCurrent(changedCurrent('current.image', {}, currentFixtures.unset)).ok).toBe(false)
    expect(parseCurrent(changedCurrent('current', { kind: 'managed' })).ok).toBe(false)
  })

  // テストケース: Base64構文・size・形式・寸法・digest・statusを壊す。
  // 期待値: 画像だけをinvalidへ縮退し、検証済み状態と設定を保持する。
  test.each([
    ['base64', 'aGVsbG8='],
    ['base64', 'a==='],
    ['base64', 'AB=='],
    ['base64', 'A'.repeat(1_398_108)],
    ['contentType', 'image/svg+xml'],
    ['width', 799],
    ['width', 2501],
    ['height', 249],
    ['height', 1800],
    ['width', 2400],
    ['digest', 'invalid'],
    ['digest', 'f'.repeat(64)],
    ['status', 'future'],
  ])('degrades invalid image %s', (key, replacement) => {
    const parsed = parseCurrent(changedCurrent(`current.image.${key}`, replacement))
    expect(parsed.ok).toBe(true)
    if (!parsed.ok || parsed.value.current.kind !== 'managed') throw new Error('managed expected')
    expect(parsed.value.current.image).toEqual({ status: 'unavailable', reason: 'invalid' })
    expect(parsed.value.current.configuration).toEqual(
      currentFixtures.managed.current.configuration,
    )
    expect(parsed.value.rich).toEqual(currentFixtures.managed.rich)
  })

  // テストケース: 設定のURL・版・項目・編集可否を壊す。
  // 期待値: 設定だけを編集不可へ縮退し、画像と操作候補を保持する。
  test.each([
    ['templateVersion', 0],
    ['templateId', 'invalid id'],
    ['fields', []],
    ['fields', [{ displayName: '案内', uri: 'https://user:pass@example.com/' }]],
    ['fields', [{ displayName: '改行\n', uri: 'https://example.com/' }]],
    ['fields', [{ displayName: '案内', uri: ' https://example.com/' }]],
    ['editability', 'future'],
    ['status', 'future'],
  ])('degrades invalid configuration %s', (key, replacement) => {
    const parsed = parseCurrent(changedCurrent(`current.configuration.${key}`, replacement))
    if (!parsed.ok || parsed.value.current.kind !== 'managed') throw new Error('managed expected')
    expect(parsed.value.current.configuration).toEqual({
      status: 'unavailable',
      reason: 'configuration_invalid',
    })
    expect(parsed.value.current.image).toEqual(currentFixtures.managed.current.image)
  })
})

describe('現在閲覧revisionの時刻照合', () => {
  // テストケース: channel APIのZ表記と閲覧APIのoffset表記を同一revisionとして比較する。
  // 期待値: timezone表記差を許容し、マイクロ秒の違いは拒否する。
  test('compares equivalent instants without losing microseconds', () => {
    const value = {
      ...currentFixtures.managed,
      channelRevision: '2026-10-18T00:00:00.123456+00:00',
    }
    expect(parseCurrentMenu(value, id, '2026-10-18T09:00:00.123456+09:00').ok).toBe(true)
    expect(parseCurrentMenu(value, id, '2026-10-18T00:00:00.123456Z').ok).toBe(true)
    expect(parseCurrentMenu(value, id, '2026-10-18T00:00:00.123457Z').ok).toBe(false)
    const inactive = {
      ...currentFixtures.inactive,
      deactivation: {
        ...currentFixtures.inactive.deactivation,
        channelUpdatedAt: '2026-10-18T00:00:00Z',
      },
    }
    expect(parseCurrentMenu(inactive, id, '2026-10-18T00:00:00Z').ok).toBe(true)
  })
})

describe('現在画像のbyte上限', () => {
  // テストケース: 元PNGに無害な末尾paddingを加え、decoded sizeを1 MiB境界まで増やす。
  // 期待値: 上限ちょうどは処理でき、1 byte超過は画像だけをinvalidにする。
  test('bounds decoded bytes before expansion at the exact 1 MiB limit', () => {
    const binary = atob(currentFixtures.managed.current.image.base64).padEnd(1_048_576, '\0')
    const parsed = parseCurrent(changedCurrent('current.image.base64', btoa(binary)))
    if (!parsed.ok || parsed.value.current.kind !== 'managed') throw new Error('managed expected')
    expect(parsed.value.current.image.status).toBe('available')
    const over = parseCurrent(changedCurrent('current.image.base64', btoa(binary + '\0')))
    if (!over.ok || over.value.current.kind !== 'managed') throw new Error('managed expected')
    expect(over.value.current.image).toEqual({ status: 'unavailable', reason: 'invalid' })
  })
})

describe('現在閲覧の提供モード', () => {
  // テストケース: Backendが許可したread_onlyとrecovery_onlyの操作候補を受け取る。
  // 期待値: recovery_onlyでも既存契約の適用解除・管理終了を利用できる。
  test('preserves server recovery actions including unlink and release', () => {
    const value = structuredClone(currentFixtures.managed)
    value.rich.mode = 'recovery_only'
    value.rich.effectiveActions = ['unlink', 'release', 'get_state', 'view_history']
    expect(parseCurrent(value)).toEqual({ ok: true, value })
    value.rich.mode = 'read_only'
    value.rich.effectiveActions = ['get_state', 'view_history']
    expect(parseCurrent(value)).toEqual({ ok: true, value })
  })
})

// テストケース: マイクロ秒を含むcurrentのUTC offset表記を同時刻のchannel revisionで検証する。
// 期待値: 成功値を検証済みchannel表記へ揃え、1マイクロ秒の競合は拒否する。
test('11.5 returns the verified requested revision without losing microseconds', () => {
  const value = { ...currentFixtures.managed, channelRevision: '2026-10-18T00:00:00.123456+00:00' }
  for (const expected of ['2026-10-18T00:00:00.123456Z', '2026-10-18T09:00:00.123456+09:00']) {
    const parsed = parseCurrentMenu(value, id, expected)
    expect(parsed.ok).toBe(true)
    if (!parsed.ok) throw new Error('current expected')
    expect(parsed.value.channelRevision).toBe(expected)
  }
  expect(parseCurrentMenu(value, id, '2026-10-18T00:00:00.123457Z').ok).toBe(false)
})

import { describe, expect, test } from 'vitest'

import {
  parseDeactivation,
  parseHistoryPage,
  parseOperation,
  parsePreview,
  parseRichMenuState,
  parseTemplates,
} from '../src/richMenuAdminDto'

const id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const id2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const now = '2026-08-03T10:00:00+09:00'
const operation = {
  operationId: id, kind: 'apply', status: 'succeeded', stage: null, result: 'succeeded',
  subjectOperationId: null, targetResourceId: null, acceptedAt: now, completedAt: now,
  nextAllowedActions: ['view_history'],
}
const observation = {
  kind: 'default_none', observedAt: now, fingerprint: 'a'.repeat(64), managedResourceId: null,
}

describe('rich menu admin DTO', () => {
  // テストケース: template・state・operation・history・deactivationの正規応答を解析する。
  // 期待値: exactなclosed DTOとして全variantを受理する。
  test('accepts exact owner API projections', () => {
    expect(parseTemplates({ items: [{
      templateId: 'jp-link-one', version: 1, displayName: '1リンク',
      canvas: { width: 2500, height: 843 },
      areas: [{ field: 'area1', description: '全面', bounds: { x: 0, y: 0, width: 2500, height: 843 } }],
      requiredFields: ['area1'], limits: { displayName: 20, uri: 1000 },
    }] })).toMatchObject({ ok: true })
    expect(parseRichMenuState({
      channelId: id, currentResource: null, blockingOperation: null, activeOperation: null,
      cleanupResources: [], latestObservation: observation,
      historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null },
      nextAllowedActions: ['new_preview', 'view_history'], mode: 'enabled',
      effectiveActions: ['new_preview', 'view_history'], unavailableReason: null,
    })).toMatchObject({ ok: true })
    expect(parseOperation(operation)).toMatchObject({ ok: true })
    expect(parseHistoryPage({ items: [{
      operation, channelId: id, channelLabel: '通知', configuration: null,
      transitions: ['accepted', 'succeeded'], defaultRelation: 'became_default', cleanupRelation: 'not_required',
    }], nextCursor: 'opaque-cursor', hasMore: true })).toMatchObject({ ok: true })
    expect(parseDeactivation({
      channelId: id, channelActive: true, channelUpdatedAt: now, operationId: id2,
      status: 'confirmation_required', reason: 'external_default', subjectOperationId: null,
      recoveryOperationId: null, nextAction: 'resolve_external_default_then_recheck',
      acceptedAt: now, updatedAt: now, completedAt: null,
    })).toMatchObject({ ok: true })
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
    ]) expect(parseOperation(invalid)).toMatchObject({ ok: false, error: { code: 'protocol_error' } })
  })

  // テストケース: preview外の応答へ確認値・URL・画像・秘密様keyを混入する。
  // 期待値: 入れ子を含め全てprotocol errorとして拒否し、previewだけexact shapeで受理する。
  test('rejects sensitive fields outside preview', () => {
    expect(parseRichMenuState({ confirmationToken: 'leak' })).toMatchObject({ ok: false })
    expect(parseHistoryPage({ items: [], nextCursor: null, hasMore: false, image: 'leak' })).toMatchObject({ ok: false })
    expect(parseTemplates({ items: [], accessToken: 'leak' })).toMatchObject({ ok: false })
    expect(parsePreview({
      channelId: id, channelLabel: '通知', templateId: 'jp-link-one', templateVersion: 1,
      fields: [{ displayName: '案内', uri: 'https://example.com/path' }],
      image: { contentType: 'image/png', width: 2500, height: 843, digest: 'b'.repeat(64), base64: 'aGVsbG8=' },
      observation, warnings: ['url_history_persisted'], confirmationToken: 'opaque', expiresAt: now,
    })).toMatchObject({ ok: true })
  })
})

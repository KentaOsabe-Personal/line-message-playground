import type { Parsed, SafeApiError } from './authDto'
import { isChannelAdminDateTime, isChannelAdminUuid } from './channelAdminDto'

export type RichMenuAction = 'new_preview' | 'apply' | 'unlink' | 'release' | 'recheck' | 'cleanup' | 'get_state' | 'view_history' | 'clear_to_disable'
export type OperationKind = 'apply' | 'unlink' | 'release' | 'recheck' | 'cleanup'
export type OperationStatus = 'accepted' | 'processing' | 'failed' | 'unknown' | 'cleanup_required' | 'recovery_active' | 'succeeded'
export type OperationStage = 'creating' | 'uploading' | 'setting_default' | 'verifying' | 'clearing_default' | 'cleaning' | 'local_release'
export type SafeResultCode = 'accepted' | 'succeeded' | 'no_change' | 'cleanup_required' | 'invalid_input' | 'template_changed' | 'image_invalid' | 'authentication_required' | 'owner_operation_blocked' | 'channel_unavailable' | 'channel_inactive' | 'stale_channel' | 'operation_conflict' | 'operation_in_progress' | 'preview_expired' | 'integration_not_ready' | 'line_rejected' | 'timeout_unknown' | 'response_unknown' | 'observation_unknown' | 'rate_limited' | 'storage_retryable' | 'storage_unavailable' | 'unexpected'

export type TemplateField = { displayName: string; uri: string }
export type TemplateDescriptor = {
  templateId: string; version: number; displayName: string
  canvas: { width: number; height: number }
  areas: { field: string; description: string; bounds: { x: number; y: number; width: number; height: number } }[]
  requiredFields: string[]; limits: { displayName: number; uri: number }
}
export type OperationView = {
  operationId: string; kind: OperationKind; status: OperationStatus; stage: OperationStage | null
  result: SafeResultCode; subjectOperationId: string | null; targetResourceId: string | null
  acceptedAt: string; completedAt: string | null; nextAllowedActions: RichMenuAction[]
}
export type ManagedResourceView = { resourceId: string; originOperationId: string; lifecycle: 'candidate' | 'applied' | 'old' | 'cleanup_required' | 'deleted' | 'released'; imageDigest: string }
export type ObservationView = { kind: 'default_none' | 'managed_default' | 'other_managed_default' | 'external_default' | 'unknown'; observedAt: string; fingerprint: string; managedResourceId: string | null }
export type RichMenuStateView = {
  channelId: string; currentResource: ManagedResourceView | null; blockingOperation: OperationView | null
  activeOperation: OperationView | null; cleanupResources: ManagedResourceView[]; latestObservation: ObservationView | null
  historySummary: { totalCount: number; latestOperationId: string | null; latestStatus: OperationStatus | null }
  nextAllowedActions: RichMenuAction[]; mode: 'read_only' | 'recovery_only' | 'enabled' | 'unavailable'
  effectiveActions: RichMenuAction[]; unavailableReason: string | null
}
export type PreviewView = {
  channelId: string; channelLabel: string; templateId: string; templateVersion: number; fields: TemplateField[]
  image: { contentType: 'image/png'; width: number; height: number; digest: string; base64: string }
  observation: ObservationView; warnings: ('external_default_replaced' | 'url_history_persisted' | 'url_must_not_contain_secrets')[]
  confirmationToken: string; expiresAt: string
}
export type HistoryEntryView = {
  operation: OperationView; channelId: string; channelLabel: string
  configuration: { templateId: string; templateVersion: number; fields: TemplateField[] } | null
  transitions: SafeResultCode[]; defaultRelation: 'became_default' | 'cleared_default' | 'not_default' | 'external_default_preserved' | 'unknown'
  cleanupRelation: 'not_required' | 'required' | 'completed' | 'unknown'
}
export type HistoryPageView = { items: HistoryEntryView[]; nextCursor: string | null; hasMore: boolean }
export type DeactivationView = {
  channelId: string; channelActive: boolean; channelUpdatedAt: string; operationId: string
  status: 'checking' | 'unlinking' | 'confirmation_required' | 'completed'; reason: string | null
  subjectOperationId: string | null; recoveryOperationId: string | null
  nextAction: 'get_state' | 'none' | 'resolve_external_default_then_recheck' | 'complete_cleanup_then_recheck' | 'recheck'
  acceptedAt: string; updatedAt: string; completedAt: string | null
}

const protocolError = (): Parsed<never> => ({ ok: false, error: { code: 'protocol_error', summary: '応答形式を確認できません。' } })
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const exact = (value: Record<string, unknown>, keys: readonly string[]) => {
  const actual = Object.keys(value).sort(); const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}
const isString = (value: unknown, min = 1, max = 255): value is string => typeof value === 'string' && value.length >= min && value.length <= max
const isInt = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number => Number.isInteger(value) && (value as number) >= min && (value as number) <= max
const isEnum = <T extends string>(value: unknown, values: readonly T[]): value is T => typeof value === 'string' && values.includes(value as T)
const uuidOrNull = (value: unknown): value is string | null => value === null || isChannelAdminUuid(value)
const dateOrNull = (value: unknown): value is string | null => value === null || isChannelAdminDateTime(value)
const sha256 = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const actions: readonly RichMenuAction[] = ['new_preview', 'apply', 'unlink', 'release', 'recheck', 'cleanup', 'get_state', 'view_history', 'clear_to_disable']
const operationKinds: readonly OperationKind[] = ['apply', 'unlink', 'release', 'recheck', 'cleanup']
const operationStatuses: readonly OperationStatus[] = ['accepted', 'processing', 'failed', 'unknown', 'cleanup_required', 'recovery_active', 'succeeded']
const operationStages: readonly OperationStage[] = ['creating', 'uploading', 'setting_default', 'verifying', 'clearing_default', 'cleaning', 'local_release']
const resultCodes: readonly SafeResultCode[] = ['accepted', 'succeeded', 'no_change', 'cleanup_required', 'invalid_input', 'template_changed', 'image_invalid', 'authentication_required', 'owner_operation_blocked', 'channel_unavailable', 'channel_inactive', 'stale_channel', 'operation_conflict', 'operation_in_progress', 'preview_expired', 'integration_not_ready', 'line_rejected', 'timeout_unknown', 'response_unknown', 'observation_unknown', 'rate_limited', 'storage_retryable', 'storage_unavailable', 'unexpected']
const listOf = <T>(value: unknown, parse: (item: unknown) => T | null, max = 50): T[] | null => {
  if (!Array.isArray(value) || value.length > max) return null
  const output: T[] = []
  for (const item of value) { const parsed = parse(item); if (parsed === null) return null; output.push(parsed) }
  return output
}

const parseField = (value: unknown): TemplateField | null => {
  if (!isRecord(value) || !exact(value, ['displayName', 'uri']) || !isString(value.displayName, 1, 255) || !isString(value.uri, 1, 2000)) return null
  try { const uri = new URL(value.uri); if (uri.protocol !== 'https:' || uri.username || uri.password) return null } catch { return null }
  return { displayName: value.displayName, uri: value.uri }
}
const parseActions = (value: unknown) => listOf(value, item => isEnum(item, actions) ? item : null, actions.length)
const parseResource = (value: unknown): ManagedResourceView | null => {
  if (!isRecord(value) || !exact(value, ['resourceId', 'originOperationId', 'lifecycle', 'imageDigest']) || !isChannelAdminUuid(value.resourceId) || !isChannelAdminUuid(value.originOperationId) || !isEnum(value.lifecycle, ['candidate', 'applied', 'old', 'cleanup_required', 'deleted', 'released']) || !sha256(value.imageDigest)) return null
  return value as ManagedResourceView
}
const parseObservationValue = (value: unknown): ObservationView | null => {
  if (!isRecord(value) || !exact(value, ['kind', 'observedAt', 'fingerprint', 'managedResourceId']) || !isEnum(value.kind, ['default_none', 'managed_default', 'other_managed_default', 'external_default', 'unknown']) || !isChannelAdminDateTime(value.observedAt) || !sha256(value.fingerprint) || !uuidOrNull(value.managedResourceId)) return null
  const requiresResource = value.kind === 'managed_default' || value.kind === 'other_managed_default'
  if (requiresResource !== (value.managedResourceId !== null)) return null
  return value as ObservationView
}
const parseOperationValue = (value: unknown): OperationView | null => {
  if (!isRecord(value) || !exact(value, ['operationId', 'kind', 'status', 'stage', 'result', 'subjectOperationId', 'targetResourceId', 'acceptedAt', 'completedAt', 'nextAllowedActions'])) return null
  const nextAllowedActions = parseActions(value.nextAllowedActions)
  if (!isChannelAdminUuid(value.operationId) || !isEnum(value.kind, operationKinds) || !isEnum(value.status, operationStatuses) || !(value.stage === null || isEnum(value.stage, operationStages)) || !isEnum(value.result, resultCodes) || !uuidOrNull(value.subjectOperationId) || !uuidOrNull(value.targetResourceId) || !isChannelAdminDateTime(value.acceptedAt) || !dateOrNull(value.completedAt) || nextAllowedActions === null) return null
  return { ...(value as Omit<OperationView, 'nextAllowedActions'>), nextAllowedActions }
}

export function parseOperation(value: unknown): Parsed<OperationView> {
  const parsed = parseOperationValue(value); return parsed === null ? protocolError() : { ok: true, value: parsed }
}

export function parseTemplates(value: unknown): Parsed<TemplateDescriptor[]> {
  if (!isRecord(value) || !exact(value, ['items'])) return protocolError()
  const items = listOf(value.items, candidate => {
    if (!isRecord(candidate) || !exact(candidate, ['templateId', 'version', 'displayName', 'canvas', 'areas', 'requiredFields', 'limits']) || !isString(candidate.templateId, 1, 64) || !/^[a-z0-9][a-z0-9-]*$/.test(candidate.templateId) || !isInt(candidate.version, 1, 1000) || !isString(candidate.displayName, 1, 100)) return null
    if (!isRecord(candidate.canvas) || !exact(candidate.canvas, ['width', 'height']) || !isInt(candidate.canvas.width, 1, 10000) || !isInt(candidate.canvas.height, 1, 10000)) return null
    if (!isRecord(candidate.limits) || !exact(candidate.limits, ['displayName', 'uri']) || !isInt(candidate.limits.displayName, 1, 255) || !isInt(candidate.limits.uri, 1, 2000)) return null
    const areas = listOf(candidate.areas, area => {
      if (!isRecord(area) || !exact(area, ['field', 'description', 'bounds']) || !isString(area.field, 1, 64) || !isString(area.description, 1, 255) || !isRecord(area.bounds) || !exact(area.bounds, ['x', 'y', 'width', 'height']) || !isInt(area.bounds.x) || !isInt(area.bounds.y) || !isInt(area.bounds.width, 1) || !isInt(area.bounds.height, 1)) return null
      return area as TemplateDescriptor['areas'][number]
    }, 20)
    const requiredFields = listOf(candidate.requiredFields, item => isString(item, 1, 64) ? item : null, 20)
    if (areas === null || areas.length === 0 || requiredFields === null || requiredFields.length === 0) return null
    return { ...(candidate as Omit<TemplateDescriptor, 'areas' | 'requiredFields'>), areas, requiredFields }
  }, 20)
  return items === null ? protocolError() : { ok: true, value: items }
}

export function parseRichMenuState(value: unknown): Parsed<RichMenuStateView> {
  if (!isRecord(value) || !exact(value, ['channelId', 'currentResource', 'blockingOperation', 'activeOperation', 'cleanupResources', 'latestObservation', 'historySummary', 'nextAllowedActions', 'mode', 'effectiveActions', 'unavailableReason']) || !isChannelAdminUuid(value.channelId)) return protocolError()
  const currentResource = value.currentResource === null ? null : parseResource(value.currentResource)
  const blockingOperation = value.blockingOperation === null ? null : parseOperationValue(value.blockingOperation)
  const activeOperation = value.activeOperation === null ? null : parseOperationValue(value.activeOperation)
  const cleanupResources = listOf(value.cleanupResources, parseResource, 50)
  const latestObservation = value.latestObservation === null ? null : parseObservationValue(value.latestObservation)
  const nextAllowedActions = parseActions(value.nextAllowedActions); const effectiveActions = parseActions(value.effectiveActions)
  if ((value.currentResource !== null && currentResource === null) || (value.blockingOperation !== null && blockingOperation === null) || (value.activeOperation !== null && activeOperation === null) || cleanupResources === null || (value.latestObservation !== null && latestObservation === null) || nextAllowedActions === null || effectiveActions === null || !isEnum(value.mode, ['read_only', 'recovery_only', 'enabled', 'unavailable']) || !(value.unavailableReason === null || isString(value.unavailableReason, 1, 64))) return protocolError()
  if (!isRecord(value.historySummary) || !exact(value.historySummary, ['totalCount', 'latestOperationId', 'latestStatus']) || !isInt(value.historySummary.totalCount) || !uuidOrNull(value.historySummary.latestOperationId) || !(value.historySummary.latestStatus === null || isEnum(value.historySummary.latestStatus, operationStatuses))) return protocolError()
  const latestPair = value.historySummary.latestOperationId !== null && value.historySummary.latestStatus !== null
  if (latestPair !== (value.historySummary.totalCount > 0) || value.mode === 'unavailable' && effectiveActions.length > 0) return protocolError()
  return { ok: true, value: { channelId: value.channelId, currentResource, blockingOperation, activeOperation, cleanupResources, latestObservation, historySummary: value.historySummary as RichMenuStateView['historySummary'], nextAllowedActions, mode: value.mode, effectiveActions, unavailableReason: value.unavailableReason } }
}

export function parsePreview(value: unknown): Parsed<PreviewView> {
  if (!isRecord(value) || !exact(value, ['channelId', 'channelLabel', 'templateId', 'templateVersion', 'fields', 'image', 'observation', 'warnings', 'confirmationToken', 'expiresAt']) || !isChannelAdminUuid(value.channelId) || !isString(value.channelLabel, 1, 255) || !isString(value.templateId, 1, 64) || !isInt(value.templateVersion, 1, 1000) || !isString(value.confirmationToken, 1, 4096) || !isChannelAdminDateTime(value.expiresAt)) return protocolError()
  const fields = listOf(value.fields, parseField, 20); const observation = parseObservationValue(value.observation)
  const warnings = listOf(value.warnings, item => isEnum(item, ['external_default_replaced', 'url_history_persisted', 'url_must_not_contain_secrets']) ? item : null, 3)
  if (fields === null || fields.length === 0 || observation === null || warnings === null || !isRecord(value.image) || !exact(value.image, ['contentType', 'width', 'height', 'digest', 'base64']) || value.image.contentType !== 'image/png' || !isInt(value.image.width, 1, 10000) || !isInt(value.image.height, 1, 10000) || !sha256(value.image.digest) || !isString(value.image.base64, 1, 20_000_000) || !/^[A-Za-z0-9+/]+={0,2}$/.test(value.image.base64)) return protocolError()
  return { ok: true, value: { ...(value as Omit<PreviewView, 'fields' | 'observation' | 'warnings'>), fields, observation, warnings } }
}

export function parseHistoryPage(value: unknown): Parsed<HistoryPageView> {
  if (!isRecord(value) || !exact(value, ['items', 'nextCursor', 'hasMore']) || !(value.nextCursor === null || isString(value.nextCursor, 1, 4096)) || typeof value.hasMore !== 'boolean' || value.hasMore !== (value.nextCursor !== null)) return protocolError()
  const items = listOf(value.items, candidate => {
    if (!isRecord(candidate) || !exact(candidate, ['operation', 'channelId', 'channelLabel', 'configuration', 'transitions', 'defaultRelation', 'cleanupRelation']) || !isChannelAdminUuid(candidate.channelId) || !isString(candidate.channelLabel, 1, 255)) return null
    const operation = parseOperationValue(candidate.operation)
    const transitions = listOf(candidate.transitions, item => isEnum(item, resultCodes) ? item : null, 50)
    let configuration: HistoryEntryView['configuration'] = null
    if (candidate.configuration !== null) {
      if (!isRecord(candidate.configuration) || !exact(candidate.configuration, ['templateId', 'templateVersion', 'fields']) || !isString(candidate.configuration.templateId, 1, 64) || !isInt(candidate.configuration.templateVersion, 1, 1000)) return null
      const fields = listOf(candidate.configuration.fields, parseField, 20); if (fields === null || fields.length === 0) return null
      configuration = { templateId: candidate.configuration.templateId, templateVersion: candidate.configuration.templateVersion, fields }
    }
    if (operation === null || transitions === null || !isEnum(candidate.defaultRelation, ['became_default', 'cleared_default', 'not_default', 'external_default_preserved', 'unknown']) || !isEnum(candidate.cleanupRelation, ['not_required', 'required', 'completed', 'unknown'])) return null
    return { operation, channelId: candidate.channelId, channelLabel: candidate.channelLabel, configuration, transitions, defaultRelation: candidate.defaultRelation, cleanupRelation: candidate.cleanupRelation }
  }, 50)
  return items === null ? protocolError() : { ok: true, value: { items, nextCursor: value.nextCursor, hasMore: value.hasMore } }
}

export function parseDeactivation(value: unknown): Parsed<DeactivationView | null> {
  if (value === null) return { ok: true, value: null }
  if (!isRecord(value) || !exact(value, ['channelId', 'channelActive', 'channelUpdatedAt', 'operationId', 'status', 'reason', 'subjectOperationId', 'recoveryOperationId', 'nextAction', 'acceptedAt', 'updatedAt', 'completedAt']) || !isChannelAdminUuid(value.channelId) || typeof value.channelActive !== 'boolean' || !isChannelAdminDateTime(value.channelUpdatedAt) || !isChannelAdminUuid(value.operationId) || !isEnum(value.status, ['checking', 'unlinking', 'confirmation_required', 'completed']) || !(value.reason === null || isString(value.reason, 1, 64)) || !uuidOrNull(value.subjectOperationId) || !uuidOrNull(value.recoveryOperationId) || !isEnum(value.nextAction, ['get_state', 'none', 'resolve_external_default_then_recheck', 'complete_cleanup_then_recheck', 'recheck']) || !isChannelAdminDateTime(value.acceptedAt) || !isChannelAdminDateTime(value.updatedAt) || !dateOrNull(value.completedAt)) return protocolError()
  return { ok: true, value: value as DeactivationView }
}

export function parseRichMenuError(value: unknown): Parsed<SafeApiError> {
  if (!isRecord(value) || !exact(value, ['error']) || !isRecord(value.error)) return protocolError()
  const keys = 'fields' in value.error ? ['code', 'nextAllowedActions', 'fields'] : ['code', 'nextAllowedActions']
  const next = parseActions(value.error.nextAllowedActions)
  if (!exact(value.error, keys) || !isEnum(value.error.code, resultCodes) || next === null) return protocolError()
  return { ok: true, value: { code: value.error.code, summary: '操作を完了できませんでした。' } }
}

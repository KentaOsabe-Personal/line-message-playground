import type { SafeApiError } from './authDto'
import type { ChannelAdminItem } from './channelAdminDto'
import type {
  CurrentContent,
  CurrentImage,
  CurrentMenuView,
  PreviewView,
  RichMenuAction,
  OperationView,
  OperationKind,
  TemplateDescriptor,
  TemplateField,
} from './richMenuAdminDto'

// 画像本体はhookだけが所有し、状態には採用済みObject URLだけを渡す。
export type CurrentDisplay = Omit<CurrentMenuView, 'current'> & {
  current:
    | Exclude<CurrentContent, { kind: 'managed' }>
    | {
        kind: 'managed'
        resourceId: string
        configuration: Extract<CurrentContent, { kind: 'managed' }>['configuration']
        image:
          | Exclude<CurrentImage, { status: 'available' }>
          | (Omit<Extract<CurrentImage, { status: 'available' }>, 'base64'> & { imageUrl: string })
      }
}
export type PreviewDisplay = Omit<PreviewView, 'image'> & {
  image: Omit<PreviewView['image'], 'base64'> & { imageUrl: string }
}
export type EditorDraft = {
  templateId: string
  templateVersion: number
  fields: Record<string, TemplateField>
}
export type EditorState =
  | { state: 'viewing'; reason?: 'configuration_unavailable' | 'template_unavailable' }
  | ({ state: 'editing'; error?: SafeApiError; pendingTemplate?: TemplateDescriptor } & EditorDraft)
  | ({ state: 'previewing'; generation: number } & EditorDraft)
  | ({ state: 'confirming'; preview: PreviewDisplay; channelRevision: string } & EditorDraft)
  | ({ state: 'preview_invalid'; reason: string } & EditorDraft)
export type CurrentReadState =
  | { state: 'loading'; generation: number }
  | { state: 'ready'; value: CurrentDisplay }
  | { state: 'failed'; error: SafeApiError }
  | { state: 'refresh_required'; reason: string }
export type CatalogState =
  | { state: 'not_loaded' }
  | { state: 'loading'; generation: number }
  | { state: 'ready'; templates: TemplateDescriptor[] }
  | { state: 'failed'; error: SafeApiError }
export type OperationRequest = {
  operationId: string
  kind: OperationKind
  subjectOperationId?: string
  targetResourceId?: string
}
export type OperationState =
  | { state: 'idle' }
  | { state: 'submitting'; request: OperationRequest }
  | { state: 'tracking'; operationId: string; operation?: OperationView }
  | { state: 'settled'; operation: OperationView }
  | { state: 'transport_unknown'; operationId: string; error: SafeApiError }
export type RichMenuAdminState = {
  operation: OperationState
  epoch: number
  channel: ChannelAdminItem | null
  current: CurrentReadState
  editor: EditorState
  editorGeneration: number
  catalog: CatalogState
}
export const initialRichMenuAdminState: RichMenuAdminState = {
  operation: { state: 'idle' },
  epoch: 0,
  channel: null,
  current: { state: 'refresh_required', reason: 'initial' },
  editor: { state: 'viewing' },
  editorGeneration: 0,
  catalog: { state: 'not_loaded' },
}
type Scoped = { epoch: number; channelId: string }
export type RichMenuAdminAction =
  | { type: 'channelSelected'; channel: ChannelAdminItem }
  | ({ type: 'channelUpdated'; channel: ChannelAdminItem } & Scoped)
  | ({ type: 'operationStarted'; request: OperationRequest; at: string } & Scoped)
  | ({ type: 'operationReceived'; operation: OperationView } & Scoped)
  | ({ type: 'operationTransportFailed'; operationId: string; error: SafeApiError } & Scoped)
  | ({ type: 'trackingResumed'; operationId: string } & Scoped)
  | { type: 'sessionInvalidated' | 'unmounted' | 'channelCleared' }
  | ({ type: 'currentStarted'; generation: number } & Scoped)
  | ({ type: 'currentSucceeded'; generation: number; value: CurrentDisplay } & Scoped)
  | ({ type: 'currentFailed'; generation: number; error: SafeApiError } & Scoped)
  | ({ type: 'refreshRequired'; reason: string } & Scoped)
  | ({ type: 'catalogStarted'; generation: number } & Scoped)
  | ({ type: 'catalogSucceeded'; generation: number; templates: TemplateDescriptor[] } & Scoped)
  | ({ type: 'catalogFailed'; generation: number; error: SafeApiError } & Scoped)
  | { type: 'beginEdit' | 'beginCreate' | 'beginRebuild' | 'cancelEdit' | 'backToEdit' }
  | { type: 'draftChanged'; fields: Record<string, TemplateField> }
  | { type: 'templateRequested'; templateId: string; templateVersion: number }
  | { type: 'templateConfirmed' | 'templateCancelled' }
  | { type: 'previewStarted'; generation: number }
  | ({
      type: 'previewSucceeded'
      generation: number
      preview: PreviewDisplay
      channelRevision: string
    } & Scoped)
  | ({ type: 'previewFailed'; generation: number; error: SafeApiError } & Scoped)
  | { type: 'previewExpired'; at: string }

const copyFields = (fields: Record<string, TemplateField>) =>
  Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, { ...field }]))
const draftOf = (editor: Exclude<EditorState, { state: 'viewing' }>): EditorDraft => ({
  templateId: editor.templateId,
  templateVersion: editor.templateVersion,
  fields: copyFields(editor.fields),
})
const emptyDraft = (template: TemplateDescriptor): EditorState => ({
  state: 'editing',
  templateId: template.templateId,
  templateVersion: template.version,
  fields: Object.fromEntries(
    template.areas.map(({ field }) => [field, { displayName: '', uri: '' }]),
  ),
})
const invalidateEditor = (editor: EditorState, reason: string): EditorState =>
  editor.state === 'viewing' ? editor : { state: 'preview_invalid', reason, ...draftOf(editor) }
const templatesOf = (state: RichMenuAdminState) =>
  state.catalog.state === 'ready' ? state.catalog.templates : []
export function allowedRichMenuActions(state: RichMenuAdminState): RichMenuAction[] {
  if (
    !state.channel?.active ||
    state.current.state !== 'ready' ||
    ['submitting', 'tracking', 'transport_unknown'].includes(state.operation.state)
  )
    return []
  const unresolved =
    state.operation.state === 'settled' &&
    ['unknown', 'cleanup_required', 'recovery_active'].includes(state.operation.operation.status)
  const { rich, current, deactivation } = state.current.value
  if (current.kind === 'inactive' || rich.mode === 'read_only' || rich.mode === 'unavailable')
    return []
  const pending = deactivation !== null && deactivation.status !== 'completed'
  return rich.effectiveActions.filter((action) =>
    unresolved ||
    pending ||
    rich.mode === 'recovery_only' ||
    rich.activeOperation ||
    rich.blockingOperation ||
    rich.latestObservation?.kind === 'unknown'
      ? ['get_state', 'recheck', 'cleanup', 'view_history'].includes(action)
      : true,
  )
}
const canEdit = (state: RichMenuAdminState) => allowedRichMenuActions(state).includes('new_preview')

const operationIdOf = (operation: OperationState): string | null =>
  operation.state === 'idle'
    ? null
    : operation.state === 'submitting'
      ? operation.request.operationId
      : operation.state === 'settled'
        ? operation.operation.operationId
        : operation.operationId
const referencedOperationIds = (value: CurrentDisplay): (string | undefined | null)[] => [
  value.rich.activeOperation?.operationId,
  value.rich.blockingOperation?.operationId,
  value.rich.historySummary.latestOperationId,
]
function canStartOperation(
  state: RichMenuAdminState,
  request: OperationRequest,
  at: string,
): boolean {
  if (state.current.state !== 'ready' || !allowedRichMenuActions(state).includes(request.kind))
    return false
  const { rich, recoveryTargets, deactivation } = state.current.value
  if (request.kind === 'apply')
    return (
      state.editor.state === 'confirming' &&
      state.editor.channelRevision === state.channel?.updatedAt &&
      Number.isFinite(Date.parse(at)) &&
      Date.parse(at) < Date.parse(state.editor.preview.expiresAt)
    )
  if (request.kind === 'unlink' || request.kind === 'release')
    return (
      request.targetResourceId === rich.currentResource?.resourceId && rich.currentResource !== null
    )
  const pending = deactivation !== null && deactivation.status !== 'completed'
  const intentSubjects = [deactivation?.subjectOperationId, deactivation?.recoveryOperationId]
  if (pending && !intentSubjects.includes(request.subjectOperationId)) return false
  if (request.kind === 'cleanup')
    return recoveryTargets.some(
      (t) =>
        t.subjectOperationId === request.subjectOperationId &&
        t.targetResourceId === request.targetResourceId,
    )
  return [rich.blockingOperation?.operationId, rich.activeOperation?.operationId].some(
    (id) => id !== undefined && id === request.subjectOperationId,
  )
}
export function transitionRichMenuAdmin(
  state: RichMenuAdminState,
  action: RichMenuAdminAction,
): RichMenuAdminState {
  if (
    action.type === 'sessionInvalidated' ||
    action.type === 'unmounted' ||
    action.type === 'channelCleared'
  )
    return { ...initialRichMenuAdminState, epoch: state.epoch + 1 }
  if (action.type === 'channelSelected')
    return { ...initialRichMenuAdminState, channel: { ...action.channel }, epoch: state.epoch + 1 }
  if (
    'epoch' in action &&
    (action.epoch !== state.epoch || action.channelId !== state.channel?.channelId)
  )
    return state
  if (state.channel === null) return state
  if (action.type === 'channelUpdated') {
    if (action.channel.channelId !== state.channel.channelId) return state
    const activeChanged = state.channel.active !== action.channel.active
    return {
      ...state,
      channel: { ...action.channel },
      current: { state: 'refresh_required', reason: 'channel_updated' },
      editor: activeChanged
        ? { state: 'viewing' }
        : invalidateEditor(state.editor, 'stale_channel'),
      editorGeneration: state.editorGeneration + 1,
    }
  }
  if (action.type === 'operationStarted') {
    if (!canStartOperation(state, action.request, action.at)) return state
    return {
      ...state,
      operation: { state: 'submitting', request: { ...action.request } },
      editor: { state: 'viewing' },
      editorGeneration: state.editorGeneration + 1,
      current: { state: 'refresh_required', reason: 'operation_started' },
    }
  }
  if (action.type === 'trackingResumed') {
    if (state.operation.state === 'submitting') return state
    const held = operationIdOf(state.operation)
    const referenced =
      state.current.state === 'ready' &&
      referencedOperationIds(state.current.value).includes(action.operationId)
    if (held !== action.operationId && !(state.operation.state === 'idle' && referenced))
      return state
    return { ...state, operation: { state: 'tracking', operationId: action.operationId } }
  }
  if (action.type === 'operationReceived') {
    if (
      operationIdOf(state.operation) !== action.operation.operationId ||
      state.operation.state === 'idle' ||
      state.operation.state === 'settled'
    )
      return state
    const operation = action.operation
    if (state.operation.state === 'submitting' && operation.kind !== state.operation.request.kind)
      return state
    const processing = ['accepted', 'processing', 'recovery_active'].includes(operation.status)
    return {
      ...state,
      operation: processing
        ? { state: 'tracking', operationId: operation.operationId, operation }
        : { state: 'settled', operation },
      current: {
        state: 'refresh_required',
        reason: processing ? 'operation_tracking' : 'operation_settled',
      },
    }
  }
  if (action.type === 'operationTransportFailed') {
    if (
      state.operation.state === 'settled' ||
      operationIdOf(state.operation) !== action.operationId
    )
      return state
    return {
      ...state,
      operation: {
        state: 'transport_unknown',
        operationId: action.operationId,
        error: action.error,
      },
    }
  }

  if (action.type === 'currentStarted')
    return {
      ...state,
      current: { state: 'loading', generation: action.generation },
      editor: invalidateEditor(state.editor, 'current_changed'),
      editorGeneration: state.editorGeneration + 1,
    }
  if (action.type === 'currentSucceeded' || action.type === 'currentFailed') {
    if (state.current.state !== 'loading' || state.current.generation !== action.generation)
      return state
    if (action.type === 'currentFailed')
      return { ...state, current: { state: 'failed', error: action.error } }
    if (
      action.value.channelId !== state.channel.channelId ||
      action.value.channelRevision !== state.channel.updatedAt
    )
      return {
        ...state,
        current: { state: 'refresh_required', reason: 'stale_channel' },
      }
    return {
      ...state,
      current: { state: 'ready', value: action.value },
      ...(!state.channel.active || action.value.current.kind === 'inactive'
        ? { editor: { state: 'viewing' as const } }
        : {}),
    }
  }
  if (action.type === 'refreshRequired')
    return {
      ...state,
      current: { state: 'refresh_required', reason: action.reason },
      editor: invalidateEditor(state.editor, action.reason),
      editorGeneration: state.editorGeneration + 1,
    }
  if (action.type === 'catalogStarted')
    return { ...state, catalog: { state: 'loading', generation: action.generation } }
  if (action.type === 'catalogSucceeded' || action.type === 'catalogFailed') {
    if (state.catalog.state !== 'loading' || state.catalog.generation !== action.generation)
      return state
    return {
      ...state,
      catalog:
        action.type === 'catalogSucceeded'
          ? { state: 'ready', templates: action.templates }
          : { state: 'failed', error: action.error },
    }
  }
  if (action.type === 'cancelEdit')
    return { ...state, editor: { state: 'viewing' }, editorGeneration: state.editorGeneration + 1 }
  if (action.type === 'backToEdit' && state.editor.state !== 'viewing')
    return {
      ...state,
      editor: { state: 'editing', ...draftOf(state.editor) },
      editorGeneration: state.editorGeneration + 1,
    }
  if (action.type === 'previewExpired') {
    if (
      state.editor.state !== 'confirming' ||
      Date.parse(action.at) < Date.parse(state.editor.preview.expiresAt)
    )
      return state
    return {
      ...state,
      editor: invalidateEditor(state.editor, 'preview_expired'),
      editorGeneration: state.editorGeneration + 1,
    }
  }
  if (!canEdit(state)) return state
  if (action.type === 'beginEdit') {
    if (state.current.state !== 'ready' || state.current.value.current.kind !== 'managed')
      return state
    const config = state.current.value.current.configuration
    if (config.status !== 'available')
      return { ...state, editor: { state: 'viewing', reason: 'configuration_unavailable' } }
    const template = templatesOf(state).find(
      (t) => t.templateId === config.templateId && t.version === config.templateVersion,
    )
    if (!template || config.editability !== 'editable')
      return { ...state, editor: { state: 'viewing', reason: 'template_unavailable' } }
    if (template.areas.length !== config.fields.length)
      return { ...state, editor: { state: 'viewing', reason: 'configuration_unavailable' } }
    return {
      ...state,
      editorGeneration: state.editorGeneration + 1,
      editor: {
        state: 'editing',
        templateId: template.templateId,
        templateVersion: template.version,
        fields: Object.fromEntries(
          template.areas.map(({ field }, i) => [field, { ...config.fields[i] }]),
        ),
      },
    }
  }
  if (action.type === 'beginCreate' || action.type === 'beginRebuild') {
    if (
      state.current.state !== 'ready' ||
      (action.type === 'beginCreate' && state.current.value.current.kind !== 'unset')
    )
      return state
    const template = templatesOf(state)[0]
    return template
      ? { ...state, editor: emptyDraft(template), editorGeneration: state.editorGeneration + 1 }
      : state
  }
  if (state.editor.state === 'viewing') return state
  if (action.type === 'draftChanged') {
    if (
      Object.keys(state.editor.fields).length !== Object.keys(action.fields).length ||
      Object.keys(state.editor.fields).some((key) => !(key in action.fields))
    )
      return state
    return {
      ...state,
      editor: { state: 'editing', ...draftOf(state.editor), fields: copyFields(action.fields) },
      editorGeneration: state.editorGeneration + 1,
    }
  }
  if (action.type === 'templateRequested') {
    const template = templatesOf(state).find(
      (t) => t.templateId === action.templateId && t.version === action.templateVersion,
    )
    if (
      !template ||
      (template.templateId === state.editor.templateId &&
        template.version === state.editor.templateVersion)
    )
      return state
    const hasInput = Object.values(state.editor.fields).some(
      (f) => f.displayName !== '' || f.uri !== '',
    )
    return {
      ...state,
      editor: hasInput
        ? { state: 'editing', ...draftOf(state.editor), pendingTemplate: template }
        : emptyDraft(template),
      editorGeneration: state.editorGeneration + 1,
    }
  }
  if (action.type === 'templateConfirmed' || action.type === 'templateCancelled') {
    if (state.editor.state !== 'editing' || !state.editor.pendingTemplate) return state
    return {
      ...state,
      editor:
        action.type === 'templateConfirmed'
          ? emptyDraft(state.editor.pendingTemplate)
          : { state: 'editing', ...draftOf(state.editor) },
      editorGeneration: state.editorGeneration + 1,
    }
  }
  if (action.type === 'previewStarted') {
    if (
      action.generation !== state.editorGeneration ||
      state.editor.state === 'previewing' ||
      (state.editor.state === 'editing' && state.editor.pendingTemplate)
    )
      return state
    return {
      ...state,
      editor: { state: 'previewing', generation: action.generation, ...draftOf(state.editor) },
    }
  }
  if (action.type === 'previewSucceeded' || action.type === 'previewFailed') {
    if (
      state.editor.state !== 'previewing' ||
      state.editor.generation !== action.generation ||
      state.editorGeneration !== action.generation
    )
      return state
    if (action.type === 'previewFailed')
      return {
        ...state,
        editor: { state: 'editing', ...draftOf(state.editor), error: action.error },
      }
    const preview = action.preview
    const fields = Object.values(state.editor.fields)
    if (
      action.channelRevision !== state.channel.updatedAt ||
      preview.channelId !== state.channel.channelId ||
      preview.templateId !== state.editor.templateId ||
      preview.templateVersion !== state.editor.templateVersion ||
      JSON.stringify(preview.fields) !== JSON.stringify(fields)
    )
      return { ...state, editor: invalidateEditor(state.editor, 'preview_changed') }
    return {
      ...state,
      editor: {
        state: 'confirming',
        ...draftOf(state.editor),
        preview,
        channelRevision: action.channelRevision,
      },
    }
  }
  return state
}

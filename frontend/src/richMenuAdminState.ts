import type { SafeApiError } from './authDto'
import type { ChannelAdminItem } from './channelAdminDto'
import type { DeactivationView, HistoryPageView, PreviewView, RichMenuStateView, TemplateDescriptor, TemplateField } from './richMenuAdminDto'

export type EditorDraft = { templateId: string; templateVersion: number; fields: Record<string, TemplateField> }
export type EditorState =
  | { state: 'empty' }
  | ({ state: 'dirty' } & EditorDraft)
  | ({ state: 'previewing'; generation: number } & EditorDraft)
  | ({ state: 'preview_valid'; generation: number; confirmationToken: string; imageUrl: string; expiresAt: string; channelRevision: string; preview?: PreviewView } & EditorDraft)
  | ({ state: 'preview_invalid'; reason: 'input_changed' | 'template_changed' | 'revision_changed' } & EditorDraft)
  | ({ state: 'preview_expired' } & EditorDraft)

export type RichMenuAdminLoaded = {
  channel: ChannelAdminItem
  rich: RichMenuStateView
  history: HistoryPageView
  templates: TemplateDescriptor[]
  deactivation: DeactivationView | null
}
export type RichMenuAdminState =
  | { state: 'idle' }
  | { state: 'loading'; generation: number }
  | ({ state: 'ready'; editor: EditorState } & RichMenuAdminLoaded)
  | ({ state: 'read_only'; editor: { state: 'empty' } } & RichMenuAdminLoaded)
  | { state: 'refresh_required'; reason: 'stale_channel' | 'unknown_result' | 'protocol_error' }
  | { state: 'load_failed'; error: SafeApiError }

export type RichMenuAdminAction =
  | { type: 'loadStarted'; generation: number }
  | { type: 'loadSucceeded'; generation: number; value: RichMenuAdminLoaded }
  | { type: 'loadFailed'; generation: number; error: SafeApiError }
  | { type: 'refreshRequired'; reason: 'stale_channel' | 'unknown_result' | 'protocol_error' }
  | ({ type: 'draftChanged' } & EditorDraft)
  | { type: 'templateChanged'; templateId: string; templateVersion: number }
  | { type: 'previewStarted'; generation: number }
  | { type: 'previewSucceeded'; generation: number; confirmationToken: string; imageUrl: string; expiresAt: string; channelRevision: string; preview?: PreviewView }
  | { type: 'channelRevisionChanged'; channelRevision: string }
  | { type: 'previewExpired'; at: string }
  | { type: 'editorCleared' }
  | { type: 'sessionInvalidated' | 'unmounted' }

export const initialRichMenuAdminState: RichMenuAdminState = Object.freeze({ state: 'idle' })
const isLoaded = (state: RichMenuAdminState): state is Extract<RichMenuAdminState, { state: 'ready' | 'read_only' }> => state.state === 'ready' || state.state === 'read_only'
const draftOf = (editor: EditorState): EditorDraft | null => editor.state === 'empty' ? null : { templateId: editor.templateId, templateVersion: editor.templateVersion, fields: { ...editor.fields } }
const invalidate = (state: Extract<RichMenuAdminState, { state: 'ready' }>, reason: 'input_changed' | 'template_changed' | 'revision_changed'): RichMenuAdminState => {
  const draft = draftOf(state.editor)
  return draft === null ? state : { ...state, editor: { state: 'preview_invalid', reason, ...draft } }
}

export function transitionRichMenuAdmin(state: RichMenuAdminState, action: RichMenuAdminAction): RichMenuAdminState {
  if (action.type === 'sessionInvalidated' || action.type === 'unmounted') return initialRichMenuAdminState
  if (action.type === 'loadStarted') return { state: 'loading', generation: action.generation }
  if (action.type === 'loadSucceeded') {
    if (state.state !== 'loading' || state.generation !== action.generation) return state
    const readOnly = !action.value.channel.active || action.value.rich.mode === 'read_only' || action.value.rich.mode === 'unavailable'
    return { state: readOnly ? 'read_only' : 'ready', ...action.value, editor: { state: 'empty' } }
  }
  if (action.type === 'loadFailed') {
    if (state.state !== 'loading' || state.generation !== action.generation) return state
    return { state: 'load_failed', error: { ...action.error } }
  }
  if (action.type === 'refreshRequired') return { state: 'refresh_required', reason: action.reason }
  if (!isLoaded(state) || state.state === 'read_only') return state
  if (action.type === 'editorCleared') return { ...state, editor: { state: 'empty' } }
  if (action.type === 'draftChanged') return { ...state, editor: state.editor.state === 'preview_valid'
    ? { state: 'preview_invalid', reason: 'input_changed', templateId: action.templateId, templateVersion: action.templateVersion, fields: { ...action.fields } }
    : { state: 'dirty', templateId: action.templateId, templateVersion: action.templateVersion, fields: { ...action.fields } } }
  if (action.type === 'templateChanged') {
    return { ...state, editor: { state: 'preview_invalid', reason: 'template_changed', templateId: action.templateId, templateVersion: action.templateVersion, fields: {} } }
  }
  if (action.type === 'previewStarted') {
    const draft = draftOf(state.editor); return draft === null ? state : { ...state, editor: { state: 'previewing', generation: action.generation, ...draft } }
  }
  if (action.type === 'previewSucceeded') {
    if (state.editor.state !== 'previewing' || state.editor.generation !== action.generation) return state
    const draft = draftOf(state.editor); if (draft === null || action.channelRevision !== state.channel.updatedAt) return invalidate(state, 'revision_changed')
    return { ...state, editor: { state: 'preview_valid', generation: action.generation, ...draft, confirmationToken: action.confirmationToken, imageUrl: action.imageUrl, expiresAt: action.expiresAt, channelRevision: action.channelRevision, ...(action.preview === undefined ? {} : { preview: action.preview }) } }
  }
  if (action.type === 'channelRevisionChanged') return state.editor.state === 'preview_valid' && action.channelRevision !== state.editor.channelRevision ? invalidate(state, 'revision_changed') : state
  if (action.type === 'previewExpired') {
    if (state.editor.state !== 'preview_valid' || new Date(action.at).getTime() < new Date(state.editor.expiresAt).getTime()) return state
    const draft = draftOf(state.editor); return draft === null ? state : { ...state, editor: { state: 'preview_expired', ...draft } }
  }
  return state
}

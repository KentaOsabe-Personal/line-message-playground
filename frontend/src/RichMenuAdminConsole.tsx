import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { createChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminApiClient } from './channelAdminApi'
import { createProtectedHttpClient } from './httpApi'
import { createRichMenuAdminApiClient } from './richMenuAdminApi'
import type { RichMenuAdminApiClient } from './richMenuAdminApi'
import RichMenuEditor from './RichMenuEditor'
import RichMenuPreview from './RichMenuPreview'
import RichMenuOperationPanel from './RichMenuOperationPanel'
import RichMenuRecoveryPanel from './RichMenuRecoveryPanel'
import RichMenuHistory from './RichMenuHistory'
import RichMenuStatePanel, { richMenuActionLabels } from './RichMenuStatePanel'
import { initialRichMenuAdminState, transitionRichMenuAdmin } from './richMenuAdminState'
import type { EditorDraft } from './richMenuAdminState'
import type { OperationKind, OperationView } from './richMenuAdminDto'

type Props = {
  channelId: string
  channelApi?: ChannelAdminApiClient
  richApi?: RichMenuAdminApiClient
  invalidated?: boolean
  onSessionInvalid?: () => void
}

export default function RichMenuAdminConsole({ channelId, channelApi: suppliedChannelApi, richApi: suppliedRichApi, invalidated = false, onSessionInvalid }: Props) {
  const [state, dispatch] = useReducer(transitionRichMenuAdmin, initialRichMenuAdminState)
  const generation = useRef(0)
  const previewGeneration = useRef(0)
  const operationGeneration = useRef(0)
  const operationLatch = useRef(false)
  const [operationBusy, setOperationBusy] = useState(false)
  const [operationResult, setOperationResult] = useState<OperationView | null>(null)
  const [operationKind, setOperationKind] = useState<OperationKind | null>(null)
  const [operationId, setOperationId] = useState<string | null>(null)
  const http = useMemo(() => createProtectedHttpClient({ onSessionInvalid }), [onSessionInvalid])
  const channelApi = useMemo(() => suppliedChannelApi ?? createChannelAdminApiClient(http), [suppliedChannelApi, http])
  const richApi = useMemo(() => suppliedRichApi ?? createRichMenuAdminApiClient(http), [suppliedRichApi, http])

  const load = useCallback(async (options: { allowDuringOperation?: boolean; preserveOperation?: boolean } = {}) => {
    if (operationLatch.current && !options.allowDuringOperation) return
    const current = ++generation.current
    previewGeneration.current += 1
    if (!options.preserveOperation) {
      operationGeneration.current += 1; operationLatch.current = false; setOperationBusy(false)
      setOperationResult(null); setOperationKind(null); setOperationId(null)
    }
    dispatch({ type: 'loadStarted', generation: current })
    try {
      const [channel, templates, rich, history, deactivation] = await Promise.all([
        channelApi.getChannel(channelId), richApi.listTemplates(), richApi.getState(channelId),
        richApi.getHistory(channelId), richApi.getDeactivation(channelId),
      ])
      const scoped = channel.channelId === channelId && rich.channelId === channelId &&
        history.items.every(item => item.channelId === channelId) &&
        (deactivation === null || deactivation.channelId === channelId)
      if (!scoped) {
        dispatch({ type: 'loadFailed', generation: current, error: { code: 'protocol_error', summary: '応答の対象を確認できません。' } })
        return
      }
      dispatch({ type: 'loadSucceeded', generation: current, value: { channel, templates, rich, history, deactivation } })
    } catch {
      dispatch({ type: 'loadFailed', generation: current, error: { code: 'load_failed', summary: '管理状態を取得できませんでした。' } })
    }
  }, [channelApi, channelId, richApi])

  useEffect(() => {
    if (invalidated) { generation.current += 1; previewGeneration.current += 1; operationGeneration.current += 1; dispatch({ type: 'sessionInvalidated' }); return }
    void load()
    return () => { generation.current += 1; previewGeneration.current += 1; operationGeneration.current += 1 }
  }, [invalidated, load])

  const editor = state.state === 'ready' ? state.editor : { state: 'empty' as const }
  const dirty = editor.state !== 'empty'
  const previewImageUrl = editor.state === 'preview_valid' ? editor.imageUrl : null

  useEffect(() => {
    if (!dirty || invalidated) return
    const handleBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [dirty, invalidated])

  useEffect(() => {
    if (previewImageUrl === null) return
    return () => URL.revokeObjectURL(previewImageUrl)
  }, [previewImageUrl])

  useEffect(() => {
    if (editor.state !== 'preview_valid') return
    const delay = Math.min(2_147_483_647, Math.max(0, new Date(editor.expiresAt).getTime() - Date.now()))
    const timeout = window.setTimeout(() => dispatch({ type: 'previewExpired', at: new Date().toISOString() }), delay)
    return () => window.clearTimeout(timeout)
  }, [editor])

  const createPreview = useCallback(async (draft: EditorDraft) => {
    if (state.state !== 'ready' || !state.rich.effectiveActions.includes('new_preview')) return
    const current = ++previewGeneration.current
    dispatch({ type: 'previewStarted', generation: current })
    try {
      const preview = await richApi.createPreview(channelId, { ...draft, channelRevision: state.channel.updatedAt })
      if (current !== previewGeneration.current) return
      if (preview.channelId !== channelId || preview.templateId !== draft.templateId || preview.templateVersion !== draft.templateVersion) {
        dispatch({ type: 'refreshRequired', reason: 'protocol_error' }); return
      }
      const bytes = Uint8Array.from(atob(preview.image.base64), character => character.charCodeAt(0))
      const imageUrl = URL.createObjectURL(new Blob([bytes], { type: preview.image.contentType }))
      dispatch({ type: 'previewSucceeded', generation: current, confirmationToken: preview.confirmationToken, imageUrl, expiresAt: preview.expiresAt, channelRevision: state.channel.updatedAt, preview })
    } catch {
      dispatch({ type: 'refreshRequired', reason: 'unknown_result' })
    }
  }, [channelId, richApi, state])

  const applyPreview = useCallback(async () => {
    if (operationLatch.current || state.state !== 'ready' || state.editor.state !== 'preview_valid' || !state.rich.effectiveActions.includes('apply')) return
    const startedOperationId = crypto.randomUUID()
    const currentOperationGeneration = ++operationGeneration.current
    setOperationKind('apply'); setOperationId(startedOperationId); setOperationResult(null)
    operationLatch.current = true; setOperationBusy(true)
    try {
      const result = await richApi.startOperation(channelId, {
        kind: 'apply', operationId: startedOperationId, channelRevision: state.channel.updatedAt,
        confirmationToken: state.editor.confirmationToken, templateId: state.editor.templateId,
        templateVersion: state.editor.templateVersion, fields: state.editor.fields,
      })
      if (currentOperationGeneration !== operationGeneration.current) return
      if (result.operationId !== startedOperationId || result.kind !== 'apply') { dispatch({ type: 'refreshRequired', reason: 'protocol_error' }); return }
      setOperationResult(result)
      await load({ allowDuringOperation: true, preserveOperation: true })
    } catch { dispatch({ type: 'refreshRequired', reason: 'unknown_result' }) }
    finally { operationLatch.current = false; setOperationBusy(false) }
  }, [channelId, load, richApi, state])

  const startRecovery = useCallback(async (kind: Extract<OperationKind, 'unlink' | 'release' | 'recheck' | 'cleanup'>, selectedTargetResourceId?: string) => {
    if (operationLatch.current || state.state !== 'ready' || !state.rich.effectiveActions.includes(kind)) return
    const startedOperationId = crypto.randomUUID()
    const currentOperationGeneration = ++operationGeneration.current
    const base = { operationId: startedOperationId, channelRevision: state.channel.updatedAt }
    const currentResourceId = state.rich.currentResource?.resourceId
    const subjectOperationId = (state.rich.activeOperation ?? state.rich.blockingOperation)?.operationId
    const cleanupResourceId = selectedTargetResourceId !== undefined && state.rich.cleanupResources.some(resource => resource.resourceId === selectedTargetResourceId) ? selectedTargetResourceId : undefined
    const input = kind === 'unlink' || kind === 'release'
      ? currentResourceId === undefined ? null : { ...base, kind, targetResourceId: currentResourceId }
      : kind === 'recheck'
        ? subjectOperationId === undefined ? null : { ...base, kind, subjectOperationId }
        : subjectOperationId === undefined || cleanupResourceId === undefined ? null : { ...base, kind, subjectOperationId, targetResourceId: cleanupResourceId }
    if (input === null) { dispatch({ type: 'refreshRequired', reason: 'protocol_error' }); return }
    setOperationKind(kind); setOperationId(startedOperationId); setOperationResult(null)
    operationLatch.current = true; setOperationBusy(true)
    try {
      const result = await richApi.startOperation(channelId, input)
      if (currentOperationGeneration !== operationGeneration.current) return
      if (result.operationId !== startedOperationId || result.kind !== kind) { dispatch({ type: 'refreshRequired', reason: 'protocol_error' }); return }
      setOperationResult(result)
      await load({ allowDuringOperation: true, preserveOperation: true })
    }
    catch { dispatch({ type: 'refreshRequired', reason: 'unknown_result' }) }
    finally { operationLatch.current = false; setOperationBusy(false) }
  }, [channelId, load, richApi, state])

  if (invalidated) return null
  if (state.state === 'idle' || state.state === 'loading') return <section aria-label="リッチメニュー管理"><p role="status">管理状態を読み込んでいます…</p></section>
  if (state.state === 'load_failed') return (
    <section aria-label="リッチメニュー管理"><div role="alert"><p>{state.error.summary}</p><button type="button" onClick={() => { void load() }}>最新状態を再取得</button></div></section>
  )
  if (state.state === 'refresh_required') return (
    <section aria-label="リッチメニュー管理"><div role="alert"><p>操作結果を確定できません。最新状態を再取得してください。</p><button type="button" onClick={() => { void load() }}>最新状態を再取得</button></div></section>
  )
  const readOnly = state.state === 'read_only'
  const previewPresentation = (() => {
    if (state.state !== 'ready' || state.editor.state !== 'preview_valid' || state.editor.preview === undefined) return null
    const preview = state.editor.preview
    return {
      preview,
      imageUrl: state.editor.imageUrl,
      templateName: state.templates.find(template => template.templateId === preview.templateId && template.version === preview.templateVersion)?.displayName ?? '組み込みテンプレート',
    }
  })()
  return (
    <section className="rich-menu-admin" aria-labelledby="rich-menu-admin-heading">
      <header><p className="eyebrow">Rich menu console</p><h2 id="rich-menu-admin-heading">{state.channel.label}</h2></header>
      <dl>
        <div><dt>チャネル状態</dt><dd>{state.channel.active ? '有効' : '無効'}</dd></div>
        <div><dt>提供モード</dt><dd>{readOnly ? '読取専用' : state.rich.mode}</dd></div>
        <div><dt>更新時点</dt><dd>{new Date(state.channel.updatedAt).toLocaleString('ja-JP')}</dd></div>
        <div><dt>履歴件数</dt><dd>{state.rich.historySummary.totalCount}</dd></div>
      </dl>
      {state.deactivation !== null && <p>無効化状態: {state.deactivation.status}</p>}
      <RichMenuStatePanel state={state.rich} readOnly={readOnly} />
      {!readOnly && (
        <div aria-label="許可された操作">
          <h3>許可された操作</h3>
          {state.rich.effectiveActions.length === 0
            ? <p>現在実行できる操作はありません。</p>
            : <ul>{state.rich.effectiveActions.map(action => <li key={action}>{richMenuActionLabels[action]}</li>)}</ul>}
        </div>
      )}
      {readOnly && <p role="status">このチャネルは保存済み状態だけを表示する読取専用です。</p>}
      {!readOnly && state.rich.effectiveActions.includes('new_preview') && (
        <RichMenuEditor
          templates={state.templates}
          draft={state.editor.state === 'empty' ? null : { templateId: state.editor.templateId, templateVersion: state.editor.templateVersion, fields: state.editor.fields }}
          onDraftChange={draft => { previewGeneration.current += 1; dispatch({ type: 'draftChanged', ...draft }) }}
          onTemplateChange={template => { previewGeneration.current += 1; dispatch({ type: 'templateChanged', templateId: template.templateId, templateVersion: template.version }) }}
          onPreview={draft => { void createPreview(draft) }}
        />
      )}
      {!readOnly && previewPresentation !== null && (
        <><RichMenuPreview preview={previewPresentation.preview} imageUrl={previewPresentation.imageUrl} templateName={previewPresentation.templateName} />
        {state.rich.effectiveActions.includes('apply') && <RichMenuOperationPanel
          channelLabel={state.channel.label} preview={previewPresentation.preview}
          currentDefault={state.rich.latestObservation?.kind ?? '未観測'} busy={operationBusy}
          result={operationKind === 'apply' ? operationResult : null} onApply={() => { void applyPreview() }}
        />}</>
      )}
      {!readOnly && state.editor.state === 'preview_invalid' && <p role="alert">以前のプレビューは適用できません。新しいプレビューを生成してください。</p>}
      {!readOnly && state.editor.state === 'preview_expired' && <p role="alert">プレビューは期限切れです。新しいプレビューを生成してください。</p>}
      {!readOnly && <RichMenuRecoveryPanel channelLabel={state.channel.label} actions={state.rich.effectiveActions}
        currentResource={state.rich.currentResource} cleanupResources={state.rich.cleanupResources}
        subjectOperation={state.rich.activeOperation ?? state.rich.blockingOperation} observation={state.rich.latestObservation}
        busy={operationBusy} result={operationKind !== null && operationKind !== 'apply' ? operationResult : null}
        onStart={(kind, targetResourceId) => { void startRecovery(kind, targetResourceId) }} />}
      {operationBusy && operationId !== null && <p role="status">操作 {operationId} を開始しています。競合する操作は実行できません。</p>}
      {operationResult !== null && <section className="panel" aria-label="保存済み操作結果"><h3>保存済み操作結果</h3><p>{operationResult.kind}: {operationResult.operationId}</p><p>段階: {operationResult.stage ?? '完了'} / 状態: {operationResult.status}（{operationResult.result}）</p><p>次の明示操作: {operationResult.nextAllowedActions.length === 0 ? 'なし' : operationResult.nextAllowedActions.map(action => richMenuActionLabels[action]).join('、')}</p></section>}
      <RichMenuHistory key={`${state.channel.updatedAt}-${state.rich.historySummary.latestOperationId ?? 'empty'}`} initialPage={state.history} readOnly={readOnly} loadNext={cursor => richApi.getHistory(channelId, cursor)} />
      <button type="button" disabled={operationBusy} onClick={() => { void load() }}>{operationBusy ? '操作完了を待っています…' : '最新状態を再取得'}</button>
    </section>
  )
}

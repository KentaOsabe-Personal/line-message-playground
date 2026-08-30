import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { ChannelAdminApiError, createChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminApiClient } from './channelAdminApi'
import { createProtectedHttpClient } from './httpApi'
import { createRichMenuAdminApiClient, RichMenuAdminApiError } from './richMenuAdminApi'
import type { RichMenuAdminApiClient } from './richMenuAdminApi'
import RichMenuEditor from './RichMenuEditor'
import RichMenuPreview from './RichMenuPreview'
import RichMenuOperationPanel from './RichMenuOperationPanel'
import RichMenuRecoveryPanel from './RichMenuRecoveryPanel'
import RichMenuHistory from './RichMenuHistory'
import RichMenuStatePanel, { operationKindLabels, operationStatusLabels, richMenuActionLabels } from './RichMenuStatePanel'
import { initialRichMenuAdminState, transitionRichMenuAdmin } from './richMenuAdminState'
import type { EditorDraft } from './richMenuAdminState'
import type { OperationKind, OperationView } from './richMenuAdminDto'
import type { DeactivationView } from './richMenuAdminDto'
import type { DeletedChannel } from './channelAdminDto'

type Props = {
  channelId: string
  channelApi?: ChannelAdminApiClient
  richApi?: RichMenuAdminApiClient
  invalidated?: boolean
  readSignal?: AbortSignal
  onSessionInvalid?: () => void
  onBack?: () => void
  onDeleted?: (result: DeletedChannel) => void
}

export default function RichMenuAdminConsole({ channelId, channelApi: suppliedChannelApi, richApi: suppliedRichApi, invalidated = false, readSignal, onSessionInvalid, onBack, onDeleted }: Props) {
  const [state, dispatch] = useReducer(transitionRichMenuAdmin, initialRichMenuAdminState)
  const generation = useRef(0)
  const previewGeneration = useRef(0)
  const operationGeneration = useRef(0)
  const operationLatch = useRef(false)
  const [operationBusy, setOperationBusy] = useState(false)
  const [operationResult, setOperationResult] = useState<OperationView | null>(null)
  const [operationKind, setOperationKind] = useState<OperationKind | null>(null)
  const [operationId, setOperationId] = useState<string | null>(null)
  const lifecycleLatch = useRef(false)
  const [lifecycleBusy, setLifecycleBusy] = useState(false)
  const [deactivationConfirm, setDeactivationConfirm] = useState(false)
  const [deactivationResult, setDeactivationResult] = useState<DeactivationView | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const http = useMemo(() => createProtectedHttpClient({ onSessionInvalid }), [onSessionInvalid])
  const channelApi = useMemo(() => suppliedChannelApi ?? createChannelAdminApiClient(http), [suppliedChannelApi, http])
  const richApi = useMemo(() => suppliedRichApi ?? createRichMenuAdminApiClient(http), [suppliedRichApi, http])

  const load = useCallback(async (options: { allowDuringOperation?: boolean; preserveOperation?: boolean } = {}) => {
    if (operationLatch.current && !options.allowDuringOperation) return
    setDeactivationConfirm(false)
    setDeleteConfirm(false)
    setDeleteError(null)
    const current = ++generation.current
    previewGeneration.current += 1
    if (!options.preserveOperation) {
      operationGeneration.current += 1; operationLatch.current = false; setOperationBusy(false)
      setOperationResult(null); setOperationKind(null); setOperationId(null)
    }
    dispatch({ type: 'loadStarted', generation: current })
    try {
      const channelRead = readSignal === undefined ? channelApi.getChannel(channelId) : channelApi.getChannel(channelId, { signal: readSignal })
      const templatesRead = readSignal === undefined ? richApi.listTemplates() : richApi.listTemplates({ signal: readSignal })
      const stateRead = readSignal === undefined ? richApi.getState(channelId) : richApi.getState(channelId, { signal: readSignal })
      const historyRead = readSignal === undefined ? richApi.getHistory(channelId) : richApi.getHistory(channelId, undefined, { signal: readSignal })
      const deactivationRead = readSignal === undefined ? richApi.getDeactivation(channelId) : richApi.getDeactivation(channelId, { signal: readSignal })
      const [channel, templates, rich, history, deactivation] = await Promise.all([
        channelRead, templatesRead, stateRead, historyRead, deactivationRead,
      ])
      const scoped = channel.channelId === channelId && rich.channelId === channelId &&
        history.items.every(item => item.channelId === channelId) &&
        (deactivation === null || deactivation.channelId === channelId)
      if (!scoped) {
        dispatch({ type: 'loadFailed', generation: current, error: { code: 'protocol_error', summary: '応答の対象を確認できません。' } })
        onBack?.()
        return
      }
      dispatch({ type: 'loadSucceeded', generation: current, value: { channel, templates, rich, history, deactivation } })
    } catch (error) {
      const code = error instanceof ChannelAdminApiError || error instanceof RichMenuAdminApiError ? error.error.code : null
      if (code === 'authentication_required') {
        onSessionInvalid?.()
        return
      }
      if (code !== null && ['owner_operation_blocked', 'provider_mismatch', 'channel_not_found'].includes(code)) {
        onBack?.()
        return
      }
      dispatch({ type: 'loadFailed', generation: current, error: { code: 'load_failed', summary: '管理状態を取得できませんでした。' } })
    }
  }, [channelApi, channelId, onBack, onSessionInvalid, readSignal, richApi])

  useEffect(() => {
    if (invalidated) { generation.current += 1; previewGeneration.current += 1; operationGeneration.current += 1; dispatch({ type: 'sessionInvalidated' }); return }
    void load()
    return () => { generation.current += 1; previewGeneration.current += 1; operationGeneration.current += 1 }
  }, [invalidated, load])

  const editor = state.state === 'ready' ? state.editor : { state: 'empty' as const }
  const previewImageUrl = editor.state === 'preview_valid' ? editor.imageUrl : null

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
    if (operationLatch.current || lifecycleLatch.current || state.state !== 'ready' || state.deactivation !== null && state.deactivation.status !== 'completed' || state.editor.state !== 'preview_valid' || !state.rich.effectiveActions.includes('apply')) return
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
    if (operationLatch.current || lifecycleLatch.current || state.state !== 'ready' || state.deactivation !== null && state.deactivation.status !== 'completed' || !state.rich.effectiveActions.includes(kind)) return
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

  const startDeactivation = useCallback(async () => {
    if (lifecycleLatch.current || operationLatch.current || state.state !== 'ready' || !state.channel.active || state.deactivation !== null && state.deactivation.status !== 'completed') return
    lifecycleLatch.current = true; setLifecycleBusy(true); setDeactivationConfirm(false)
    try {
      const result = await richApi.startDeactivation(channelId, {
        operationId: crypto.randomUUID(), expectedUpdatedAt: state.channel.updatedAt,
      })
      setDeactivationResult(result)
      await load({ allowDuringOperation: true, preserveOperation: true })
    } catch { dispatch({ type: 'refreshRequired', reason: 'unknown_result' }) }
    finally { lifecycleLatch.current = false; setLifecycleBusy(false) }
  }, [channelId, load, richApi, state])

  const recheckDeactivation = useCallback(async () => {
    if (lifecycleLatch.current || operationLatch.current || state.state !== 'ready' || state.deactivation?.status !== 'confirmation_required') return
    lifecycleLatch.current = true; setLifecycleBusy(true)
    try {
      const result = await richApi.recheckDeactivation(channelId, {
        operationId: state.deactivation.operationId,
        recoveryOperationId: crypto.randomUUID(),
        expectedUpdatedAt: state.channel.updatedAt,
      })
      setDeactivationResult(result)
      await load({ allowDuringOperation: true, preserveOperation: true })
    } catch { dispatch({ type: 'refreshRequired', reason: 'unknown_result' }) }
    finally { lifecycleLatch.current = false; setLifecycleBusy(false) }
  }, [channelId, load, richApi, state])

  const reactivateChannel = useCallback(async () => {
    if (lifecycleLatch.current || operationLatch.current || (state.state !== 'read_only' && state.state !== 'ready') || state.channel.active) return
    lifecycleLatch.current = true; setLifecycleBusy(true)
    try {
      await channelApi.setState(channelId, { expectedUpdatedAt: state.channel.updatedAt, active: true })
      await load({ allowDuringOperation: true })
    } catch { dispatch({ type: 'refreshRequired', reason: 'unknown_result' }) }
    finally { lifecycleLatch.current = false; setLifecycleBusy(false) }
  }, [channelApi, channelId, load, state])

  const deleteChannel = useCallback(async () => {
    if (lifecycleLatch.current || operationLatch.current || (state.state !== 'read_only' && state.state !== 'ready') || state.deactivation !== null && state.deactivation.status !== 'completed') return
    lifecycleLatch.current = true; setLifecycleBusy(true); setDeleteError(null)
    try {
      const result = await channelApi.delete(channelId, state.channel.updatedAt)
      setDeleteConfirm(false)
      onDeleted?.(result)
    } catch (error) {
      const summary = error instanceof ChannelAdminApiError && error.error.code === 'channel_referenced'
        ? '削除を阻止する参照があります。表示された回復操作を完了し、最新状態を再取得してから再確認してください。'
        : '削除結果を確定できません。部分削除を成功として扱わず、最新状態を再取得してください。'
      setDeleteError(summary)
    } finally { lifecycleLatch.current = false; setLifecycleBusy(false) }
  }, [channelApi, channelId, onDeleted, state])

  if (invalidated) return null
  if (state.state === 'idle' || state.state === 'loading') return <section aria-label="リッチメニュー管理"><p role="status">管理状態を読み込んでいます…</p></section>
  if (state.state === 'load_failed') return (
    <section aria-label="リッチメニュー管理"><div role="alert"><p>{state.error.summary}</p><button type="button" onClick={() => { void load() }}>最新状態を再取得</button></div></section>
  )
  if (state.state === 'refresh_required') return (
    <section aria-label="リッチメニュー管理"><div role="alert"><p>操作結果を確定できません。最新状態を再取得してください。</p><button type="button" onClick={() => { void load() }}>最新状態を再取得</button></div></section>
  )
  const readOnly = state.state === 'read_only'
  const lifecycleLocked = state.deactivation !== null && state.deactivation.status !== 'completed'
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
      <header className="rich-menu-header"><div><p className="eyebrow">リッチメニュー管理</p><h2 id="rich-menu-admin-heading">{state.channel.label}</h2><p>メニューの内容を作成し、見た目を確認してからLINEに反映できます。</p></div>
        <span className={state.channel.active ? 'status active' : 'status inactive'}>{state.channel.active ? '利用中' : '停止中'}</span>
      </header>
      <dl className="rich-menu-overview">
        <div><dt>LINEの状態</dt><dd>{state.rich.latestObservation === null ? '未確認' : state.rich.latestObservation.kind === 'default_none' ? 'メニューなし' : state.rich.latestObservation.kind === 'managed_default' ? 'このアプリで反映中' : state.rich.latestObservation.kind === 'external_default' ? 'LINE側のメニューを反映中' : '確認が必要'}</dd></div>
        <div><dt>管理中のメニュー</dt><dd>{state.rich.currentResource === null ? 'なし' : 'あり'}</dd></div>
        <div><dt>操作履歴</dt><dd>{state.rich.historySummary.totalCount}件</dd></div>
      </dl>
      {state.deactivation !== null && <p>無効化状態: {state.deactivation.status}</p>}
      {!readOnly && !lifecycleLocked && <RichMenuRecoveryPanel channelLabel={state.channel.label} actions={state.rich.effectiveActions}
        currentResource={state.rich.currentResource} cleanupResources={state.rich.cleanupResources}
        subjectOperation={state.rich.activeOperation ?? state.rich.blockingOperation} observation={state.rich.latestObservation}
        busy={operationBusy || lifecycleBusy} result={operationKind !== null && operationKind !== 'apply' ? operationResult : null}
        onStart={(kind, targetResourceId) => { void startRecovery(kind, targetResourceId) }} />}
      <RichMenuStatePanel state={state.rich} readOnly={readOnly} />
      {readOnly && <p className="panel uncertain" role="status">このチャネルは停止中のため、保存済み状態だけを表示しています。</p>}
      <details className="admin-details">
        <summary>チャネルの停止・削除</summary>
      <section className="lifecycle-settings" aria-label="チャネルライフサイクル">
        <h3>チャネル設定</h3>
        {state.channel.richMenuRefreshRequired && <p role="status">再有効化後の最新チャネル状態とリッチメニュー実状態を取得しました。過去の入力やプレビューは復元していません。</p>}
        {state.channel.active && (state.deactivation === null || state.deactivation.status === 'completed') && !deactivationConfirm && (
          <button type="button" disabled={operationBusy || lifecycleBusy} onClick={() => setDeactivationConfirm(true)}>チャネルを無効化</button>
        )}
        {!state.channel.active && state.channel.credentialsState === 'configured' && (
          <button type="button" disabled={lifecycleBusy} onClick={() => { void reactivateChannel() }}>チャネルを再有効化</button>
        )}
        {!state.channel.active && state.channel.credentialsState === 'repair_required' && <p>再有効化にはチャネル一覧で資格情報を修復してください。</p>}
        {!deleteConfirm && <button type="button" className="danger" disabled={lifecycleBusy || operationBusy || lifecycleLocked} onClick={() => setDeleteConfirm(true)}>チャネルを物理削除</button>}
        {deleteConfirm && (
          <div role="dialog" aria-label="チャネル物理削除の確認">
            <h4>チャネル物理削除の確認</h4>
            <p role="alert">この削除は取り消せません。削除直前に全参照を再確認し、部分削除は成功として扱いません。</p>
            <p>現在のリッチメニュー参照: {state.rich.currentResource === null ? '管理対象なし' : state.rich.currentResource.lifecycle} / 操作: {(state.rich.activeOperation ?? state.rich.blockingOperation)?.status ?? 'なし'} / 後片付け: {state.rich.cleanupResources.length}件</p>
            <p>同じ完了単位で削除される確定済み履歴 {state.rich.historySummary.totalCount}件</p>
            <button type="button" className="danger" disabled={lifecycleBusy || lifecycleLocked} onClick={() => { void deleteChannel() }}>物理削除を確定</button>
            <button type="button" disabled={lifecycleBusy} onClick={() => setDeleteConfirm(false)}>キャンセル</button>
          </div>
        )}
        {deleteError !== null && <p role="alert">{deleteError}</p>}
        {deactivationConfirm && (
          <div role="dialog" aria-label="チャネル無効化の確認">
            <h4>チャネル無効化の確認</h4>
            <p>リッチメニュー管理状態: {state.rich.currentResource === null ? '管理対象なし' : state.rich.currentResource.lifecycle}</p>
            <p>LINE実状態: {state.rich.latestObservation?.kind ?? '未観測'}</p>
            <p>進行中操作: {(state.rich.activeOperation ?? state.rich.blockingOperation)?.status ?? 'なし'} / 後片付け: {state.rich.cleanupResources.length === 0 ? '不要' : `${state.rich.cleanupResources.length}件`}</p>
            <p>管理対象が現在既定なら、同じ無効化意図の中で解除を確認してから無効化します。</p>
            <button type="button" disabled={lifecycleBusy} onClick={() => { void startDeactivation() }}>無効化を確定</button>
            <button type="button" disabled={lifecycleBusy} onClick={() => setDeactivationConfirm(false)}>キャンセル</button>
          </div>
        )}
        {lifecycleBusy && <p role="status">同じ無効化意図を処理しています。競合操作は実行できません。</p>}
        {lifecycleLocked && <p role="status">無効化の確認中は競合する操作を実行できません。保存状態の取得、または表示された同じ無効化の再確認だけを行ってください。</p>}
        {(state.deactivation ?? deactivationResult)?.status === 'confirmation_required' && (
          <><p role="alert">{(state.deactivation ?? deactivationResult)?.reason === 'external_default'
            ? 'アプリ外の既定は変更しません。外部で既定を解消してから同じ無効化を再確認してください。'
            : '無効化を完了できません。必要な回復操作の後、同じ無効化を再確認してください。'}</p>
          <button type="button" disabled={lifecycleBusy || operationBusy} onClick={() => { void recheckDeactivation() }}>同じ無効化を再確認</button></>
        )}
      </section>
      </details>
      <div className="rich-menu-workflow">
      {!readOnly && !lifecycleLocked && state.rich.effectiveActions.includes('new_preview') && (
        <RichMenuEditor
          templates={state.templates}
          draft={state.editor.state === 'empty' ? null : { templateId: state.editor.templateId, templateVersion: state.editor.templateVersion, fields: state.editor.fields }}
          onDraftChange={draft => { previewGeneration.current += 1; dispatch({ type: 'draftChanged', ...draft }) }}
          onTemplateChange={template => { previewGeneration.current += 1; dispatch({ type: 'templateChanged', templateId: template.templateId, templateVersion: template.version }) }}
          onPreview={draft => { void createPreview(draft) }}
        />
      )}
      {!readOnly && !lifecycleLocked && previewPresentation !== null && (
        <><RichMenuPreview preview={previewPresentation.preview} imageUrl={previewPresentation.imageUrl} templateName={previewPresentation.templateName} />
        {state.rich.effectiveActions.includes('apply') && <RichMenuOperationPanel
          channelLabel={state.channel.label} preview={previewPresentation.preview}
          currentDefault={state.rich.latestObservation?.kind ?? '未観測'} busy={operationBusy || lifecycleBusy}
          result={operationKind === 'apply' ? operationResult : null} onApply={() => { void applyPreview() }}
        />}</>
      )}
      {!readOnly && !lifecycleLocked && state.editor.state === 'preview_invalid' && <p role="alert">以前のプレビューは適用できません。新しいプレビューを生成してください。</p>}
      {!readOnly && !lifecycleLocked && state.editor.state === 'preview_expired' && <p role="alert">プレビューは期限切れです。新しいプレビューを生成してください。</p>}
      </div>
      {operationBusy && operationId !== null && <p role="status">操作 {operationId} を開始しています。競合する操作は実行できません。</p>}
      {operationResult !== null && <section className={`operation-result operation-result-banner ${operationResult.status === 'succeeded' ? 'success' : operationResult.status === 'unknown' || operationResult.status === 'cleanup_required' ? 'uncertain' : ''}`} aria-label="保存済み操作結果" role="status">
        <div><div><p className="eyebrow">操作結果</p><h3>{operationKindLabels[operationResult.kind]}</h3></div><span className={`status ${operationResult.status === 'succeeded' ? 'active' : 'inactive'}`}>{operationStatusLabels[operationResult.status]}</span></div>
        <p>次にできること: {operationResult.nextAllowedActions.length === 0 ? 'ありません' : operationResult.nextAllowedActions.map(action => richMenuActionLabels[action]).join('、')}</p>
        <details className="technical-details"><summary>操作IDと技術情報</summary><code>{operationResult.operationId}</code><p>{operationResult.kind} / {operationResult.stage ?? 'completed'} / {operationResult.status}（{operationResult.result}）</p></details>
      </section>}
      <details className="history-details" open={state.rich.historySummary.latestStatus === 'unknown' || state.rich.historySummary.latestStatus === 'cleanup_required'}>
        <summary>操作履歴 <span>{state.rich.historySummary.totalCount}件</span></summary>
        <RichMenuHistory key={`${state.channel.updatedAt}-${state.rich.historySummary.latestOperationId ?? 'empty'}`} initialPage={state.history} readOnly={readOnly}
          loadNext={cursor => readSignal === undefined ? richApi.getHistory(channelId, cursor) : richApi.getHistory(channelId, cursor, { signal: readSignal })} />
      </details>
      <div className="refresh-row"><span>最終更新: {new Date(state.channel.updatedAt).toLocaleString('ja-JP')}</span><button type="button" className="secondary" disabled={operationBusy || lifecycleBusy} onClick={() => { void load() }}>{operationBusy || lifecycleBusy ? '操作完了を待っています…' : '最新状態を再取得'}</button></div>
    </section>
  )
}

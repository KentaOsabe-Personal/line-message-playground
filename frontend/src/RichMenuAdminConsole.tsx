import { useLayoutEffect, useMemo, useRef, useState } from 'react'

import { createChannelAdminApiClient, type ChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminItem, DeletedChannel } from './channelAdminDto'
import { createProtectedHttpClient } from './httpApi'
import { createRichMenuAdminApiClient, type RichMenuAdminApiClient } from './richMenuAdminApi'
import { allowedRichMenuActions } from './richMenuAdminState'
import RichMenuCurrentMenu from './RichMenuCurrentMenu'
import RichMenuDesignIcon from './RichMenuDesignIcon'
import RichMenuEditor from './RichMenuEditor'
import RichMenuOperationPanel from './RichMenuOperationPanel'
import RichMenuPreview from './RichMenuPreview'
import RichMenuRecoveryPanel from './RichMenuRecoveryPanel'
import RichMenuStatePanel from './RichMenuStatePanel'
import { useRichMenuAdmin } from './useRichMenuAdmin'

type Props = {
  channelId: string
  initialChannel: ChannelAdminItem
  channelApi?: ChannelAdminApiClient
  richApi?: RichMenuAdminApiClient
  invalidated?: boolean
  readSignal?: AbortSignal
  onSessionInvalid?: () => void
  onBack?: () => void
  onDeleted?: (result: DeletedChannel) => void
}

export default function RichMenuAdminConsole({
  channelId,
  initialChannel,
  channelApi: suppliedChannelApi,
  richApi: suppliedRichApi,
  invalidated,
  readSignal,
  onSessionInvalid,
  onBack,
  onDeleted,
}: Props) {
  const http = useMemo(() => createProtectedHttpClient(), [])
  const channelApi = useMemo(
    () => suppliedChannelApi ?? createChannelAdminApiClient(http),
    [suppliedChannelApi, http],
  )
  const richApi = useMemo(
    () => suppliedRichApi ?? createRichMenuAdminApiClient(http),
    [suppliedRichApi, http],
  )
  const controller = useRichMenuAdmin({
    channelId,
    initialChannel,
    channelApi,
    richApi,
    invalidated,
    readSignal,
    onSessionInvalid,
    onUnavailable: onBack,
    onDeleted,
  })
  const { state, lifecycle } = controller
  const [stopConfirmation, setStopConfirmation] = useState(false)
  const host = useRef<HTMLElement>(null)
  const primary = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const previousEditor = useRef(state.editor.state)
  const focused = useRef<HTMLElement | null>(null)
  const editor = state.editor
  useLayoutEffect(() => {
    const previous = previousEditor.current
    previousEditor.current = editor.state
    if (editor.state === 'confirming' && previous !== 'confirming')
      host.current?.querySelector<HTMLElement>('#rich-menu-preview-heading')?.focus()
    else if (editor.state === 'editing' && previous !== 'editing' && previous !== 'previewing')
      host.current?.querySelector<HTMLElement>('#rich-menu-editor-heading')?.focus()
    else if (editor.state === 'viewing' && previous !== 'viewing')
      (primary.current ?? heading.current)?.focus()
    else if (
      focused.current &&
      !focused.current.isConnected &&
      document.activeElement === document.body
    )
      heading.current?.focus()
  })
  if (!state.channel) return null
  const current = state.current.state === 'ready' ? state.current.value : null
  const actions = allowedRichMenuActions(state)
  const busy =
    lifecycle.busy ||
    ['submitting', 'tracking', 'transport_unknown'].includes(state.operation.state)
  const pending = current?.deactivation != null && current.deactivation.status !== 'completed'
  const result = state.operation.state === 'settled' ? state.operation.operation : null
  const viewing = editor.state === 'viewing'
  const templates = state.catalog.state === 'ready' ? state.catalog.templates : []
  const refresh = () => {
    if (busy || state.current.state === 'loading') return
    setStopConfirmation(false)
    void controller.refresh()
  }
  const cancel = () => controller.cancelEdit()
  const recoveryPanel =
    viewing && ['ready', 'loading'].includes(state.current.state) ? (
      <RichMenuRecoveryPanel
        primaryAction={
          <>
            {viewing &&
              actions.includes('new_preview') &&
              (current?.current.kind === 'managed' || current?.current.kind === 'unset') && (
                <button
                  ref={primary}
                  disabled={busy || state.catalog.state === 'loading'}
                  onClick={() => {
                    void (current?.current.kind === 'unset'
                      ? controller.beginCreate()
                      : controller.beginEdit())
                  }}
                >
                  <RichMenuDesignIcon kind="edit" />
                  {current?.current.kind === 'unset' ? 'メニューを作成' : '編集する'}
                </button>
              )}
          </>
        }
        refreshAction={
          <>
            {['ready', 'loading'].includes(state.current.state) && (
              <button
                className="secondary"
                disabled={busy}
                aria-disabled={busy || state.current.state === 'loading'}
                onClick={refresh}
              >
                <RichMenuDesignIcon kind="refresh" />
                更新
              </button>
            )}
          </>
        }
        channelLabel={state.channel.label}
        actions={actions}
        currentResource={current?.rich.currentResource ?? null}
        cleanupResources={current?.rich.cleanupResources ?? []}
        subjectOperation={current?.rich.activeOperation ?? current?.rich.blockingOperation ?? null}
        observation={current?.rich.latestObservation ?? null}
        busy={busy}
        result={null}
        recoveryTargets={current?.recoveryTargets ?? []}
        active={state.channel.active}
        mode={current?.rich.mode}
        deactivationPending={pending}
        onRecovery={(request) => {
          void controller.startRecovery(request)
        }}
      />
    ) : null
  const operationPanel =
    editor.state === 'confirming' || busy || state.operation.state !== 'idle' ? (
      <RichMenuOperationPanel
        embedded={editor.state === 'confirming'}
        onBack={editor.state === 'confirming' ? controller.backToEdit : undefined}
        onCancel={editor.state === 'confirming' ? cancel : undefined}
        channelLabel={state.channel.label}
        preview={editor.state === 'confirming' ? editor.preview : null}
        valid={actions.includes('apply')}
        busy={busy}
        result={result}
        operation={state.operation}
        onApply={() => {
          void controller.apply()
        }}
        onTrack={() => {
          void controller.trackOperation()
        }}
        refreshFailed={
          result !== null && ['failed', 'refresh_required'].includes(state.current.state)
        }
        onRefresh={refresh}
      />
    ) : null
  return (
    <section
      className="rich-menu-admin rich-menu-detail rich-menu-design"
      ref={host}
      onFocusCapture={(event) => {
        focused.current = event.target
      }}
    >
      <header className="rich-menu-header">
        <p className="rich-menu-breadcrumb">チャネル / リッチメニュー</p>
        <h2 ref={heading} tabIndex={-1}>
          {state.channel.label}
        </h2>
        <RichMenuStatePanel current={state.current} readOnly={!state.channel.active} />
      </header>
      <RichMenuCurrentMenu
        channelLabel={state.channel.label}
        current={state.current}
        collapsed={!viewing}
        actions={recoveryPanel}
        onRefresh={refresh}
        onImageError={() => {
          if (current?.current.kind === 'managed' && current.current.image.status === 'available')
            controller.currentImageFailed(current.current.image.imageUrl)
        }}
      />
      {state.catalog.state === 'loading' && <p role="status">テンプレートを確認しています…</p>}
      {state.catalog.state === 'failed' && (
        <p role="alert">テンプレートを取得できません。編集操作から再取得してください。</p>
      )}
      {viewing &&
        (editor.reason ||
          (actions.includes('new_preview') &&
            current?.current.kind !== 'managed' &&
            current?.current.kind !== 'unset')) && (
          <div>
            <p role="alert">
              同じテンプレートと版の全設定を利用できません。別のテンプレートで作り直せます。
            </p>
            <button
              disabled={busy}
              onClick={() => {
                void controller.beginRebuild()
              }}
            >
              別のテンプレートで作り直す
            </button>
          </div>
        )}
      {!viewing && editor.state !== 'confirming' && (
        <RichMenuEditor
          editing={current?.current.kind === 'managed'}
          templates={templates}
          draft={editor}
          busy={busy || editor.state === 'previewing'}
          fieldErrors={editor.state === 'editing' ? editor.error?.fields : undefined}
          onDraftChange={(draft) => controller.changeDraft(draft.fields)}
          onPreview={() => {
            void controller.preview()
          }}
          onTemplateChange={(template) => {
            controller.changeTemplate(template.templateId, template.version)
            controller.confirmTemplate()
          }}
          onCancel={cancel}
        />
      )}
      {editor.state === 'editing' && editor.error && <p role="alert">{editor.error.summary}</p>}
      {editor.state === 'preview_invalid' && (
        <div role="alert">
          <p>プレビューが失効しました。入力を保持しています。</p>
          <button
            disabled={busy}
            onClick={() => {
              void controller.preview()
            }}
          >
            プレビューを再生成
          </button>
        </div>
      )}
      {editor.state === 'confirming' && (
        <>
          <RichMenuPreview
            preview={editor.preview}
            imageUrl={editor.preview.image.imageUrl}
            templateName={
              templates.find(
                (t) => t.templateId === editor.templateId && t.version === editor.templateVersion,
              )?.displayName ?? editor.templateId
            }
            actions={operationPanel}
          />
        </>
      )}
      {editor.state !== 'confirming' && operationPanel}
      <details>
        <summary>チャネルの停止・削除</summary>
        <section className="lifecycle-settings" aria-label="チャネルライフサイクル">
          {state.channel.active && !pending && (
            <button disabled={busy || !current} onClick={() => setStopConfirmation(true)}>
              チャネルを無効化
            </button>
          )}
          {!state.channel.active && state.channel.credentialsState === 'configured' && (
            <button
              disabled={busy || !current}
              onClick={() => {
                void controller.reactivate()
              }}
            >
              チャネルを再有効化
            </button>
          )}
          {!state.channel.active && state.channel.credentialsState === 'repair_required' && (
            <p>再有効化にはチャネル管理で資格情報を修復してください。</p>
          )}
          {stopConfirmation && current && !pending && (
            <div role="group" aria-label="チャネル無効化の確認">
              <p>対象チャネル: {state.channel.label}</p>
              <p>管理対象が現在既定なら、適用解除と管理資源の削除を確認してから無効化します。</p>
              <button
                disabled={busy}
                onClick={() => {
                  setStopConfirmation(false)
                  void controller.startDeactivation()
                }}
              >
                無効化を確定
              </button>
              <button disabled={busy} onClick={() => setStopConfirmation(false)}>
                取消
              </button>
            </div>
          )}
          {pending && <p role="status">無効化の確認中は競合する操作を実行できません。</p>}
          {current?.deactivation?.status === 'confirmation_required' && (
            <>
              <p role="alert">
                {current.deactivation.nextAction === 'complete_cleanup_then_recheck'
                  ? '許可された後片付けを完了してから無効化を再確認してください。'
                  : '必要な回復操作や外部既定の解消後、同じ無効化を再確認してください。'}
              </p>
              <button
                disabled={
                  busy ||
                  ['none', 'get_state', 'complete_cleanup_then_recheck'].includes(
                    current.deactivation.nextAction,
                  )
                }
                onClick={() => {
                  void controller.recheckDeactivation()
                }}
              >
                同じ無効化を再確認
              </button>
            </>
          )}
          <button
            className="danger"
            disabled={busy || !current || pending}
            onClick={controller.requestDelete}
          >
            チャネルを物理削除
          </button>
          {lifecycle.deleteConfirmation && (
            <div role="group" aria-label="チャネル物理削除の確認">
              <p>対象チャネル: {lifecycle.deleteConfirmation.label}</p>
              <p>
                この削除は取り消せません。現在のリッチメニューなどの参照があれば削除できません。
              </p>
              <p>
                {lifecycle.deleteConfirmation.hasHistory
                  ? '適用履歴もチャネルと一緒に削除されます。'
                  : '削除される適用履歴はありません。'}
              </p>
              <button
                className="danger"
                disabled={busy}
                onClick={() => {
                  void controller.deleteChannel()
                }}
              >
                物理削除を確定
              </button>
              <button disabled={busy} onClick={controller.cancelDelete}>
                取消
              </button>
            </div>
          )}
          {lifecycle.busy && <p role="status">チャネルの操作を確認しています…</p>}
          {lifecycle.error && <p role="alert">{lifecycle.error.summary}</p>}
          {lifecycle.result?.status === 'completed' && (
            <p role="status">チャネルの無効化が完了しました。</p>
          )}
        </section>
      </details>
    </section>
  )
}

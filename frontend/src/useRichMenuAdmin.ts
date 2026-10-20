import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { SafeApiError } from './authDto'
import { ChannelAdminApiError, type ChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminItem, DeletedChannel } from './channelAdminDto'
import {
  RichMenuAdminApiError,
  type RichMenuAdminApiClient,
  type RichMenuOperationInput,
} from './richMenuAdminApi'
import type { DeactivationView, TemplateField, CurrentMenuView } from './richMenuAdminDto'
import {
  allowedRichMenuActions,
  initialRichMenuAdminState,
  transitionRichMenuAdmin,
  type CurrentDisplay,
  type RichMenuAdminAction,
  type RichMenuAdminState,
  type OperationRequest,
} from './richMenuAdminState'

export type RichMenuAdminOptions = {
  channelId: string
  initialChannel: ChannelAdminItem
  channelApi: ChannelAdminApiClient
  richApi: RichMenuAdminApiClient
  readSignal?: AbortSignal
  invalidated?: boolean
  onSessionInvalid?: () => void
  onUnavailable?: () => void
  onDeleted?: (result: DeletedChannel) => void
}
export type DeleteConfirmation = {
  channelId: string
  label: string
  revision: string
  hasHistory: boolean
}
type LifecycleState = {
  busy: boolean
  result: DeactivationView | null
  error: SafeApiError | null
  deleteConfirmation: DeleteConfirmation | null
}
const initialLifecycle: LifecycleState = {
  busy: false,
  result: null,
  error: null,
  deleteConfirmation: null,
}
export type RecoveryRequest =
  | { kind: 'unlink' | 'release'; targetResourceId: string }
  | { kind: 'recheck'; subjectOperationId: string }
  | { kind: 'cleanup'; subjectOperationId: string; targetResourceId: string }
const errorOf = (error: unknown): SafeApiError =>
  error instanceof ChannelAdminApiError || error instanceof RichMenuAdminApiError
    ? error.error
    : { code: 'network_error', summary: 'Backendに接続できません。' }
const imageUrl = (image: { base64: string; contentType: string }) =>
  URL.createObjectURL(
    new Blob([Uint8Array.from(atob(image.base64), (c) => c.charCodeAt(0))], {
      type: image.contentType,
    }),
  )

export function useRichMenuAdmin({
  channelId,
  initialChannel,
  channelApi,
  richApi,
  readSignal,
  invalidated = false,
  onSessionInvalid,
  onUnavailable,
  onDeleted,
}: RichMenuAdminOptions) {
  const callbacks = useRef({ onSessionInvalid, onUnavailable, onDeleted })
  useEffect(() => {
    callbacks.current = { onSessionInvalid, onUnavailable, onDeleted }
  }, [onSessionInvalid, onUnavailable, onDeleted])
  const [failedCurrentImage, setFailedCurrentImage] = useState<string | null>(null)
  const [state, dispatch] = useReducer(transitionRichMenuAdmin, initialRichMenuAdminState)
  const [lifecycle, setLifecycle] = useState(initialLifecycle)
  const lifecycleRef = useRef(initialLifecycle)
  const updateLifecycle = useCallback((patch: Partial<LifecycleState>) => {
    lifecycleRef.current = { ...lifecycleRef.current, ...patch }
    setLifecycle(lifecycleRef.current)
  }, [])
  const live = useRef(initialRichMenuAdminState)
  const route = useRef(0)
  const reads = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const mutationLatch = useRef(false)
  const trackingRead = useRef<AbortController | null>(null)
  const previewUrl = useRef<string | null>(null)
  const expiry = useRef<ReturnType<typeof setTimeout> | null>(null)
  const catalogs = useRef(0)
  const currentUrl = useRef<string | null>(null)
  const send = useCallback((action: RichMenuAdminAction) => {
    live.current = transitionRichMenuAdmin(live.current, action)
    dispatch(action)
    const current = live.current.current
    if (
      current.state !== 'ready' ||
      current.value.current.kind !== 'managed' ||
      current.value.current.image.status !== 'available' ||
      current.value.current.image.imageUrl !== currentUrl.current
    ) {
      if (currentUrl.current) URL.revokeObjectURL(currentUrl.current)
      currentUrl.current = null
    }
    const editor = live.current.editor
    if (editor.state !== 'confirming' || editor.preview.image.imageUrl !== previewUrl.current) {
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current)
      previewUrl.current = null
      if (expiry.current !== null) clearTimeout(expiry.current)
      expiry.current = null
    }
  }, [])
  const clearImage = useCallback(() => {
    if (currentUrl.current) URL.revokeObjectURL(currentUrl.current)
    currentUrl.current = null
    setFailedCurrentImage(null)
  }, [])
  const clear = useCallback(() => {
    route.current += 1
    controller.current?.abort()
    trackingRead.current?.abort()
    mutationLatch.current = false
    clearImage()
    send({ type: 'sessionInvalidated' })
    updateLifecycle(initialLifecycle)
  }, [clearImage, send, updateLifecycle])
  const handleAccess = useCallback(
    (error: SafeApiError) => {
      if (error.code === 'authentication_required') {
        clear()
        callbacks.current.onSessionInvalid?.()
        return true
      }
      if (
        [
          'owner_operation_blocked',
          'provider_mismatch',
          'channel_not_found',
          'owner_unavailable',
        ].includes(error.code)
      ) {
        clear()
        callbacks.current.onUnavailable?.()
        return true
      }
      return false
    },
    [clear],
  )
  const load = useCallback(
    async (refreshChannel: boolean) => {
      const channel = live.current.channel
      if (!channel || channel.channelId !== channelId || readSignal?.aborted) return
      const lifetime = route.current
      const generation = ++reads.current
      controller.current?.abort()
      const read = new AbortController()
      controller.current = read
      const valid = () =>
        lifetime === route.current &&
        generation === reads.current &&
        !read.signal.aborted &&
        live.current.channel?.channelId === channelId
      clearImage()
      updateLifecycle({ deleteConfirmation: null })
      const scope = { epoch: live.current.epoch, channelId }
      send({ type: 'currentStarted', generation, ...scope })
      try {
        const selected = refreshChannel
          ? await channelApi.getChannel(channelId, { signal: read.signal })
          : channel
        if (!valid()) return
        if (selected.channelId !== channelId) throw new Error('invalid channel')
        if (refreshChannel) {
          send({ type: 'channelUpdated', channel: selected, ...scope })
          send({ type: 'currentStarted', generation, ...scope })
        }
        const result: CurrentMenuView = await richApi.getCurrent(channelId, selected.updatedAt, {
          signal: read.signal,
        })
        if (!valid()) return
        if (result.channelId !== channelId || result.channelRevision !== selected.updatedAt) {
          send({ type: 'refreshRequired', reason: 'current_changed', ...scope })
          return
        }
        let value: CurrentDisplay
        if (result.current.kind === 'managed' && result.current.image.status === 'available') {
          const { base64, ...image } = result.current.image
          let displayImage: Extract<CurrentDisplay['current'], { kind: 'managed' }>['image']
          try {
            const url = imageUrl({ base64, contentType: image.contentType })
            currentUrl.current = url
            displayImage = { ...image, imageUrl: url }
          } catch {
            displayImage = { status: 'unavailable', reason: 'invalid' }
          }
          value = { ...result, current: { ...result.current, image: displayImage } }
        } else value = result as CurrentDisplay
        send({ type: 'currentSucceeded', generation, value, ...scope })
      } catch (error) {
        if (!valid()) return
        const safe = errorOf(error)
        if (handleAccess(safe) || safe.code === 'aborted') return
        if (['current_changed', 'stale_channel'].includes(safe.code))
          send({ type: 'refreshRequired', reason: safe.code, ...scope })
        else send({ type: 'currentFailed', generation, error: safe, ...scope })
      }
    },
    [channelApi, channelId, clearImage, handleAccess, readSignal, richApi, send, updateLifecycle],
  )
  useEffect(() => {
    if (invalidated || readSignal?.aborted || initialChannel.channelId !== channelId) {
      clear()
      return
    }
    send({ type: 'channelSelected', channel: initialChannel })
    const abort = () => clear()
    readSignal?.addEventListener('abort', abort, { once: true })
    void load(false)
    return () => {
      readSignal?.removeEventListener('abort', abort)
      clear()
    }
  }, [channelId, initialChannel, invalidated, readSignal, clear, load, send])
  const currentImageFailed = useCallback(
    (url: string) => {
      const current = live.current.current
      if (
        current.state !== 'ready' ||
        current.value.current.kind !== 'managed' ||
        current.value.current.image.status !== 'available' ||
        current.value.current.image.imageUrl !== url
      )
        return
      clearImage()
      setFailedCurrentImage(url)
    },
    [clearImage],
  )
  const trackOperation = useCallback(
    async (operationId?: string) => {
      const snapshot = live.current
      const held = snapshot.operation
      const id =
        operationId ??
        (held.state === 'idle'
          ? null
          : held.state === 'submitting'
            ? held.request.operationId
            : held.state === 'settled'
              ? held.operation.operationId
              : held.operationId)
      if (!id || !snapshot.channel) return
      const scope = { epoch: snapshot.epoch, channelId }
      send({ type: 'trackingResumed', operationId: id, ...scope })
      const after = live.current.operation
      if (after.state !== 'tracking' || after.operationId !== id) return
      trackingRead.current?.abort()
      const read = new AbortController()
      trackingRead.current = read
      const lifetime = route.current
      const valid = () =>
        route.current === lifetime &&
        !read.signal.aborted &&
        live.current.channel?.channelId === channelId
      try {
        const operation = await richApi.getOperation(id, { signal: read.signal })
        if (!valid()) return
        if (operation.operationId !== id) throw new Error('invalid operation')
        send({ type: 'operationReceived', operation, ...scope })
        if (live.current.operation.state === 'settled') await load(true)
      } catch (error) {
        if (!valid()) return
        const safe = errorOf(error)
        if (!handleAccess(safe))
          send({ type: 'operationTransportFailed', operationId: id, error: safe, ...scope })
      }
    },
    [channelId, richApi, send, handleAccess, load],
  )
  useEffect(() => {
    if (state.current.state !== 'ready' || state.operation.state !== 'idle') return
    const rich = state.current.value.rich
    const reference =
      rich.activeOperation?.operationId ??
      rich.blockingOperation?.operationId ??
      (['accepted', 'processing', 'unknown', 'recovery_active'].includes(
        rich.historySummary.latestStatus ?? '',
      )
        ? rich.historySummary.latestOperationId
        : null)
    if (reference) void trackOperation(reference)
  }, [state, trackOperation])
  const mutate = async (selection: RecoveryRequest | { kind: 'apply' }) => {
    if (mutationLatch.current) return
    const snapshot = live.current
    if (!snapshot.channel || snapshot.channel.channelId !== channelId) return
    const operationId = crypto.randomUUID()
    const request: OperationRequest = { ...selection, operationId }
    const scope = { epoch: snapshot.epoch, channelId }
    const at = new Date().toISOString()
    const next = transitionRichMenuAdmin(snapshot, {
      type: 'operationStarted',
      request,
      at,
      ...scope,
    })
    if (next === snapshot) return
    let input: RichMenuOperationInput
    if (selection.kind === 'apply') {
      if (snapshot.editor.state !== 'confirming') return
      input = {
        kind: 'apply',
        operationId,
        channelRevision: snapshot.channel.updatedAt,
        confirmationToken: snapshot.editor.preview.confirmationToken,
        templateId: snapshot.editor.templateId,
        templateVersion: snapshot.editor.templateVersion,
        fields: snapshot.editor.fields,
      }
    } else input = { ...selection, operationId, channelRevision: snapshot.channel.updatedAt }
    mutationLatch.current = true
    const lifetime = route.current
    send({ type: 'operationStarted', request, at, ...scope })
    const valid = () => lifetime === route.current && live.current.channel?.channelId === channelId
    try {
      const operation = await richApi.startOperation(channelId, input)
      if (!valid()) return
      if (operation.operationId !== operationId || operation.kind !== input.kind)
        throw new Error('invalid operation')
      send({ type: 'operationReceived', operation, ...scope })
      if (live.current.operation.state === 'settled') await load(true)
      else await trackOperation(operationId)
    } catch (error) {
      if (!valid()) return
      const safe = errorOf(error)
      if (handleAccess(safe)) return
      send({ type: 'operationTransportFailed', operationId, error: safe, ...scope })
      await trackOperation(operationId)
    } finally {
      if (valid()) mutationLatch.current = false
    }
  }
  const begin = async (type: 'beginEdit' | 'beginCreate' | 'beginRebuild') => {
    const snapshot = live.current
    if (mutationLatch.current) return
    if (
      !snapshot.channel ||
      snapshot.channel.channelId !== channelId ||
      !allowedRichMenuActions(snapshot).includes('new_preview')
    )
      return
    const lifetime = route.current
    const editorGeneration = snapshot.editorGeneration
    const generation = ++catalogs.current
    const scope = { epoch: snapshot.epoch, channelId }
    const valid = () =>
      route.current === lifetime &&
      live.current.channel?.channelId === channelId &&
      live.current.editorGeneration === editorGeneration &&
      catalogs.current === generation
    if (snapshot.catalog.state !== 'ready') {
      send({ type: 'catalogStarted', generation, ...scope })
      try {
        const templates = await richApi.listTemplates({ signal: controller.current?.signal })
        if (!valid()) return
        send({ type: 'catalogSucceeded', generation, templates, ...scope })
      } catch (error) {
        if (!valid()) return
        const safe = errorOf(error)
        if (!handleAccess(safe)) send({ type: 'catalogFailed', generation, error: safe, ...scope })
        return
      }
    }
    if (valid()) send({ type })
  }
  const preview = async () => {
    if (mutationLatch.current) return
    if (live.current.editor.state === 'preview_invalid') await load(true)
    const snapshot = live.current
    if (
      !snapshot.channel ||
      snapshot.channel.channelId !== channelId ||
      snapshot.editor.state === 'viewing' ||
      snapshot.editor.state === 'confirming' ||
      snapshot.editor.state === 'previewing'
    )
      return
    const generation = snapshot.editorGeneration
    const lifetime = route.current
    const scope = { epoch: snapshot.epoch, channelId }
    const revision = snapshot.channel.updatedAt
    const draft = {
      templateId: snapshot.editor.templateId,
      templateVersion: snapshot.editor.templateVersion,
      fields: snapshot.editor.fields,
    }
    send({ type: 'previewStarted', generation })
    if (live.current.editor.state !== 'previewing') return
    const valid = () =>
      lifetime === route.current &&
      live.current.editorGeneration === generation &&
      live.current.editor.state === 'previewing' &&
      live.current.channel?.channelId === channelId
    try {
      const result = await richApi.createPreview(channelId, { ...draft, channelRevision: revision })
      if (!valid()) return
      if (
        result.channelId !== channelId ||
        result.templateId !== draft.templateId ||
        result.templateVersion !== draft.templateVersion ||
        JSON.stringify(result.fields) !== JSON.stringify(Object.values(draft.fields)) ||
        Date.parse(result.expiresAt) <= Date.now()
      ) {
        send({ type: 'refreshRequired', reason: 'preview_changed', ...scope })
        return
      }
      const { base64, ...image } = result.image
      const url = imageUrl({ base64, contentType: image.contentType })
      previewUrl.current = url
      send({
        type: 'previewSucceeded',
        generation,
        preview: { ...result, image: { ...image, imageUrl: url } },
        channelRevision: revision,
        ...scope,
      })
      const accepted = live.current
      if (accepted.editor.state === 'confirming') {
        const expire = () => {
          send({ type: 'previewExpired', at: new Date().toISOString() })
          if (live.current.editor.state === 'confirming')
            expiry.current = setTimeout(
              expire,
              Math.min(2147483647, Math.max(0, Date.parse(result.expiresAt) - Date.now())),
            )
        }
        expiry.current = setTimeout(
          expire,
          Math.min(2147483647, Math.max(0, Date.parse(result.expiresAt) - Date.now())),
        )
      }
    } catch (error) {
      if (!valid()) return
      const safe = errorOf(error)
      if (handleAccess(safe)) return
      if (['stale_channel', 'current_changed', 'preview_expired'].includes(safe.code))
        send({ type: 'refreshRequired', reason: safe.code, ...scope })
      else send({ type: 'previewFailed', generation, error: safe, ...scope })
    }
  }
  const pendingDeactivation = () => {
    const value = live.current.current
    return (
      value.state === 'ready' &&
      value.value.deactivation !== null &&
      value.value.deactivation.status !== 'completed'
    )
  }
  const lifecycleChange = async (kind: 'stop' | 'recheck' | 'reactivate' | 'delete') => {
    if (mutationLatch.current) return
    const snapshot = live.current
    if (
      !snapshot.channel ||
      snapshot.channel.channelId !== channelId ||
      snapshot.current.state !== 'ready' ||
      ['submitting', 'tracking', 'transport_unknown'].includes(snapshot.operation.state)
    )
      return
    const selected = snapshot.channel
    const deactivation = snapshot.current.value.deactivation
    const pending = pendingDeactivation()
    if (kind === 'stop' && (!selected.active || pending)) return
    if (
      kind === 'recheck' &&
      (!selected.active ||
        !deactivation ||
        deactivation.status !== 'confirmation_required' ||
        deactivation.nextAction === 'none' ||
        deactivation.nextAction === 'get_state' ||
        deactivation.nextAction === 'complete_cleanup_then_recheck')
    )
      return
    if (kind === 'reactivate' && (selected.active || pending)) return
    if (kind === 'delete') {
      const confirmation = lifecycleRef.current.deleteConfirmation
      if (
        pending ||
        !confirmation ||
        confirmation.channelId !== channelId ||
        confirmation.revision !== selected.updatedAt
      )
        return
    }
    const lifetime = route.current
    const scope = { epoch: snapshot.epoch, channelId }
    const valid = () => lifetime === route.current && live.current.channel?.channelId === channelId
    mutationLatch.current = true
    updateLifecycle({ busy: true, error: null, deleteConfirmation: null })
    send({ type: 'cancelEdit' })
    send({ type: 'refreshRequired', reason: 'lifecycle_started', ...scope })
    try {
      if (kind === 'delete') {
        const deleted = await channelApi.delete(channelId, selected.updatedAt)
        if (!valid()) return
        if (deleted.channelId !== channelId) throw new Error('invalid channel')
        clear()
        callbacks.current.onDeleted?.(deleted)
        return
      }
      if (kind === 'reactivate') {
        const reactivated = await channelApi.setState(channelId, {
          active: true,
          expectedUpdatedAt: selected.updatedAt,
        })
        if (!valid()) return
        if (reactivated.channelId !== channelId) throw new Error('invalid channel')
      } else {
        const result =
          kind === 'stop'
            ? await richApi.startDeactivation(channelId, {
                operationId: crypto.randomUUID(),
                expectedUpdatedAt: selected.updatedAt,
              })
            : await richApi.recheckDeactivation(channelId, {
                operationId: deactivation!.operationId,
                recoveryOperationId: crypto.randomUUID(),
                expectedUpdatedAt: selected.updatedAt,
              })
        if (!valid()) return
        if (result.channelId !== channelId) throw new Error('invalid channel')
        updateLifecycle({ result })
      }
      await load(true)
    } catch (error) {
      if (!valid()) return
      const safe = errorOf(error)
      if (handleAccess(safe)) return
      updateLifecycle({
        error:
          safe.code === 'channel_referenced'
            ? {
                ...safe,
                summary:
                  '削除を阻止する参照があります。回復操作を完了し、最新状態を再取得してください。',
              }
            : safe,
      })
    } finally {
      if (valid()) {
        mutationLatch.current = false
        updateLifecycle({ busy: false })
      }
    }
  }
  const requestDelete = () => {
    const snapshot = live.current
    if (
      !snapshot.channel ||
      snapshot.channel.channelId !== channelId ||
      snapshot.current.state !== 'ready' ||
      pendingDeactivation() ||
      mutationLatch.current
    )
      return
    updateLifecycle({
      deleteConfirmation: {
        channelId,
        label: snapshot.channel.label,
        revision: snapshot.channel.updatedAt,
        hasHistory: snapshot.current.value.rich.historySummary.totalCount > 0,
      },
      error: null,
    })
  }
  let displayState: RichMenuAdminState = state
  if (
    state.current.state === 'ready' &&
    state.current.value.current.kind === 'managed' &&
    state.current.value.current.image.status === 'available' &&
    state.current.value.current.image.imageUrl === failedCurrentImage
  ) {
    displayState = {
      ...state,
      current: {
        state: 'ready',
        value: {
          ...state.current.value,
          current: {
            ...state.current.value.current,
            image: { status: 'unavailable', reason: 'invalid' },
          },
        },
      },
    }
  }
  return {
    state:
      invalidated || state.channel?.channelId !== channelId
        ? initialRichMenuAdminState
        : displayState,
    refresh: async () => {
      if (!mutationLatch.current) await load(true)
    },
    apply: () => mutate({ kind: 'apply' }),
    startRecovery: (selection: RecoveryRequest) => mutate(selection),
    trackOperation: () => trackOperation(),
    lifecycle,
    requestDelete,
    cancelDelete: () => updateLifecycle({ deleteConfirmation: null }),
    deleteChannel: () => lifecycleChange('delete'),
    startDeactivation: () => lifecycleChange('stop'),
    recheckDeactivation: () => lifecycleChange('recheck'),
    reactivate: () => lifecycleChange('reactivate'),
    currentImageFailed,
    beginEdit: () => begin('beginEdit'),
    beginCreate: () => begin('beginCreate'),
    beginRebuild: () => begin('beginRebuild'),
    cancelEdit: () => send({ type: 'cancelEdit' }),
    backToEdit: () => send({ type: 'backToEdit' }),
    changeDraft: (fields: Record<string, TemplateField>) => send({ type: 'draftChanged', fields }),
    changeTemplate: (templateId: string, templateVersion: number) =>
      send({ type: 'templateRequested', templateId, templateVersion }),
    confirmTemplate: () => send({ type: 'templateConfirmed' }),
    cancelTemplate: () => send({ type: 'templateCancelled' }),
    preview,
  }
}

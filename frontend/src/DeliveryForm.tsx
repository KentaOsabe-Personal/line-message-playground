import { useEffect, useMemo, useReducer, useRef, useState } from 'react'

import {
  createDeliveryApiClient,
  createLinkedDeliveryApiClient,
  DeliveryApiError,
} from './deliveryApi'
import type {
  DeliveryApiClient,
  LinkedDeliveryApiClient,
  SendDeliveryRequest,
} from './deliveryApi'
import type {
  DeliveryChannelChoice,
  DeliveryRecipientChoice,
  DeliveryResult,
  SafeError,
} from './deliveryDto'
import {
  initialDeliveryState,
  initialLinkedDeliveryState,
  transitionDelivery,
  transitionLinkedDelivery,
} from './deliveryState'
import { createProtectedHttpClient } from './httpApi'

type LegacyProps = {
  client?: DeliveryApiClient
  createOperationId?: () => string
  onSessionInvalid?: () => void
}

const fieldErrors = (error: DeliveryApiError) => ({
  subject: error.error.fields?.subject?.[0],
  body: error.error.fields?.body?.[0],
  message: error.error.fields?.message?.[0] ?? (error.error.fields ? undefined : error.error.summary),
})

function LegacyDeliveryForm({ client, createOperationId = () => crypto.randomUUID(), onSessionInvalid }: LegacyProps) {
  const deliveryClient = useMemo(
    () => client ?? createDeliveryApiClient(createProtectedHttpClient({
      onSessionInvalid,
    })),
    [client, onSessionInvalid],
  )
  const [state, dispatch] = useReducer(transitionDelivery, initialDeliveryState)
  const submitInFlight = useRef(false)

  const applyResult = (result: DeliveryResult) => {
    if (result.status === 'processing') dispatch({ type: 'processing', result })
    else if (result.status === 'succeeded') dispatch({ type: 'succeeded', result })
    else dispatch({ type: 'failed', result })
  }

  const preview = async () => {
    if (state.phase !== 'editing') return
    try {
      const result = await deliveryClient.preview({ subject: state.subject, body: state.body })
      dispatch({ type: 'previewed', subject: state.subject, body: state.body, ...result })
    } catch (error) {
      const apiError = error instanceof DeliveryApiError ? error : new DeliveryApiError({ code: 'unexpected', summary: '配信処理を完了できませんでした。' })
      dispatch({ type: 'validationFailed', errors: fieldErrors(apiError) })
    }
  }

  const send = async (request: SendDeliveryRequest) => {
    try {
      applyResult(await deliveryClient.send(request))
    } catch (error) {
      if (error instanceof DeliveryApiError && error.error.code !== 'network_error') dispatch({ type: 'rejected', error: error.error })
      else dispatch({ type: 'networkFailed' })
    }
  }

  const submit = async () => {
    if (state.phase !== 'preview' || submitInFlight.current) return
    submitInFlight.current = true
    const request = { subject: state.subject, body: state.body, confirmationToken: state.confirmationToken, operationId: createOperationId() }
    dispatch({ type: 'submitted', operationId: request.operationId })
    try {
      await send(request)
    } finally {
      submitInFlight.current = false
    }
  }

  const checkStatus = async () => {
    if (state.phase !== 'processing' && state.phase !== 'uncertain') return
    const operationId = state.operationId
    dispatch({ type: 'checkStarted' })
    try {
      applyResult(await deliveryClient.checkStatus(operationId))
    } catch (error) {
      if (error instanceof DeliveryApiError && error.httpStatus === 404) dispatch({ type: 'statusMissing' })
      else dispatch({ type: 'networkFailed' })
    }
  }

  const retrySameOperation = async () => {
    if (state.phase !== 'uncertain' || !state.canRetrySameOperation || submitInFlight.current) return
    submitInFlight.current = true
    const request = { subject: state.subject, body: state.body, confirmationToken: state.confirmationToken, operationId: state.operationId }
    dispatch({ type: 'retryStarted' })
    try {
      await send(request)
    } finally {
      submitInFlight.current = false
    }
  }

  return (
    <section className="delivery" aria-labelledby="delivery-title">
      <h2 id="delivery-title">LINEテスト配信</h2>
      {state.phase === 'editing' && (
        <form onSubmit={(event) => { event.preventDefault(); void preview() }}>
          <label>件名<input name="subject" value={state.subject} onChange={(event) => dispatch({ type: 'edited', subject: event.target.value, body: state.body })} aria-invalid={Boolean(state.errors.subject)} /></label>
          {state.errors.subject && <p className="field-error">{state.errors.subject}</p>}
          <label>本文<textarea name="body" rows={7} value={state.body} onChange={(event) => dispatch({ type: 'edited', subject: state.subject, body: event.target.value })} aria-invalid={Boolean(state.errors.body)} /></label>
          {state.errors.body && <p className="field-error">{state.errors.body}</p>}
          {state.errors.message && <p className="notice error">{state.errors.message}</p>}
          <button type="submit">送信内容を確認</button>
        </form>
      )}

      {state.phase === 'preview' && (
        <div className="panel preview-panel">
          <h3>実際に送信する内容</h3>
          <pre>{state.formattedText}</pre>
          <div className="actions">
            <button type="button" className="secondary" onClick={() => dispatch({ type: 'edited', subject: state.subject, body: state.body })}>入力へ戻る</button>
            <button type="button" onClick={() => void submit()}>確認した内容を送信</button>
          </div>
        </div>
      )}

      {(state.phase === 'submitting' || state.phase === 'checking') && <div className="panel progress" aria-live="polite"><p>{state.phase === 'submitting' ? 'LINEへ送信中です…' : '配信状態を確認中です…'}</p><button disabled>処理中</button></div>}

      {state.phase === 'processing' && <div className="panel progress" aria-live="polite"><h3>配信を処理中です</h3><p>送信操作は受け付けられました。結果が確定するまで再送しないでください。</p><button type="button" onClick={() => void checkStatus()}>状態を再確認</button></div>}

      {state.phase === 'uncertain' && <div className="panel uncertain" aria-live="polite"><h3>送信結果を確認できません</h3><p>{state.summary}</p><button type="button" onClick={() => void checkStatus()}>状態を再確認</button>{state.canRetrySameOperation && <button type="button" className="secondary" onClick={() => void retrySameOperation()}>同じ送信操作を再試行</button>}</div>}

      {state.phase === 'succeeded' && <div className="panel success" aria-live="polite"><h3>LINEに受け付けられました</h3><p>次の確認済み内容が送信されました。</p><pre>{state.formattedText}</pre><button type="button" onClick={() => dispatch({ type: 'newDelivery' })}>新しい配信</button></div>}

      {state.phase === 'failed' && <div className={`panel ${state.result.status === 'unknown' ? 'uncertain' : 'error'}`} aria-live="polite"><h3>送信成功として確定していません</h3><p>{state.result.error.summary}</p><pre>{state.formattedText}</pre><button type="button" onClick={() => dispatch({ type: 'newDelivery' })}>新しい配信</button></div>}
    </section>
  )
}

type LoadState<T> =
  | { status: 'loading' }
  | { status: 'loaded'; items: T[] }
  | { status: 'error'; error: SafeError }

type Props = LegacyProps & {
  linkedClient?: LinkedDeliveryApiClient
}

const channelReason = (choice: DeliveryChannelChoice): string | null =>
  choice.unavailableReason === 'channel_inactive' ? 'チャネルが無効です' : null

const recipientReason = (choice: DeliveryRecipientChoice): string | null => {
  if (choice.unavailableReason === 'channel_inactive') return 'チャネルが無効です'
  if (choice.unavailableReason === 'recipient_disabled') return 'recipientが無効です'
  if (choice.unavailableReason === 'not_friend') return '友だち状態ではありません'
  if (choice.unavailableReason === 'friendship_unknown') return '友だち状態を確認できません'
  return null
}

const friendshipLabel = (choice: DeliveryRecipientChoice): string => {
  if (choice.friendshipState === 'friend') return '友だち'
  if (choice.friendshipState === 'not_friend') return '友だちではない'
  return '友だち状態不明'
}

const normalizeError = (error: unknown, summary: string): SafeError =>
  error instanceof DeliveryApiError
    ? error.error
    : { code: 'unexpected', summary }

function LinkedDeliveryForm({
  linkedClient,
  onSessionInvalid,
}: Pick<Props, 'linkedClient' | 'onSessionInvalid'>) {
  const deliveryClient = useMemo(
    () => linkedClient ?? createLinkedDeliveryApiClient(createProtectedHttpClient({
      onSessionInvalid,
    })),
    [linkedClient, onSessionInvalid],
  )
  const [state, dispatch] = useReducer(
    transitionLinkedDelivery,
    initialLinkedDeliveryState,
  )
  const [channels, setChannels] = useState<LoadState<DeliveryChannelChoice>>({
    status: 'loading',
  })
  const [recipients, setRecipients] = useState<LoadState<DeliveryRecipientChoice>>({
    status: 'loaded',
    items: [],
  })
  const [channelLoadVersion, setChannelLoadVersion] = useState(0)
  const [recipientLoadVersion, setRecipientLoadVersion] = useState(0)
  const previewRequestSequence = useRef(0)
  const selectedChannelId = state.input.channelId

  useEffect(() => {
    let active = true
    setChannels({ status: 'loading' })
    void deliveryClient.listChannels().then(
      (items) => {
        if (active) setChannels({ status: 'loaded', items })
      },
      (error: unknown) => {
        if (active) {
          setChannels({
            status: 'error',
            error: normalizeError(error, 'チャネル一覧を取得できませんでした。'),
          })
        }
      },
    )
    return () => {
      active = false
    }
  }, [deliveryClient, channelLoadVersion])

  useEffect(() => {
    let active = true
    if (selectedChannelId === null) {
      setRecipients({ status: 'loaded', items: [] })
      return () => {
        active = false
      }
    }
    setRecipients({ status: 'loading' })
    void deliveryClient.listRecipients(selectedChannelId).then(
      (items) => {
        if (active) setRecipients({ status: 'loaded', items })
      },
      (error: unknown) => {
        if (active) {
          setRecipients({
            status: 'error',
            error: normalizeError(error, 'recipient一覧を取得できませんでした。'),
          })
        }
      },
    )
    return () => {
      active = false
    }
  }, [deliveryClient, recipientLoadVersion, selectedChannelId])

  const selectedRecipient = recipients.status === 'loaded'
    ? recipients.items.find((choice) =>
        choice.recipientId === state.input.recipientId && choice.deliveryAvailable)
    : undefined
  const canPreview =
    state.phase === 'editing' &&
    selectedRecipient !== undefined &&
    state.input.subject.trim().length > 0 &&
    state.input.body.trim().length > 0

  const preview = async () => {
    if (!canPreview || state.input.channelId === null || state.input.recipientId === null) return
    previewRequestSequence.current += 1
    const requestId = `preview-${previewRequestSequence.current}`
    const input = {
      ...state.input,
      channelId: state.input.channelId,
      recipientId: state.input.recipientId,
    }
    dispatch({ type: 'previewStarted', requestId })
    try {
      const result = await deliveryClient.preview({
        channelId: input.channelId,
        recipientId: input.recipientId,
        subject: input.subject,
        body: input.body,
        receiptRequested: input.receiptRequested,
      })
      dispatch({ type: 'previewSucceeded', requestId, preview: result })
    } catch (error) {
      dispatch({
        type: 'previewRejected',
        requestId,
        error: normalizeError(error, '送信内容を確認できませんでした。'),
      })
    }
  }

  const editing = state.phase === 'editing' || state.phase === 'previewing'

  return (
    <section className="delivery" aria-labelledby="delivery-title">
      <h2 id="delivery-title">LINEテスト配信</h2>

      {editing && (
        <form onSubmit={(event) => { event.preventDefault(); void preview() }}>
          <fieldset className="target-group">
            <legend>配信元チャネル</legend>
            {channels.status === 'loading' && (
              <p role="status" aria-live="polite">チャネルを読み込んでいます…</p>
            )}
            {channels.status === 'error' && (
              <div className="notice error" role="alert">
                <p>{channels.error.summary}</p>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setChannelLoadVersion((version) => version + 1)}
                >
                  チャネルを再読み込み
                </button>
              </div>
            )}
            {channels.status === 'loaded' && channels.items.length === 0 && (
              <p className="notice" role="status">登録済みチャネルがありません。</p>
            )}
            {channels.status === 'loaded' && channels.items.length > 0 && (
              <ul className="target-options">
                {channels.items.map((choice) => (
                  <li
                    key={choice.channelId}
                    className={choice.deliveryAvailable ? 'target-option' : 'target-option unavailable'}
                  >
                    <label>
                      <input
                        type="radio"
                        name="channelId"
                        value={choice.channelId}
                        checked={state.input.channelId === choice.channelId}
                        disabled={!choice.deliveryAvailable}
                        onChange={() => dispatch({
                          type: 'channelChanged',
                          channelId: choice.channelId,
                        })}
                      />
                      <span>
                        <strong>{choice.label}</strong>
                        <small>{choice.active ? '有効' : '無効'}</small>
                        {channelReason(choice) !== null && (
                          <small className="target-unavailable">{channelReason(choice)}</small>
                        )}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>

          <fieldset className="target-group" disabled={selectedChannelId === null}>
            <legend>配信先recipient</legend>
            {selectedChannelId === null && (
              <p className="notice">先に配信元チャネルを選択してください。</p>
            )}
            {selectedChannelId !== null && recipients.status === 'loading' && (
              <p role="status" aria-live="polite">recipientを読み込んでいます…</p>
            )}
            {selectedChannelId !== null && recipients.status === 'error' && (
              <div className="notice error" role="alert">
                <p>{recipients.error.summary}</p>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setRecipientLoadVersion((version) => version + 1)}
                >
                  recipientを再読み込み
                </button>
              </div>
            )}
            {selectedChannelId !== null &&
              recipients.status === 'loaded' &&
              recipients.items.length === 0 && (
                <p className="notice" role="status">
                  登録済みrecipientがありません。登録または状態確認が必要です。
                </p>
              )}
            {selectedChannelId !== null &&
              recipients.status === 'loaded' &&
              recipients.items.length > 0 && (
                <>
                  <ul className="target-options">
                    {recipients.items.map((choice) => (
                      <li
                        key={choice.recipientId}
                        className={choice.deliveryAvailable ? 'target-option' : 'target-option unavailable'}
                      >
                        <label>
                          <input
                            type="radio"
                            name="recipientId"
                            value={choice.recipientId}
                            checked={state.input.recipientId === choice.recipientId}
                            disabled={!choice.deliveryAvailable}
                            onChange={() => dispatch({
                              type: 'recipientChanged',
                              recipientId: choice.recipientId,
                            })}
                          />
                          <span>
                            <strong>{choice.displayName}</strong>
                            <small>{friendshipLabel(choice)}</small>
                            {!choice.enabled && <small>無効</small>}
                            {recipientReason(choice) !== null && (
                              <small className="target-unavailable">{recipientReason(choice)}</small>
                            )}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                  {!recipients.items.some((choice) => choice.deliveryAvailable) && (
                    <p className="notice error" role="status">
                      配信可能なrecipientがありません。登録または状態確認が必要です。
                    </p>
                  )}
                </>
              )}
          </fieldset>

          <label>
            件名
            <input
              name="subject"
              value={state.input.subject}
              onChange={(event) => dispatch({
                type: 'subjectChanged',
                subject: event.target.value,
              })}
              aria-invalid={Boolean(state.phase === 'editing' && state.errors.subject)}
            />
          </label>
          {state.phase === 'editing' && state.errors.subject && (
            <p className="field-error">{state.errors.subject}</p>
          )}
          <label>
            本文
            <textarea
              name="body"
              rows={7}
              value={state.input.body}
              onChange={(event) => dispatch({
                type: 'bodyChanged',
                body: event.target.value,
              })}
              aria-invalid={Boolean(state.phase === 'editing' && state.errors.body)}
            />
          </label>
          {state.phase === 'editing' && state.errors.body && (
            <p className="field-error">{state.errors.body}</p>
          )}
          <label className="receipt-option">
            <input
              type="checkbox"
              name="receiptRequested"
              checked={state.input.receiptRequested}
              onChange={(event) => dispatch({
                type: 'receiptChanged',
                receiptRequested: event.target.checked,
              })}
            />
            受け取り確認を付ける
          </label>
          {state.phase === 'editing' && state.errors.message && (
            <p className="notice error" role="alert">{state.errors.message}</p>
          )}
          <button type="submit" disabled={!canPreview}>
            {state.phase === 'previewing' ? '確認内容を読み込んでいます…' : '送信内容を確認'}
          </button>
        </form>
      )}

      {state.phase === 'preview' && (
        <div className="panel preview-panel">
          <h3>実際に送信する内容</h3>
          <dl className="target-summary">
            <div><dt>配信元</dt><dd>{state.preview.channelLabel}</dd></div>
            <div><dt>配信先</dt><dd>{state.preview.recipientDisplayName}</dd></div>
          </dl>
          <pre>{state.preview.formattedText}</pre>
          <button
            type="button"
            className="secondary"
            onClick={() => dispatch({ type: 'backToEditing' })}
          >
            入力へ戻る
          </button>
        </div>
      )}
    </section>
  )
}

export default function DeliveryForm(props: Props) {
  if (props.client !== undefined) {
    return <LegacyDeliveryForm {...props} />
  }
  return <LinkedDeliveryForm {...props} />
}

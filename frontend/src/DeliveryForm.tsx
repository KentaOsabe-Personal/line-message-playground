import { useMemo, useReducer, useRef } from 'react'

import { createDeliveryApiClient, DeliveryApiError } from './deliveryApi'
import type { DeliveryApiClient, SendDeliveryRequest } from './deliveryApi'
import type { DeliveryResult } from './deliveryDto'
import { initialDeliveryState, transitionDelivery } from './deliveryState'
import { createProtectedHttpClient } from './httpApi'

type Props = {
  client?: DeliveryApiClient
  createOperationId?: () => string
  onSessionInvalid?: () => void
}

const fieldErrors = (error: DeliveryApiError) => ({
  subject: error.error.fields?.subject?.[0],
  body: error.error.fields?.body?.[0],
  message: error.error.fields?.message?.[0] ?? (error.error.fields ? undefined : error.error.summary),
})

export default function DeliveryForm({ client, createOperationId = () => crypto.randomUUID(), onSessionInvalid }: Props) {
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

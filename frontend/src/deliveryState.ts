import type {
  DeliveryFailure,
  DeliveryResult,
  DeliverySuccess,
  LinkedDeliveryStatus,
  LinkedPreviewResponse,
  SafeError,
} from './deliveryDto'

export type FieldErrors = { subject?: string; body?: string; message?: string }
type Content = { subject: string; body: string; formattedText: string; confirmationToken: string }
type Operation = Content & { operationId: string }
export type DeliveryRejection = { status: 'rejected'; operationId: string; error: SafeError }

export type DeliveryUIState =
  | { phase: 'editing'; subject: string; body: string; errors: FieldErrors }
  | ({ phase: 'preview' } & Content)
  | ({ phase: 'submitting' } & Operation)
  | ({ phase: 'processing'; acceptedAt: string; expiresAt: string } & Operation)
  | ({ phase: 'uncertain'; summary: string; canRetrySameOperation: boolean } & Operation)
  | ({ phase: 'checking' } & Operation)
  | { phase: 'succeeded'; subject: string; body: string; formattedText: string; result: DeliverySuccess }
  | { phase: 'failed'; subject: string; body: string; formattedText: string; result: DeliveryFailure | DeliveryRejection }

export type DeliveryEvent =
  | { type: 'edited'; subject: string; body: string }
  | ({ type: 'previewed' } & Content)
  | { type: 'submitted'; operationId: string }
  | { type: 'processing'; result: Extract<DeliveryResult, { status: 'processing' }> }
  | { type: 'networkFailed' }
  | { type: 'retryStarted' }
  | { type: 'checkStarted' }
  | { type: 'statusMissing' }
  | { type: 'succeeded'; result: DeliverySuccess }
  | { type: 'failed'; result: DeliveryFailure }
  | { type: 'rejected'; error: SafeError }
  | { type: 'validationFailed'; errors: FieldErrors }
  | { type: 'newDelivery' }

export const initialDeliveryState: DeliveryUIState = { phase: 'editing', subject: '', body: '', errors: {} }

const hasOperation = (state: DeliveryUIState): state is Extract<DeliveryUIState, { operationId: string }> => 'operationId' in state
const hasMatchingOperation = (state: DeliveryUIState, operationId: string): state is Extract<DeliveryUIState, { operationId: string }> => hasOperation(state) && state.operationId === operationId
const terminal = (state: Extract<DeliveryUIState, { operationId: string }>, result: DeliverySuccess | DeliveryFailure): DeliveryUIState => ({ phase: result.status === 'succeeded' ? 'succeeded' : 'failed', subject: state.subject, body: state.body, formattedText: state.formattedText, result } as DeliveryUIState)

export function transitionDelivery(state: DeliveryUIState, event: DeliveryEvent): DeliveryUIState {
  if (event.type === 'edited') return { phase: 'editing', subject: event.subject, body: event.body, errors: {} }
  if (event.type === 'previewed' && state.phase === 'editing') return { phase: 'preview', ...event }
  if (event.type === 'validationFailed' && state.phase === 'editing') return { ...state, errors: event.errors }
  if (event.type === 'submitted' && state.phase === 'preview') return { phase: 'submitting', subject: state.subject, body: state.body, formattedText: state.formattedText, confirmationToken: state.confirmationToken, operationId: event.operationId }
  if (event.type === 'processing' && (state.phase === 'submitting' || state.phase === 'checking') && hasMatchingOperation(state, event.result.operationId)) return { ...state, phase: 'processing', acceptedAt: event.result.acceptedAt, expiresAt: event.result.expiresAt }
  if (event.type === 'networkFailed' && state.phase === 'submitting') return { ...state, phase: 'uncertain', summary: '送信結果を確認できません。', canRetrySameOperation: false }
  if (event.type === 'networkFailed' && state.phase === 'checking') return { ...state, phase: 'uncertain', summary: '送信結果を確認できません。', canRetrySameOperation: false }
  if (event.type === 'retryStarted' && state.phase === 'uncertain' && state.canRetrySameOperation) return { phase: 'submitting', subject: state.subject, body: state.body, formattedText: state.formattedText, confirmationToken: state.confirmationToken, operationId: state.operationId }
  if (event.type === 'checkStarted' && (state.phase === 'processing' || state.phase === 'uncertain')) return { phase: 'checking', subject: state.subject, body: state.body, formattedText: state.formattedText, confirmationToken: state.confirmationToken, operationId: state.operationId }
  if (event.type === 'statusMissing' && state.phase === 'checking') return { phase: 'uncertain', subject: state.subject, body: state.body, formattedText: state.formattedText, confirmationToken: state.confirmationToken, operationId: state.operationId, summary: '受付記録を確認できません。', canRetrySameOperation: true }
  if (event.type === 'succeeded' && hasMatchingOperation(state, event.result.operationId)) return terminal(state, event.result)
  if (event.type === 'failed' && hasMatchingOperation(state, event.result.operationId)) return terminal(state, event.result)
  if (event.type === 'rejected' && state.phase === 'submitting') return { phase: 'failed', subject: state.subject, body: state.body, formattedText: state.formattedText, result: { status: 'rejected', operationId: state.operationId, error: event.error } }
  if (event.type === 'newDelivery' && (state.phase === 'succeeded' || state.phase === 'failed')) return initialDeliveryState
  return state
}

export type LinkedEditingInput = {
  channelId: string | null
  recipientId: string | null
  subject: string
  body: string
  receiptRequested: boolean
}

export type LinkedFieldErrors = {
  channelId?: string
  recipientId?: string
  subject?: string
  body?: string
  receiptRequested?: string
  message?: string
}

type LinkedPreviewContext = {
  input: LinkedEditingInput
  preview: LinkedPreviewResponse
}
type LinkedOperationContext = LinkedPreviewContext & { operationId: string }
type LinkedStatus<S extends LinkedDeliveryStatus['status']> = LinkedDeliveryStatus & { status: S }
export type LinkedDeliveryUIState =
  | { phase: 'editing'; input: LinkedEditingInput; errors: LinkedFieldErrors }
  | { phase: 'previewing'; input: LinkedEditingInput; requestId: string }
  | ({ phase: 'preview' } & LinkedPreviewContext)
  | ({ phase: 'submitting' } & LinkedOperationContext)
  | ({ phase: 'processing'; result: LinkedStatus<'processing'> } & LinkedOperationContext)
  | ({ phase: 'checking'; previous: LinkedStatus<'processing' | 'unknown'> | null } & LinkedOperationContext)
  | ({ phase: 'uncertain'; error: SafeError; canRetrySameOperation: boolean } & LinkedOperationContext)
  | ({ phase: 'unknown'; result: LinkedStatus<'unknown'> } & LinkedOperationContext)
  | ({ phase: 'succeeded'; result: LinkedStatus<'succeeded'> } & LinkedOperationContext)
  | ({ phase: 'failed'; result: LinkedStatus<'failed'> } & LinkedOperationContext)

export type LinkedDeliveryEvent =
  | { type: 'channelChanged'; channelId: string | null }
  | { type: 'recipientChanged'; recipientId: string | null }
  | { type: 'subjectChanged'; subject: string }
  | { type: 'bodyChanged'; body: string }
  | { type: 'receiptChanged'; receiptRequested: boolean }
  | { type: 'previewStarted'; requestId: string }
  | { type: 'previewSucceeded'; requestId: string; preview: LinkedPreviewResponse }
  | { type: 'previewRejected'; requestId: string; error: SafeError }
  | { type: 'backToEditing' }
  | { type: 'submitted'; operationId: string }
  | { type: 'deliveryUpdated'; result: LinkedDeliveryStatus }
  | { type: 'networkFailed' }
  | { type: 'checkStarted' }
  | { type: 'statusMissing' }
  | { type: 'newDelivery' }

export const initialLinkedDeliveryState: LinkedDeliveryUIState = {
  phase: 'editing',
  input: {
    channelId: null,
    recipientId: null,
    subject: '',
    body: '',
    receiptRequested: false,
  },
  errors: {},
}

const copyLinkedInput = (input: LinkedEditingInput): LinkedEditingInput => ({ ...input })
const copyLinkedPreview = (preview: LinkedPreviewResponse): LinkedPreviewResponse => ({ ...preview })
const copyLinkedError = (error: SafeError): SafeError => ({
  code: error.code,
  summary: error.summary,
  ...(error.fields === undefined
    ? {}
    : {
        fields: Object.fromEntries(
          Object.entries(error.fields).map(([field, messages]) => [field, [...messages]]),
        ),
      }),
})
const copyLinkedStatus = <T extends LinkedDeliveryStatus>(result: T): T => ({
  ...result,
  snapshot: { ...result.snapshot },
  receipt: { ...result.receipt },
  ...('error' in result ? { error: copyLinkedError(result.error) } : {}),
}) as T

const editableInput = (state: LinkedDeliveryUIState): LinkedEditingInput | null =>
  state.phase === 'editing' || state.phase === 'previewing' || state.phase === 'preview'
    ? state.input
    : null

const editLinkedInput = (
  state: LinkedDeliveryUIState,
  update: (input: LinkedEditingInput) => LinkedEditingInput,
): LinkedDeliveryUIState => {
  const current = editableInput(state)
  if (current === null) return state
  const input = update(current)
  if (
    input.channelId === current.channelId &&
    input.recipientId === current.recipientId &&
    input.subject === current.subject &&
    input.body === current.body &&
    input.receiptRequested === current.receiptRequested
  ) return state
  return { phase: 'editing', input, errors: {} }
}

const linkedFieldErrors = (error: SafeError): LinkedFieldErrors => {
  const first = (field: keyof LinkedEditingInput) => error.fields?.[field]?.[0]
  return {
    ...(first('channelId') === undefined ? {} : { channelId: first('channelId') }),
    ...(first('recipientId') === undefined ? {} : { recipientId: first('recipientId') }),
    ...(first('subject') === undefined ? {} : { subject: first('subject') }),
    ...(first('body') === undefined ? {} : { body: first('body') }),
    ...(first('receiptRequested') === undefined
      ? {}
      : { receiptRequested: first('receiptRequested') }),
    message: error.summary,
  }
}

const previewMatchesInput = (
  preview: LinkedPreviewResponse,
  input: LinkedEditingInput,
): boolean =>
  preview.channelId === input.channelId &&
  preview.recipientId === input.recipientId &&
  preview.receiptRequested === input.receiptRequested

export function transitionLinkedDelivery(
  state: LinkedDeliveryUIState,
  event: LinkedDeliveryEvent,
): LinkedDeliveryUIState {
  if (event.type === 'channelChanged') {
    return editLinkedInput(state, (input) => ({
      ...input,
      channelId: event.channelId,
      recipientId: event.channelId === input.channelId ? input.recipientId : null,
    }))
  }
  if (event.type === 'recipientChanged') {
    return editLinkedInput(state, (input) => ({ ...input, recipientId: event.recipientId }))
  }
  if (event.type === 'subjectChanged') {
    return editLinkedInput(state, (input) => ({ ...input, subject: event.subject }))
  }
  if (event.type === 'bodyChanged') {
    return editLinkedInput(state, (input) => ({ ...input, body: event.body }))
  }
  if (event.type === 'receiptChanged') {
    return editLinkedInput(state, (input) => ({
      ...input,
      receiptRequested: event.receiptRequested,
    }))
  }
  if (event.type === 'previewStarted' && state.phase === 'editing') {
    return { phase: 'previewing', input: copyLinkedInput(state.input), requestId: event.requestId }
  }
  if (
    event.type === 'previewSucceeded' &&
    state.phase === 'previewing' &&
    state.requestId === event.requestId
  ) {
    if (!previewMatchesInput(event.preview, state.input)) {
      return {
        phase: 'editing',
        input: copyLinkedInput(state.input),
        errors: { message: '確認結果が選択内容と一致しません。もう一度確認してください。' },
      }
    }
    return {
      phase: 'preview',
      input: copyLinkedInput(state.input),
      preview: copyLinkedPreview(event.preview),
    }
  }
  if (
    event.type === 'previewRejected' &&
    state.phase === 'previewing' &&
    state.requestId === event.requestId
  ) {
    return {
      phase: 'editing',
      input: copyLinkedInput(state.input),
      errors: linkedFieldErrors(copyLinkedError(event.error)),
    }
  }
  if (event.type === 'backToEditing' && state.phase === 'preview') {
    return { phase: 'editing', input: copyLinkedInput(state.input), errors: {} }
  }
  if (event.type === 'submitted' && state.phase === 'preview') {
    return {
      phase: 'submitting',
      input: copyLinkedInput(state.input),
      preview: copyLinkedPreview(state.preview),
      operationId: event.operationId,
    }
  }
  if (
    event.type === 'deliveryUpdated' &&
    (state.phase === 'submitting' || state.phase === 'checking') &&
    state.operationId === event.result.operationId
  ) {
    const result = copyLinkedStatus(event.result)
    if (result.status === 'processing') {
      return { ...state, phase: 'processing', result: result as LinkedStatus<'processing'> }
    }
    if (result.status === 'unknown') {
      return { ...state, phase: 'unknown', result: result as LinkedStatus<'unknown'> }
    }
    if (result.status === 'succeeded') {
      return { ...state, phase: 'succeeded', result: result as LinkedStatus<'succeeded'> }
    }
    return { ...state, phase: 'failed', result: result as LinkedStatus<'failed'> }
  }
  if (
    event.type === 'networkFailed' &&
    (state.phase === 'submitting' || state.phase === 'checking')
  ) {
    return {
      phase: 'uncertain',
      input: copyLinkedInput(state.input),
      preview: copyLinkedPreview(state.preview),
      operationId: state.operationId,
      error: { code: 'network_error', summary: '送信結果を確認できません。状態を確認してください。' },
      canRetrySameOperation: false,
    }
  }
  if (
    event.type === 'checkStarted' &&
    (state.phase === 'processing' || state.phase === 'unknown' || state.phase === 'uncertain')
  ) {
    return {
      phase: 'checking',
      input: copyLinkedInput(state.input),
      preview: copyLinkedPreview(state.preview),
      operationId: state.operationId,
      previous: state.phase === 'uncertain' ? null : copyLinkedStatus(state.result),
    }
  }
  if (event.type === 'statusMissing' && state.phase === 'checking') {
    return {
      phase: 'uncertain',
      input: copyLinkedInput(state.input),
      preview: copyLinkedPreview(state.preview),
      operationId: state.operationId,
      error: { code: 'operation_not_found', summary: '受付記録を確認できません。' },
      canRetrySameOperation: true,
    }
  }
  if (
    event.type === 'newDelivery' &&
    (state.phase === 'succeeded' || state.phase === 'failed')
  ) {
    return initialLinkedDeliveryState
  }
  return state
}

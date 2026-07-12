import type { DeliveryFailure, DeliveryResult, DeliverySuccess, SafeError } from './deliveryDto'

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

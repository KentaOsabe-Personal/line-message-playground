import type { SafeApiError } from './authDto'
import type { ChannelAdminItem } from './channelAdminDto'

export type OperationStateMap = Record<string, 'pending'>
export type ChannelAdminState =
  | { state: 'idle' }
  | { state: 'loading'; generation: number }
  | { state: 'empty'; operations: OperationStateMap }
  | { state: 'ready'; items: ChannelAdminItem[]; operations: OperationStateMap }
  | { state: 'load_failed'; error: SafeApiError }
  | { state: 'refresh_required'; reason: 'unknown_result' | 'stale_channel' }

export type ChannelAdminAction =
  | { type: 'loadStarted'; generation: number }
  | { type: 'loadSucceeded'; generation: number; items: ChannelAdminItem[] }
  | { type: 'loadFailed'; generation: number; error: SafeApiError }
  | { type: 'operationStarted'; key: string }
  | { type: 'operationFailed'; key: string; error: SafeApiError }
  | { type: 'mutationSucceeded'; key: string; item: ChannelAdminItem }
  | { type: 'deleteSucceeded'; key: string; channelId: string }
  | { type: 'operationCompleted'; key: string }

export const initialChannelAdminState: ChannelAdminState = { state: 'idle' }
const hasOperations = (state: ChannelAdminState): state is Extract<ChannelAdminState, { operations: OperationStateMap }> =>
  state.state === 'empty' || state.state === 'ready'
const withoutOperation = (operations: OperationStateMap, key: string): OperationStateMap =>
  Object.fromEntries(Object.entries(operations).filter(([operationKey]) => operationKey !== key))

export function transitionChannelAdmin(
  state: ChannelAdminState,
  action: ChannelAdminAction,
): ChannelAdminState {
  if (action.type === 'loadStarted') return { state: 'loading', generation: action.generation }
  if (action.type === 'loadSucceeded') {
    if (state.state !== 'loading' || state.generation !== action.generation) return state
    const items = action.items.map((item) => ({ ...item }))
    return items.length === 0
      ? { state: 'empty', operations: {} }
      : { state: 'ready', items, operations: {} }
  }
  if (action.type === 'loadFailed') {
    if (state.state !== 'loading' || state.generation !== action.generation) return state
    return { state: 'load_failed', error: { ...action.error } }
  }
  if (action.type === 'operationStarted') {
    if (!hasOperations(state) || state.operations[action.key] !== undefined) return state
    return { ...state, operations: { ...state.operations, [action.key]: 'pending' } }
  }
  if (action.type === 'operationFailed') {
    if (!hasOperations(state) || state.operations[action.key] === undefined) return state
    if (action.error.code === 'network_error') return { state: 'refresh_required', reason: 'unknown_result' }
    if (action.error.code === 'stale_channel') return { state: 'refresh_required', reason: 'stale_channel' }
    return { ...state, operations: withoutOperation(state.operations, action.key) }
  }
  if (action.type === 'mutationSucceeded') {
    if (!hasOperations(state) || state.operations[action.key] === undefined) return state
    const operations = withoutOperation(state.operations, action.key)
    if (state.state === 'empty') return { state: 'ready', items: [{ ...action.item }], operations }
    const found = state.items.some((item) => item.channelId === action.item.channelId)
    const items = found
      ? state.items.map((item) => item.channelId === action.item.channelId ? { ...action.item } : item)
      : [...state.items, { ...action.item }]
    return { state: 'ready', items, operations }
  }
  if (action.type === 'deleteSucceeded') {
    if (!hasOperations(state) || state.operations[action.key] === undefined) return state
    const operations = withoutOperation(state.operations, action.key)
    if (state.state === 'empty') return { state: 'empty', operations }
    const items = state.items.filter((item) => item.channelId !== action.channelId)
    return items.length === 0 ? { state: 'empty', operations } : { state: 'ready', items, operations }
  }
  if (action.type === 'operationCompleted') {
    if (!hasOperations(state) || state.operations[action.key] === undefined) return state
    return { ...state, operations: withoutOperation(state.operations, action.key) }
  }
  return state
}

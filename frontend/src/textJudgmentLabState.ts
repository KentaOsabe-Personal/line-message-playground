import type { JudgmentResponse } from './textJudgmentLabTypes'

export type Immutable<T> = { readonly [K in keyof T]: Immutable<T[K]> }
export type JudgmentSnapshot = Readonly<{
  text: string
  result: Immutable<JudgmentResponse>
}>
export type ResultRef = Readonly<{
  entryId: number
  side: 'single' | 'original' | 'rewritten'
}>
export type SingleOutcome =
  | { kind: 'pending' }
  | { kind: 'succeeded'; result: Immutable<JudgmentResponse> }
  | { kind: 'failed'; message: string }
export type LabEntry =
  | { kind: 'single'; id: number; text: string; outcome: SingleOutcome }
  | { kind: 'comparison'; id: number; original: JudgmentSnapshot; rewritten: JudgmentSnapshot }
export type Submission = Readonly<{
  id: number
  draft: string
  text: string
  original: JudgmentSnapshot | null
}>
export type LabComposer =
  | { kind: 'editing'; draft: string; original: JudgmentSnapshot | null; error: string | null }
  | { kind: 'pending'; submission: Submission }
export type LabState = Readonly<{
  entries: readonly LabEntry[]
  composer: LabComposer
}>
export type LabAction =
  | { type: 'edit'; draft: string }
  | { type: 'select'; source: ResultRef; replacementConfirmed: boolean }
  | { type: 'cancel_comparison' }
  | { type: 'start'; id: number }
  | { type: 'succeed'; id: number; result: JudgmentResponse }
  | { type: 'fail'; id: number; message: string }

export const initialLabState: LabState = {
  entries: [],
  composer: { kind: 'editing', draft: '', original: null, error: null },
}

export function findSuccessfulResult(state: LabState, source: ResultRef): JudgmentSnapshot | null {
  const entry = state.entries.find((item) => item.id === source.entryId)
  if (!entry) return null
  if (entry.kind === 'single') {
    return source.side === 'single' && entry.outcome.kind === 'succeeded'
      ? { text: entry.text, result: structuredClone(entry.outcome.result) }
      : null
  }
  if (source.side === 'single') return null
  return structuredClone(entry[source.side])
}

export function isValidDraft(draft: string): boolean {
  return draft.trim() !== '' && [...draft].length <= 1000
}

export function transition(state: LabState, action: LabAction): LabState {
  if (state.composer.kind === 'pending') {
    const submission = state.composer.submission
    if ((action.type !== 'succeed' && action.type !== 'fail') || action.id !== submission.id)
      return state
    if (action.type === 'fail') {
      return {
        entries: submission.original
          ? state.entries
          : state.entries.map((entry): LabEntry =>
              entry.kind === 'single' && entry.id === submission.id
                ? { ...entry, outcome: { kind: 'failed', message: action.message } }
                : entry,
            ),
        composer: {
          kind: 'editing',
          draft: submission.draft,
          original: submission.original,
          error: action.message,
        },
      }
    }
    const result: Immutable<JudgmentResponse> = structuredClone(action.result)
    return {
      entries: submission.original
        ? [
            ...state.entries,
            {
              kind: 'comparison',
              id: submission.id,
              original: submission.original,
              rewritten: { text: submission.text, result },
            },
          ]
        : state.entries.map((entry): LabEntry =>
            entry.kind === 'single' && entry.id === submission.id
              ? { ...entry, outcome: { kind: 'succeeded', result } }
              : entry,
          ),
      composer: { kind: 'editing', draft: '', original: null, error: null },
    }
  }
  const composer = state.composer
  switch (action.type) {
    case 'edit':
      return { ...state, composer: { ...composer, draft: action.draft } }
    case 'select': {
      const original = findSuccessfulResult(state, action.source)
      if (!original || (composer.draft !== '' && !action.replacementConfirmed)) return state
      return {
        ...state,
        composer: { kind: 'editing', draft: original.text, original, error: null },
      }
    }
    case 'cancel_comparison':
      return { ...state, composer: { ...composer, original: null, error: null } }
    case 'start': {
      if (!isValidDraft(composer.draft)) return state
      const submission: Submission = {
        id: action.id,
        draft: composer.draft,
        text: composer.draft.trim(),
        original: composer.original ? structuredClone(composer.original) : null,
      }
      return {
        entries: submission.original
          ? state.entries
          : [
              ...state.entries,
              {
                kind: 'single',
                id: action.id,
                text: submission.text,
                outcome: { kind: 'pending' },
              },
            ],
        composer: { kind: 'pending', submission },
      }
    }
    default:
      return state
  }
}

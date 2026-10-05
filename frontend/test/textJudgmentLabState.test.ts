import { describe, expect, test } from 'vitest'

import {
  findSuccessfulResult,
  initialLabState,
  isValidDraft,
  type LabAction,
  transition,
  type LabState,
} from '../src/textJudgmentLabState'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import fixture from './fixtures/text-judgment-lab-v3.json'

function result() {
  const parsed = parseJudgmentResponse(structuredClone(fixture))
  if (!parsed.ok) throw new Error('判定fixtureが不正です')
  return parsed.value
}

function history(): LabState {
  return {
    entries: [
      { kind: 'single', id: 1, text: '元の文章', outcome: { kind: 'succeeded', result: result() } },
      { kind: 'single', id: 2, text: '待機', outcome: { kind: 'pending' } },
      { kind: 'single', id: 3, text: '失敗', outcome: { kind: 'failed', message: '失敗' } },
      {
        kind: 'comparison',
        id: 4,
        original: { text: '比較の元', result: result() },
        rewritten: { text: '比較の後', result: { ...result(), model: 'different-model' } },
      },
    ],
    composer: { kind: 'editing', draft: '', original: null, error: null },
  }
}

const source = { entryId: 1, side: 'single' } as const

describe('LabState 選択と編集（task 1.1）', () => {
  // テストケース: 入力欄が空の状態で、通常の成功結果と比較結果の両側をそれぞれ選択する。
  // 期待値: 各文章と対応する判定結果が比較元へコピーされる。
  test.each([
    [source, '元の文章', fixture.model],
    [{ entryId: 4, side: 'original' } as const, '比較の元', fixture.model],
    [{ entryId: 4, side: 'rewritten' } as const, '比較の後', 'different-model'],
  ])('selects successful source %j', (ref, text, model) => {
    const state = history()
    const selected = transition(state, { type: 'select', source: ref, replacementConfirmed: false })
    expect(selected.composer).toEqual({
      kind: 'editing',
      draft: text,
      original: { text, result: { ...fixture, model } },
      error: null,
    })
    expect(selected.entries).toBe(state.entries)
  })

  // テストケース: 判定が未完了または失敗した結果、存在しないID、履歴の種類に合わない側を選択する。
  // 期待値: 成功結果を返さず、状態を変更しない。
  test.each([
    { entryId: 2, side: 'single' },
    { entryId: 3, side: 'single' },
    { entryId: 99, side: 'single' },
    { entryId: 1, side: 'original' },
    { entryId: 4, side: 'single' },
  ] as const)('ignores invalid source %j', (ref) => {
    const state = history()
    expect(findSuccessfulResult(state, ref)).toBeNull()
    expect(transition(state, { type: 'select', source: ref, replacementConfirmed: true })).toBe(
      state,
    )
  })

  // テストケース: 成功結果を比較元に選び、元の応答に含まれる値と編集中の文章を変更する。
  // 期待値: 結果に含まれる値も個別にコピーされ、元の応答や入力を編集しても保存済みの比較元と編集前の状態は変わらない。
  test('isolates deeply readonly snapshots from source mutation and draft editing', () => {
    const response = result()
    const state: LabState = {
      ...initialLabState,
      entries: [
        {
          kind: 'single',
          id: 1,
          text: '元の文章',
          outcome: { kind: 'succeeded', result: response },
        },
      ],
    }
    const selected = transition(state, { type: 'select', source, replacementConfirmed: false })
    response.answers.intent.probabilities.request = 0
    response.answers.sentiment.legend['0'] = '変更'
    response.answers.urgency.noul = 0
    const edited = transition(selected, { type: 'edit', draft: '編集した文章' })
    if (selected.composer.kind !== 'editing' || edited.composer.kind !== 'editing')
      throw new Error('編集中ではありません')
    expect(edited.composer.original).toEqual({ text: '元の文章', result: fixture })
    expect(edited.composer.original).toBe(selected.composer.original)
    expect(selected.composer.draft).toBe('元の文章')
    expect(edited.composer.original?.result.answers).not.toBe(response.answers)
    const copied = findSuccessfulResult(history(), { entryId: 4, side: 'rewritten' })
    expect(copied?.result.model).toBe('different-model')
  })

  // テストケース: 入力欄に文章または空白だけがある状態で、入力の置換を断った後に承認する。
  // 期待値: 断った場合は入力・比較元・エラーを保持し、承認した場合だけ入力と比較元を同時に置き換えてエラーを解除する。
  test.each(['入力中', '  \n '])('requires confirmation for nonempty draft %j', (draft) => {
    const original = { text: '以前の元', result: result() }
    const state: LabState = {
      ...history(),
      composer: { kind: 'editing', draft, original, error: '旧エラー' },
    }
    expect(transition(state, { type: 'select', source, replacementConfirmed: false })).toBe(state)
    expect(
      transition(state, { type: 'select', source, replacementConfirmed: true }).composer,
    ).toEqual({
      kind: 'editing',
      draft: '元の文章',
      original: { text: '元の文章', result: fixture },
      error: null,
    })
  })

  // テストケース: 比較を中止する。
  // 期待値: 編集中の入力と履歴を残し、比較元とエラーだけを解除する。
  test('cancels comparison without discarding draft or history', () => {
    const state: LabState = {
      ...history(),
      composer: {
        kind: 'editing',
        draft: '編集中',
        original: { text: '元', result: result() },
        error: '失敗',
      },
    }
    const cancelled = transition(state, { type: 'cancel_comparison' })
    expect(cancelled.composer).toEqual({
      kind: 'editing',
      draft: '編集中',
      original: null,
      error: null,
    })
    expect(cancelled.entries).toBe(state.entries)
  })
})

function withDraft(draft: string): LabState {
  return transition(initialLabState, { type: 'edit', draft })
}
function comparison(draft = '  書き換え\n文章  '): LabState {
  return transition(
    transition(history(), {
      type: 'select',
      source,
      replacementConfirmed: false,
    }),
    { type: 'edit', draft },
  )
}

describe('LabState 判定の開始と完了（task 1.2）', () => {
  // テストケース: 空の入力、空白だけの入力、通常の文字や補助平面文字を含む入力で文字数の境界を確認する。
  // 期待値: 前後の空白を除いた文章が空でなく、空白を含む入力全体がUnicodeコードポイントで1,000文字以下のときだけ判定を開始できる。
  test.each([
    ['', false],
    [' \n\t', false],
    ['あ', true],
    ['😀', true],
    ['あ'.repeat(1000), true],
    ['あ'.repeat(1001), false],
    ['😀'.repeat(1000), true],
    ['😀'.repeat(1001), false],
    [' ' + 'あ'.repeat(999), true],
    [' ' + 'あ'.repeat(1000), false],
    ['\n' + '😀'.repeat(999), true],
  ])('validates full draft case %#', (draft, valid) => {
    expect(isValidDraft(draft)).toBe(valid)
    const state = withDraft(draft)
    const started = transition(state, { type: 'start', id: 5 })
    if (!valid) expect(started).toBe(state)
    else expect(started.composer.kind).toBe('pending')
  })

  // テストケース: 空白と改行を含む文章で通常の判定を開始し、成功または失敗で完了する。
  // 期待値: 開始時に前後の空白を除いた文章を履歴へ一件追加し、完了時は同じ一件を更新する。
  test.each(['succeed', 'fail'] as const)('updates one normal entry on %s', (type) => {
    const draft = ' \n判定\n文章  '
    const started = transition(withDraft(draft), { type: 'start', id: 1 })
    expect(started.composer).toEqual({
      kind: 'pending',
      submission: { id: 1, draft, text: '判定\n文章', original: null },
    })
    expect(started.entries).toEqual([
      { kind: 'single', id: 1, text: '判定\n文章', outcome: { kind: 'pending' } },
    ])
    const response = result()
    const completed = transition(
      started,
      type === 'succeed'
        ? { type, id: 1, result: response }
        : { type, id: 1, message: '安全な失敗案内' },
    )
    expect(completed.entries).toEqual([
      {
        kind: 'single',
        id: 1,
        text: '判定\n文章',
        outcome:
          type === 'succeed'
            ? { kind: 'succeeded', result: fixture }
            : { kind: 'failed', message: '安全な失敗案内' },
      },
    ])
    expect(completed.composer).toEqual({
      kind: 'editing',
      draft: type === 'succeed' ? '' : draft,
      original: null,
      error: type === 'succeed' ? null : '安全な失敗案内',
    })
    response.answers.sentiment.probabilities['1'] = 0
    if (type === 'succeed') expect(findSuccessfulResult(completed, source)?.result).toEqual(fixture)
    expect(started.entries[0]).toEqual({
      kind: 'single',
      id: 1,
      text: '判定\n文章',
      outcome: { kind: 'pending' },
    })
  })

  // テストケース: 比較を開始し、比較元とは異なる判定結果で成功する。
  // 期待値: 開始時には履歴を追加せず、成功時だけ比較結果を一組追加して入力と比較元の選択を解除する。
  test('adds exactly one comparison with submitted snapshots only on success', () => {
    const editing = comparison()
    const started = transition(editing, { type: 'start', id: 5 })
    expect(started.entries).toBe(editing.entries)
    expect(started.composer).toEqual({
      kind: 'pending',
      submission: {
        id: 5,
        draft: '  書き換え\n文章  ',
        text: '書き換え\n文章',
        original: { text: '元の文章', result: fixture },
      },
    })
    const response = { ...result(), model: 'rewritten-model', elapsedMs: 99.9 }
    const completed = transition(started, { type: 'succeed', id: 5, result: response })
    expect(completed.entries).toHaveLength(5)
    expect(completed.entries[4]).toEqual({
      kind: 'comparison',
      id: 5,
      original: { text: '元の文章', result: fixture },
      rewritten: { text: '書き換え\n文章', result: response },
    })
    response.answers.intent.confidence = 0
    const entry = completed.entries[4]
    if (entry.kind !== 'comparison') throw new Error('比較ではありません')
    expect(entry.rewritten.result.answers.intent.confidence).toBe(fixture.answers.intent.confidence)
    expect(completed.entries.slice(0, 4)).toEqual(editing.entries)
    expect(completed.composer).toEqual(initialLabState.composer)
    expect(transition(completed, { type: 'succeed', id: 5, result: result() })).toBe(completed)
  })

  // テストケース: 比較の判定に失敗した後、入力を修正して手動で再送し、判定に成功する。
  // 期待値: 空白と改行を含む送信時の入力と比較元を復元し、失敗した時点では送信履歴を追加しない。
  test('restores comparison draft and source for explicit retry', () => {
    const editing = comparison()
    const started = transition(editing, { type: 'start', id: 5 })
    const failed = transition(started, { type: 'fail', id: 5, message: '通信失敗' })
    expect(failed.entries).toBe(editing.entries)
    expect(failed.composer).toEqual({ ...editing.composer, error: '通信失敗' })
    const corrected = transition(failed, { type: 'edit', draft: '修正して再送' })
    expect(corrected.composer.kind).toBe('editing')
    const retried = transition(corrected, { type: 'start', id: 6 })
    const completed = transition(retried, { type: 'succeed', id: 6, result: result() })
    expect(completed.entries[4]).toEqual({
      kind: 'comparison',
      id: 6,
      original: { text: '元の文章', result: fixture },
      rewritten: { text: '修正して再送', result: fixture },
    })
  })

  // テストケース: 通常または比較の判定待ち中に変更操作を行い、以前の判定IDで成功または失敗を通知する。
  // 期待値: 入力の編集、比較元の選択、比較の中止、追加送信、以前の判定の成功・失敗通知では、いずれも状態が変わらない。
  test.each([false, true])(
    'ignores all modifications and stale completion while pending (%s)',
    (comparing) => {
      const pending = transition(comparing ? comparison() : withDraft('通常'), {
        type: 'start',
        id: 5,
      })
      const actions: LabAction[] = [
        { type: 'edit', draft: '禁止' },
        { type: 'select', source, replacementConfirmed: true },
        { type: 'cancel_comparison' },
        { type: 'start', id: 6 },
        { type: 'succeed', id: 4, result: result() },
        { type: 'fail', id: 4, message: '旧失敗' },
      ]
      expect(pending.composer.kind).toBe('pending')
      for (const action of actions) expect(transition(pending, action)).toBe(pending)
    },
  )

  // テストケース: 判定に失敗した後で別の比較元を選び、新しい判定の実行中と完了後に以前の判定の成功・失敗通知を受け取る。
  // 期待値: 以前の判定の完了通知を受け取っても、新しい入力・比較元・履歴は変わらない。
  test('rejects stale outcomes across a new comparison', () => {
    const old = transition(comparison(), { type: 'start', id: 5 })
    const failed = transition(old, { type: 'fail', id: 5, message: '時間切れ' })
    const selected = transition(failed, {
      type: 'select',
      source: { entryId: 4, side: 'rewritten' },
      replacementConfirmed: true,
    })
    const pending = transition(selected, { type: 'start', id: 6 })
    const done = transition(pending, { type: 'succeed', id: 6, result: result() })
    for (const state of [selected, pending, done]) {
      expect(transition(state, { type: 'succeed', id: 5, result: result() })).toBe(state)
      expect(transition(state, { type: 'fail', id: 5, message: '旧失敗' })).toBe(state)
    }
    expect(done.entries[4]).toEqual({
      kind: 'comparison',
      id: 6,
      original: { text: '比較の後', result: { ...fixture, model: 'different-model' } },
      rewritten: { text: '比較の後', result: fixture },
    })
  })

  // テストケース: 比較元と同じ文章を判定し、その結果から同じ文章を再比較する。
  // 期待値: 比較元と同じ文章でも判定でき、比較結果のどちら側からも再選択して別の判定IDで履歴に追加できる。
  test.each(['original', 'rewritten'] as const)(
    'allows same text and repeated comparison from %s',
    (side) => {
      const selected = transition(history(), {
        type: 'select',
        source,
        replacementConfirmed: false,
      })
      const done = transition(transition(selected, { type: 'start', id: 5 }), {
        type: 'succeed',
        id: 5,
        result: result(),
      })
      const again = transition(done, {
        type: 'select',
        source: { entryId: 5, side },
        replacementConfirmed: false,
      })
      const repeated = transition(transition(again, { type: 'start', id: 6 }), {
        type: 'succeed',
        id: 6,
        result: result(),
      })
      expect(repeated.entries).toHaveLength(6)
      expect(repeated.entries[5]).toEqual({
        kind: 'comparison',
        id: 6,
        original: { text: '元の文章', result: fixture },
        rewritten: { text: '元の文章', result: fixture },
      })
    },
  )
})

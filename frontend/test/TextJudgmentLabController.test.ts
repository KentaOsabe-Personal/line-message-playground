import { inspectionFor, v2Request } from './textJudgmentLabV2Fixture'
import { describe, expect, it, vi } from 'vitest'

import { createTextJudgmentLabController } from '../src/useTextJudgmentLab'
import type { JudgmentResponse } from '../src/textJudgmentLabTypes'

const response = (consultationId: string, requestId: string, revision: number): JudgmentResponse => ({
  contractVersion: 2,
  consultationId,
  requestId,
  revision,
  model: 'jev-test',
  inspection: inspectionFor(v2Request()),
  evidence: {
    topic: { kind: 'known', value: 'missing_notification' },
    relevance: 'in_scope',
    change: 'keep',
    scope: { kind: 'known', value: 'all' },
    workaround: { kind: 'known', value: 'can_read' },
    result: { kind: 'unmentioned' },
    impact: 'low',
    urgency: { kind: 'known', value: false },
  },
  details: {
    choices: Object.fromEntries(['topic', 'relevance', 'change', 'scope', 'workaround', 'result', 'impact_evidence'].map((key) => [key, { type: 'choice', choice: 'x', probabilities: { x: 1 }, confidence: 1 }])) as unknown as JudgmentResponse['details']['choices'],
    score: { type: 'score', score: 0.2, legend: { '0': '支障なし', '1': '不便だが別の操作で目的を達成できる', '2': '目的を達成できない' }, probabilities: { '0': 0.9, '1': 0.1, '2': 0 }, confidence: 0.9 },
    noul: { type: 'noul', noul: 0.1 },
    jevElapsedMs: 123,
  },
})

describe('文章判定ラボcontroller', () => {
  it('期限切れの後着結果を捨て、本文をdraftへ戻して自動再送しない', async () => {
    let finish!: (value: JudgmentResponse) => void
    let now = 0
    const judge = vi.fn(() => new Promise<JudgmentResponse>((resolve) => { finish = resolve }))
    const controller = createTextJudgmentLabController({ judge }, { now: () => now, uuid: vi.fn().mockReturnValueOnce('consultation-1').mockReturnValueOnce('request-1') })

    const pending = controller.submit('通知が来ません', 'text', 'token')
    const sent = controller.getState()
    expect(sent.pending?.deadlineAt).toBe(15_000)
    now = 15_000
    finish(response(sent.core.consultationId, sent.pending!.requestId, sent.core.revision))
    await pending

    expect(controller.getState().pending).toBeNull()
    expect(controller.getState().draft).toBe('通知が来ません')
    expect(controller.getState().messages.at(-1)?.kind).toBe('failed')
    expect(judge).toHaveBeenCalledTimes(1)
  })

  it('中断と新規開始は通信をabortし、相談IDが異なる後着結果を反映しない', async () => {
    let finish!: (value: JudgmentResponse) => void
    const judge = vi.fn((_token: string, _request: unknown, signal?: AbortSignal) => new Promise<JudgmentResponse>((resolve) => {
      finish = resolve
      expect(signal).toBeInstanceOf(AbortSignal)
    }))
    const uuid = vi.fn().mockReturnValueOnce('consultation-1').mockReturnValueOnce('request-1').mockReturnValueOnce('message-1').mockReturnValueOnce('consultation-2')
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid })
    const pending = controller.submit('相談', 'text', 'token')
    const old = controller.getState()
    controller.restart()
    finish(response(old.core.consultationId, old.pending!.requestId, old.core.revision))
    await pending
    expect(controller.getState().core.consultationId).toBe('consultation-2')
    expect(controller.getState().messages).toEqual([])
  })

  it('選択肢はHTTP通信せず、revision不一致の過去選択肢を拒否する', () => {
    const judge = vi.fn()
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValue('id') })
    const revision = controller.getState().core.revision
    expect(controller.choose('topic', 'missing_notification', revision, true)).toBe(true)
    expect(controller.choose('scope', 'all', revision, true)).toBe(false)
    expect(judge).not.toHaveBeenCalled()
  })

  it('15秒応答がなければabortしてsnapshotへ戻す', async () => {
    vi.useFakeTimers()
    try {
      let signal: AbortSignal | undefined
      const judge = vi.fn((_token: string, _request: unknown, value?: AbortSignal) => {
        signal = value
        return new Promise<JudgmentResponse>(() => undefined)
      })
      const controller = createTextJudgmentLabController({ judge }, { now: () => Date.now(), uuid: vi.fn().mockReturnValueOnce('c').mockReturnValueOnce('r').mockReturnValueOnce('m') })
      void controller.submit('相談', 'text', 'token')
      await vi.advanceTimersByTimeAsync(15_000)
      expect(signal?.aborted).toBe(true)
      expect(controller.getState().pending).toBeNull()
      expect(controller.getState().draft).toBe('相談')
    } finally {
      vi.useRealTimers()
    }
  })

  it('認証失効を認証境界へ通知し、判定前snapshotを維持する', async () => {
    const onAccessFailure = vi.fn()
    const judge = vi.fn(async () => { throw new (await import('../src/textJudgmentLabApi')).LabHttpError('reauthentication_required') })
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValueOnce('c').mockReturnValueOnce('r').mockReturnValueOnce('m'), onAccessFailure })
    await controller.submit('相談', 'text', 'token')
    expect(onAccessFailure).toHaveBeenCalledWith('auth_expired')
    expect(controller.getState().core.revision).toBe(0)
  })

  // テストケース: 要求Aを新規相談で破棄した直後に要求Bを送る
  // 期待値: AのfinallyはBのdeadlineとabortを消さず、Bは15秒で失敗する
  it('置換済み要求のfinallyが新しい要求のdeadlineを解除しない', async () => {
    vi.useFakeTimers()
    try {
      let finishA!: (value: JudgmentResponse) => void
      const judge = vi.fn()
        .mockImplementationOnce(() => new Promise<JudgmentResponse>((resolve) => { finishA = resolve }))
        .mockImplementationOnce(() => new Promise<JudgmentResponse>(() => undefined))
      const uuid = vi.fn()
        .mockReturnValueOnce('c1').mockReturnValueOnce('r1').mockReturnValueOnce('m1')
        .mockReturnValueOnce('c2').mockReturnValueOnce('r2').mockReturnValueOnce('m2')
      const controller = createTextJudgmentLabController({ judge }, { now: () => Date.now(), uuid })
      const requestA = controller.submit('A', 'text', 'token')
      const old = controller.getState().pending!
      controller.restart()
      void controller.submit('B', 'text', 'token')
      finishA(response(old.consultationId, old.requestId, old.revision))
      await requestA
      await vi.advanceTimersByTimeAsync(15_000)
      expect(controller.getState().pending).toBeNull()
      expect(controller.getState().draft).toBe('B')
    } finally {
      vi.useRealTimers()
    }
  })

  // テストケース: 一般障害とaccess unavailableを受ける
  // 期待値: snapshotと本文を復帰し、自動再送せず、access障害だけ認証境界へ通知する
  it('一般障害とaccess unavailableを区別して本文を一度だけ復帰する', async () => {
    const { LabHttpError } = await import('../src/textJudgmentLabApi')
    const onAccessFailure = vi.fn()
    const judge = vi.fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockRejectedValueOnce(new LabHttpError('access_unavailable'))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValueOnce('c').mockReturnValueOnce('r1').mockReturnValueOnce('m1').mockReturnValueOnce('r2').mockReturnValueOnce('m2'), onAccessFailure })
    await controller.submit('一回目', 'text', 'token')
    expect(controller.getState().draft).toBe('一回目')
    expect(judge).toHaveBeenCalledTimes(1)
    controller.setDraft('二回目')
    await controller.submit('二回目', 'text', 'token')
    expect(controller.getState().failure).toBe('access_unavailable')
    expect(onAccessFailure).toHaveBeenCalledWith('access_unavailable')
    expect(judge).toHaveBeenCalledTimes(2)
  })

  // テストケース: requestId、revision、相談IDが応答と一致しない
  // 期待値: いずれも判定を採用せず、snapshotへ戻る
  it.each([
    ['requestId', (value: JudgmentResponse) => ({ ...value, requestId: 'different' })],
    ['revision', (value: JudgmentResponse) => ({ ...value, revision: value.revision + 1 })],
    ['相談ID', (value: JudgmentResponse) => ({ ...value, consultationId: 'different' })],
  ])('%s不一致の応答を捨てる', async (_label, mutate) => {
    const judge = vi.fn(async (_token: string, request: { consultationId: string; requestId: string; revision: number }) => mutate(response(request.consultationId, request.requestId, request.revision)))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValueOnce('c').mockReturnValueOnce('r').mockReturnValueOnce('m') })
    await controller.submit('相談', 'text', 'token')
    expect(controller.getState().core.revision).toBe(0)
    expect(controller.getState().messages[0].kind).toBe('failed')
  })

  // テストケース: controllerを同じpageで使い続ける
  // 期待値: 会話はメモリだけに残り、Web Storageへ書き込まない
  it('page保持中は会話を継続し、storageへ保存しない', () => {
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const controller = createTextJudgmentLabController({ judge: vi.fn() }, { now: () => 1, uuid: vi.fn().mockReturnValue('id') })
    controller.setDraft('保持する下書き')
    expect(controller.getState().draft).toBe('保持する下書き')
    expect(storage).not.toHaveBeenCalled()
    storage.mockRestore()
  })
})

describe('task 10.4 発言時の記録', () => {
  // テストケース: 範囲と結果を日本語の選択肢で回答し、その後の判定に送信する。
  // 期待値: 選択肢で回答した発言には、回答時のラベル、回答前の会話状態、適用結果だけを保存し、後から変更しない。
  it('日本語選択ラベルと入力前snapshotを固定する', async () => {
    let sequence = 0
    const judge = vi.fn(async (_token, request) => response(request.consultationId, request.requestId, request.revision))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: () => `id-${sequence++}` })
    controller.choose('topic', 'notification_settings', 0, true)
    controller.choose('scope', 'all', 1, true)
    const choice = controller.getState().messages[1]
    expect(choice.kind).toBe('choice')
    expect(choice.text).toBe('全体')
    expect(choice.previousQuestion.prompt).toBe('通知を設定したい範囲を教えてください。')
    expect(choice.before.confirmed.scope).toBeNull()
    expect('judgment' in choice).toBe(false)
    if (choice.kind !== 'choice') throw new Error('choice required')
    expect(choice.application.decisions).toEqual([])
    expect(choice.application.newlyConfirmed).toEqual([{ field: 'scope', value: 'all' }])
    const frozen = JSON.stringify(choice)
    controller.choose('urgency', 'no', 2, true)
    controller.choose('result', 'not_tried', 3, true)
    expect(controller.getState().messages[3].text).toBe('まだ試していない')
    await controller.submit('確認します', 'text', 'token')
    expect(judge.mock.calls[0][1].context.recentUserTexts).toEqual(['急いでいない', 'まだ試していない'])
    expect(JSON.stringify(choice)).toBe(frozen)
    expect(Object.isFrozen(choice.before.confirmed)).toBe(true)
    expect(Object.isFrozen(choice.application.skipped)).toBe(true)
  })
  // テストケース: 判定待ちの記録を確認し、判定成功後に別の操作と元の応答データの変更を行う。
  // 期待値: 要求、入力前の会話状態、質問、判定、適用結果を同じ発言に保存する。内部の配列や項目も後から変更しない。
  it('pendingを成功記録へ一度だけ置き換え、過去の内部値を保持する', async () => {
    let resolve!: (result: JudgmentResponse) => void
    let sequence = 0
    let now = 10
    const judge = vi.fn(() => new Promise<JudgmentResponse>(finish => { resolve = finish }))
    const controller = createTextJudgmentLabController({ judge }, { now: () => now, uuid: () => `id-${sequence++}` })
    const work = controller.submit('通知が来ない', 'example', 'token')
    const pending = controller.getState().messages[0]
    expect(pending.kind).toBe('pending')
    expect(pending.previousQuestion.prompt).toBe('どちらについて相談しますか？')
    expect('judgment' in pending).toBe(false)
    if (pending.kind !== 'pending') throw new Error('pending required')
    expect(pending.request.context.confirmed).toEqual(pending.before.confirmed)
    expect(Object.isFrozen(pending.request.context.recentUserTexts)).toBe(true)
    const result = response(pending.request.consultationId, pending.request.requestId, pending.request.revision)
    now = 60
    resolve(result)
    await work
    const record = controller.getState().messages[0]
    if (record.kind !== 'judged') throw new Error('judged required')
    expect(record.id).toBe(pending.id)
    expect(record.before).toEqual(pending.before)
    expect(record.application.next).toEqual(controller.getState().core.stage)
    expect(record.uiElapsedMs).toBe(50)
    const frozen = JSON.stringify(record)
    result.details.choices.scope.probabilities = { damaged: 1 }
    controller.choose('result', 'not_tried', 1, true)
    controller.setInteractive(false)
    expect(JSON.stringify(record)).toBe(frozen)
    expect(Object.isFrozen(record.judgment.details.choices.scope.probabilities)).toBe(true)
    controller.dispose()
    expect(controller.getState().messages).toEqual([])
  })
  // テストケース: 一般的な判定失敗、認証失効、利用確認の障害、相談の中断をそれぞれ発生させる。
  // 期待値: 入力前の会話状態と入力内容を保持する。結果を適用しなかった発言の記録には、判定、適用結果、要求、数値を保存しない。
  it('失敗種別と中断を非適用unionとして固定する', async () => {
    const { LabHttpError } = await import('../src/textJudgmentLabApi')
    let sequence = 0
    for (const [error, failure] of [[new Error('network'), 'judgment_failed'],
      [new LabHttpError('reauthentication_required'), 'auth_expired'],
      [new LabHttpError('access_unavailable'), 'access_unavailable']] as const) {
      const controller = createTextJudgmentLabController({ judge: vi.fn().mockRejectedValue(error) }, { uuid: () => `id-${sequence++}` })
      await controller.submit('復帰する入力', 'text', 'token')
      const record = controller.getState().messages[0]
      expect(record).toMatchObject({ kind: 'failed', failure, text: '復帰する入力' })
      expect(controller.getState().core).toEqual(record.before)
      expect(controller.getState().draft).toBe('復帰する入力')
      for (const key of ['judgment', 'application', 'request', 'uiElapsedMs']) expect(key in record).toBe(false)
    }
    let resolve!: (value: JudgmentResponse) => void
    const controller = createTextJudgmentLabController({ judge: vi.fn(() => new Promise<JudgmentResponse>(finish => { resolve = finish })) }, { uuid: () => `id-${sequence++}` })
    const pending = controller.submit('中断する入力', 'text', 'token')
    const request = controller.getState().pending!
    controller.interrupt()
    const record = controller.getState().messages[0]
    expect(record.kind).toBe('interrupted')
    for (const key of ['judgment', 'application', 'request', 'uiElapsedMs']) expect(key in record).toBe(false)
    resolve(response(request.consultationId, request.requestId, request.revision))
    await pending
    expect(controller.getState().messages[0]).toBe(record)
    controller.restart()
    expect(controller.getState().messages).toEqual([])
  })
  // テストケース: 古い会話の更新番号revision、認証による操作停止、判定待ち、許可されていない選択肢による回答を試みる。
  // 期待値: 拒否した操作では発言を作らない。選択肢のみで回答する制約と、1000 Unicodeコードポイントの入力上限も守る。
  it('受付拒否は発言や要求を作らない', async () => {
    let sequence = 0
    const judge = vi.fn(async (_token, request) => {
      const result = response(request.consultationId, request.requestId, request.revision)
      result.evidence = { ...result.evidence, scope: { kind: 'needs_review' }, topic: { kind: 'unmentioned' }, urgency: { kind: 'unmentioned' }, workaround: { kind: 'unmentioned' } }
      return result
    })
    const controller = createTextJudgmentLabController({ judge }, { uuid: () => `id-${sequence++}` })
    expect(controller.choose('topic', 'bad', 0, true)).toBe(false)
    expect(controller.choose('scope', 'all', 0, true)).toBe(false)
    expect(controller.choose('topic', 'missing_notification', 0, false)).toBe(false)
    controller.setInteractive(false)
    expect(controller.choose('topic', 'missing_notification', 0, true)).toBe(false)
    expect(controller.getState().messages).toEqual([])
    controller.setInteractive(true)
    controller.choose('topic', 'missing_notification', 0, true)
    expect(controller.choose('scope', 'all', 0, true)).toBe(false)
    await controller.submit('🙂'.repeat(1001), 'text', 'token')
    expect(judge).not.toHaveBeenCalled()
    await controller.submit('曖昧1', 'text', 'token')
    await controller.submit('曖昧2', 'text', 'token')
    const before = controller.getState().messages.length
    await controller.submit('禁止入力', 'text', 'token')
    expect(controller.getState().messages).toHaveLength(before)
    expect(judge).toHaveBeenCalledTimes(2)
  })
})

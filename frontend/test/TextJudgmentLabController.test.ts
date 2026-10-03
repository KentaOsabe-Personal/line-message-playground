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
    expect(controller.getState().messages.at(-1)?.status).toBe('failed')
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
    expect(controller.getState().messages[0].status).toBe('failed')
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

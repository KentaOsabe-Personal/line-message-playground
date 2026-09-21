import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TextJudgmentDetails from '../src/TextJudgmentDetails'
import TextJudgmentLab from '../src/TextJudgmentLab'
import TextJudgmentLabPage from '../src/TextJudgmentLabPage'
import { LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import { createTextJudgmentLabController } from '../src/useTextJudgmentLab'
import type { JudgmentResponse } from '../src/textJudgmentLabTypes'

describe('文章判定ラボ表示', () => {
  let container: HTMLDivElement
  let root: Root
  beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
  afterEach(async () => { await act(async () => root.unmount()); container.remove() })

  it('外部送信説明、例文、label付き入力と会話logを表示し、IME中Enterでは送信しない', async () => {
    const judge = vi.fn((_token: string, _request: unknown, _signal?: AbortSignal) => new Promise<JudgmentResponse>(() => undefined))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValue('id') })
    await act(async () => root.render(<TextJudgmentLab controller={controller} access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }} getValidIdToken={() => 'token'} />))
    expect(container.textContent).toContain('外部サービスJev')
    expect(container.textContent).toContain('どちらについて相談しますか？')
    expect(container.textContent).toContain('通知が届かない')
    expect(container.querySelector('[role="log"]')).not.toBeNull()
    const textarea = container.querySelector('textarea')!
    expect(container.querySelector(`label[for="${textarea.id}"]`)).not.toBeNull()
    await act(async () => {
      textarea.value = '変換中'
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
      textarea.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
      textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(judge).not.toHaveBeenCalled()
  })

  // テストケース: 通常の自由文をtextareaへ入力して送信する
  // 期待値: 現在の相談文脈と本文を一回だけjudgeへ渡す
  it('自由文をUIから一回だけ判定要求へ渡す', async () => {
    const judge = vi.fn((_token: string, _request: unknown, _signal?: AbortSignal) => new Promise<JudgmentResponse>(() => undefined))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValueOnce('c').mockReturnValueOnce('r').mockReturnValueOnce('m') })
    await act(async () => root.render(<TextJudgmentLab controller={controller} access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }} getValidIdToken={() => 'token'} />))
    const textarea = container.querySelector('textarea')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(textarea, '通知が来ないです')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(judge).toHaveBeenCalledTimes(1)
    expect(judge.mock.calls[0][1]).toMatchObject({ text: '通知が来ないです', context: { question: 'start' } })
  })

  it('判定詳細を初期状態で閉じ、全数値と二つの測定区間を表示する', async () => {
    const judgment = {
      contractVersion: 1, consultationId: 'c', requestId: 'r', revision: 0, model: 'jev-model',
      evidence: {} as JudgmentResponse['evidence'],
      details: {
        choices: {
          topic: { type: 'choice', choice: 'missing_notification', probabilities: { missing_notification: 0.8123, notification_settings: 0.1877 }, confidence: 0.77 },
          relevance: { type: 'choice', choice: 'in_scope', probabilities: { in_scope: 0.91, out_of_scope: 0.09 }, confidence: 0.82 },
          change: { type: 'choice', choice: 'keep', probabilities: { keep: 0.88, restart: 0.12 }, confidence: 0.83 },
          scope: { type: 'choice', choice: 'all', probabilities: { all: 0.7, specific: 0.3 }, confidence: 0.74 },
          workaround: { type: 'choice', choice: 'can_read', probabilities: { can_read: 0.68, cannot_read: 0.32 }, confidence: 0.69 },
          result: { type: 'choice', choice: 'unmentioned', probabilities: { unmentioned: 0.95, done: 0.05 }, confidence: 0.9 },
          impact_evidence: { type: 'choice', choice: 'present', probabilities: { present: 0.76, absent: 0.24 }, confidence: 0.71 },
        },
        score: { type: 'score', score: 1.25, legend: { '0': '支障なし', '1': '不便だが別の操作で目的を達成できる', '2': '目的を達成できない' }, probabilities: { '0': 0.1, '1': 0.6, '2': 0.3 }, confidence: 0.66 },
        noul: { type: 'noul', noul: 0.234 }, jevElapsedMs: 123,
      },
    } as unknown as JudgmentResponse
    await act(async () => root.render(<TextJudgmentDetails judgment={judgment} uiElapsedMs={456} />))
    const details = container.querySelector('details')!
    expect(details.open).toBe(false)
    expect(container.textContent).toContain('81.2%')
    expect(container.textContent).toContain('1.25')
    expect(container.textContent).toContain('通信と本人確認を含む待ち時間: 456 ms')
    expect(container.textContent).toContain('Jev通信と応答検証の時間: 123 ms')
    expect(container.textContent).toContain('正答率の保証ではありません')
    expect(container.textContent).toContain('通知が届かない')
    expect(container.textContent).toContain('支障の記述あり: 76.0%')
    expect(container.textContent).toContain('対象内: 91.0%')
    expect(container.textContent).toContain('現在の相談を継続: 88.0%')
    expect(container.textContent).toContain('全体・すべてのトーク: 70.0%')
    expect(container.textContent).toContain('LINEを開けば確認できる: 68.0%')
    expect(container.textContent).toContain('未言及: 95.0%')
    expect(container.textContent).toContain('confidence: 90.0%')
    expect(container.textContent).toContain('0: 10.0%')
    expect(container.textContent).toContain('1: 60.0%')
    expect(container.textContent).toContain('2: 30.0%')
    expect(container.textContent).toContain('急ぎの要望がある確率: 23.4%')
    expect(container.textContent).toContain('jev-model')
    expect(container.textContent).not.toContain('missing_notification:')
  })

  // テストケース: 現在の選択肢を選ぶ
  // 期待値: HTTP通信せず、本人発言へ「選択肢で回答」と表示し、過去の選択肢を操作不能にする
  it('選択肢回答を明示し、pending・終了状態で許可操作だけを残す', async () => {
    const judge = vi.fn(() => new Promise<JudgmentResponse>(() => undefined))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValue('id') })
    await act(async () => root.render(<TextJudgmentLab controller={controller} access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }} getValidIdToken={() => 'token'} />))
    const topic = [...container.querySelectorAll('button')].find((button) => button.textContent === '通知が届かない')!
    await act(async () => topic.click())
    expect(judge).not.toHaveBeenCalled()
    expect(container.textContent).toContain('選択肢で回答')
    expect(topic.isConnected).toBe(false)
    await act(async () => controller.interrupt())
    expect(container.querySelector('textarea')).toBeNull()
    expect(container.textContent).toContain('相談を中断しました')
    expect(container.textContent).toContain('新しい相談を始める')
  })

  // テストケース: 例文を送信して判定待ちになり、一般障害で失敗する
  // 期待値: 待機中は入力・選択肢だけを止め、中断・新規は残し、失敗後に本文とfocusを復帰する
  it('例文のpendingとfailureで操作性とfocusを切り替える', async () => {
    let reject!: (error: Error) => void
    const judge = vi.fn(() => new Promise<JudgmentResponse>((_resolve, rejectPromise) => { reject = rejectPromise }))
    const controller = createTextJudgmentLabController({ judge }, { now: () => 1, uuid: vi.fn().mockReturnValueOnce('c').mockReturnValueOnce('r').mockReturnValueOnce('m') })
    await act(async () => root.render(<TextJudgmentLab controller={controller} access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }} getValidIdToken={() => 'token'} />))
    const example = [...container.querySelectorAll('button')].find((button) => button.textContent === 'LINEの通知が届きません')!
    await act(async () => example.click())
    expect(container.textContent).toContain('判定中です')
    expect(container.querySelector('textarea')?.disabled).toBe(true)
    expect([...container.querySelectorAll('button')].find((button) => button.textContent === '相談を終了する')?.disabled).toBe(false)
    await act(async () => reject(new Error('network')))
    const textarea = container.querySelector('textarea')!
    expect(container.textContent).toContain('判定できませんでした')
    expect(textarea.value).toBe('LINEの通知が届きません')
    expect(document.activeElement).toBe(textarea)
    expect(judge).toHaveBeenCalledTimes(1)
  })

  // テストケース: 独立ラボpageで本人不一致になる
  // 期待値: PageFrameと拒否理由だけを表示し、相談本文・入力・管理shellを表示しない
  it('独立pageの初回認証拒否では会話UIを隠す', async () => {
    const liffAdapter: LinePlatformLiffAdapter = {
      initialize: vi.fn().mockResolvedValue('liff_browser'), ensureProfilePermission: vi.fn(), isLoggedIn: vi.fn().mockReturnValue(true),
      login: vi.fn(), reauthenticate: vi.fn(), logout: vi.fn(), getIdToken: vi.fn().mockReturnValue('token'), getAccessToken: vi.fn().mockReturnValue(null),
    }
    const api: LabHttpClient = {
      checkAccess: vi.fn().mockRejectedValue(new LabHttpError('not_allowed')),
      judge: vi.fn(),
    }
    await act(async () => root.render(<TextJudgmentLabPage api={api} authGateProps={{
      liffAdapter,
      config: { liffId: '123-lab', liffUrl: 'https://liff.line.me/123-lab', entryUrl: 'https://lab.example.test/labs/text-judgment' },
    }} />))
    expect(container.textContent).toContain('文章判定ラボ')
    expect(container.textContent).toContain('このラボは利用できません')
    expect(container.querySelector('textarea')).toBeNull()
    expect(container.textContent).not.toContain('外部サービスJev')
  })
})

import { inspectionFor, v2Request } from './textJudgmentLabV2Fixture'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import TextJudgmentLab from '../src/TextJudgmentLab'
import TextJudgmentLabPage from '../src/TextJudgmentLabPage'
import { LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import { createTextJudgmentLabController, type TextJudgmentLabController } from '../src/useTextJudgmentLab'
import type { JudgmentRequest, JudgmentResponse } from '../src/textJudgmentLabTypes'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type EvidenceOverrides = Partial<JudgmentResponse['evidence']>

const baseEvidence = (): JudgmentResponse['evidence'] => ({
  topic: { kind: 'unmentioned' },
  relevance: 'in_scope',
  change: 'keep',
  scope: { kind: 'unmentioned' },
  workaround: { kind: 'unmentioned' },
  result: { kind: 'unmentioned' },
  impact: 'low',
  urgency: { kind: 'unmentioned' },
})

function responseFor(
  request: JudgmentRequest,
  evidence: EvidenceOverrides,
  observed: { score?: number; noul?: number; topicChoice?: string } = {},
): JudgmentResponse {
  const topicChoice = observed.topicChoice ?? (
    evidence.topic?.kind === 'known' ? evidence.topic.value : 'unmentioned'
  )
  const resolvedEvidence = { ...baseEvidence(), ...evidence }
  const evidenceChoice = <T,>(value: { kind: 'known'; value: T } | { kind: 'unmentioned' } | { kind: 'needs_review' }) =>
    value.kind === 'known' ? String(value.value) : value.kind === 'needs_review' ? 'unclear' : 'unmentioned'
  const choice = (value: string, values: readonly string[]) => ({
    type: 'choice' as const,
    choice: value,
    probabilities: Object.fromEntries(values.map((candidate) => [candidate, candidate === value ? 1 : 0])),
    confidence: 1,
  })
  return {
    contractVersion: 2,
    consultationId: request.consultationId,
    requestId: request.requestId,
    revision: request.revision,
    model: 'jev-1.13.0',
    inspection: inspectionFor(request),
    evidence: resolvedEvidence,
    details: {
      choices: {
        topic: choice(topicChoice, ['missing_notification', 'notification_settings', 'both', 'unmentioned', 'unclear']),
        relevance: choice(resolvedEvidence.relevance === 'needs_review' ? 'unclear' : resolvedEvidence.relevance, ['in_scope', 'mixed', 'out_of_scope', 'unclear']),
        change: choice(resolvedEvidence.change === 'needs_review' ? 'unclear' : resolvedEvidence.change, ['keep', 'restart', 'unclear']),
        scope: choice(evidenceChoice(resolvedEvidence.scope), ['all', 'specific', 'unknown', 'unmentioned', 'unclear']),
        workaround: choice(evidenceChoice(resolvedEvidence.workaround), ['can_read', 'cannot_read', 'unknown', 'unmentioned', 'unclear']),
        result: choice(evidenceChoice(resolvedEvidence.result), ['done', 'not_done', 'not_tried', 'cannot_check', 'unmentioned', 'unclear']),
        impact_evidence: choice(resolvedEvidence.impact === 'needs_review' ? 'unclear' : 'present', ['present', 'absent', 'unclear']),
      },
      score: {
        type: 'score',
        score: observed.score ?? 0,
        legend: { '0': '支障なし', '1': '不便だが別の操作で目的を達成できる', '2': '目的を達成できない' },
        probabilities: { '0': observed.score === 0 ? 1 : 0, '1': observed.score === 1 ? 1 : 0, '2': observed.score === 2 ? 1 : 0 },
        confidence: 1,
      },
      noul: { type: 'noul', noul: observed.noul ?? 0 },
      jevElapsedMs: 25,
    },
  }
}

function fixedJudge(...fixtures: readonly [EvidenceOverrides, { score?: number; noul?: number; topicChoice?: string }?][]) {
  let index = 0
  return vi.fn(async (_token: string, request: JudgmentRequest) => {
    const fixture = fixtures[index++]
    if (fixture === undefined) throw new Error('FIXTURE_EXHAUSTED')
    return responseFor(request, fixture[0], fixture[1])
  })
}

describe('文章判定ラボの相談全体', () => {
  let container: HTMLDivElement
  let root: Root
  let id = 0

  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    id = 0
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  const renderLab = async (judge: LabHttpClient['judge']) => {
    const controller = createTextJudgmentLabController({ judge }, { now: () => 100, uuid: () => `id-${++id}` })
    await act(async () => root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01T00:00:00Z', remainingMs: 60_000 }}
        getValidIdToken={() => 'id-token'}
      />,
    ))
    return controller
  }

  const click = async (label: string) => {
    const button = [...container.querySelectorAll('button')].find((candidate) => candidate.textContent === label)
    expect(button, `button: ${label}`).toBeDefined()
    await act(async () => button!.click())
  }

  const submitText = async (text: string) => {
    const textarea = container.querySelector('textarea')
    expect(textarea).not.toBeNull()
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(textarea, text)
      textarea!.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  }

  // テストケース: 通知不達の例文をhighかつ急ぎの固定判定へ通し、各質問へ選択肢で回答する。
  // 期待値: 範囲、回避策、急ぎ、要点先行案内、結果確認、解決終了まで一問ずつ進む。
  test('7.1 例文から通知不達のhigh・急ぎ経路を解決まで進める', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'missing_notification' },
      impact: 'high',
      urgency: { kind: 'needs_review' },
    }, { score: 2, noul: 0.5 }])
    const controller = await renderLab(judge)

    await click('LINEの通知が届きません')
    expect(container.textContent).toContain('通知が届かない範囲を教えてください。')
    await click('すべてのトーク')
    expect(container.textContent).toContain('LINEを開けばメッセージを確認できますか？')
    await click('確認できる')
    expect(container.textContent).toContain('お急ぎですか？')
    await click('急いでいる')
    expect(container.textContent).toContain('iPhoneとLINEの通知設定を確認してください。')
    expect(container.querySelector('.lab-guide details')?.hasAttribute('open')).toBe(false)
    await click('解決した')

    expect(container.textContent).toContain('通知が届くようになりました。相談を終了します。')
    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'resolved' })
    expect(judge).toHaveBeenCalledTimes(1)
  })

  // テストケース: 通知不達を自由文で送り、LINEを開いても読めない固定判定を返す。
  // 期待値: 急ぎを聞かず即時に未解決終了し、公式ヘルプへの安全なリンクを示す。
  test('7.1 cannot_readは急ぎ確認なしで公式ヘルプ付き未解決終了にする', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'specific' },
      workaround: { kind: 'known', value: 'cannot_read' },
      impact: 'high',
      urgency: { kind: 'needs_review' },
    }, { score: 2, noul: 0.5 }])
    const controller = await renderLab(judge)

    await submitText('通知を開いてもメッセージが読めません')

    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'unresolved' })
    expect(container.textContent).not.toContain('お急ぎですか？')
    const help = container.querySelector<HTMLAnchorElement>('a[href^="https://"]')
    expect(help?.textContent).toContain('LINE公式案内')
    expect(help?.href).toMatch(/^https:\/\/(help|guide)\.line\.me\//)
    expect(help?.rel).toContain('noopener')
    expect(help?.rel).toContain('noreferrer')
  })

  // テストケース: 支障が要確認の通知不達相談で案内へ進み、まだ届かないと回答する。
  // 期待値: 回避策を確認し、未解決終了後も通知向け公式ヘルプを示す。
  test('7.1 impact要確認とnot_doneを回避策確認・公式ヘルプへ接続する', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
      impact: 'needs_review',
      urgency: { kind: 'known', value: false },
    }, { score: 1.5, noul: 0 }])
    const controller = await renderLab(judge)

    await submitText('通知が来ないようですが支障の程度は説明できません')
    expect(container.textContent).toContain('LINEを開けばメッセージを確認できますか？')
    await click('分からない')
    await click('まだ届かない')

    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'unresolved' })
    expect(container.querySelector<HTMLAnchorElement>('a[href^="https://"]')?.textContent).toContain('LINE公式案内')
  })

  // テストケース: 言い換え、否定、曖昧回答を固定応答で順に判定する。
  // 期待値: Choice・Score・Noulの詳細を発言へ残し、曖昧回答だけ現在質問を維持する。
  test('7.1 言い換え・否定・曖昧回答の判定詳細と分岐を比較できる', async () => {
    const judge = fixedJudge(
      [{ topic: { kind: 'known', value: 'missing_notification' }, scope: { kind: 'known', value: 'all' }, impact: 'high' }, { score: 2, noul: 0.1 }],
      [{ workaround: { kind: 'known', value: 'can_read' }, impact: 'high' }, { score: 2, noul: 0.1 }],
      [{ urgency: { kind: 'needs_review' }, impact: 'high' }, { score: 2, noul: 0.5 }],
    )
    const controller = await renderLab(judge)

    await submitText('どのトークも通知されません')
    expect(container.textContent).toContain('LINEを開けばメッセージを確認できますか？')
    await submitText('開かないと読めないわけではありません')
    expect(container.textContent).toContain('お急ぎですか？')
    await submitText('どちらとも言えません')

    expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'urgency' })
    expect(controller.getState().core.clarification).toEqual({ question: 'urgency', mode: 'open' })
    const details = container.querySelectorAll('details.lab-judgment-details')
    expect(details).toHaveLength(3)
    expect(details[0].textContent).toContain('2.00')
    expect(details[0].textContent).toContain('通知の範囲: 全体・すべてのトーク')
    expect(details[1].textContent).toContain('代替確認: LINEを開けば確認できる')
    expect(details[0].textContent).toContain('急ぎの要望がある確率: 10.0%')
    expect(details[2].textContent).toContain('急ぎの要望がある確率: 50.0%')
  })

  // テストケース: 設定相談の自由文を特定トーク、Score high、急ぎなしとして固定判定する。
  // 期待値: 回避策を質問せず詳細を開いた設定案内へ進み、設定完了で終了する。
  test('7.2 設定相談はScore highでも回避策を省略して設定完了まで進める', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'specific' },
      impact: 'high',
      urgency: { kind: 'known', value: false },
    }, { score: 2, noul: 0 }])
    const controller = await renderLab(judge)

    await submitText('特定のトークだけ通知を設定したいです')

    expect(container.textContent).not.toContain('LINEを開けばメッセージを確認できますか？')
    expect(container.textContent).toContain('対象トークの通知を切り替えます。')
    expect(container.querySelector('.lab-guide details')?.hasAttribute('open')).toBe(true)
    await click('設定できた')
    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'settings_completed' })
    expect(container.textContent).toContain('通知設定を完了しました。相談を終了します。')
  })

  // テストケース: 設定相談の全体案内で未試行と確認不能を順に選ぶ。
  // 期待値: どちらでも終了せず、同じ案内と結果選択肢を保持する。
  test.each([
    ['まだ試していない', 'not_tried'],
    ['確認できない', 'cannot_check'],
  ])('7.2 %sでは設定案内と結果回答を保持する', async (label, expected) => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'all' },
      impact: 'high',
      urgency: { kind: 'known', value: false },
    }, { score: 2, noul: 0 }])
    const controller = await renderLab(judge)

    await submitText('通知設定を全体で変えたいです')
    await click(label)

    expect(controller.getState().core.stage).toMatchObject({ kind: 'guidance', guideId: 'settings_all' })
    expect(container.textContent).toContain('iPhoneとLINEの通知を希望に合わせて設定します。')
    expect(container.textContent).toContain(label)
    expect(controller.getState().messages.at(-1)).toMatchObject({ source: 'choice', text: expected })
  })

  // テストケース: 設定相談の案内後に設定失敗を選ぶ。
  // 期待値: 未解決終了し、設定向け公式ヘルプへの固定HTTPSリンクを示す。
  test('7.2 設定失敗は公式ヘルプ付き未解決終了にする', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'known', value: 'all' },
      impact: 'high',
      urgency: { kind: 'known', value: false },
    }, { score: 2, noul: 0 }])
    const controller = await renderLab(judge)

    await submitText('通知設定の方法を知りたいです')
    await click('設定できない')

    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'unresolved' })
    expect(container.textContent).toContain('通知を設定できないため、この相談は未解決として終了します。')
    const help = container.querySelector<HTMLAnchorElement>('a[href^="https://"]')
    expect(help?.href).toMatch(/^https:\/\/(help|guide)\.line\.me\//)
    expect(help?.rel).toContain('noopener')
    expect(help?.rel).toContain('noreferrer')
  })

  // テストケース: 同じ質問へ曖昧な自由文を二回送り、その後に選択肢で確定する。
  // 期待値: 初回は自由文を残し、二回目だけchoices-onlyとなり、確定後の次質問で自由入力を復帰する。
  test('7.3 二度目の曖昧回答だけchoices-onlyにし、選択後は自由入力を復帰する', async () => {
    const judge = fixedJudge(
      [{ topic: { kind: 'needs_review' } }, { topicChoice: 'unclear' }],
      [{ topic: { kind: 'needs_review' } }, { topicChoice: 'unclear' }],
    )
    const controller = await renderLab(judge)

    await submitText('うまく説明できません')
    expect(container.querySelector('textarea')?.disabled).toBe(false)
    await submitText('やはり分かりません')
    expect(controller.getState().core.clarification).toEqual({ question: 'topic', mode: 'choices_only' })
    expect(container.querySelector('[role="status"]')?.textContent).toContain('この質問は選択肢で回答してください。')
    expect(container.querySelector('textarea')?.disabled).toBe(true)

    await click('通知が届かない')
    expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(controller.getState().core.clarification).toBeNull()
    expect(container.querySelector('textarea')?.disabled).toBe(false)
  })

  // テストケース: 確定済み相談の範囲質問中に対象外、続いて対象内外混在の固定応答を返す。
  // 期待値: 対象外では確定値と質問を維持し、混在では対象内の回答だけを採用する。
  test('7.3 対象外と混在を確定回答を壊さず処理する', async () => {
    const judge = fixedJudge(
      [{ relevance: 'out_of_scope', impact: 'high' }],
      [{ relevance: 'mixed', scope: { kind: 'known', value: 'specific' }, impact: 'low', urgency: { kind: 'known', value: false } }],
    )
    const controller = await renderLab(judge)
    await click('通知が届かない')

    await submitText('天気を教えてください')
    expect(container.textContent).toContain('このラボで扱えるのは')
    expect(controller.getState().core.confirmed).toMatchObject({ topic: 'missing_notification', scope: null })
    expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'scope' })

    await submitText('ゲームの話もありますが、特定トークだけです')
    expect(container.textContent).toContain('対応できない部分を除き')
    expect(controller.getState().core.confirmed).toMatchObject({ topic: 'missing_notification', scope: 'specific' })
    expect(controller.getState().core.stage).toMatchObject({ kind: 'guidance', guideId: 'missing_specific' })
  })

  // テストケース: 進行中相談で二相談を検出し、現在相談と別相談を順に選ぶ。
  // 期待値: 現相談では元の質問へ戻り、別相談では自動切替せず新規開始だけを案内する。
  test('7.3 二相談pickerは現相談へ復帰し、別相談と訂正は新規開始案内に留める', async () => {
    const judge = fixedJudge(
      [{ topic: { kind: 'known', value: 'both' }, impact: 'high' }, { topicChoice: 'both' }],
      [{ topic: { kind: 'known', value: 'both' }, impact: 'high' }, { topicChoice: 'both' }],
      [{ change: 'restart', scope: { kind: 'known', value: 'all' }, impact: 'high' }],
    )
    const controller = await renderLab(judge)
    await click('通知が届かない')

    await submitText('通知が届かず、設定方法も知りたいです')
    await click('通知が届かない')
    expect(controller.getState().core.stage).toEqual({ kind: 'question', question: 'scope' })
    expect(controller.getState().core.confirmed.scope).toBeNull()

    await submitText('設定方法の相談もあります')
    await click('通知の設定方法を知りたい')
    expect(container.textContent).toContain('現在の相談を終了し、新しい相談を始めてください。')
    expect(controller.getState().core.confirmed.topic).toBe('missing_notification')

    await submitText('やはり設定相談へ変えたいです')
    expect(container.textContent).toContain('現在の相談を終了し、新しい相談を始めてください。')
    expect(controller.getState().core.confirmed.topic).toBe('missing_notification')

    await click('新しい相談を始める')
    expect(controller.getState().core.revision).toBe(0)
    expect(controller.getState().core.confirmed.topic).toBeNull()
    expect(controller.getState().messages).toHaveLength(0)
  })

  // テストケース: 進行中相談を本人操作で中断し、やり直す。
  // 期待値: 中断は読取専用で残り、新規開始後は以前の発言・判定・確定回答を引き継がない。
  test('7.3 中断とやり直しで前相談を引き継がない', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'notification_settings' },
      scope: { kind: 'unmentioned' },
      impact: 'low',
    }])
    const controller = await renderLab(judge)
    await submitText('通知設定について相談します')
    await click('相談を終了する')
    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'interrupted' })
    expect(container.querySelector('textarea')).toBeNull()

    await click('新しい相談を始める')
    expect(controller.getState().messages).toHaveLength(0)
    expect(controller.getState().core.confirmed).toEqual({ topic: null, scope: null, workaround: null, urgency: null })
    expect(container.querySelector('textarea')).not.toBeNull()
  })

  // テストケース: 判定済み発言の後に二件目をpendingにし、その最中に相談を中断する。
  // 期待値: 過去の詳細は閲覧可能、追加入力は停止、中断は可能、後着結果は破棄される。
  test('7.4 pending中も過去判定を閲覧でき、中断後の後着結果を破棄する', async () => {
    let resolveLate!: (value: JudgmentResponse) => void
    const judge = vi.fn(async (_token: string, request: JudgmentRequest) => {
      if (judge.mock.calls.length === 1) {
        return responseFor(request, {
          topic: { kind: 'known', value: 'missing_notification' },
          scope: { kind: 'unmentioned' },
          impact: 'high',
        }, { score: 2, noul: 0.5 })
      }
      return new Promise<JudgmentResponse>((resolve) => {
        resolveLate = resolve
      })
    })
    const controller = await renderLab(judge)
    await submitText('通知が届かず困っています')
    await submitText('すべてのトークです')

    expect(container.querySelectorAll('.lab-judgment-details')).toHaveLength(1)
    expect(container.textContent).toContain('判定中です。')
    expect(container.querySelector('textarea')?.disabled).toBe(true)
    await click('相談を終了する')
    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'interrupted' })

    const pendingRequest = judge.mock.calls[1][1]
    await act(async () => resolveLate(responseFor(pendingRequest, {
      scope: { kind: 'known', value: 'all' },
      impact: 'high',
    })))
    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'interrupted' })
    expect(controller.getState().messages.at(-1)?.status).toBe('interrupted')
  })

  // テストケース: pending中に新しい相談を開始し、その後に旧相談の成功応答が届く。
  // 期待値: 新しい相談は空で始まり、旧相談の後着結果を反映しない。
  test('7.4 pending中の新規開始で旧相談を置換し、後着結果を破棄する', async () => {
    let resolveLate!: (value: JudgmentResponse) => void
    let capturedRequest!: JudgmentRequest
    const judge = vi.fn((_token: string, request: JudgmentRequest) => {
      capturedRequest = request
      return new Promise<JudgmentResponse>((resolve) => { resolveLate = resolve })
    })
    const controller = await renderLab(judge)
    await submitText('旧相談の本文')
    const oldConsultationId = capturedRequest.consultationId

    await click('新しい相談を始める')
    expect(controller.getState().core.consultationId).not.toBe(oldConsultationId)
    expect(controller.getState().messages).toHaveLength(0)
    expect(controller.getState().core.revision).toBe(0)

    await act(async () => resolveLate(responseFor(capturedRequest, {
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'known', value: 'all' },
      impact: 'low',
      urgency: { kind: 'known', value: false },
    })))
    expect(controller.getState().messages).toHaveLength(0)
    expect(controller.getState().core.stage).toEqual({ kind: 'start' })
  })

  // テストケース: 判定が15秒以内に完了せず、その後に成功応答が届く。
  // 期待値: snapshotと本文へ復帰し、後着応答で会話を進めない。
  test('7.4 15秒超過を失敗にして本文を復帰し、後着結果を破棄する', async () => {
    vi.useFakeTimers()
    let resolveLate!: (value: JudgmentResponse) => void
    let capturedRequest!: JudgmentRequest
    const judge = vi.fn((_token: string, request: JudgmentRequest) => {
      capturedRequest = request
      return new Promise<JudgmentResponse>((resolve) => { resolveLate = resolve })
    })
    const controller = await renderLab(judge)
    await submitText('タイムアウトする相談本文')

    await act(async () => vi.advanceTimersByTimeAsync(15_000))
    expect(controller.getState().failure).toBe('judgment_failed')
    expect(controller.getState().draft).toBe('タイムアウトする相談本文')
    expect(container.textContent).toContain('判定できませんでした。')

    await act(async () => resolveLate(responseFor(capturedRequest, {
      topic: { kind: 'known', value: 'missing_notification' },
      impact: 'low',
    })))
    expect(controller.getState().core.revision).toBe(0)
    expect(judge).toHaveBeenCalledTimes(1)
  })

  // テストケース: 判定中のaccess障害後、空の利用確認だけを本人操作で再試行する。
  // 期待値: 会話と本文を保持し、自動本文再送なしで復帰後の通常送信を待つ。
  test('7.4 access障害は空の利用確認だけを再試行し、本文を自動再送しない', async () => {
    const checkAccess = vi.fn()
      .mockResolvedValueOnce({ status: 'authorized', expiresAt: '2099-01-01T00:01:00Z', serverTime: '2099-01-01T00:00:00Z' })
      .mockResolvedValueOnce({ status: 'authorized', expiresAt: '2099-01-01T00:02:00Z', serverTime: '2099-01-01T00:00:00Z' })
    const judge = vi.fn().mockRejectedValueOnce(new LabHttpError('access_unavailable'))
    const api: LabHttpClient = { checkAccess, judge }
    const liffAdapter: LinePlatformLiffAdapter = {
      initialize: vi.fn().mockResolvedValue('liff_browser'), ensureProfilePermission: vi.fn(), isLoggedIn: vi.fn().mockReturnValue(true),
      login: vi.fn(), reauthenticate: vi.fn(), logout: vi.fn(), getIdToken: vi.fn().mockReturnValue('id-token'), getAccessToken: vi.fn().mockReturnValue(null),
    }
    await act(async () => root.render(<TextJudgmentLabPage api={api} authGateProps={{
      liffAdapter,
      config: { liffId: '123-lab', liffUrl: 'https://liff.line.me/123-lab/labs/text-judgment', entryUrl: 'https://lab.example.test/liff/labs/text-judgment' },
    }} />))

    await submitText('自動再送してはいけない本文')
    expect(container.textContent).toContain('利用確認を再試行')
    expect(container.querySelector('textarea')?.value).toBe('自動再送してはいけない本文')
    expect(judge).toHaveBeenCalledTimes(1)

    await click('利用確認を再試行')
    expect(checkAccess).toHaveBeenCalledTimes(2)
    expect(judge).toHaveBeenCalledTimes(1)
    expect(container.querySelector('textarea')?.value).toBe('自動再送してはいけない本文')
  })

  // テストケース: 判定済み会話を持つpageでpageshow復帰し、その後pageを再mountする。
  // 期待値: 保持pageでは会話を継続し、再読込相当の新しいpage寿命では会話を復元しない。
  test('7.4 保持ページ復帰では会話を継続し、再読込では復元しない', async () => {
    const checkAccess = vi.fn().mockResolvedValue({
      status: 'authorized', expiresAt: '2099-01-01T00:01:00Z', serverTime: '2099-01-01T00:00:00Z',
    })
    const judge = vi.fn(async (_token: string, request: JudgmentRequest) => responseFor(request, {
      topic: { kind: 'known', value: 'missing_notification' },
      scope: { kind: 'unmentioned' },
      impact: 'high',
    }, { score: 2, noul: 0.5 }))
    const api: LabHttpClient = { checkAccess, judge }
    const liffAdapter: LinePlatformLiffAdapter = {
      initialize: vi.fn().mockResolvedValue('liff_browser'), ensureProfilePermission: vi.fn(), isLoggedIn: vi.fn().mockReturnValue(true),
      login: vi.fn(), reauthenticate: vi.fn(), logout: vi.fn(), getIdToken: vi.fn().mockReturnValue('id-token'), getAccessToken: vi.fn().mockReturnValue(null),
    }
    const page = <TextJudgmentLabPage api={api} authGateProps={{
      liffAdapter,
      config: { liffId: '123-lab', liffUrl: 'https://liff.line.me/123-lab/labs/text-judgment', entryUrl: 'https://lab.example.test/liff/labs/text-judgment' },
    }} />
    await act(async () => root.render(page))
    await submitText('ページ寿命canary')
    expect(container.textContent).toContain('ページ寿命canary')

    await act(async () => window.dispatchEvent(new PageTransitionEvent('pageshow')))
    expect(checkAccess).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('ページ寿命canary')

    await act(async () => root.unmount())
    root = createRoot(container)
    await act(async () => root.render(page))
    expect(checkAccess).toHaveBeenCalledTimes(3)
    expect(container.textContent).not.toContain('ページ寿命canary')
    expect(container.textContent).toContain('どちらについて相談しますか？')
  })

  // テストケース: 認証期限切れ状態へ移行してから現在の選択肢を操作する。
  // 期待値: 会話は残るがtextareaと全選択肢が停止し、再認証まで進行しない。
  test('7.4 認証期限切れでは会話を保持して追加入力と選択肢を停止する', async () => {
    const judge = fixedJudge([{
      topic: { kind: 'known', value: 'missing_notification' },
      impact: 'high',
    }])
    const controller = await renderLab(judge)
    await submitText('通知が来ません')
    const revision = controller.getState().core.revision

    await act(async () => root.render(
      <TextJudgmentLab controller={controller} access={{ kind: 'reauthentication_required' }} getValidIdToken={() => null} />,
    ))
    expect(container.querySelector('textarea')?.disabled).toBe(true)
    expect([...container.querySelectorAll('[aria-label="現在の選択肢"] button')].every((button) => button.hasAttribute('disabled'))).toBe(true)
    await click('相談を終了する')
    expect(controller.getState().core.revision).toBe(revision + 1)
    expect(controller.getState().core.stage).toEqual({ kind: 'ended', outcome: 'interrupted' })
  })

  // テストケース: canaryの相談本文・ID token・判定結果を含む一往復を実行する。
  // 期待値: DOM上の必要表示以外にstorage、URL、通常consoleへ本文・判定・秘密を残さない。
  test('7.5 本文・判定・秘密をstorage、URL、通常ログへ残さない', async () => {
    localStorage.clear()
    sessionStorage.clear()
    const initialUrl = window.location.href
    const consoleSpies = [
      vi.spyOn(console, 'log').mockImplementation(() => undefined),
      vi.spyOn(console, 'info').mockImplementation(() => undefined),
      vi.spyOn(console, 'warn').mockImplementation(() => undefined),
      vi.spyOn(console, 'error').mockImplementation(() => undefined),
    ]
    const textCanary = '相談本文-canary-7-5'
    const tokenCanary = '秘密-token-canary-7-5'
    const judge = vi.fn(async (token: string, request: JudgmentRequest) => {
      expect(token).toBe(tokenCanary)
      return responseFor(request, {
        topic: { kind: 'known', value: 'missing_notification' },
        scope: { kind: 'known', value: 'all' },
        impact: 'low',
        urgency: { kind: 'known', value: false },
      }, { score: 0, noul: 0 })
    })
    const controller = createTextJudgmentLabController({ judge }, { now: () => 100, uuid: () => `id-${++id}` })
    await act(async () => root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01T00:00:00Z', remainingMs: 60_000 }}
        getValidIdToken={() => tokenCanary}
      />,
    ))

    await submitText(textCanary)

    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(window.location.href).toBe(initialUrl)
    expect(window.location.href).not.toContain(textCanary)
    expect(container.textContent).toContain(textCanary)
    expect(container.textContent).not.toContain(tokenCanary)
    expect(consoleSpies.every((spy) => spy.mock.calls.length === 0)).toBe(true)
  })

  // テストケース: 通知不達と設定相談の案内、未解決終了の固定公式リンクを表示する。
  // 期待値: すべてHTTPS allowlist由来でnoopener noreferrerを持つ。
  test.each([
    ['missing_notification', '通知が届かない', 'すべてのトーク', '急いでいない'],
    ['notification_settings', '通知の設定方法を知りたい', '全体', '急いでいない'],
  ])('7.5 %sの全公式リンクに安全属性を付ける', async (_topic, topicLabel, scopeLabel, urgencyLabel) => {
    await renderLab(fixedJudge())
    await click(topicLabel)
    await click(scopeLabel)
    if (topicLabel === '通知が届かない') await click('確認できる')
    await click(urgencyLabel)

    const links = [...container.querySelectorAll<HTMLAnchorElement>('a[href^="https://"]')]
    expect(links.length).toBeGreaterThan(0)
    expect(links.every((link) => link.rel.includes('noopener') && link.rel.includes('noreferrer'))).toBe(true)
  })
})

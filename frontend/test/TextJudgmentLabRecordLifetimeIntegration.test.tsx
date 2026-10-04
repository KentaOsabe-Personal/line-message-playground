import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import TextJudgmentLab from '../src/TextJudgmentLab'
import { createLabHttpClient } from '../src/textJudgmentLabApi'
import type { JudgmentRequest, JudgmentResponse } from '../src/textJudgmentLabTypes'
import { createTextJudgmentLabController } from '../src/useTextJudgmentLab'
import { boundaryResponse } from './textJudgmentLabBoundaryFixture'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
let container: HTMLDivElement
let root: Root
beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})
const uuidFactory = () => {
  let id = 0
  return () => `00000000-0000-4000-8000-${String(++id).padStart(12, '0')}`
}

// テストケース: 成功応答が15秒の待機期限の直前・一致・直後に届く。
// 期待値: 期限の直前に届いた判定だけを採用して記録する。期限と一致する場合や期限後は、会話状態と入力を元に戻し、数値や適用結果を残さない。
test.each([14_999, 15_000, 15_001])(
  'deadline %smsの会話と発言記録を同時に隔離する',
  async (elapsed) => {
    let now = 0
    let finish!: (value: JudgmentResponse) => void
    let request!: JudgmentRequest
    const judge = vi.fn((_token: string, sent: JudgmentRequest) => {
      request = sent
      return new Promise<JudgmentResponse>((resolve) => {
        finish = resolve
      })
    })
    const controller = createTextJudgmentLabController(
      { judge },
      { now: () => now, uuid: uuidFactory() },
    )
    const before = controller.getState().core
    const pending = controller.submit('deadline検証の架空本文', 'text', 'safe-token')
    now = elapsed
    finish(boundaryResponse(request))
    await pending
    const state = controller.getState()
    expect(state.pending).toBeNull()
    const record = state.messages[0]
    expect(record.kind).toBe(elapsed < 15_000 ? 'judged' : 'failed')
    if (elapsed < 15_000) {
      if (record.kind !== 'judged') throw new Error('成功記録がありません')
      expect(record.application.next).toEqual(state.core.stage)
      expect(record.uiElapsedMs).toBe(elapsed)
    } else {
      expect(state.core).toEqual(before)
      expect(state.draft).toBe(request.text)
      for (const key of ['judgment', 'application', 'request', 'uiElapsedMs'])
        expect(record).not.toHaveProperty(key)
    }
    expect(judge).toHaveBeenCalledTimes(1)
    controller.dispose()
  },
)

// テストケース: 正常な成功応答の後、HTTP通信障害・必須項目を欠く成功応答・認証失効・LINE照会障害・要求と対応しない応答を、実際のHTTPアダプターに返す。
// 期待値: 過去の発言に保存した記録はすべて維持する。失敗した発言には判定を付けず、その本文を次の判定に送る文脈から除外する。
test.each([
  'network',
  'missing-inspection',
  'auth-expired',
  'line-unavailable',
  'consultationId',
  'requestId',
  'revision',
] as const)('%sで記録・入力・coreを保護する', async (failure) => {
  const requests: JudgmentRequest[] = []
  let call = 0
  const fetcher = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
    if (typeof init?.body !== 'string') throw new Error('JSON要求がありません')
    const request = JSON.parse(init.body) as JudgmentRequest
    requests.push(request)
    const response = boundaryResponse(request)
    if (++call === 2) {
      if (failure === 'network') throw new TypeError('通信canary')
      if (failure === 'missing-inspection')
        return new Response(JSON.stringify({ ...response, inspection: undefined }))
      if (failure === 'auth-expired' || failure === 'line-unavailable')
        return new Response(
          JSON.stringify({
            error: {
              code: failure === 'auth-expired' ? 'reauthentication_required' : 'access_unavailable',
              message: '固定失敗',
            },
          }),
          { status: failure === 'auth-expired' ? 401 : 503 },
        )
      return new Response(
        JSON.stringify({
          ...response,
          [failure]:
            failure === 'revision' ? request.revision + 1 : '00000000-0000-4000-8000-999999999999',
        }),
      )
    }
    return new Response(JSON.stringify(response))
  })
  const onAccessFailure = vi.fn()
  const controller = createTextJudgmentLabController(createLabHttpClient(fetcher), {
    now: () => 1,
    uuid: uuidFactory(),
    onAccessFailure,
  })
  await act(async () =>
    root.render(
      <TextJudgmentLab
        controller={controller}
        access={{ kind: 'authorized', expiresAt: '2099-01-01', remainingMs: 1000 }}
        getValidIdToken={() => 'safe-token'}
      />,
    ),
  )
  await act(async () => controller.submit('受理済み本文', 'text', 'safe-token'))
  const first = controller.getState().messages[0]
  const serialized = JSON.stringify(first)
  const oldRow = container.querySelector('[aria-label="あなたのメッセージ"]')!
  const oldHtml = oldRow.innerHTML
  const before = controller.getState().core
  await act(async () => controller.submit('除外する失敗本文', 'text', 'safe-token'))
  expect(controller.getState().core).toEqual(before)
  expect(controller.getState().draft).toBe('除外する失敗本文')
  expect(controller.getState().messages[0]).toBe(first)
  expect(JSON.stringify(first)).toBe(serialized)
  expect(oldRow.innerHTML).toBe(oldHtml)
  const failed = controller.getState().messages[1]
  expect(failed.kind).toBe('failed')
  for (const key of ['judgment', 'application', 'request', 'uiElapsedMs'])
    expect(failed).not.toHaveProperty(key)
  const failedRow = container.querySelectorAll('[aria-label="あなたのメッセージ"]')[1]
  expect(failedRow.textContent).toContain('結果は適用していません')
  expect(failedRow.querySelector('.lab-judgment-details')).toBeNull()
  expect(fetcher).toHaveBeenCalledTimes(2)
  if (failure === 'auth-expired' || failure === 'line-unavailable')
    expect(onAccessFailure).toHaveBeenCalledWith(
      failure === 'auth-expired' ? 'auth_expired' : 'access_unavailable',
    )
  else expect(onAccessFailure).not.toHaveBeenCalled()
  // 認証が回復した後も、本人が操作したときだけ再送する。本文を自動で再送しない。
  await act(async () => controller.submit('新たな本人操作', 'text', 'safe-token'))
  expect(requests[2].context.recentUserTexts).toEqual(['受理済み本文'])
  expect(JSON.stringify(first)).toBe(serialized)
  await act(async () => controller.dispose())
})

// テストケース: 記録がある会話で判定を待つ間に、認証による操作停止・相談の中断・新規開始・ページ破棄を行い、その後に成功応答が届く。
// 期待値: 後から届いた成功応答は、会話にも記録にも反映しない。会話を保持する場合は過去の記録を変更せず、破棄する場合はすべての記録を消す。
test.each(['auth', 'interrupt', 'restart', 'dispose'] as const)(
  '%s後の成功は会話にも記録にも入らない',
  async (operation) => {
    let finish!: (value: JudgmentResponse) => void
    let sent!: JudgmentRequest
    let calls = 0
    const judge = vi.fn(async (_token: string, request: JudgmentRequest) => {
      if (++calls === 1) return boundaryResponse(request)
      sent = request
      return new Promise<JudgmentResponse>((resolve) => {
        finish = resolve
      })
    })
    const controller = createTextJudgmentLabController(
      { judge },
      { now: () => 1, uuid: uuidFactory() },
    )
    await controller.submit('過去の架空相談', 'text', 'safe-token')
    const first = JSON.stringify(controller.getState().messages[0])
    const pending = controller.submit('後着する架空回答', 'text', 'safe-token')
    if (operation === 'auth') controller.setInteractive(false, 'auth_expired')
    else if (operation === 'interrupt') controller.interrupt()
    else if (operation === 'restart') controller.restart()
    else controller.dispose()
    const beforeLate = JSON.stringify(controller.getState())
    finish(boundaryResponse(sent))
    await pending
    expect(JSON.stringify(controller.getState())).toBe(beforeLate)
    if (operation === 'auth' || operation === 'interrupt') {
      expect(JSON.stringify(controller.getState().messages[0])).toBe(first)
      expect(controller.getState().messages[1].kind).toBe(
        operation === 'auth' ? 'failed' : 'interrupted',
      )
    } else expect(controller.getState().messages).toEqual([])
    controller.dispose()
  },
)

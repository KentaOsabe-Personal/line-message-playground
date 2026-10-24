import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import RichMenuAdminConsole from '../src/RichMenuAdminConsole'
import RichMenuAdminPage from '../src/RichMenuAdminPage'
import type { ChannelAdminItem } from '../src/channelAdminDto'
import type { CurrentMenuView, OperationView, TemplateDescriptor } from '../src/richMenuAdminDto'
import fixtures from './fixtures/rich-menu-current.json'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
const channelId = fixtures.managed.channelId
const revision = fixtures.managed.channelRevision
const channel: ChannelAdminItem = {
  channelId,
  label: '通知チャネル',
  messagingApiChannelId: '123',
  botUserId: `U${'a'.repeat(32)}`,
  providerId: '456',
  active: true,
  credentialsState: 'configured',
  credentialsUpdatedAt: revision,
  createdAt: revision,
  updatedAt: revision,
  webhookUrl: `https://example.test/api/line/webhooks/${channelId}/`,
  deactivationSummary: null,
  richMenuRefreshRequired: false,
}
const template: TemplateDescriptor = {
  templateId: 'jp-link-one',
  version: 1,
  displayName: '1リンク',
  canvas: { width: 2500, height: 843 },
  areas: [
    { field: 'whole', description: '全面', bounds: { x: 0, y: 0, width: 2500, height: 843 } },
  ],
  requiredFields: ['whole'],
  limits: { displayName: 20, uri: 1000 },
}
let view: CurrentMenuView
let selected: ChannelAdminItem
let templates: TemplateDescriptor[]
let status: number
let container: HTMLDivElement
let root: Root
const requests: {
  path: string
  method: string
  body: Record<string, unknown>
  signal?: AbortSignal | null
}[] = []
const reply = (body: unknown, code = 200) => new Response(JSON.stringify(body), { status: code })
let operationHandler: (body: Record<string, unknown>) => Response | Promise<Response>
let lifecycleHandler: (path: string, body: Record<string, unknown>) => Response | Promise<Response>
let operationRead: OperationView | null
const operation = (
  body: Record<string, unknown>,
  overrides: Partial<OperationView> = {},
): OperationView => ({
  operationId: String(body.operationId),
  kind: body.kind as OperationView['kind'],
  status: 'succeeded',
  stage: null,
  result: 'succeeded',
  subjectOperationId: typeof body.subjectOperationId === 'string' ? body.subjectOperationId : null,
  targetResourceId: typeof body.targetResourceId === 'string' ? body.targetResourceId : null,
  acceptedAt: revision,
  completedAt: revision,
  nextAllowedActions: [],
  ...overrides,
})
const fetchHandler: typeof fetch = async (input, init) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const method = init?.method ?? 'GET'
  const body = (typeof init?.body === 'string' ? JSON.parse(init.body) : {}) as Record<
    string,
    unknown
  >
  requests.push({ path, method, body, signal: init?.signal })
  if (path.includes('/history/')) return reply({ error: { code: 'temporarily_unavailable' } }, 503)
  if (path.includes('/current/'))
    return status === 200
      ? reply(view)
      : reply(
          {
            error: {
              code: status === 404 ? 'channel_not_found' : 'temporarily_unavailable',
              nextAllowedActions: [],
            },
          },
          status,
        )
  if (path.endsWith('/templates/')) return reply({ items: templates })
  if (path.endsWith('/preview/'))
    return reply({
      channelId,
      channelLabel: selected.label,
      templateId: body.templateId,
      templateVersion: body.templateVersion,
      fields: Object.values(body.fields as object),
      image: {
        contentType: fixtures.managed.current.image.contentType,
        width: 2500,
        height: 843,
        digest: fixtures.managed.current.image.digest,
        base64: fixtures.managed.current.image.base64,
      },
      observation: fixtures.managed.rich.latestObservation,
      warnings: [],
      confirmationToken: `safe-fixture-${calls('/preview/').length}`,
      expiresAt: new Date(Date.now() + 600_000).toISOString(),
    })
  if (path.endsWith('/operations/') && method === 'POST') return operationHandler(body)
  if (path.includes('/rich-menus/operations/'))
    return operationRead
      ? reply(operationRead)
      : reply({ error: { code: 'operation_not_found', nextAllowedActions: [] } }, 404)
  if (method !== 'GET') return lifecycleHandler(path, body)
  if (path === `/api/line/channels/${channelId}/`) return reply(selected)
  return reply({ error: { code: 'temporarily_unavailable' } }, 503)
}
const fetchMock = vi.fn<typeof fetch>(fetchHandler)
async function render(page = false) {
  await act(async () =>
    root.render(
      page ? (
        <MemoryRouter initialEntries={[`/liff/rich-menus/${channelId}`]}>
          <Routes>
            <Route path="/liff/rich-menus/:channelId" element={<RichMenuAdminPage />} />
          </Routes>
        </MemoryRouter>
      ) : (
        <RichMenuAdminConsole channelId={channelId} initialChannel={selected} />
      ),
    ),
  )
}
function button(label: string) {
  const value = [...container.querySelectorAll('button')].find((item) => item.textContent === label)
  if (!value) throw new Error(`button missing: ${label}`)
  return value
}
async function click(label: string) {
  await act(async () => button(label).click())
}
const calls = (part: string) => requests.filter((request) => request.path.includes(part))
beforeEach(() => {
  view = structuredClone(fixtures.managed) as CurrentMenuView
  selected = { ...channel }
  templates = [template]
  status = 200
  operationRead = null
  requests.length = 0
  operationHandler = (body) => reply(operation(body))
  lifecycleHandler = () =>
    reply({ error: { code: 'channel_referenced', summary: '参照があります。' } }, 409)
  fetchMock.mockReset().mockImplementation(fetchHandler)
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => `blob:${crypto.randomUUID()}`)
      static revokeObjectURL = vi.fn()
    },
  )
  document.cookie = 'csrftoken=safe-fixture'
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// テストケース: Pageから詳細を開き、履歴APIが利用不能のまま更新・編集する。
// 期待値: channel/currentを一度ずつ取得し、明示編集後だけ全設定を表示しfocusを移す。
test('10.1 composes validated current without history or duplicate initial reads', async () => {
  await render(true)
  expect(calls('/api/line/channels/')).toHaveLength(1)
  expect(calls('/current/')).toHaveLength(1)
  expect(calls('/templates/')).toHaveLength(0)
  expect(container.querySelector('input')).toBeNull()
  expect(container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')?.alt).toBe(
    '通知チャネル の現在のメニュー',
  )
  const refresh = button('更新')
  refresh.focus()
  await click('更新')
  expect(document.activeElement).toBe(refresh)
  await click('編集する')
  expect(container.querySelector('input')?.value).toBe('公式サイト')
  expect(document.activeElement?.id).toBe('rich-menu-editor-heading')
  expect(container.querySelector('details.rich-menu-current')).not.toBeNull()
  await click('キャンセル')
  expect(document.activeElement).toBe(button('編集する'))
  expect(container.querySelector('input')).toBeNull()
  expect(calls('/history/')).toHaveLength(0)
  expect(calls('/state/')).toHaveLength(0)
  expect(calls('/deactivation/')).toHaveLength(0)
})

// テストケース: 新APIが404を返す詳細画面を開く。
// 期待値: 旧APIや空の成功表示へfallbackせず、操作を閉じる。
test('10.1 does not treat an undeployed current endpoint as success', async () => {
  status = 404
  await render()
  expect(container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')).toBeNull()
  expect(container.textContent).not.toContain('適用中')
  expect(calls('/state/')).toHaveLength(0)
  expect(calls('/history/')).toHaveLength(0)
})

async function input(index: number, value: string) {
  await act(async () => {
    const element = container.querySelectorAll('input')[index]
    element.value = value
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function preview() {
  await act(async () =>
    container
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  )
}

// テストケース: 適用済み設定を編集し、確認・戻る・入力変更・取消・適用連打を行う。
// 期待値: 全項目と確認影響を表示し、取消は変更せず、適用は一回で現在画像を再取得する。
test('10.2 edits saved settings, returns, cancels and applies once through HTTP', async () => {
  await render()
  await click('編集する')
  expect(container.querySelectorAll('input')[1].value).toBe(
    fixtures.managed.current.configuration.fields[0].uri,
  )
  await preview()
  expect(document.activeElement?.id).toBe('rich-menu-preview-heading')
  expect(container.textContent).toContain('領域 1: 公式サイト')
  expect(container.textContent).toContain(fixtures.managed.current.configuration.fields[0].uri)
  expect(container.textContent).toContain('利用者別')
  expect(container.textContent).toContain('端末での実際の表示は保証しません')
  expect(container.textContent).toContain('有効期限')
  expect(container.querySelectorAll('img[alt]:not([alt=""])')).toHaveLength(2)
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  expect(open).not.toHaveBeenCalled()
  await click('リンクを開く')
  expect(open).toHaveBeenCalledWith(
    fixtures.managed.current.configuration.fields[0].uri,
    '_blank',
    'noopener,noreferrer',
  )
  await click('編集へ戻る')
  expect(document.activeElement?.id).toBe('rich-menu-editor-heading')
  expect(container.querySelector('#rich-menu-preview-heading')).toBeNull()
  await input(0, '変更した案内')
  await preview()
  expect(calls('/preview/').at(-1)?.body.fields).toEqual({
    whole: {
      displayName: '変更した案内',
      uri: fixtures.managed.current.configuration.fields[0].uri,
    },
  })
  await click('キャンセル')
  expect(calls('/operations/')).toHaveLength(0)
  await click('編集する')
  expect(container.querySelector('input')?.value).toBe('公式サイト')
  await preview()
  const oldImage = container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')?.src
  const apply = button('LINEに反映')
  operationHandler = (body) => {
    view = structuredClone(fixtures.managed) as CurrentMenuView
    return reply(operation(body))
  }
  await act(async () => {
    apply.click()
    apply.click()
  })
  expect(calls('/operations/')).toHaveLength(1)
  expect(calls('/operations/')[0].signal).toBeUndefined()
  expect(calls('/current/')).toHaveLength(2)
  expect(container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')?.src).not.toBe(
    oldImage,
  )
  expect(container.textContent).toContain('直前の操作: 完了')
  expect(calls('/history/')).toHaveLength(0)
})

// テストケース: cleanup成功または通常適用の成功・失敗後に新しい編集と確認を行う。
// 期待値: 直前結果を保持し、新しいoperation IDの適用を一回だけPOSTする。
test.each(['cleanup', 'apply_succeeded', 'apply_failed'] as const)(
  '10.2 applies a new preview after %s settles',
  async (previous) => {
    if (previous === 'cleanup') {
      view = structuredClone(fixtures.cleanup) as CurrentMenuView
      operationRead = fixtures.cleanup.rich.blockingOperation as OperationView
    }
    await render()
    operationHandler = (body) => {
      view = structuredClone(fixtures.managed) as CurrentMenuView
      return reply(
        operation(
          body,
          previous === 'apply_failed'
            ? {
                status: 'failed',
                result: 'line_rejected',
              }
            : {},
        ),
      )
    }
    if (previous === 'cleanup') await click('残ったデータ 1 を削除')
    else {
      await click('編集する')
      await preview()
      await click('LINEに反映')
    }
    const initialPosts = calls('/operations/').filter((request) => request.method === 'POST')
    expect(initialPosts).toHaveLength(1)
    const priorId = initialPosts[0].body.operationId
    expect(container.textContent).toContain('直前の操作:')
    const readsBeforeEditing = calls('/current/').length
    expect(readsBeforeEditing).toBeGreaterThanOrEqual(2)
    await click('編集する')
    await input(0, '次のメニュー')
    await preview()
    expect(button('LINEに反映').disabled).toBe(false)
    operationHandler = (body) => reply(operation(body))
    const apply = button('LINEに反映')
    await act(async () => {
      apply.click()
      apply.click()
    })
    const posts = calls('/operations/').filter((request) => request.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[1].body).toMatchObject({ kind: 'apply' })
    expect(posts[1].body.operationId).not.toBe(priorId)
    expect(calls('/current/')).toHaveLength(readsBeforeEditing + 1)
  },
)

// テストケース: 未設定から作成しtemplate変更を取消・承認する。
// 期待値: 作成時だけ空入力にし、取消は保持、承認時は全入力を消去する。
test('10.2 creates explicitly and confirms or cancels a template change', async () => {
  view = structuredClone(fixtures.unset) as CurrentMenuView
  templates.push({ ...template, version: 2, displayName: '新版' })
  await render()
  await click('メニューを作成')
  expect(container.querySelector('input')?.value).toBe('')
  await input(0, '案内')
  await input(1, 'https://example.com/new?full=1#target')
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const change = async () => {
    await act(async () => {
      const select = container.querySelector('select')!
      select.value = 'jp-link-one:2'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
  }
  await change()
  expect(container.querySelector('input')?.value).toBe('案内')
  expect(container.querySelector('select')?.value).toBe('jp-link-one:1')
  confirm.mockReturnValue(true)
  await change()
  expect(container.querySelector('input')?.value).toBe('')
  expect(container.querySelector('select')?.value).toBe('jp-link-one:2')
  await input(0, '新しい案内')
  await input(1, 'https://example.com/new')
  await preview()
  expect(calls('/preview/').at(-1)?.body.templateVersion).toBe(2)
  expect(calls('/history/')).toHaveLength(0)
})

// テストケース: 保存版が廃止された設定を編集する。
// 期待値: 理由を示し、明示した作り直し後だけ空入力を開始する。
test('10.2 rebuilds a retired template only after an explicit choice', async () => {
  if (view.current.kind !== 'managed' || view.current.configuration.status !== 'available')
    throw new Error('fixture')
  view.current.configuration.templateVersion = 99
  view.current.configuration.editability = 'template_unavailable'
  await render()
  await click('編集する')
  expect(container.querySelector('input')).toBeNull()
  expect(container.textContent).toContain('同じテンプレートと版')
  await click('別のテンプレートで作り直す')
  expect(container.querySelector('input')?.value).toBe('')
  expect(container.querySelector('select')?.value).toBe('jp-link-one:1')
  expect(calls('/operations/')).toHaveLength(0)
})

// テストケース: 確認プレビューを期限切れにして同じ入力から再生成する。
// 期待値: 期限後は適用できず、channel/current確認後に再生成し入力を保持する。
test('10.2 expires confirmation and regenerates after a current refresh', async () => {
  vi.useFakeTimers()
  await render()
  await click('編集する')
  await preview()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(600_001)
  })
  expect(container.querySelector('#rich-menu-preview-heading')).toBeNull()
  expect(container.textContent).toContain('プレビューが失効')
  expect(container.querySelector('input')?.value).toBe('公式サイト')
  await click('プレビューを再生成')
  expect(calls('/current/')).toHaveLength(2)
  expect(calls('/preview/')).toHaveLength(2)
  expect(container.querySelector('#rich-menu-preview-heading')).not.toBeNull()
  expect(calls('/operations/')).toHaveLength(0)
})

// テストケース: 適用解除を取消後に確定し、外部既定の再表示または再取得失敗を返す。
// 期待値: 現在資源だけを一度解除し、確定結果を保持して過去画像を表示しない。
test.each([false, true])(
  '10.2 unlinks and retains a settled result when refresh fails=%s',
  async (fail) => {
    await render()
    await click('適用解除')
    await click('取消')
    expect(calls('/operations/')).toHaveLength(0)
    operationHandler = (body) => {
      view = structuredClone(fixtures.external) as CurrentMenuView
      if (fail) status = 503
      return reply(operation(body))
    }
    await click('適用解除')
    await click('適用解除を確定')
    expect(calls('/operations/')[0].body).toMatchObject({
      kind: 'unlink',
      targetResourceId: fixtures.managed.rich.currentResource.resourceId,
      channelRevision: revision,
    })
    expect(container.textContent).toContain('直前の操作: 完了')
    expect(container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')).toBeNull()
    expect(container.textContent).toContain(fail ? '現在表示の再取得に失敗' : 'アプリ外')
    expect(calls('/current/')).toHaveLength(2)
    expect(calls('/history/')).toHaveLength(0)
  },
)

// テストケース: currentのactive/blocking/latest参照から結果不明操作を開き明示再確認する。
// 期待値: 同じoperationをGETし、自動POSTなしで元subjectだけを再確認する。
test.each(['active', 'blocking', 'latest'] as const)(
  '10.3 resumes %s operation and explicitly rechecks the same subject',
  async (source) => {
    const unknown = operation(
      { operationId: fixtures.managed.rich.historySummary.latestOperationId, kind: 'apply' },
      {
        status: 'unknown',
        result: 'timeout_unknown',
        completedAt: null,
        nextAllowedActions: ['recheck', 'get_state'],
      },
    )
    view = structuredClone(fixtures.attention) as CurrentMenuView
    view.rich.latestObservation = {
      ...fixtures.managed.rich.latestObservation,
      kind: 'unknown',
      managedResourceId: null,
    }
    view.rich.effectiveActions = ['recheck', 'get_state']
    view.rich.nextAllowedActions = ['recheck', 'get_state']
    if (source === 'active') view.rich.activeOperation = unknown
    else if (source === 'blocking') view.rich.blockingOperation = unknown
    else
      view.rich.historySummary = {
        totalCount: 1,
        latestOperationId: unknown.operationId,
        latestStatus: 'unknown',
      }
    operationRead = unknown
    await render()
    expect(calls(`/operations/${unknown.operationId}/`)).toHaveLength(1)
    expect(calls('/operations/').filter((r) => r.method === 'POST')).toHaveLength(0)
    if (source === 'latest') {
      // 最新参照だけではrecheck subjectとの関係が不足するため表示は安全側へ閉じる。
      expect(container.textContent).toContain('確認対象との関係を特定できません')
      return
    }
    operationHandler = (body) => {
      view = structuredClone(fixtures.managed) as CurrentMenuView
      return reply(operation(body))
    }
    await click('前回の結果を確認')
    const posts = calls('/operations/').filter((r) => r.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toMatchObject({
      kind: 'recheck',
      subjectOperationId: unknown.operationId,
    })
    expect(calls('/history/')).toHaveLength(0)
  },
)

// テストケース: 停止intent下のcleanup候補と現在対象・旧対象を表示する。
// 期待値: intentに結びつく一組だけ送信し、停止中の競合操作と自動再確認を禁止する。
test('10.3 cleans only the permitted pair during pending deactivation', async () => {
  view = structuredClone(fixtures.cleanup) as CurrentMenuView
  const subject = fixtures.cleanup.rich.blockingOperation
  operationRead = subject as OperationView
  view.deactivation = {
    ...fixtures.inactive.deactivation,
    channelActive: true,
    status: 'confirmation_required',
    reason: 'cleanup_required',
    subjectOperationId: subject.operationId,
    nextAction: 'complete_cleanup_then_recheck',
    completedAt: null,
  }
  await render()
  expect(container.querySelector('input')).toBeNull()
  expect(button('チャネルを物理削除').disabled).toBe(true)
  expect(button('同じ無効化を再確認').disabled).toBe(true)
  expect(container.textContent).not.toContain('適用解除を確定')
  operationHandler = (body) => {
    view = structuredClone(fixtures.unset) as CurrentMenuView
    return reply(operation(body))
  }
  await click('残ったデータ 1 を削除')
  expect(calls('/operations/').filter((r) => r.method === 'POST')[0].body).toMatchObject(
    fixtures.cleanup.recoveryTargets[0],
  )
  expect(calls('/deactivation/recheck/')).toHaveLength(0)
  expect(calls('/history/')).toHaveLength(0)
})

// テストケース: cleanupの関係が不明または別subjectに属する停止intentを表示する。
// 期待値: 任意資源の削除や別subjectのPOSTを許可しない。
test.each([false, true])('10.3 refuses unrelated cleanup pending=%s', async (pending) => {
  view = structuredClone(fixtures.cleanup) as CurrentMenuView
  operationRead = fixtures.cleanup.rich.blockingOperation as OperationView
  if (pending)
    view.deactivation = {
      ...fixtures.inactive.deactivation,
      channelActive: true,
      status: 'confirmation_required',
      subjectOperationId: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      nextAction: 'complete_cleanup_then_recheck',
      completedAt: null,
    }
  else view.recoveryTargets = []
  await render()
  if (pending) await click('残ったデータ 1 を削除')
  else expect(container.textContent).toContain('後片付け対象との関係を確認できません')
  expect(calls('/operations/').filter((r) => r.method === 'POST')).toHaveLength(0)
})

// テストケース: 無効化の確認待ちから同じintentを明示再確認して停止・再有効化する。
// 期待値: 新規POSTを自動再送せず、再有効化後はcurrent取得完了まで変更を閉じる。
test('10.3 stops, explicitly rechecks and waits for current after reactivation', async () => {
  const pending = {
    ...fixtures.inactive.deactivation,
    channelActive: true,
    status: 'confirmation_required',
    reason: 'external_default',
    nextAction: 'resolve_external_default_then_recheck',
    completedAt: null,
  }
  lifecycleHandler = (path, body) => {
    if (path.endsWith('/deactivation/')) {
      view = { ...view, deactivation: pending } as CurrentMenuView
      return reply(pending)
    }
    if (path.endsWith('/deactivation/recheck/')) {
      expect(body.operationId).toBe(pending.operationId)
      selected = { ...selected, active: false }
      view = structuredClone(fixtures.inactive) as CurrentMenuView
      return reply(fixtures.inactive.deactivation)
    }
    selected = { ...selected, active: true }
    view = structuredClone(fixtures.unset) as CurrentMenuView
    return reply(selected)
  }
  await render()
  await click('チャネルを無効化')
  await click('無効化を確定')
  expect(calls('/deactivation/').filter((r) => r.method === 'POST')).toHaveLength(1)
  expect(calls('/deactivation/recheck/')).toHaveLength(0)
  await click('同じ無効化を再確認')
  expect(container.textContent).toContain('停止中')
  expect(container.querySelector('input')).toBeNull()
  let finish!: (response: Response) => void
  const normal = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => {
    if (typeof url === 'string' && url.includes('/current/'))
      return new Promise<Response>((resolve) => {
        finish = resolve
      })
    return normal(url, init)
  })
  await click('チャネルを再有効化')
  expect(container.textContent).toContain('読み込み中')
  expect(container.textContent).not.toContain('メニューを作成')
  await act(async () => finish(reply(view)))
  expect(container.textContent).toContain('メニューを作成')
  expect(container.querySelector('input')).toBeNull()
  expect(calls('/history/')).toHaveLength(0)
})

// テストケース: 停止中のチャネルで履歴の有無を使って物理削除を確認し、参照阻止後に再確認する。
// 期待値: 件数一覧を取得せず履歴消失を明示し、409を成功扱いせず確定削除だけを通知する。
test('10.3 confirms history loss, respects references and deletes once', async () => {
  selected = { ...selected, active: false }
  view = structuredClone(fixtures.inactive) as CurrentMenuView
  view.rich.historySummary = fixtures.managed.rich
    .historySummary as CurrentMenuView['rich']['historySummary']
  await render()
  await click('チャネルを物理削除')
  expect(container.textContent).toContain('適用履歴もチャネルと一緒に削除')
  await click('物理削除を確定')
  expect(container.textContent).toContain('削除を阻止する参照')
  expect(container.textContent).not.toContain('編集する')
  await click('更新')
  await click('チャネルを物理削除')
  lifecycleHandler = () => reply({ channelId, label: selected.label, deleted: true })
  const confirm = button('物理削除を確定')
  await act(async () => {
    confirm.click()
    confirm.click()
  })
  expect(requests.filter((r) => r.method === 'DELETE')).toHaveLength(2)
  expect(container.textContent).toBe('')
  expect(calls('/history/')).toHaveLength(0)
})

// テストケース: 通常適用・外部変更・画像失敗・設定不明・廃止版の管理対象を終了する。
// 期待値: 現在管理資源だけを確認してreleaseへ渡し、確定後は現在表示を再取得する。
test.each([
  'managed',
  'attention',
  'image_missing',
  'mapping_missing',
  'templateUnavailable',
] as const)('10.4 releases only current ownership for %s', async (key) => {
  view = structuredClone(fixtures[key]) as CurrentMenuView
  await render()
  await click('管理終了')
  await click('取消')
  expect(calls('/operations/')).toHaveLength(0)
  await click('管理終了')
  expect(container.textContent).toContain('現在既定と資源は変更・削除しません')
  expect(container.textContent).toContain('履歴は保持')
  operationHandler = (body) => {
    view = structuredClone(fixtures.external) as CurrentMenuView
    return reply(operation(body))
  }
  await click('管理終了を確定')
  const posts = requests.filter((r) => r.method === 'POST')
  expect(posts).toHaveLength(1)
  expect(posts[0].body).toMatchObject({
    kind: 'release',
    targetResourceId: fixtures.managed.rich.currentResource.resourceId,
  })
  expect(container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')).toBeNull()
  expect(container.textContent).toContain('直前の操作: 完了')
  expect(container.textContent).toContain('アプリ外')
  expect(calls('/current/')).toHaveLength(2)
  expect(calls('/history/')).toHaveLength(0)
})

// テストケース: currentなし・cleanup・停止・許可されないmode・進行中操作を開く。
// 期待値: 管理終了を提示せず、別資源や外部IDを対象として送信しない。
test.each([
  'unset',
  'external',
  'cleanup',
  'inactive',
  'read_only',
  'recovery_only',
  'unavailable',
  'active',
  'unknown',
] as const)('10.4 closes release for %s', async (key) => {
  if (key in fixtures)
    view = structuredClone(fixtures[key as keyof typeof fixtures]) as CurrentMenuView
  else if (['read_only', 'recovery_only', 'unavailable'].includes(key)) {
    view.rich.mode = key as CurrentMenuView['rich']['mode']
    view.rich.effectiveActions = ['get_state', 'view_history']
  } else {
    const status = key === 'active' ? 'processing' : 'unknown'
    const op = operation(
      { operationId: fixtures.managed.rich.historySummary.latestOperationId, kind: 'apply' },
      { status, completedAt: null, result: 'accepted' },
    )
    view = structuredClone(fixtures.attention) as CurrentMenuView
    view.rich.activeOperation = op
    view.rich.effectiveActions = ['get_state']
    operationRead = op
  }
  if (key === 'inactive') selected = { ...selected, active: false }
  if (key === 'cleanup') operationRead = fixtures.cleanup.rich.blockingOperation as OperationView
  await render()
  expect([...container.querySelectorAll('button')].some((b) => b.textContent === '管理終了')).toBe(
    false,
  )
  expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0)
})

// テストケース: 管理終了確認の表示後に最新状態が変わる。
// 期待値: 更新開始時に旧確認を破棄し、古い対象の確定操作からPOSTしない。
test('10.4 discards a stale release confirmation during refresh', async () => {
  await render()
  await click('管理終了')
  const stale = button('管理終了を確定')
  view = structuredClone(fixtures.external) as CurrentMenuView
  await click('更新')
  await act(async () => stale.click())
  expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0)
  expect(container.textContent).not.toContain('管理終了を確定')
})

// テストケース: アプリ外既定または外部変更で要確認の詳細から作り直しを選ぶ。
// 期待値: 現在資源の編集を提示せず、明示作り直し後だけ空入力を開始する。
test.each(['external', 'attention'] as const)(
  '10.1 starts an explicit rebuild instead of an inert edit for %s',
  async (key) => {
    view = structuredClone(fixtures[key]) as CurrentMenuView
    await render()
    expect(
      [...container.querySelectorAll('button')].some(
        (item) => item.textContent === 'メニューを編集',
      ),
    ).toBe(false)
    expect(container.querySelector('input')).toBeNull()
    await click('別のテンプレートで作り直す')
    expect(container.querySelector('input')?.value).toBe('')
    expect(document.activeElement?.id).toBe('rich-menu-editor-heading')
    expect(calls('/templates/')).toHaveLength(1)
    expect(requests.filter((request) => request.method === 'POST')).toHaveLength(0)
  },
)

// テストケース: 通常閲覧の編集・更新・解除を表示する。
// 期待値: 編集を主操作、更新と解除を既存secondaryスタイルの補助操作として区別する。
test('10.1 distinguishes primary editing from secondary refresh and unlink', async () => {
  await render()
  expect(button('編集する').classList.contains('secondary')).toBe(false)
  expect(button('更新').classList.contains('secondary')).toBe(true)
  expect(button('適用解除').classList.contains('secondary')).toBe(true)
})

// テストケース: channel APIのZ表記とcurrent APIの+00:00表記で同じrevisionを返す。
// 期待値: 初回と更新後に現在画像・編集を使え、表記差だけで更新要求へ戻らない。
test('11.5 accepts real API revision formats through DTO, hook and Console', async () => {
  selected.updatedAt = revision.replace('+00:00', 'Z')
  await render(true)
  expect(container.textContent).toContain('適用中')
  expect(container.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')).not.toBeNull()
  await click('更新')
  expect(container.textContent).toContain('適用中')
  await click('編集する')
  expect(document.activeElement?.id).toBe('rich-menu-editor-heading')
  await preview()
  expect(document.activeElement?.id).toBe('rich-menu-preview-heading')
  await click('キャンセル')
  expect(document.activeElement?.textContent).toBe('編集する')
  expect(calls('/operations/')).toHaveLength(0)
})

// テストケース: 現在メニューの取得が失敗している画面を開く。
// 期待値: 更新導線は一つだけで、内容のない結果パネルを表示しない。
test('11.4 exposes one recovery refresh without an empty result card', async () => {
  status = 503
  await render()
  expect(
    [...container.querySelectorAll('button')].filter((value) => value.textContent === '更新'),
  ).toHaveLength(1)
  expect(container.querySelector('[aria-label="LINEへの反映と直前結果"]')).toBeNull()
})

// テストケース: 通常閲覧から編集を開始する。
// 期待値: 操作結果も適用確認もない間は空の結果パネルを挟まない。
test('11.4 keeps viewing and editing free of empty operation cards', async () => {
  await render()
  expect(container.querySelector('[aria-label="LINEへの反映と直前結果"]')).toBeNull()
  await click('編集する')
  expect(container.querySelector('[aria-label="LINEへの反映と直前結果"]')).toBeNull()
  await preview()
  expect(container.querySelector('[aria-label="LINEへの反映と直前結果"]')).not.toBeNull()
})

// テストケース: 過去資源が残るが実行可能な回復対象・操作がない実運用相当の状態を表示する。
// 期待値: 成功だけに見せず確認案内を出し、任意削除や編集・解除を提供しない。
test('11.4 explains cleanup restrictions when no safe recovery action exists', async () => {
  view.rich.cleanupResources = structuredClone(
    fixtures.cleanup.rich.cleanupResources,
  ) as CurrentMenuView['rich']['cleanupResources']
  view.rich.activeOperation = null
  view.rich.blockingOperation = null
  view.rich.nextAllowedActions = ['get_state', 'view_history']
  view.rich.effectiveActions = ['get_state', 'view_history']
  view.recoveryTargets = []
  await render()
  expect(container.textContent).toContain(
    '過去の適用処理に不整合があります。管理者による復旧が必要です。',
  )
  expect(
    container.querySelectorAll('[aria-label="リッチメニュー状態"] [role="alert"]'),
  ).toHaveLength(1)
  expect(container.textContent).not.toContain('LINE側の変更または未解決の操作を確認')
  expect(container.textContent).not.toContain('次にできる操作:')
  expect(container.querySelector('[aria-label="リッチメニュー状態"] [role="alert"]')).not.toBeNull()
  expect(container.textContent).not.toContain('適用中')
  expect(container.textContent).not.toContain('編集する')
  expect(container.textContent).not.toContain('残ったデータ 1 を削除')
})

// テストケース: 更新のcurrent応答を遅延し、focus中の更新ボタンを再操作する。
// 期待値: 更新中もfocus可能な要素を維持し、aria-disabledで再受付を防いで一度だけGETする。
test('11.4 retains the refresh focus target while ignoring repeated refreshes', async () => {
  await render()
  let resolveCurrent!: (response: Response) => void
  fetchMock.mockImplementation((input, init) => {
    if (typeof input === 'string' && input.includes('/current/'))
      return new Promise<Response>((resolve) => {
        resolveCurrent = resolve
      })
    return fetchHandler(input, init)
  })
  const refresh = button('更新')
  refresh.focus()
  await click('更新')
  expect(refresh.disabled).toBe(false)
  expect(refresh.getAttribute('aria-disabled')).toBe('true')
  expect(document.activeElement).toBe(refresh)
  await click('更新')
  expect(
    fetchMock.mock.calls.filter(
      ([input]) => typeof input === 'string' && input.includes('/current/'),
    ),
  ).toHaveLength(2)
  await act(async () => resolveCurrent(reply(view)))
  expect(refresh.getAttribute('aria-disabled')).toBe('false')
  expect(document.activeElement).toBe(refresh)
})

// テストケース: 適用済み画面でFigmaの見出し・状態・画像カード内操作を表示する。
// 期待値: 状態を見出し横に置き、編集・解除・更新が同じカード内で順に並ぶ。
test('Figma aligns the status badge and current menu actions', async () => {
  await render()
  expect(container.querySelector('.rich-menu-design')).not.toBeNull()
  expect(
    container.querySelector('.rich-menu-header .rich-menu-state-badge')?.textContent,
  ).toContain('適用中')
  const card = container.querySelector('.rich-menu-current-card')
  expect(card?.querySelector<HTMLImageElement>('img[alt]:not([alt=""])')?.alt).toBe(
    '通知チャネル の現在のメニュー',
  )
  expect(
    [...card!.querySelectorAll('.rich-menu-detail-actions button')].map((b) => b.textContent),
  ).toEqual(['編集する', '適用解除', '更新'])
  expect(card?.querySelector('.recovery-card')).toBeNull()
})

// テストケース: 保存設定を編集し、プレビューの反映・戻る・取消を確認する。
// 期待値: 編集用の見出しと2列の項目構造を使い、副操作は確認カード内のsecondaryで表示する。
test('Figma groups edit fields and preview actions without technical ids', async () => {
  await render()
  await click('編集する')
  expect(container.querySelector('#rich-menu-editor-heading')?.textContent).toContain(
    'メニューを編集',
  )
  expect(container.querySelector('.rich-menu-edit-row')?.querySelectorAll('label')).toHaveLength(2)
  expect(button('キャンセル').className).toContain('secondary')
  await preview()
  const panel = container.querySelector('.rich-menu-preview .rich-menu-inline-apply')
  expect(panel).not.toBeNull()
  expect(panel?.contains(button('LINEに反映'))).toBe(true)
  expect(panel?.contains(button('編集へ戻る'))).toBe(true)
  expect(panel?.contains(button('キャンセル'))).toBe(true)
  expect(button('編集へ戻る').className).toContain('secondary')
  expect(button('キャンセル').className).toContain('secondary')
  expect(container.textContent).not.toContain('jp-link-one')
})

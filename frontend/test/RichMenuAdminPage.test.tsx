import { StrictMode, act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import RichMenuAdminPage from '../src/RichMenuAdminPage'
import { ChannelAdminApiError } from '../src/channelAdminApi'
import type { ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'
import type { RichMenuAdminApiClient } from '../src/richMenuAdminApi'

vi.mock('../src/RichMenuAdminConsole', () => ({
  default: ({ channelId }: { channelId: string }) => `console:${channelId}`,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channel: ChannelAdminItem = {
  channelId: '123e4567-e89b-42d3-a456-426614174000', label: '動的タイトルbot',
  messagingApiChannelId: '1234567890', botUserId: `U${'a'.repeat(32)}`, providerId: '456',
  active: true, credentialsState: 'configured', credentialsUpdatedAt: '2026-08-29T10:00:00+09:00',
  createdAt: '2026-08-29T09:00:00+09:00', updatedAt: '2026-08-29T10:00:00+09:00',
  webhookUrl: 'https://example.com/api/line/webhooks/123e4567-e89b-42d3-a456-426614174000/',
  deactivationSummary: null, richMenuRefreshRequired: false,
}

const channelApi = (getChannel: ChannelAdminApiClient['getChannel']): ChannelAdminApiClient => ({
  listChannels: vi.fn(), getChannel, register: vi.fn(), update: vi.fn(), setState: vi.fn(), delete: vi.fn(), checkConnection: vi.fn(),
})

const richApi = {} as RichMenuAdminApiClient
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
  vi.clearAllMocks()
})

async function renderAt(path: string, api: ChannelAdminApiClient) {
  await act(async () => root.render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/liff/rich-menus/:channelId" element={<RichMenuAdminPage channelApi={api} richApi={richApi} />} />
      </Routes>
    </MemoryRouter>,
  ))
}

// テストケース: 形式不正、not-found、owner scope外のchannel IDでdetail pageを開く。
// 期待値: すべて同じ非開示表示とselector Linkだけへ縮約する。
test.each([
  ['not-a-uuid', vi.fn()],
  [channel.channelId, vi.fn().mockRejectedValue(new ChannelAdminApiError({ code: 'channel_not_found', summary: 'secret detail' }, 404))],
  [channel.channelId, vi.fn().mockRejectedValue(new ChannelAdminApiError({ code: 'owner_operation_blocked', summary: 'scope detail' }, 403))],
])('5.2 collapses unsafe channel lookup for %s', async (channelId, getChannel) => {
  await renderAt(`/liff/rich-menus/${channelId}`, channelApi(getChannel))
  expect(container.textContent).toContain('対象が見つかりません')
  expect(container.textContent).not.toContain('secret detail')
  expect(container.textContent).not.toContain('scope detail')
  expect(container.querySelector('a[href="/liff/rich-menus"]')).not.toBeNull()
  expect(container.textContent).not.toContain('console:')
})

// テストケース: provider ID未設定channelのdetail URLへ直接アクセスする。
// 期待値: 管理Consoleを表示せず、チャネル設定導線だけを示す。
test('5.2 blocks management for a provider-missing channel', async () => {
  await renderAt(`/liff/rich-menus/${channel.channelId}`, channelApi(vi.fn().mockResolvedValue({ ...channel, providerId: null })))
  expect(container.textContent).toContain('provider IDが設定されていません')
  expect(container.querySelector('a[href="/liff/channels"]')).not.toBeNull()
  expect(container.textContent).not.toContain('console:')
})

// テストケース: 利用可能channelのdetail URLを開く。
// 期待値: channel名の動的title、固定h1、常時selector Linkと既存Consoleを表示する。
test('5.2 renders dynamic metadata and the bounded console for a valid channel', async () => {
  const getChannel = vi.fn().mockResolvedValue(channel)
  await renderAt(`/liff/rich-menus/${channel.channelId}`, channelApi(getChannel))
  expect(document.title).toBe('動的タイトルbot | リッチメニュー管理')
  expect(container.querySelector('h1')?.textContent).toBe('リッチメニュー管理')
  expect(container.querySelector('a[href="/liff/rich-menus"]')).not.toBeNull()
  expect(container.textContent).toContain(`console:${channel.channelId}`)
  const signal = getChannel.mock.calls[0]?.[1]?.signal as AbortSignal
  expect(signal.aborted).toBe(false)
  await act(async () => root.unmount())
  expect(signal.aborted).toBe(true)
  root = createRoot(container)
})

// テストケース: React Strict Modeのeffect再実行下で利用可能channelのdetail URLを開く。
// 期待値: 検証用cleanupで中止したsignalを再利用せず、新しい読込みで管理Consoleへ収束する。
test('loads rich-menu detail with a fresh signal after the Strict Mode effect replay', async () => {
  const getChannel = vi.fn().mockResolvedValue(channel)
  await act(async () => root.render(
    <StrictMode>
      <MemoryRouter initialEntries={[`/liff/rich-menus/${channel.channelId}`]}>
        <Routes>
          <Route path="/liff/rich-menus/:channelId" element={<RichMenuAdminPage channelApi={channelApi(getChannel)} richApi={richApi} />} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  ))

  expect(container.textContent).toContain(`console:${channel.channelId}`)
  expect(getChannel).toHaveBeenCalledTimes(2)
  const firstSignal = getChannel.mock.calls[0]?.[1]?.signal as AbortSignal
  const latestSignal = getChannel.mock.calls[1]?.[1]?.signal as AbortSignal
  expect(firstSignal.aborted).toBe(true)
  expect(latestSignal.aborted).toBe(false)
})

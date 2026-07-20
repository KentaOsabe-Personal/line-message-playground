import { renderToString } from 'react-dom/server'
import { expect, test } from 'vitest'
import App, { OwnerConsole } from '../src/App'
import type { AuthGateContext } from '../src/AuthGate'

// テストケース: アプリケーションのルートを認証開始前に描画する。
// 期待値: タイトルと認証中状態を表示し、配信画面はまだmountしない。
test('renders the application title behind the authentication gate', () => {
  const html = renderToString(<App />)
  expect(html).toContain('LINE Message Playground')
  expect(html).toContain('LINEログインを初期化しています')
  expect(html).not.toContain('LINEテスト配信')
  expect(html).not.toContain('送信内容を確認')
})

const context = (
  session: AuthGateContext['session'],
): AuthGateContext => ({
  session,
  getAccessToken: () => null,
  reauthenticate: () => undefined,
  reauthenticateForUnlink: () => undefined,
  unlinkReauthenticationReady: false,
  onSessionReceived: () => undefined,
  refreshSession: async () => undefined,
})

// テストケース: `/liff`のowner consoleをactiveとunlink pendingで描画する。
// 期待値: activeでは管理・配信を統合し、pendingではrecoveryだけを表示する。
test('integrates account and delivery views according to the owner state', () => {
  const active = renderToString(<OwnerConsole {...context({
    state: 'authenticated',
    profile: { displayName: 'Owner', linked: true },
  })} />)
  const pending = renderToString(<OwnerConsole {...context({
    state: 'unlinking',
    stage: 'local_deletion_pending',
    retryAction: 'retry_local_delete',
  })} />)

  expect(active).toContain('アカウント管理')
  expect(active).toContain('LINEテスト配信')
  expect(pending).toContain('全連携解除を処理中です')
  expect(pending).not.toContain('LINEテスト配信')
})

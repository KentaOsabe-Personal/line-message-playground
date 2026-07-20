import { renderToString } from 'react-dom/server'
import { expect, test } from 'vitest'
import App from '../src/App'

// テストケース: アプリケーションのルートを認証開始前に描画する。
// 期待値: タイトルと認証中状態を表示し、配信画面はまだmountしない。
test('renders the application title behind the authentication gate', () => {
  const html = renderToString(<App />)
  expect(html).toContain('LINE Message Playground')
  expect(html).toContain('LINEログインを初期化しています')
  expect(html).not.toContain('LINEテスト配信')
  expect(html).not.toContain('送信内容を確認')
})

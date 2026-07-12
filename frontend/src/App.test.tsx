import { renderToString } from 'react-dom/server'
import { expect, test } from 'vitest'
import App from './App'

// テストケース: アプリケーションのルートコンポーネントを文字列として描画する。
// 期待値: 描画結果にアプリケーション名「LINE Message Playground」が含まれる。
test('renders the application title', () => {
  const html = renderToString(<App />)
  expect(html).toContain('LINE Message Playground')
  expect(html).toContain('LINEテスト配信')
  expect(html).toContain('送信内容を確認')
})

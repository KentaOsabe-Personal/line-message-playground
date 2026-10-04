# Frontendの指示

`frontend/` の設計・実装・レビューに適用します。共通の安全規則と実行コマンドはrootのAGENTSとtech steeringに従います。

## 配置と責務

**場所**: `/frontend/`  
**目的**: React UI、ブラウザ側の状態、Frontend のビルド・テスト設定  
**実装場所**: `/frontend/src/`
**テスト場所**: `/frontend/test/`

`main.tsx` は `BrowserRouter` を含むアプリケーション起動とグローバル CSS 読み込み、`App.tsx` は route tree と認証済み shell の合成を担当します。現在は flat 構造を維持しているため、feature directory、共通 components、hooks 等の分割規則はまだ固定しません。

flat 構造でも、UI とイベント接続、状態遷移、HTTP 通信、境界 DTO の検証はモジュールの責務として分離します。複雑な画面状態は純粋な遷移関数へ切り出し、Component へ通信状態や再試行判断を埋め込みません。

確立済みの責務接尾辞を使い、Component は表示と操作の接続、`*Api.ts` は HTTP 手順と安全なエラー変換、`*Dto.ts` は `unknown` な境界データの実行時検証、`*State.ts` は純粋な状態遷移を担当します。cookie、CSRF、共通 fetch 設定や LIFF SDK は専用 adapter に閉じ込め、各 Component から直接扱いません。

URL 駆動の画面では、`appRoutes.ts` が定義済み path、metadata、安全な復帰先検証を所有し、`AuthGate.tsx` が session transition、`AppLayout.tsx` と `PageFrame.tsx` が共通 navigation、title、focus、status 表現を所有します。`*Page.tsx` は route parameter の検証と既存機能 Component の mount 期間を扱う薄い adapter とし、API／DTO／State の責務を移しません。

React Router への依存は composition root、共通 shell、route page、および明示的な画面遷移リンクを提供する UI Component に限定します。機能の `*Api.ts`、`*Dto.ts`、`*State.ts` から router を import せず、画面遷移と業務状態機械を分離します。新しい owner 機能画面は定義済み route、共通 shell、単一機能の page adapter を追加し、他画面の Component を同時 mount しません。

## 型安全性

- TypeScript は `strict`、`isolatedModules`、`noEmit` を有効にする
- JavaScript を混在させず、API レスポンス等の境界データには型を与える
- Production build は `tsc -b` を Vite build より先に実行し、型エラーをビルドの失敗とする

## 静的チェック

コード・テスト・設定・依存の変更後は、rootから `sh scripts/check.sh frontend` を実行します。ESLintの型情報を使うLint、React Hooks検査、Prettierの整形チェック、TypeScript型検査をFrontend全体へ適用します。準備・修正・対象範囲は [READMEのローカル品質チェック](../README.md#ローカル品質チェック) が正本です。Lintを通すための一括無効化や、Promiseの失敗・Hooksの依存を無視する修正は行いません。

## 画面境界

- `BrowserRouter` と静的 route registry を画面選択の基準とし、認証済み機能は route-driven shell の配下へ置く
- 認証後の復帰先は `/liff` 配下の定義済み path だけを許可し、外部 URL、query、hash、非 canonical な動的 ID を復帰先として信頼しない
- 現在の route に対応する機能だけを mount し、route page は既存 Component の合成と寿命だけを担当する。API schema、DTO 検証、業務状態機械を router loader／action や page adapter へ複製しない
- 画面外の機能データを先読みせず、別画面の機能を同時 mount しない
- 表示用 read は画面離脱時に `AbortSignal` で中止するか後着結果を破棄する。Backend が受け付けた mutation は画面離脱で中断・自動再送せず、operation ID と authoritative なサーバー状態から追跡する

### UI とアクセシビリティ

- Tailwind CSS の共通 theme token を色、余白、角丸、影、focus 表現の source of truth とし、機能固有 CSS に値を重複させない
- 共通 shell は wide／narrow の navigation、単一のページ見出し、`document.title`、route 変更時の main focus を一貫して提供する
- loading、success、failure、unknown は色だけに依存せず、テキストと semantic role で区別する。keyboard focus、contrast、長い識別子の折返し、reduced motion を共通品質として検証する

## 命名

- Component ファイルと Component: `PascalCase`（例: `App.tsx` / `App`）
- 型: `PascalCase`
- 変数、関数、state: `camelCase`
- Component テスト: `<Component>.test.tsx`

## import

外部パッケージを先に置き、同じ `src` 内は相対 import を使います。拡張子は省略します。path alias は現在設定されていないため、`@/` 等を前提にしません。

```typescript
import { renderToString } from 'react-dom/server'
import App from './App'
```

## テスト

- Vitestとjsdomを使う。テストは `frontend/test/` の `*.test.ts`／`*.test.tsx` に置く。
- 各テスト定義の直前に日本語コメントで `テストケース:` と `期待値:` を1行ずつ記載し、入力・操作と観測可能な期待結果を示す。
- 外部作用、状態遷移、並行更新の境界は、競合、安全性、処理時間もリスクに応じて検証する。サービスを横断するquery budgetの検証はBackendの規則と合わせる。
- 管理画面の秘密入力を共有stateやread modelに保存しない。LINE業務状態・API契約を変更する場合は [line-integration.md](../.kiro/steering/line-integration.md) も読む。

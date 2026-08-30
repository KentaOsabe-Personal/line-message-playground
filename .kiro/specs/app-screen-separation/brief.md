# Brief: app-screen-separation

## Problem

LINE Message Playground の owner は、認証後の一画面にアカウント管理、チャネル管理、リッチメニュー管理、メッセージ配信が同時に表示・mountされるため、目的の操作を見つけにくく、無関係な機能データまで読み込まれる。URLで機能画面を直接開けず、再読み込み、戻る・進む、ページタイトル、focus、狭い画面でのナビゲーションもアプリ全体として統一されていない。

## Current State

`App.tsx`が認証後に複数の機能Componentを同時mountし、`AuthGate.tsx`が認証処理とowner表示・logout UIを兼ねる。リッチメニュー管理はチャネル管理内のlocal stateで切り替わり、独立URLを持たない。UIは1,000行を超える単一CSSへ機能別styleが重複しており、React RouterとTailwind CSSは未導入である。

既存のAPI client、DTO validation、reducer、stale response抑止、二重送信防止、結果不明の再確認などの業務・安全性契約は実装済みであり、本対応では維持する。

## Desired Outcome

`/liff`を認証入口とし、認証後は中間トップを挟まずチャネル管理へ移動する。チャネル管理、アカウント管理、リッチメニュー管理、メッセージ配信を独立URLへ分割し、共通headerと統一されたデザインから各機能へ直接移動できるようにする。

現在URLに対応する機能だけをmountして必要なAPIだけを呼び、既存業務機能とBackend契約を変えずに全画面をTailwind CSSの同一デザイン体系へ移行する。

## Approach

共通基盤から段階移行する。最初にReact RouterのDeclarative mode、認証layout、共通header、内部route allowlistを導入し、その上へ既存4機能Componentをroute単位で移す。次にリッチメニューのチャネル選択／詳細URLを分離し、認証・logout・全連携解除・画面離脱時状態をroute境界へ統合する。最後に既存全画面をTailwind CSSへ移行し、route、mount/API分離、accessibility、既存機能回帰をテストする。

既存のAPI、DTO、状態機械を再実装せず利用することで、画面構造の変更と業務ロジックの回帰リスクを分離できるため、この方法を選ぶ。

## Scope

- **In**: `/`から`/liff`へのredirectと認証後のチャネル管理直行、定義済み静的／動的route、404、`BrowserRouter`、内部routeだけを許可する認証後復帰、共通headerとresponsive navigation、4機能画面の責務分離、リッチメニューのチャネル選択／詳細画面、全連携解除・session失効・logoutのroute制御、未保存入力と受付済み処理の画面離脱契約、route単位のmount/API読込み、表示用requestの中断または後着応答抑止、loading／error表示、`document.title`、route変更時の`main` focus、Tailwind CSSによる全画面のUI統一、WCAG 2.2 Level AAを目標とするkeyboard・contrast・status表現、Vitest／jsdomによる自動確認
- **Out**: 新しい業務機能、Backend業務ロジック変更、API schema変更、新規API endpoint、Database変更・migration、Mobile-first設計、LINEアプリ内browserの正式保証、WCAG認証・第三者監査、新規E2E／visual regression framework、Bootstrap等のUI component library

## Boundary Candidates

- Router／認証layout: route定義、内部復帰先、session失効、logout、全連携解除の画面制御
- 共通UI shell: header、navigation、チャネル管理への開始導線、page title、route focus、responsive menu
- 機能page分離: 既存Account、Channel、Delivery Componentのroute単位mountと責務維持
- リッチメニューroute: 全チャネル選択、利用不可理由、動的`channelId`検証、read-only詳細、戻る導線
- Design system／検証: Tailwind theme、既存CSS撤去、accessibility、route／API isolation／回帰test

## Out of Boundary

- 既存のowner認証、チャネル、recipient、配信、リッチメニュー資源操作のBackend契約を変更しない
- 既存の確認、冪等性、競合制御、結果不明、回復操作を簡略化または自動再実行しない
- 画面分割を理由に機能追加、Backendへの状態保存、ブラウザ永続領域への秘密値・入力本文・プレビュー保存を行わない

## Upstream / Downstream

- **Upstream**: `line-account-linking`のowner sessionと全連携解除、`line-channel-admin-ui`のチャネル管理UI契約、`line-rich-menu-admin-lifecycle`の管理・回復・read-only契約、`linked-recipient-delivery`の配信・operation契約、既存Vite／ngrok配信経路
- **Downstream**: 将来の機能画面追加、共通navigation拡張、配信履歴・利用量画面、UI design systemの継続利用

## Existing Spec Touchpoints

- **Extends**: 既存Specは再オープンしない。本Specは完了済みの`line-account-linking`、`line-channel-admin-ui`、`line-rich-menu-admin-lifecycle`、`linked-recipient-delivery`が公開するFrontend機能をroute別画面へ再配置する
- **Adjacent**: `line-channel-foundation`、`line-friendship-sync`、`line-webhook-command-dispatch`のBackend状態と外部作用を変更せず、表示・操作契約だけを既存Component経由で利用する

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 30〜38件（依存追加、router／auth shell、各page分離、rich menu route、Tailwind全画面移行、responsive／accessibility、route・API isolation・回帰test、build確認を含む）
- **Independent responsibility seams**: 5（Router／認証layout、共通UI shell、既存機能page分離、リッチメニューroute、design system／検証）
- **Rationale**: 30〜39件のreview attention帯だが、すべてが「既存FrontendをURL単位の画面へ分割し、共通UIへ統一する」という一つの利用者成果へ収束する。新しいBackend状態機械、migration、外部API workflow、独立rolloutはなく、既存API・DTO・状態機械を維持できる。内部workstreamの依存順とintegration test境界を明示すれば、単一のbounded review範囲として扱える

## Constraints

- 仕様の言語は日本語とする
- React 19、TypeScript 6、Vite 8、Vitest、jsdom、および既存のstrict TypeScript設定を維持する
- React Routerは保守中のstable releaseを使用し、Declarative modeの`BrowserRouter`を`react-router`から利用する
- Tailwind CSSと`@tailwindcss/vite`はVite 8を正式対応する4.2.2以降の同一stable versionを固定する。両projectはMIT Licenseで、既存projectとのlicense問題はない
- Node 24 containerと`package-lock.json`／`npm ci`による再現可能なbuildを維持する
- LIFF Endpoint URLとLINEログインredirect URIは`/liff`のまま維持し、深い内部URLへの復帰はallowlistで保護する
- Viteからngrokまでの配信経路は`/liff/...`の直接アクセスへSPA entryを返す
- 表示用GETは可能なら`AbortSignal`対応し、対応できない場合もunmount後の応答を状態へ反映しない。Backend受付済みmutationは画面離脱で中断・再送しない
- 資格情報、本文、プレビュー、LINE user IDをURL、永続storage、log、errorへ追加しない
- 自動確認は既存Vitest／jsdom、`npm test`、`npm run build`、`git diff --check`を使用する

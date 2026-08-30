# 調査記録と設計判断

## Summary

- **Feature**: `app-screen-separation`
- **Discovery Scope**: Extension（light discovery）
- **Key Findings**:
  - 現在の`App.tsx`は認証後にアカウント、チャネル、配信を同時にmountし、リッチメニューは`ChannelAdminConsole`のlocal stateで切り替えるため、URLとデータ読込みの画面境界が存在しない。
  - API client、DTO runtime validation、純粋なstate reducer、認証generation fenceは再利用できる。一方、`liffConfig.ts`の`/liff`限定、GETの`AbortSignal`非対応、リッチメニューの離脱確認は本Specに合わせた変更が必要である。
  - RoadmapがReact Router Declarative modeとTailwind CSSを固定している。2026-08-29時点の公式情報とpackage metadataから、React Router 8.3.1、Tailwind CSS 4.3.3、`@tailwindcss/vite` 4.3.3がReact 19.2.7、Node 24、Vite 8の現行構成と整合する。

## Research Log

### 既存Frontendの画面境界

- **Context**: route単位mountと既存業務契約の再利用可否を確認した。
- **Sources Consulted**: `frontend/src/App.tsx`、`AuthGate.tsx`、`ChannelAdminConsole.tsx`、`AccountConsole.tsx`、`RichMenuAdminConsole.tsx`、`DeliveryForm.tsx`、関連テスト。
- **Findings**:
  - `OwnerConsole`が3機能を同時mountし、各Componentがmount時に独自のAPI読込みを開始する。
  - チャネル管理からリッチメニュー管理への遷移は`selectedRichMenuChannelId`であり、URLとbrowser historyを更新しない。
  - `AuthGate`は認証state machineと401失効処理を持つが、owner表示とlogout UIも所有する。共通headerからlogoutを呼ぶ公開contextはない。
  - 機能内の確認、operation latch、revision競合、unknown回復は既存Component／reducerに閉じており、route wrapperへ移す必要はない。
- **Implications**:
  - `App.tsx`をrouteと認証収束のcomposition rootにし、既存Consoleを1 routeにつき1つだけmountするpage wrapperで包む。
  - `AuthGate`は認証制御を維持し、表示headerを共通layoutへ移す。既存業務state machineは作り直さない。

### Browser routeとLIFF復帰

- **Context**: 直接アクセス、固定LIFF redirect、外部URL拒否を同時に満たす必要がある。
- **Sources Consulted**: `frontend/src/liffConfig.ts`、`liffClient.ts`、`authState.ts`、`AuthGate.test.tsx`、`.kiro/steering/roadmap.md`。
- **Findings**:
  - 現在の`liffConfig.ts`は`currentPathname === '/liff'`だけを許可し、サブルートの直接アクセスをconfiguration errorにする。
  - LIFF Endpoint URLとLINEログインredirect URIはRoadmapにより`/liff`固定である。
  - 認証redirectを越えて元画面へ戻すには、queryや外部URLではなく、route定義から検証済みのpathnameだけをtab-localに一時保持する必要がある。
- **Implications**:
  - route metadataを安全な内部pathの唯一の定義元とし、静的pathまたは正規UUIDを持つrich-menu detailだけを復帰先として許可する。
  - 認証開始前に許可済みpathnameを`sessionStorage`へ保存し、`/liff`での認証成功後に一回だけ消費する。未知path、origin、query、hashは保存しない。

### 読込み中止と受付済み操作

- **Context**: route離脱時の後着応答と、外部作用の自動再実行禁止を分離する必要がある。
- **Sources Consulted**: `frontend/src/httpApi.ts`、各`*Api.ts`、各Consoleのeffect、`deliveryState.ts`、関連テスト。
- **Findings**:
  - `ProtectedHttpClient`は`AbortSignal`を受けず、既存Componentはgeneration、request ID、effect-local flagで一部の後着表示だけを抑止する。
  - mutationには既存のoperation ID、confirmation token、revision、二重操作lockがある。画面離脱を理由に同じmutationを自動再送してはならない。
  - 配信operation IDは現在memory-onlyで、route unmount後に同一browser sessionで追跡を再開できない。
- **Implications**:
  - GETと状態確認だけにoptional `AbortSignal`を通し、abortを利用者向けerrorへ変換しない。POST／PATCH／DELETEにはroute abort signalを渡さない。
  - unmount後のmutation応答はgeneration fenceでUIへ反映せず、再訪時にBackendの保存状態をGETする。
  - 配信は正規UUIDのoperation IDだけを`sessionStorage`へ保持し、本文、subject、recipient、confirmation token、previewは保持しない。

### Routerとdesign systemの依存確認

- **Context**: Roadmapが指定する依存の導入方式、互換性、browser範囲を確認した。
- **Sources Consulted**: React Router公式Declarative installation／`BrowserRouter` API、Tailwind CSS公式Vite installation／compatibility、npm package metadata、`frontend/package.json`、`frontend/Dockerfile`。
- **Findings**:
  - React Router公式はDeclarative modeで`react-router`から`BrowserRouter`をimportし、`Routes`／`Route`、`Link`、`NavLink`を使う構成を示す。8.3.1はReact／React DOM 19.2.7以上とNode 22.22以上を要求し、既存React 19.2.7とNode 24を満たす。
  - Tailwind CSS公式はVite pluginとして`tailwindcss`と`@tailwindcss/vite`を同時導入し、CSSから`@import "tailwindcss"`する方式を示す。両packageの現行4.3.3を同版固定し、pluginのpeer dependencyはVite 8を含む。
  - Tailwind CSS v4の公式browser範囲はChrome 111、Safari 16.4、Firefox 128であり、要件12.1と一致する。未対応の新しいutilityに依存しない。
- **Implications**:
  - Data mode／Framework modeや自作routerは導入しない。機能データ取得は既存Componentに残す。
  - Tailwind theme variableを色、spacing、radius、focus ringの唯一のdesign tokenとし、既存CSSはrich-menu固有の画像grid等、utilityで表現しにくい最小範囲へ縮小する。

### 現行検証基盤

- **Context**: 新しいE2E／visual regression基盤なしで検証可能か確認した。
- **Sources Consulted**: `frontend/vite.config.ts`、`frontend/test/`、`package.json`、baseline command結果。
- **Findings**:
  - Vitest 4.1.10とjsdom 28.1.0を使用し、DOMは`createRoot`、`act`、標準selectorで検証する。Testing Library、axe、E2E runnerは未導入である。
  - `NGROK_DOMAIN=example.com`を与えたbaselineでは32 test files／179 testsとproduction buildが成功した。
  - title、focus、history、navigation semanticsはjsdomで検証できる。contrast、responsive、横scroll、実browser互換は手動browser matrixが必要である。
- **Implications**:
  - 既存Vitestを拡張し、新しいtest frameworkは追加しない。正式browser範囲はproduction buildと手動確認をvalidation hookとして記録する。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|---|---|---|---|---|
| React Router Declarative mode | `BrowserRouter`と明示的なroute tree、routeごとのpage wrapper | Roadmap準拠、標準history、`Link`／`NavLink` semantics、動的segmentと404を型付き境界へ集約できる | runtime依存追加、テストにrouter contextが必要 | 採用 |
| 自作History adapter | `pushState`／`popstate`を独自storeで包む | 依存が増えない | Link semantics、nested layout、404、focus連携を再実装し、Roadmapにも反する | 不採用 |
| Data Router | loader/actionが取得とmutationを所有 | route data lifecycleを統合できる | 既存API／reducer責務を移動し、業務回帰と範囲を増やす | 不採用 |
| 既存CSSの漸進修正 | 現行selectorへ共通色とmedia queryを追加 | dependency追加なし | 画面別重複とdesign token不統一を残し、Roadmapに反する | 不採用 |
| Tailwind CSS v4 | Vite plugin、theme variable、utility classで全画面統一 | 要件のbrowser下限と一致、zero-runtime、共通tokenを強制しやすい | 大きなmarkup差分と手動visual QAが必要 | 採用 |

## Design Decisions

### Decision: routeを画面実行境界にする

- **Context**: URL、現在画面だけのmount、共通layoutを一つの仕組みへ一般化する。
- **Alternatives Considered**:
  1. `BrowserRouter`のnested routeとpage wrapper。
  2. `App`内のpathname switch。
- **Selected Approach**: `BrowserRouter`とDeclarative `Routes`を使い、全保護routeを`AuthGate`と`AppLayout`の内側へ置く。root redirectとwildcard 404を明示する。
- **Rationale**: 複数要件を「URLが実行中pageを一意に決める」という一つの契約へ一般化できる。
- **Trade-offs**: route test fixtureが必要になるが、自作history実装を避けられる。
- **Follow-up**: Vite/ngrokの全定義pathがSPA entryを返すことを直接アクセスで確認する。

### Decision: 認証復帰情報とowner一時情報を一つのtab-local adapterへ閉じる

- **Context**: 固定`/liff`redirect、logout clear、配信追跡、秘密非保存を一貫させる。
- **Alternatives Considered**:
  1. query parameterでreturn URLを渡す。
  2. 任意URLを`sessionStorage`へ保存する。
  3. 許可routeと正規operation IDだけを専用adapterで保存する。
- **Selected Approach**: `ownerSessionStorage.ts`が`pendingReturnPath`、unlink再認証marker、delivery operation IDだけをruntime validation付きで扱い、logout／全連携解除完了で一括消去する。
- **Rationale**: URLへのowner情報追加とopen redirectを防ぎ、要件8.5の最小追跡だけを提供する。
- **Trade-offs**: tabを越えた復元は行わない。
- **Follow-up**: storage unavailable時はfail closedでトップまたは明示的な再操作へ収束することをテストする。

### Decision: read cancellationとmutation継続を別契約にする

- **Context**: navigation時に取得を中止しつつ、受付済み外部作用を取り消したり再送したりしない。
- **Alternatives Considered**:
  1. 全requestにpage-level signalを渡す。
  2. generation fenceだけを使う。
  3. read requestだけsignalを受け、全async処理をgeneration fenceで保護する。
- **Selected Approach**: 3を採用する。GET／安全な状態確認だけをabort可能にし、mutationは既存operation contractを維持する。
- **Rationale**: cancellationと業務冪等性の責務を混同しない。
- **Trade-offs**: API interfaceにoptional read optionsを追加するが、HTTP schemaは変わらない。
- **Follow-up**: AbortErrorがnetwork error UIを発生させないこと、mutation unmount後に自動再送されないことを検証する。

### Decision: 既存業務Componentをpage adapterで再利用する

- **Context**: 画面分割で既存の確認、競合、回復を回帰させない。
- **Alternatives Considered**:
  1. 各機能UIをroute前提で全面再実装する。
  2. page wrapperから既存Consoleへnavigation／meta／session callbackだけを注入する。
- **Selected Approach**: 2を採用する。見出し階層、inline rich-menu切替、離脱確認、read optionsだけを変更し、API／DTO／reducerを保持する。
- **Rationale**: interfaceを一般化しながら、実装範囲を現在要件へ限定できる。
- **Trade-offs**: Tailwind移行でmarkup差分は大きいが、業務状態遷移は変えない。
- **Follow-up**: 既存179 testをroute contextに適応し、機能回帰を維持する。

## Design-stage Spec Size Assessment

- **Verdict**: `PASS (single-spec)`
- **Projected executable tasks**: 36〜39件（1〜3時間単位、依存導入、routing、認証、5 page、read cancellation、session追跡、Tailwind移行、テスト、browser QAを含む）
- **Independent responsibility seams**: 5（Router／認証layout、共通UI shell、既存機能page分離、リッチメニューroute、design system／横断検証）
- **Workstreams and order**:
  1. 依存・route contract・SPA配信前提
  2. AuthGate／owner session／共通layout
  3. 既存機能page分離とrich-menu選択／詳細
  4. read cancellation／delivery operation追跡
  5. Tailwind統一UI／accessibility／全回帰検証
- **Rationale**: 30〜39件のreview attention帯であるが、すべてが「URLを単一の画面実行境界にする」一つのFrontend成果へ収束する。Backend、API schema、DB、migration、新しい外部workflow、独立rolloutはなく、file owner、依存順、contract、統合testを明示できる。設計reviewは1回のlocal repairで収束可能であり、複合境界リスクの分割条件に該当しない。

## Risks & Mitigations

- Browser historyと認証redirectが競合する — fixed `/liff` redirectと一回限りのallowlisted return pathを分離し、root／logoutだけ`replace`する。
- 既存mutationがunmount後にstate更新する — generation fenceで描画反映を止め、mutation signalや自動再送を追加しない。
- 画面分割でrich-menu lifecycle contractを欠落する — detail pageは既存`RichMenuAdminConsole`とAPI／reducerをそのまま再利用し、selectorは一覧と導線だけを所有する。
- Tailwind移行でcontrast／focus／overflowが回帰する — theme token、DOM test、対応browserの手動matrix、production buildを完了条件にする。
- Vite devでは動くがdirect reloadの配信経路が崩れる — ViteをSPA modeとして明示し、ngrok経由を含む全定義pathのentry返却をvalidation hookにする。
- React Router 8のNode下限 — Node 24のDockerfileをruntime prerequisiteとして維持し、依存更新時にengine／peer dependencyを再確認する。

## Design Review Gate

- **Pass 1 mechanical review**: 全74個の受入条件IDがtraceabilityに存在し、Boundary Commitmentsの4区分、具体的なFile Structure Plan、design-stage size assessmentに欠落またはplaceholderがないことを確認した。
- **Pass 1 local repair**: summary上の設計componentとfile ownerの対応を、`App.tsx`、`appRoutes.ts`、page adapter群、`httpApi.ts`、`style.css`へ明記した。
- **Architecture and executability review**: Router、Auth、shared chrome、feature page、IO、styleの依存方向が一方向であり、Backend変更や隠れたmigrationを必要としない。read cancellationとmutation継続、selectorとdetail、normal layoutとunlink recoveryの責任が分離されている。
- **Size review**: 36〜39件のreview attention帯だが、5 workstreamのfile owner、依存順、integration test、rollback境界が明示され、同じ構造的問題は再発していない。
- **Verdict**: `PASS (single-spec)`。requirements／design gapと`SPLIT_REQUIRED`条件は検出されなかった。

## References

- [React Router Declarative installation](https://reactrouter.com/start/declarative/installation) — `react-router`と`BrowserRouter`の公式導入方式。
- [React Router BrowserRouter API](https://reactrouter.com/api/declarative-routers/BrowserRouter) — Browser History APIを使うDeclarative router契約。
- [Tailwind CSS with Vite](https://tailwindcss.com/docs/installation/using-vite) — `tailwindcss`と`@tailwindcss/vite`の公式構成。
- [Tailwind CSS compatibility](https://tailwindcss.com/docs/compatibility) — Chrome 111、Safari 16.4、Firefox 128のbrowser下限。
- `.kiro/steering/product.md`、`tech.md`、`structure.md`、`roadmap.md`、`spec-sizing.md` — product、stack、配置、依存、サイズ判断。
- `frontend/src/`、`frontend/test/` — 既存実装と回帰testの一次情報。

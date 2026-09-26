# Roadmap

## Overview

LINE Message Playground を、固定設定の自分宛て配信から、LIFF／LINEログイン、Webhook、複数のLINE公式アカウント、登録済み配信先を扱える自分専用の通知コンソールへ段階的に拡張する。

最初にDocker Composeへngrokを直接組み込み、スマートフォンのLINEアプリからローカルのFrontendとWebhookへ到達できる開発導線を作る。その後、チャネル資格情報の暗号化基盤を先行させ、LINEアカウント連携、Webhookによる友だち状態同期、連携先への安全な配信、専用管理画面の順で新規specを進める。

## Approach Decision

- **Chosen**: 基盤先行・管理UI後付け。ngrok、複数チャネル資格情報、LINEアカウント連携の後、Webhookを「検証済みイベント受付」「友だち状態同期」「許可リスト型interaction」の3段階へ分け、配信と管理UIを依存順に積み上げる
- **Why**: 認証のない段階で秘密情報管理画面を公開せず、配信とWebhookが同じ資格情報取得境界を利用できる。Webhook内でも、外部公開されたセキュリティ境界、recipient状態遷移、replyを伴う外部作用を分離すると、各成果を独立してレビュー・検証できる
- **Rejected alternatives**: 単一の`line-webhook-interaction`は25〜31タスクと複数の独立責任を抱える。受付と全イベント処理の2分割では状態同期とreply外部作用が同居する。署名検証とイベント台帳まで別specにする4分割は、常に一体で必要な受付保証を細分化しすぎる

## Scope

- **In**: ngrokによる開発用HTTPS導線、複数Messaging APIチャネル、暗号化したアクセストークンとチャネルシークレットのDB保存、LIFF／LINEログイン、配信先登録、チャネル別Webhook、友だち状態同期、登録済み連携先への配信、明示的な受取確認、チャネル管理画面
- **Out**: 不特定多数向けサービス、複数管理者のRBAC、異なるプロバイダー間の本人統合、broadcast／narrowcast、予約配信、汎用チャットボット、配信到達・既読の保証、本番公開基盤、外部KMSやワーカー基盤

## Constraints

- 自分だけが利用する個人学習環境を維持し、学習に不要なLINEユーザーデータを保存しない
- ngrokは開発用途に限定して通常のComposeサービスとして起動する。Compose起動中はFrontendに加えて公開Webhookやhealth endpointも外部から到達可能になるため、公開URLを共有せず、利用後は全サービスを停止する
- ngrokのauthtokenはLINE資格情報とは別のインフラ秘密情報として環境変数から注入し、DBへ保存しない
- LINEのアクセストークンとチャネルシークレットは認証付き暗号で暗号化してDBへ保存し、専用の暗号化マスターキーだけを環境変数へ残す
- 暗号化キーを失うと復号できないため、ローテーション手順とDB外バックアップ方針を持つ
- 最初は同一プロバイダー配下のLINE Login／Messaging APIチャネルを対象とする。同一人物でも異なるプロバイダーではユーザーIDが異なる
- 1つのLINEログインチャネルにリンクできるLINE公式アカウントは1つである。2つ目以降の友だち状態は各Messaging APIチャネルのWebhookを主情報とする
- Webhookはチャネル別の不透明な公開キーで候補を選び、生のrequest bodyに対する署名検証後にだけ内容を信頼する
- queue／workerを新設しない初期段階では、Webhook同期処理を重複記録と軽量な状態更新に限定して速やかに2xxを返す
- 通常のpush成功を端末到達や既読とみなさず、postbackは利用者による明示的な受取確認として区別する

## Boundary Strategy

- **Why this split**: チャネル資格情報、本人認証、Webhook受付、友だち状態projection、許可リスト型interaction、送信操作、秘密情報管理UIは異なるセキュリティ境界と失敗特性を持つ。Webhook受付は後続処理が信頼できるイベント境界だけを提供し、状態同期と外部replyを並行可能な別specにする
- **Shared seams to watch**: 検証済みイベントenvelope、イベント台帳と各handlerの処理結果、チャネルIDとユーザーIDの対応、未連携ユーザーの非登録、recipientのイベント時刻、reply tokenの一回性、Webhook postbackと配信記録の関連付け

## Spec Size Assessment

- **Verdict**: SPLIT_REQUIRED
- **Projected executable tasks before split**: 25〜31件（公開受付、migration、競合・再送、状態遷移、外部reply、統合・セキュリティテスト、運用文書を含む）
- **Independent responsibility seams**: 4（チャネル選択・署名／destination検証、イベント台帳・重複排除、follow／unfollow状態同期、message／postback dispatchと即時reply）
- **Rationale**: 件数は現在の40件基準未満だが、4つの独立成果に加えて複数の状態／外部作用workflow、上流・下流責任の同居、反復する境界横断統合という複合リスクがあるため、単一レビュー範囲ではなく分割を維持する

## Direct Implementation Prerequisite

- [x] ngrok-compose-development-tunnel -- 公式ngrok Agentを通常のComposeサービスへ追加し、固定の開発用HTTPSドメインからViteと`/api`へ到達できるようにする。実装・ローカル疎通確認済み。外部トンネルの実機確認には利用者固有の`NGROK_AUTHTOKEN`と`NGROK_DOMAIN`が必要。Dependencies: none

## Superseded Specs

- line-webhook-interaction -- Requirements前のサイズゲートで`SPLIT_REQUIRED`となり、`line-webhook-ingress`、`line-friendship-sync`、`line-webhook-command-dispatch`へ置換した。既存ファイルは判断履歴として保持し、以後の一括spec生成対象にしない

## Phase 2: LINEリッチメニュー

### Overview

完了済みの複数チャネル管理とowner認証の上へ、アプリ組み込みテンプレートからチャネル既定リッチメニューを生成し、安全に適用・照合・解除できる機能を追加する。初期版はURI actionだけを扱い、アプリ外リッチメニューや利用者別リッチメニューの所有権を侵害しない。

上流でテンプレート、決定的画像生成、確認済みプレビュー、LINE資源の状態機械、履歴、結果不明からの照合を提供する。下流ではその契約をowner向け管理画面へ接続し、チャネル無効化・再有効化・物理削除と一貫した操作へ統合する。

### Approach Decision

- **Chosen**: 新機能を`line-rich-menu-foundation`と`line-rich-menu-admin-lifecycle`の2つの新規Specへ分割する。既存Specは再オープンせず、完了済みの公開境界を上流依存・統合接点として扱う
- **Why**: 画像生成とLINE資源管理は一つの「確認済み設定を追跡可能なチャネル既定資源へ収束させる」Backend成果へまとまる。owner UIとチャネル状態変更は、その成果を利用する管理体験として別にレビューできる。2分割なら利用者成果を過度に細分化せず、各Specを40件未満に保てる
- **Rejected alternatives**: 単一Specは50〜70タスクと複数の補償状態を抱える。3分割は画像プレビューと資源適用をさらに分けられるが、初期版では常に同じ適用フローで統合されるためSpec間契約と承認工程が増える。既存`line-channel-admin-ui`の再オープンは、コード変更先と新機能の責任境界を混同する

### Scope

- **In**: 3種類の組み込みテンプレート、表示名とHTTPSリンク検証、日本語画像生成、10分間のプレビュー、チャネル既定リッチメニューの適用・置換・解除、LINE実状態照合、外部変更警告、管理終了、履歴、結果不明の再確認、明示的な後片付け、owner向け管理UI、チャネル無効化・再有効化・物理削除との統合
- **Out**: 利用者別リッチメニュー、URI以外のaction、ownerによる画像アップロード、自由レイアウト・配色・フォント編集、カスタムテンプレート、統計、日時予約、過去メニューへのロールバック、生成画像履歴、アプリ外リッチメニューの編集または削除

### Constraints

- owner session、同一provider確認、exact-origin CSRF、暗号化チャネル資格情報、`updatedAt` revisionを再利用する
- LINEへの外部通信中は長時間のDB lockを保持せず、戻り時にowner・provider・チャネルrevisionと操作状態を再検証する
- LINEのrich-menu mutationにはretry keyがないため、結果不明時に新規作成を自動再試行せず、所有権を証明できる情報とLINE実状態から既存操作へ収束させる
- アプリ外リッチメニューは内容を編集せず資源を削除しない。Messaging APIから完全な内容を取得できない状態も安全な分類へ縮約する
- 画像はLINEの寸法・形式・アスペクト比と1MB上限を作成前に満たす。uploadは`api-data.line.me`と明示的なContent-Typeを扱う
- PillowはPython 3.14対応版を候補とし、同梱日本語フォントは版・ファイル・weight・digest・OFL-1.1表示をDesignで固定する
- 429、タイムアウト、結果不明、候補または旧資源の削除失敗を自動再試行しない

### Boundary Strategy

- **Why this split**: foundationはテンプレートからLINE資源・履歴までの整合性とheadless operation契約を所有する。admin-lifecycleはその契約を使う画面状態と、チャネル無効化・再有効化・削除のorchestrationを所有する
- **Shared seams to watch**: owner／provider／channel revisionを結ぶプレビュー、操作IDと所有権marker、管理対象IDとLINE実状態、`unknown`／`cleanup_required`時の操作禁止、無効化の開始・照合・完了契約、適用中資源のreference probe、履歴だけを伴うチャネル削除cleanup

### Spec Size Assessment

- **Verdict**: SPLIT_REQUIRED
- **Projected executable tasks before split**: 50〜70件（依存・font asset、migration、画像golden test、LINE gateway、複数段階の外部作用、競合・回復、API、UI、チャネルライフサイクル、統合・セキュリティテストを含む）
- **Independent responsibility seams**: 6（テンプレート／画像生成、プレビュー、LINE gateway、資源操作／履歴／照合、owner管理UI、チャネルライフサイクル統合）
- **Rationale**: 40件基準を超え、さらに結果不明、候補・旧資源cleanup、無効化要確認という複数の補償状態、上流基盤と下流利用機能、反復する境界横断統合が重なるため、単一Specではbounded reviewが安定しない

## Specs (dependency order)

- [x] line-channel-foundation -- 複数Messaging APIチャネルと暗号化資格情報をDBで管理し、安全な取得・初期登録・鍵ローテーション境界を提供する。Dependencies: ngrok-compose-development-tunnel
- [x] line-account-linking -- LIFF／LINEログインで本人確認し、LINE identityとチャネル別配信先関係を登録・解除する。Dependencies: line-channel-foundation
- [x] line-webhook-ingress -- チャネル別Webhookをraw bodyから検証し、destination照合、空イベント疎通、イベント重複排除、安全な受付監査を提供する。Dependencies: line-channel-foundation
- [x] line-friendship-sync -- 検証済みfollow／unfollowを既存のチャネル別recipientへ時系列どおり反映し、未連携ユーザーを自動登録しない。Dependencies: line-webhook-ingress, line-account-linking
- [x] line-webhook-command-dispatch -- 検証済みmessage／postbackを許可リストから処理し、限定replyと後続actionの安全な拡張契約を提供する。Dependencies: line-webhook-ingress, line-channel-foundation, line-account-linking
- [x] linked-recipient-delivery -- 登録済みチャネルと配信先を選び、既存の確認・冪等性・監査を維持してpushし、明示的な受取確認を追跡する。Dependencies: line-channel-foundation, line-account-linking, line-friendship-sync, line-webhook-command-dispatch
- [x] line-channel-admin-ui -- 自分専用の認証済み画面からチャネルとwrite-only資格情報を登録・更新・無効化する。Dependencies: line-channel-foundation, line-account-linking, line-webhook-ingress, linked-recipient-delivery
- [x] line-rich-menu-foundation -- 組み込みテンプレートと決定的画像生成から、チャネル既定リッチメニューの冪等な適用・照合・解除・履歴・後片付けまでを提供する。Dependencies: line-channel-foundation, line-account-linking
- [x] line-rich-menu-admin-lifecycle -- foundationの契約をowner向け管理画面へ接続し、状態・履歴・回復操作とチャネル無効化・再有効化・物理削除を統合する。Dependencies: line-rich-menu-foundation, line-channel-admin-ui

## Phase 3: アプリ画面分割

### Overview

完了済みのowner認証、チャネル管理、アカウント管理、リッチメニュー管理、メッセージ配信を一画面から独立URLへ分割する。共通headerとトップ画面を追加し、現在routeの機能だけをmountして必要なAPIだけを読み込む。既存の業務・安全性契約とBackend APIは変更しない。

### Approach Decision

- **Chosen**: 共通基盤から段階移行する。Router／認証layout／共通headerを先に導入し、既存Componentをroute単位で移し、リッチメニューの選択／詳細URLと認証状態を統合した後、全画面をTailwind CSSへ移行する
- **Why**: 既存API client、DTO validation、reducer、競合・二重送信・結果不明の安全性契約を維持しながら、画面構造とUIを段階的に検証できる
- **Rejected alternatives**: 薄いroute wrapperでは`AuthGate`、リッチメニューlocal state、重複CSSが残り確定要件を満たしにくい。画面層の全面再構築は既存業務状態の回帰リスクとタスク数を増やす

### Scope

- **In**: `/liff`トップ、4機能route、リッチメニュー選択／詳細route、404、共通header、認証後の安全なroute復帰、logout・session失効・全連携解除の制御、route単位mount／API読込み、title／focus、responsive／accessibility、Tailwind CSSによる全画面統一、既存機能回帰test
- **Out**: Backend、API schema、Database、新しい業務機能、E2E／visual regression framework、Mobile-first、LINEアプリ内browserの正式保証

### Constraints

- React RouterのDeclarative modeと`BrowserRouter`を使用する
- Tailwind CSSと`@tailwindcss/vite`はVite 8対応の4.2.2以降を同じstable versionで固定する
- LIFF Endpoint URLとLINEログインredirect URIは`/liff`のまま維持する
- 既存の確認、冪等性、結果不明、回復操作、秘密情報非露出を維持する

### Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 30〜38件
- **Independent responsibility seams**: 5（Router／認証layout、共通UI shell、既存機能page分離、リッチメニューroute、design system／検証）
- **Rationale**: review attention帯だが、一つのFrontend利用者成果へ収束し、新しいBackend状態機械、migration、外部API workflow、独立rolloutを持たない。内部workstreamの依存順と統合test境界を明示して単一Specとする

### Specs (dependency order)

- [x] app-screen-separation -- 既存4機能を独立URLへ分割し、共通navigation、認証route制御、route単位読込み、Tailwind CSSの統一UIを提供する。Dependencies: line-account-linking, line-channel-admin-ui, line-rich-menu-admin-lifecycle, linked-recipient-delivery

---
_更新日: 2026-08-29。Phase 3としてFrontendのアプリ画面分割を追加。_

## Phase 4: 文章判定ラボ

### Overview

本人のiPhone・LINEアカウントで、チャットUI、JevのChoice・Score・Noul、文章判定による会話分岐を学ぶ開発用LINEミニアプリを追加する。「通知が届かない」「通知の設定方法を知りたい」の2種類を、入力・追加質問から固定案内・結果確認まで試し、発言ごとの判定結果を観察する。

正式Specの入力は[文章判定ラボ 要件整理](../../TEXT_JUDGMENT_LAB_REQUIREMENTS.md)とし、詳細な境界・引継ぎ事項は[text-judgment-labのbrief](../specs/text-judgment-lab/brief.md)に記録する。単一SpecとA案の選択をユーザー確認済み。正式な要件・設計・タスク・実装の承認は、それぞれのフェーズで行う。

### Approach Decision

- **Chosen**: Path Cの新規単一Spec。A案としてReactの純粋な状態遷移と画面内メモリで会話を進め、Djangoはラボ専用の本人確認・認可、入力検証、Jev判定・応答正規化を担当する。選択肢回答では通信しない。
- **Why**: 会話を復元しない個人検証に適し、既存React・LIFF SDK・Django・HTTPX・ngrokを再利用できる。独立したラボ入口と利用権限を追加し、既存管理権限との混同を避けられる。
- **Rejected alternatives**: B案のサーバー側会話進行はPythonへルールをまとめられるが、選択肢にも通信と失敗処理が必要になる。今回の範囲ではA案の方が実装量を抑えやすい。

### Scope

- **In**: 本人向け開発用入口と認証、2種類の相談、自由文・例文・選択肢、3種類のJev判定、未回答項目の抽出、曖昧な判定の確認、対象外・複数相談・訂正希望への対応、iPhone向け手順と公式ヘルプ、結果確認・終了・中断・やり直し、判定詳細、timeout・遅延結果の破棄、基本テストと少数の本人実機確認。
- **Out**: Android向け案内、他の利用者への提供、一般公開、生成AIによる回答文作成、画像・音声、LINEトーク通知、会話履歴の保存・一覧・分析・復元、部分訂正・巻き戻し、相談の自動切替、複数画面同期、端末設定・通知到達の自動検知、大量評価・精度目標。

### Constraints

- 管理用のowner sessionをラボ認証で発行せず、ラボの利用許可を管理APIへ流用しない。既存の管理認証・チャネル管理・配信の契約は維持する。
- 開発用ミニアプリのLIFF ID・検証対象チャネルを既存管理用設定から分け、サーバーで本人を検証する。具体的な認証保持・期限・復帰方式は設計で決める。
- JevのAPIキーはサーバーだけで保持し、入力には架空の相談を使う。会話と判定結果は画面のメモリに保持し、新規相談・再読み込みで持ち越さない。
- 判定中も中断・新規相談を可能にし、古い応答を反映しない。失敗では会話を進めず、通常入力から送り直す。自動再試行や部分結果での継続は追加しない。
- モデル・質問文・境界値・入力長・timeout・最小限の利用制限・数値説明は設計で決める。日本語の実判定、本人のConsole設定とiPhone動作は未検証で、実装時の確認範囲に含める。

### Boundary Strategy

- **単一Specとする理由**: 入口・認証、判定API、会話遷移、画面は一つのラボ体験へ収束する。独立した提供成果、履歴移行、複数の外部資源の補償処理を持たないため、内部責務と統合点を明示して単一のレビュー範囲にする。
- **共有境界**: ラボとownerの認可分離、検証対象チャネル、判定DTOと確定回答の優先関係、質問順、相談・要求の識別と後着結果の破棄、画面と認証の寿命。
- **既存Specとの接点**: `line-account-linking`と`app-screen-separation`は隣接する基盤として参照し、再オープンしない。独立routeの配線と既存認証・画面の回帰確認は新Spec内で扱う。

### Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 30〜38件。1件1〜3時間として、入口・認証5〜6、判定接続5〜7、会話遷移8〜10、画面6〜8、統合・回帰・実機確認手順6〜7を見込む。
- **Independent responsibility seams**: 5（入口・本人確認、判定境界、会話状態遷移、画面・案内・観察、統合・検証）。外部連携はLINE本人検証とJev判定の2系統。
- **Rationale**: review attention帯だが、独立rolloutや永続会話・補償フローを追加しない。契約定義、判定adapterと会話遷移、画面接続、統合確認の順に進める。Requirements・Design・Tasksで再判定し、40件以上またはレビューが構造的に収束しない場合は分割へ戻る。

### Specs (dependency order)

- [x] text-judgment-lab -- 本人向け開発用LINEミニアプリで、通知相談の会話、Jev判定、固定案内と判定詳細の観察を提供する。Dependencies: line-account-linking, app-screen-separation

---
_更新日: 2026-09-20。既存の完了状態を保持し、Phase 4として文章判定ラボのDiscovery結果を追加。_

# Brief: line-rich-menu-detail-ui

## Problem

認証済みownerがリッチメニュー詳細画面を開いても、現在適用されている画像を確認できない。状態一覧と操作履歴が画面の多くを占め、現在の内容の確認と変更に必要な操作を見つけにくい。

## Current State

- 対象は `/liff/rich-menus/{channelId}`。共通ナビゲーション、チャネル選択、owner認証、適用・解除・回復、およびチャネルの停止・削除は実装済み。
- 詳細画面の初期取得はチャネル、テンプレート、管理状態、履歴一覧、無効化状態をまとめて扱っている。履歴取得の失敗も画面全体の読み込みに影響する。
- LINE画像取得のgatewayは寸法・形式・digest等の観測結果を返すが、表示用の画像本体を返す公開契約はない。
- 管理状態は現在資源を参照し、管理資源は作成元操作を参照する。操作には設定スナップショットがある。この対応を利用できるか設計で検証し、履歴の先頭を現在の設定と推測しない。
- 現在のeditorにはテンプレートが見つからない場合に先頭テンプレートへ進む経路がある。適用済み設定から編集する際は、古い版を別の版へ自動置換しない扱いが必要。
- 入力資料は [RICH_MENU_UI_PLAN.md](../../../docs/requirements/RICH_MENU_UI_PLAN.md) と、下記Figmaの改修案。計画書6.1にある既存spec更新方針は、2026-10-18のユーザー選択により本briefの新規単一spec方式へ変更する。

## Desired Outcome

ownerが対象チャネルと現在のメニューを画像中心に確認し、必要なときだけ編集を開始できる。現在の画像と未適用のプレビューを取り違えず、確認してLINEへ反映し、キャンセルすれば現在の表示へ戻れる。異常時は操作できない理由と次の操作が分かり、既存の回復機能を維持する。

## Approach

2026-10-18にユーザーが選択した、新規単一specによる変更分の管理を採用する。feature名は `line-rich-menu-detail-ui`。DiscoveryのPath Cとし、既存specの本文、承認状態、完了タスクを変更しない。今回の変更契約は本specで所有し、旧契約との優先関係をRequirementsで明示する。

| 方針 | 利点 | 欠点・判断 |
| --- | --- | --- |
| 新規単一spec（選択） | 閲覧APIから詳細画面までを一つの利用者成果としてレビューでき、既存の承認履歴を保てる | 旧specとの重複・変更箇所を本specで明記する必要がある |
| 既存specの更新 | 従来の責任別仕様へ直接反映できる | 複数の完了済みspecを再度更新する管理が複雑になるため不採用 |
| BackendとFrontendを新規2specへ分割 | 境界別にレビューできる | 今回は独立した提供成果や新しい補償フローがなく、承認・契約管理を増やすため不採用 |

既存のReact、Tailwind CSS、Django、LINE gateway、認証・revision検証を再利用する。新しいUIライブラリ、画像保存基盤、適用・解除の状態機械は追加しない。閲覧APIの契約を先に固め、FrontendのDTO・通信・状態管理・表示へ接続し、回帰と画面確認で完了を検証する。

### 方針選択後の独立調査

2026-10-18に新しい調査workerが、コードと公式資料から互換性・依存・制約を確認した。現時点でこの方針を妨げる致命的制約は見つかっていない。

- `current_resource` → `origin_operation` → `configuration_snapshot` は初期migrationから存在する。新たな関連付けのためのmigrationは不要な可能性が高いが、欠落・不正snapshotを含む既存データの互換性はDesignとテストで確定する。
- [LINE公式APIリファレンス](https://developers.line.biz/en/reference/messaging-api/#get-rich-menu-image)に画像取得APIがあり、既存gatewayが使用するSDKの取得経路を再利用できる。現在の観測値のみの戻り値と表示用画像の型は分けて設計する。
- 既存stateの操作情報で回復を継続でき、`historySummary`はチャネル削除の確認にも使う。履歴一覧の取得・表示だけを除去する。
- 新しい依存・フォントは導入しない。同梱Noto Sans JPは版・digestとOFL 1.1を既存起動処理で検証する。Tailwindのlock記録はMIT、[Pillow公式](https://pillow.readthedocs.io/en/stable/about.html)ではMIT-CMUと継続リリースを確認した。全依存の固定版に対するライセンス全文・保守状況・脆弱性監査は今回の調査対象を網羅していないため、全面監査済みとは扱わない。

### Figma参照と画面方針

- [正常時・ダーク・1440px](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9117)、[390px](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9189)。チャネル名、短い状態、現在の画像、編集・適用解除・更新、停止・削除の折りたたみを配置する。
- [未設定](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9261)では「メニューを作成」から開始する。
- [編集中](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9349)では現在のメニューを折りたたみ、全項目を初期入力したフォームを表示する。
- [変更内容の確認中](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9565)では未適用であること、対象チャネル、全項目の表示名・完全URL、期限、置換影響を示す。
- [画像取得失敗](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9787)と[操作結果不明](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9887)を区別する。前者の文言は適用中と確認できた場合に限り、後者では古い画像を現在の画像として表示しない。
- [正常時・ライト](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9971)も参照する。狭い画面では操作を縦に配置する。
- Figmaのメニュー内イラスト・ラベルはデモ内容。実装ではAPIから取得した画像を表示し、画像生成テンプレートへデモの絵柄やアイコンを追加しない。共通ヘッダー・テーマは既存アプリを利用し、全画面の再設計は行わない。
- Figmaに専用フレームがない読み込み中、外部メニュー、状態不一致、期限切れ、処理中、後片付け待ち、停止中も計画の対象とする。

## Scope

- In: 管理対象の現在画像と保存済み設定の閲覧、実状態との対応確認、owner・provider・revisionの検証、画像取得失敗の分離、現在表示と編集・確認の切り替え、編集の初期入力とキャンセル、履歴表示と初期一覧取得の撤去、条件に応じた回復案内、操作後の再取得、後着応答と画像URLの破棄、ライト・ダーク・狭幅・キーボード対応、関連テストと統合検証。
- Out: アプリ外メニューの内容取得・編集・削除、利用者別メニュー、画像アップロード、画像生成デザインの変更、新規テンプレート、自由レイアウト、画像履歴保存、下書き永続化、過去設定へのロールバック、別の履歴画面、共通ヘッダーや認証基盤の再設計。

## Boundary Candidates

1. Backendの閲覧境界。現在資源と作成時設定の対応、LINE画像取得、認可・競合確認、安全な応答分類を所有する。
2. Frontendの詳細画面。DTO検証、通信、純粋な状態遷移、閲覧・編集・確認表示と画像の寿命をそれぞれ既存の責務に沿って所有する。
3. 境界間の統合検証。既存データでの表示、操作後の更新、認証失効・競合・回復、および実画面を確認する。

独立して提供する成果は「現在のメニューを確認・変更できる詳細画面」の一つ。外部連携の追加は読み取り専用の画像取得経路であり、新しい外部変更・補償フローは設けない。

## Out of Boundary

既存のテンプレート検証・画像生成、10分間の確認トークン、LINE資源の所有権、適用・解除・管理終了・再確認・後片付けの状態機械、履歴記録と履歴API、チャネル無効化・再有効化・物理削除の業務契約は上流仕様が所有する。本specはそれらの判定を画面側へ複製しない。

## Upstream / Downstream

- Upstream: `line-rich-menu-foundation` の管理資源・設定・画像取得・操作契約、`line-rich-menu-admin-lifecycle` の管理UIとチャネルライフサイクル、`app-screen-separation` のroute・認証・画面寿命、既存owner sessionとチャネル資格情報管理。
- Downstream: チャネル別リッチメニュー詳細画面を使うowner。閲覧APIはこの画面へ接続し、他機能への展開や新しい利用者向け画面は追加しない。

## Existing Spec Touchpoints

- Extends: `line-rich-menu-foundation`。現在の管理対象と対応設定の閲覧を追加する。旧Requirement 10.8の「画像binaryをプレビュー以外へ返さない」制約について、認可済みの現在画像閲覧応答だけを本specで例外として定義する。秘密情報・ログ・履歴への非露出と画像履歴を保存しない契約は維持する。
- Extends: `line-rich-menu-admin-lifecycle`。Requirement 1・4の常設状態一覧、Requirement 8・9の履歴画面表示、および操作後の履歴表示更新を本specの画像中心・履歴非表示の契約で置き換える。Backendの履歴記録・参照APIは撤去しない。編集・確認・回復の既存の安全条件を維持する。
- Adjacent: `app-screen-separation`。Requirement 6.3の無効チャネルの履歴表示部分を本specの保存済み情報の読取専用表示で置き換える。URL、戻る導線、共通ナビゲーション、画面単位の取得、離脱時の確認なし破棄、受付済み操作の追跡を維持する。旧admin-lifecycleの離脱確認を復活させない。
- Adjacent: `line-channel-admin-ui`、`line-account-linking`。認可、provider、チャネルrevision、停止・削除確認、操作禁止条件を維持する。
- 正式なRequirementsでは変更する旧要件と維持する契約を対応付ける。旧spec全体を無効化せず、今回明示する差分に限り本specの承認済み要件を優先する。旧specの本文・spec.json・完了チェックは書き換えない。
- `CONTEXT.md`と共通steeringに残る履歴画面の説明や現在画像とプレビューの区別は、要件承認後に必要な範囲で同期する。用語変更には `domain-modeling` を使い、過去のADRの所有権・履歴削除・無効化・置換関係の決定を維持する。

## Spec Size Assessment

- Verdict: PASS (single-spec)
- Projected executable tasks: 8〜11件、各1〜3時間。変更差分に対する見積もりであり、完了済み上流specの全タスクを再計上しない。
- Independent responsibility seams: 3（Backend閲覧、Frontend表示・状態管理、統合検証）。独立提供成果は1件。
- External workflows: 新規の読み取り専用閲覧1系統。適用・解除・回復・チャネル状態変更の既存フローは再利用し、新しい状態機械・補償フローを追加しない。
- Projected work units: 現在資源と設定の参照・対応検証、画像gatewayと外部応答の検証、認可・競合を含む閲覧service/API、Frontend DTO・通信、閲覧と画像状態、編集・確認・キャンセル、履歴撤去と回復・操作後更新の回帰、狭幅・テーマ・アクセシビリティと統合確認の8単位を基本とする。競合テスト、既存データ互換性・migration、画面検証が各単位内の1〜3時間に収まらなければ別単位にし、最大11件を見込む。
- Internal workstreams and dependency order: Backendの対応関係と閲覧契約 → Frontend DTO・通信・状態 → 画面と既存操作の接続 → 統合・回帰・実画面確認。
- Rationale: 40件未満で、単一の利用者成果へ収束する垂直スライス。上流の資源管理基盤は完了済みであり、別々のrolloutや新しい補償フローを同居させない。境界間契約と検証点を明示すれば単一レビュー範囲で扱える。Requirements・Design・Tasksで再評価し、タスクを圧縮して判定を維持しない。

## Constraints

- 現在画像を表示できるのは、LINE上の現在既定とこのアプリの管理対象との対応を確認できた場合だけとする。外部メニュー・結果不明・状態不一致・停止中に古い画像を代用しない。
- 画像取得の外部通信中はDB lockを保持せず、戻り時にowner、provider、チャネルrevision、対象資源と操作状態を再検証する。認可失効を画像領域だけのエラーへ縮小しない。
- 現在の資源と設定の対応は保存済みの関係から検証する。不足時は安全に案内し、履歴の順番や似た値から補完しない。既存の適用済みデータを再適用せず表示できることを検証する。
- 画像と全設定の応答形式、エラー分類、query budget、およびDB migrationの要否はDesignで確定する。新しい画像保存基盤は導入しない。
- 現在画像に確認用プレビューの10分期限を付けず、閲覧で確認トークンの発行やLINEの変更を行わない。適用前の確認期限は従来どおり維持する。
- 古いテンプレート版や不完全な対応設定で編集を開始できない場合は、その理由と作り直しが必要なことを案内する。別の版へ無断で置換しない。
- 未設定と画像取得失敗を区別する。画像取得失敗だけで画面全体を失敗にせず、操作可否はBackendが返す許可状態に従う。
- 適用・解除後の表示は再取得結果から決め、解除後が必ず未設定になるとは仮定しない。結果不明では同じ操作を確認し、外部作用を自動再送しない。
- 履歴一覧は詳細画面から取得・表示しない。一方、回復対象、削除確認用の履歴有無、進行中操作など必要な状態情報は残す。回復に必要な識別子を内部で保持することと、通常画面に表示することを分ける。
- 画像の縦横比と全体表示を保ち、コンパクト・ラージ両方に対応する。長いチャネル名・日本語・URL、代替テキスト、フォーカス、色だけに依存しない状態表示を検証する。
- チャネル変更、画面離脱、認証失効時に画像URLと表示情報・未適用入力を破棄し、後着応答を採用しない。Backend受付済みの操作を離脱によって中断・自動再送しない。
- 本briefはDiscoveryの合意範囲であり、Requirements・Design・Tasks・Implementationの承認を兼ねない。次の入口は `$kiro-spec-init line-rich-menu-detail-ui` とする。

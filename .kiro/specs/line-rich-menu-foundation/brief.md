# Brief: line-rich-menu-foundation

## Problem

ownerは登録済みMessaging APIチャネルを管理できるが、LINEトークへ表示するチャネル既定リッチメニューをこのアプリから作成・確認・適用できない。手作業では、画像要件、複数段階のLINE API操作、外部変更、タイムアウト後の結果不明、不要資源の後片付けを安全かつ再現可能に扱えない。

## Current State

`line-channel-foundation`は暗号化資格情報とrevision-awareなチャネル取得を提供し、`linked-recipient-delivery`は10分間の確認、操作ID、外部通信後の再検証、結果不明の基本パターンを持つ。一方、リッチメニューテンプレート、画像生成依存・日本語フォント、管理対象資源・操作・履歴モデル、rich menu gateway、実状態照合は存在しない。

## Desired Outcome

Backendが、ownerと対象チャネルに結び付いた確認済みプレビューから、一意なチャネル既定リッチメニュー操作を開始できる。同じ操作は複数資源を作らず保存済み結果へ収束し、適用・置換・解除・管理終了・結果不明の再確認・後片付けを、アプリ外資源へ触れずに追跡できる。

## Approach

新しいBackendドメイン境界に、版付き組み込みテンプレート、決定的画像renderer、10分間のプレビュー、LINE rich menu gateway、管理対象資源・操作・履歴の状態機械をまとめる。チャネル資格情報・owner authorization・revisionは既存の公開境界から取得し、外部通信をDB transaction外で行った後に状態を再検証する。下流UIとチャネル無効化からも利用できるheadless operation／reference契約を先に公開する。

## Scope

- **In**: 3種類の組み込みテンプレート、安定したIDと版、表示名・HTTPSリンク検証、同梱日本語フォントによる決定的PNGまたはJPEG生成、LINE制約と1MB上限検証、10分間のプレビュー、確認digestとrevision binding、rich menu作成・画像upload・既定設定・既定ID確認・既定解除・削除、適用・置換・解除・管理終了、外部変更分類、操作IDによる冪等化、結果不明の照合、候補・旧資源の明示的cleanup、適用履歴、下流向け状態照会・操作・reference／cleanup契約、Backend API
- **Out**: owner向け管理画面、チャネル無効化・再有効化・物理削除のorchestration、利用者別リッチメニュー、URI以外のaction、ownerによる画像アップロード、自由な座標・配色・フォント編集、カスタムテンプレート、統計、日時予約、ロールバック、生成画像ファイルの履歴保存、アプリ外資源の編集または削除

## Boundary Candidates

- 版付きテンプレートカタログ、項目validation、決定的な文字配置と画像encoding
- owner・channel・template版・現在既定・入力digestを結ぶ10分間のプレビュー
- LINE rich menu JSON APIと`api-data.line.me`の画像uploadを安全な結果型へ縮約するgateway
- 管理対象資源、操作ID、履歴、チャネル単位排他、結果不明照合、候補・旧資源cleanupの状態機械
- 下流のチャネル無効化と削除が利用するheadless operation、reference probe、history cleanup契約

## Out of Boundary

- Frontendの編集・プレビュー・履歴・回復状態と画面遷移
- チャネルの`active`状態を変更する最終判断と無効化要確認のowner体験
- Messaging APIで管理できないOfficial Account Manager資源の内容取得
- 保存済みIDまたは強い所有権証明がないLINE資源の削除
- 将来用のテンプレート供給元やaction種別の先行実装

## Upstream / Downstream

- **Upstream**: `line-channel-foundation`の暗号化資格情報・チャネルrevision・公開ID、`line-account-linking`のowner sessionと同一provider、既存のexact-origin CSRF、`linked-recipient-delivery`で確立した確認・冪等性・結果分類の設計パターン
- **Downstream**: `line-rich-menu-admin-lifecycle`の管理画面、回復操作、チャネル無効化・再有効化・物理削除統合。将来の追加テンプレート供給元、利用者別リッチメニュー、別action種別

## Existing Spec Touchpoints

- **Extends**: なし。完了済みSpecを再オープンせず、新規リッチメニュー機能が既存の公開境界を利用する
- **Adjacent**: `line-channel-foundation`の資格情報・revision・reference directory、`line-account-linking`のowner/provider境界、`linked-recipient-delivery`のconfirmation／operationパターン、`line-channel-admin-ui`の下流統合。既存Modelや配信固有状態を直接所有しない

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 28〜38件（依存・font asset、migration、rendererとgolden test、preview、gateway、状態機械、履歴、照合・cleanup、API、競合・統合・セキュリティテストを含む）
- **Independent responsibility seams**: 5（テンプレート／画像、プレビュー、LINE gateway、資源操作／履歴／照合、下流連携契約）
- **Rationale**: 30〜39件のreview attention帯へ入る可能性はあるが、全workstreamは「確認済み設定を追跡可能なチャネル既定資源へ収束させるBackend能力」という一つの成果とrolloutへ収束する。内部owner、依存順、契約、integration testをDesignで明示し、Tasksで40件以上またはbounded review不収束となった場合はDiscoveryへ戻す

## Constraints

- LINE rich-menu mutationにはretry keyがない。createその他の結果不明時に新規操作を自動再試行せず、operation固有の認証可能なownership marker、保存済みID、list／get／defaultの観測から安全に照合する
- LINEのlistは即時整合性を保証せず、観測できないことだけで失敗や不存在と断定しない。収束条件、保留期間、再操作禁止条件をDesignで確定する
- Get defaultの403、404、管理対象ID不一致を、取得できない外部資源の内容を推測せず安全な実状態分類へ変換する
- 画像uploadは`api-data.line.me`、明示的Content-Type、JPEGまたはPNG、幅800〜2500px、高さ250px以上、aspect比1.45以上、最大1MBを満たす。画像はupload後に差し替えられない
- PillowはPython 3.14対応版を候補とする。同梱日本語フォントの版、ファイル、weight、SHA-256、glyph範囲、OFL-1.1のcopyright／license同梱をDesignで固定する
- 429、タイムアウト、候補削除失敗、旧資源削除失敗を自動再試行せず、ownerの明示操作と安全な状態照合を要求する
- 外部通信中に長時間のDB lockを保持せず、戻り時にowner・provider・channel revisionと操作状態を再検証する
- 通常ログと確認トークンへ資格情報、LINE生応答、生成画像binary、LINE user ID、完全URLを直接含めない。owner専用の適用履歴には要件どおり完全URLを保存・表示し、秘密値を含めないよう事前に注意を示す

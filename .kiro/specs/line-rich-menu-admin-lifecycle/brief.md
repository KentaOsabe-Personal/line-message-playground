# Brief: line-rich-menu-admin-lifecycle

## Problem

Backendに安全なリッチメニュー資源管理能力があっても、ownerが対象チャネル、テンプレート、全項目、LINE実状態、適用結果、履歴を一画面で確認し、失敗や外部変更から安全に回復する管理体験がなければ利用できない。また、適用中・結果不明・後片付け中のリッチメニューを無視して既存のチャネル無効化や物理削除を進めると、LINE上の表示とアプリの保存状態が矛盾する。

## Current State

`line-channel-admin-ui`は各チャネルカード、登録・更新・有効化・無効化・削除・接続確認を提供するが、チャネル状態は基本的にactive／inactiveの同期変更である。リッチメニュー専用画面、dirty input、生成画像preview、実状態照合、操作追跡、履歴、無効化要確認を表すFrontend／Backend state machineは存在しない。

## Desired Outcome

ownerが登録済みチャネルから専用画面を開き、テンプレートと全項目を入力し、生成画像・リンク・外部設定警告を確認して適用できる。適用・置換・解除・管理終了・再確認・後片付けの状態と履歴を安全に扱い、チャネル無効化はLINE上の解除確認後だけ完了し、再有効化と物理削除もfoundationの状態契約と一貫する。

## Approach

`line-rich-menu-foundation`が公開するpreview、operation、reconciliation、reference／cleanup契約を唯一のBackend機能境界として利用する。Frontendは管理Component、`*Api.ts`、`*Dto.ts`、`*State.ts`へ分離し、未適用入力をメモリだけに保持する。既存チャネル管理のstate changeとreference directoryはcomposition rootでfoundation契約へ接続するが、その変更理由と統合テストはこの新規Specが所有する。

## Rollout Handoff

foundation単独導入中はmutationを有効化せず、次の設定を維持する。

```dotenv
LINE_RICH_MENU_MUTATION_MODE=read_only
LINE_RICH_MENU_REFERENCE_PROBE_INTEGRATED=false
LINE_RICH_MENU_HISTORY_PURGE_INTEGRATED=false
LINE_RICH_MENU_INTEGRATION_MARKER=
```

本Specがrich menu reference probeを`ChannelReferenceDirectory`へ登録し、rollback-only history purgeをチャネル削除transactionへ組み込み、両方の統合テストを通した同一releaseでのみ、次の4変数を同時に切り替える。markerの固定値は`line-rich-menu-admin-lifecycle-v1`とし、一部だけを先行変更しない。

```dotenv
LINE_RICH_MENU_MUTATION_MODE=enabled
LINE_RICH_MENU_REFERENCE_PROBE_INTEGRATED=true
LINE_RICH_MENU_HISTORY_PURGE_INTEGRATED=true
LINE_RICH_MENU_INTEGRATION_MARKER=line-rich-menu-admin-lifecycle-v1
```

既存の管理資源または未解決operationがある状態でapplyだけを停止するrollback／forward-fix時は、probe・purge・markerを維持したまま`LINE_RICH_MENU_MUTATION_MODE=recovery_only`へ変更する。管理状態が存在しないfoundation単独状態だけが`read_only`へ戻れる。Requirements、Design、Tasksの各フェーズでは、この切替条件と同時変更を実行可能タスクおよび統合テストへ引き継ぐ。

## Scope

- **In**: チャネルカードからの導線、チャネル専用管理画面、テンプレート選択と全項目入力、テンプレート変更時の消去確認、dirty navigation確認、セッション失効時のメモリ消去、スマートフォン相当preview、リンクの手動テスト、preview期限表示、適用・置換・解除・管理終了の確認、LINE実状態と外部変更警告、操作status、結果不明の再確認、旧・候補資源cleanupの明示操作、履歴ページング、無効チャネルのread-only表示、チャネル無効化の解除・照合・無効化要確認、再有効化時の非復元、適用中資源による物理削除拒否、history-only時の同時cleanup
- **Out**: テンプレート・画像renderer・preview token・LINE gateway・リッチメニュー操作状態機械の内部実装、既存チャネル資格情報管理の再設計、利用者別リッチメニュー、URI以外のaction、ownerによる画像アップロード、自由レイアウト、統計、予約、ロールバック、端末表示確認結果の保存

## Boundary Candidates

- 編集入力、テンプレート変更、preview期限、dirty navigation、session失効を扱うFrontend state machine
- foundation APIのstrict DTO、safe error、status pollingではないowner明示の再確認・cleanup手順
- LINE実状態、アプリ外設定、適用・置換・解除・管理終了・履歴を表示する管理Component
- foundationのheadless unlink／reconciliationを使うチャネル無効化・無効化要確認・再有効化orchestration
- 適用中referenceは削除を止め、history-onlyはチャネルと一緒に消す物理削除統合

## Out of Boundary

- LINE rich menu resourceの所有権判定、外部失敗分類、冪等性、照合アルゴリズムの再実装
- `linechannels`へリッチメニューModelやLINE gatewayを直接追加すること
- アプリ外リッチメニューの内容表示、編集、削除
- 利用者別リッチメニューを理由とするチャネル既定操作の変更
- ブラウザ永続ストレージへの下書き・URL・preview画像保存

## Upstream / Downstream

- **Upstream**: `line-rich-menu-foundation`のpreview、operation、実状態、履歴、reconciliation、headless unlink、reference／history cleanup契約。`line-channel-admin-ui`のowner console、channel revision、exact-origin CSRF、管理API／DTO／state分離
- **Downstream**: 実機でのチャネル既定リッチメニュー検証、将来のテンプレート追加、利用者別リッチメニュー、rich menu action拡張。これらは初期版の管理画面契約を暗黙に拡張しない

## Existing Spec Touchpoints

- **Extends**: なし。リッチメニューという新機能が既存チャネル管理画面と状態変更境界へ統合する責任を持つ
- **Adjacent**: `line-channel-admin-ui`のカード・管理state・無効化／削除、`line-channel-foundation`のchannel revisionとreference directory、`line-account-linking`のowner session。既存の資格情報write-only契約、配信、Webhook、recipient状態を変更しない

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 22〜30件（Backend orchestration／API、Frontend DTO・API・state・Component、無効化要確認、削除cleanup、競合・統合・セキュリティテストを含む）
- **Independent responsibility seams**: 4（編集・preview UI、管理操作と回復UI、チャネル無効化／再有効化、物理削除統合）
- **Rationale**: 各seamはfoundationの同じチャネル状態契約を使い、「ownerがリッチメニューとチャネルのライフサイクルを矛盾なく管理できる」という一つの利用者成果へ収束する。foundation内部を再実装せず、Frontendとorchestrationの責務を明示すれば29件前後の単一review scopeとして維持できる

## Constraints

- FrontendからLINE APIを直接呼ばず、foundationの安全なAPI表現だけを使用する
- 未適用の表示名、完全URL、preview画像をlocalStorage、sessionStorage、IndexedDB、URL、操作履歴へ保存しない。owner session失効時はメモリから消去する
- preview確認前、preview期限切れ、channel revision・現在既定・template版の変更後はLINE状態を変更しない
- アプリ外リッチメニューの存在は警告できる範囲で表示し、取得できない内容を推測または管理対象へ自動取り込みしない
- `unknown`、`cleanup_required`、無効化要確認の間は競合する適用・解除・チャネル状態変更を止め、自動再試行しない
- 管理対象リッチメニュー適用中のチャネル無効化は、同じowner操作内でfoundationの解除を開始し、LINE実状態を確認できた場合だけinactiveへ確定する
- 現在のactive／inactiveだけでは表せない無効化要確認を永続化し、再認証後に同じ無効化操作へ収束させる状態所有とrevision契約をDesignで確定する
- チャネル再有効化で以前のリッチメニューを自動復元しない。適用中資源は物理削除を妨げるが、履歴だけなら削除を妨げずチャネルと同時に削除する
- FrontendとBackendの各テスト定義直前に、日本語の`テストケース:`と`期待値:`コメントを置く

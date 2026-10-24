# Research & Design Decisions: line-rich-menu-detail-ui

## Summary

- **Feature**: `line-rich-menu-detail-ui`
- **調査日**: 2026-10-18（Asia/Tokyo）
- **Discovery Scope**: Extension。light discoveryを起点に、画像を返す認可境界・外部通信前後の競合・既存データ互換性を追加深掘りした。新規外部サービス、新規mutation状態機械、新規依存はない。
- **Key Findings**: 現在資源から作成元設定を直接たどれる。既存画像downloadはbytesを捨てて観測値だけを返す。現行画面はhistoryと編集フォームへ常時依存し、保存済み設定の初期入力を持たない。
- Requirementsは `requirements-approved`、`approvals.requirements.approved: true`。design/researchの既存成果物はなく、新規作成。既存の未追跡brief・requirements・計画書は保持する。

## Research Log

### 既存の永続化と対応設定

- **Sources**: `backend/linerichmenus/models.py`、`migrations/0001_initial.py`、`repository.py:get_state/_configuration_from_snapshot`、`services.py:_configuration_snapshot`、`tests/test_repository_queries.py`。
- **Findings**: channel stateのcurrent resource、resourceのorigin operation、operationのconfiguration snapshotが既存の関係として存在する。snapshotはtemplateId/templateVersionと順序付きfields。履歴fixtureには補助キーversion/channelLabelがあるが現在の生成処理では必須ではない。
- **Implications**: schema追加・backfill不要。現在の編集値をhistory.items[0]から選ばない。参照関係、owner/provider/channel、apply種別、確定状態、入力構造を確認する。異常データはconfiguration unavailableへ分類し、古い正常snapshotは再適用なしで使う。
- **Limit**: 実DBの全件品質監査は行っていない。互換性は既存形式を再現したfixtureと不正関係のfixtureで実装時に確認する。

### 実状態と競合境界

- **Sources**: `services.py:get_state/_verify_scope_unchanged`、`reconciliation.py:observe_channel`、`lineaccounts/admin_authorization.py`、`linechannels/admin_repositories.py`、`admin_lifecycle_repositories.py`。
- **Findings**: 既存get_stateは外部照会後にowner/channelを確認するが、その手続きだけでは画像取得中のcurrent resource・operation・deactivation変化を一括で検出できない。owner fenceはtransaction内呼出を要求する。停止中はactiveを要求するchannel snapshotを使えない。
- **Implications**: 別のcurrent read serviceで、短い開始transactionと最終transactionを所有する。公開channel metadata、revision lock、deactivation read portをcontainerから注入する。外部I/O中にlockを保持しない。停止中は保存済み情報の同一transaction読取だけとする。
- **Capabilities**: repositoryはactive/blocking/cleanupによる制限、reconcilerは既定関係による候補、readinessはmode制限を持つ。現行get_stateのようにlocal gateをreconciliationの通常操作で上書きしない。新しい閲覧projectionでは既存の禁止条件を優先する。
- **Recovery**: cleanupResourcesの列挙だけではreplacement関係が表現されない。currentにrelation検証済みrecoveryTargetsを加え、実行側の既存判定と同じpredicateを共用する。無関係な旧資源・関係不明のlegacyデータをUIから選べるようにしない。

### 再レビュー: 管理終了の契約と操作候補

- **Sources**: `line-rich-menu-foundation` requirements 7.7〜7.8、`line-rich-menu-admin-lifecycle` requirements 6.2・6.5、`services.py:_start_release/DefaultMutationReadiness.project`、`repository.py:accept/finalize_release`、`reconciliation.py:observe_channel`、`tests/test_services.py:test_release_does_not_call_line_and_marks_resource_released`。
- **Finding 1**: 既存releaseはLINEを呼ばず、管理対象をreleasedにしてcurrent参照を外す。削除を伴うという旧design・CONTEXTの記述が誤りだった。今回のrequirementsはこの業務契約を上書きしていない。確認文言と用語を既存契約へ揃え、管理終了後のcleanupも追加しない。
- **Finding 2**: 現行reconcilerはmanaged_defaultの場合だけreleaseを候補へ含める。一方、既存releaseの受付は現在既定との一致を要求せず、同じチャネルのappliedなcurrent対象、認可、revision、操作・停止intentの競合を検証する。外部変更時にreconcilerの候補を直接使うと、実行可能な管理終了が表示から欠落する。
- **Decision**: CurrentReadRepositoryは保存済み対象の所有関係と受付条件に対応する適格性をsnapshotへ投影する。CurrentMenuServiceはlocal/deactivation制限を優先し、既知の外部変更時に限り適格なcurrentへのreleaseを候補へ補い、最後に既存readinessでmodeを適用する。通常適用中のreleaseは補助の折りたたみに残す。unknownや対象不明、結果不明をreleaseで回避しない。
- **Scope**: current専用の表示用projectionとUI接続がB1/B2の責務。旧state endpoint、reconciler、mutationの業務判定は維持し、新しい状態機械や外部作用を導入しない。FrontendはeffectiveActionsに従い、外部IDやcleanup候補から対象を選ばない。
- **Validation**: T9で観測×対象×制約×modeの候補と、current→確認→既存release→currentを検証する。画像・設定だけの失敗は所有関係が有効なreleaseを妨げず、表示後の競合は既存mutationが拒否する。LINE変更・削除0、履歴保持、released資源の再取込・削除禁止を回帰対象とする。
- **Documentation**: CONTEXTの管理終了を既存specと実装に同期する。新しい不可逆な業務判断ではなく誤記の修正であるため、ADRは追加しない。

### LINE画像取得

- **Sources**: [LINE画像download](https://developers.line.biz/en/reference/messaging-api/#download-rich-menu-image)、[既定ID取得](https://developers.line.biz/en/reference/messaging-api/#get-default-rich-menu-id)、[画像制約](https://developers.line.biz/en/reference/messaging-api/#upload-rich-menu-image)、`backend/linerichmenus/gateway.py`。
- **Findings**: 公式APIは画像binaryを返すGETを提供し、画像用hostはapi-data.line.me。404は資源不存在または画像未設定であり、現在既定がないこととは同義ではない。既定ID取得の403/404は既存gatewayのexternal/none分類を維持する。
- **Constraints**: PNG/JPEG、幅800〜2500、高さ250以上、幅/高さ1.45以上、1 MB上限。既存コードは1 MiBを上限としている。画像downloadは2,000 requests/secの枠だが、アプリ側は一回の閲覧に最大1回とし自動polling・retryをしない。
- **Implications**: 既存downloadの観測型を壊さずdownload_contentを追加する。元bytesを検証し、rendererで再生成しない。画像の前後でdefaultを確認し、最終DB fenceを通す。プレビュー確認値・10分期限は発行しない。
- **Gateway上の注意**: 現行 `_read_blob` はread全量、`_decode_image` はload後に寸法判定する。表示用download_contentはHTTPXで上限読込し、共通decodeの展開前寸法確認を整え、既存downloadの結果分類を回帰確認する。SDK例外やbytesを通常ログへ出さない。

### 現行Frontendと画面寿命

- **Sources**: `RichMenuAdminPage.tsx`、`RichMenuAdminConsole.tsx`、`richMenuAdminState.ts`、`richMenuAdminApi.ts`、`richMenuAdminDto.ts`、`RichMenuEditor.tsx`、`RichMenuPreview.tsx`、`RichMenuRecoveryPanel.tsx`。
- **Findings**: pageとConsoleがチャネルを重複取得し、Consoleはhistory取得を含むPromise.allに依存する。stateにhistoryが必須。editorは一致版がない場合templates[0]へfallbackする。プレビュー画像のObject URLはあるが、期限・対象・templateの確認情報は折りたたみに入っている。
- **Implications**: 初期channelをpageから渡しcurrentだけを取得する。catalogは明示編集時に取得し、履歴とcatalog障害を閲覧の依存から外す。hookへ通信・寿命を抽出し、reducerは純粋に保つ。draftと直前operation結果をread失敗から独立させる。
- **Failure paths**: 成功応答だけでなく失敗応答、画像decode、preview取消、認可失効の後着も世代照合が必要。read signalはmutationへ渡さない。操作IDは内部追跡に残し、通常表示から外す。

### Figmaとアプリ内ブラウザ

- **直接確認したFigma**: [1440px閲覧](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9117)、[390px閲覧](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9189)、[編集](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9349)、[確認](https://www.figma.com/design/pftoVz4YXLTNZAXkvoqhOf?node-id=9-9565)。読み取り専用screenshotを使用し、Figmaは変更していない。
- **現行アプリで直接確認**: チャネル一覧からリッチメニュー詳細を開き、画像なし、状態一覧、履歴件数、常設editor、停止・削除折りたたみ、更新導線を確認した。表示されたデータの変更操作は実行していない。
- **採用**: チャネル名・短い状態・画像・編集/解除/更新・停止/削除の順、編集中の現在画像details、確認中の別画像、狭幅で縦並びの操作。
- **適用範囲**: shell/themeは既存を使う。Figmaの画像内イラストはデモ。現行catalogはjp-link-one/two/three版1、すべて2500×843であり、Figmaのラージ6項目は既存runtimeにない。画像表示は任意の検証済み縦横比を扱い、ラージは表示fixtureで検証する。新規catalogは追加しない。
- **未実施**: 本ターンでは全異常状態・ライトのFigma frameを直接取り直していない。残りの参照先はbriefにあり、light/darkと異常状態は実装後のT8で確認する。現在の画面は実装結果の受入確認ではなく調査資料として扱う。

### 依存とruntime

- **Sources**: frontend/package.json、backend/requirements.txt、core steering、frontend/backend AGENTS、line-integration steering。
- **Findings**: React 19.2.7、TS 6.0.3、Router 8.3.1、Tailwind 4.3.3、Django 6.0.7、DRF 3.17.1、LINE SDK 3.25.0、HTTPX 0.28.1、Pillow 12.3.0を既存の固定版として使える。
- **採用／自作**: Object URL、AbortController、details/summary、既存themeを採用し、新しいUI・画像ライブラリを加えない。現在資源の関係と認可fenceはアプリ固有であり、既存repository/port上に小さなread serviceを作る。
- **確認範囲**: 新規依存がないため全依存のライセンス・最新版本・脆弱性全面監査は行っていない。固定版SDKの実通信はfake gatewayと既存gateway testsで検証し、実LINEへの変更を調査で行わない。


### 固定版SDKの非preload動作確認

- 既存BackendコンテナのSDKソースを `inspect` で読み取り確認した。LINEへの通信と設定変更は行っていない。通常メソッドは `_preload_content` を拒否し、with_http_infoへ誘導するが、その返却時にも `ApiResponse(raw_data=response_data.data)` が評価され、画像bodyを全量取得する。
- このためdownload_contentに既存HTTPX 0.28.1のstreamを使い、固定host、redirect禁止、上限+1 byte、5秒deadline、retryなしをgatewayに閉じ込める。既存観測用downloadのSDK経路と戻り型は維持する。新依存は追加しない。
- 初回のDocker socket読取はsandbox制限で失敗したが、許可された読み取り再実行で確認を完了した。秘密情報・環境変数・資格情報にはアクセスしていない。

## Architecture Pattern Evaluation

| Option | 利点 | 制約 | 結論 |
| --- | --- | --- | --- |
| 既存stateへ画像を追加 | endpointが増えない | 厳格なDTOと他利用者、既存観測readに画像取得コストを波及 | 不採用 |
| state・設定・画像を別endpoint | 画像だけを段階表示できる | 各結果を結ぶrevision/tokenと複数回認可が必要 | 今回は不採用 |
| current専用JSON envelope | 一つの検証済み結果で対応関係を保ち、画像失敗だけを分離 | Base64の増量、状態表示もdownload完了を待つ | 採用 |
| 画像のpublic URL／画像保存 | 再利用しやすい | 認可・保存寿命・purge・配信基盤が増える | 対象外 |

## Design Decisions

### Decision: 現在表示は一つのread modelにまとめる

- **Context**: 画像と設定の対応、取得中の状態変更、停止中の表示を一貫させる必要がある。
- **Selected Approach**: current endpointに既存rich state、deactivation、current content、回復対象をまとめ、開始/終了fenceを所有する。画像失敗は200の部分結果、認可・競合は全体エラー。
- **Rationale**: 全要件で共通する「同じ前提の結果だけを表示する」問題を一つの契約へ集約できる。
- **Trade-offs**: 画像を待つ間はcurrent領域全体がloadingになる。チャネル名・戻る導線は先に表示する。Base64は1 MiBの画像上限で抑える。
- **Follow-up**: HTTP/APIテストでpartial failureとno-store、32 query budget、最大3外部GETを検証する。

### Decision: 編集開始とプレビュー確認を明示的に分ける

- **Selected Approach**: viewing/editing/previewing/confirming/preview_invalidを純粋stateへ置き、通信とURL寿命をhookに抽出する。編集へ戻る際はdraftを残して確認値を捨てる。
- **Rationale**: 現在表示と未適用表示を混ぜず、入力変更後の古い確認値利用を防ぐ。一般的なフォームフレームワークや永続draftは不要。
- **Trade-offs**: 入力を変えずに戻った場合も再生成が必要。要件が要求する入力保持を満たし、確認値の管理を単純化できる。

### Decision: 既存の関係を使い、画像保存とmigrationを追加しない

- **Selected Approach**: 初期migrationからのcurrent→origin→snapshotを検証して利用し、不正な対応は作り直し案内へ進める。template同一版がない場合は別版で補完しない。
- **Rationale**: 新規associationやhistory検索は不要。過去ADRの所有権・置換因果を保てる。
- **Trade-offs**: 関係不明のデータは自動修復しない。既存適用を壊さず、可能な画像閲覧と既存回復を残す。

### Decision: 管理終了は既存契約を維持し、閲覧側の操作候補だけを補う

- **Context**: 操作の意味と観測に対する候補提示は別の責務であり、reconcilerの候補だけでは外部変更時の管理終了を表示できない。
- **Selected Approach**: 既存releaseを採用し、current内で保存関係・観測・local gate・modeを合成する。表示用候補と実行時の再検証を区別し、フロントエンドに業務判定を複製しない。
- **Rationale**: 外部呼出を増やさず、既存の管理終了・履歴・削除除外を保ちながら要件3.4・6.7の回復導線を成立させる。既存readinessを再利用でき、新しいサービスや永続モデルは不要。
- **Trade-offs**: currentと旧stateでは外部変更時の操作候補が異なる。候補表と既存mutationを接続するT9により、表示できても実行契約に合わない操作を防ぐ。

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 19〜23件、各1〜3時間。再レビューで操作候補projectionと既存release接続の検証を独立した1件として追加した。briefの8〜11件から、fence・gateway上限検証・hook抽出・回復対象projection・競合/UI検証を独立に見積もって増加した。上流の完了済み状態機械を再実装する件数ではない。
- **独立した責任境界**: B1 Backend閲覧、B2 Frontend状態と表示、B3統合検証。提供成果は現在画像から安全に変更へ進む詳細画面の1件。
- **外部ワークフロー**: 新規read-only閲覧1系統。既存適用・解除・回復・停止のmutationを接続し、新しい補償フローは0件。

| Workstream | 1〜3時間の実行単位候補 | 件数 |
| --- | --- | --- |
| B1 | 閲覧値型/HTTP schema、関係snapshot、最終fence、画像bytes境界、read service合成、HTTP/presenter、backend互換・競合検証、管理終了候補projection/既存release接続検証 | 8〜10 |
| B2 | DTO/API、reducer、hook抽出/画像寿命、閲覧panel、編集初期化/取消、確認panel、回復/ライフサイクル接続、layoutとaccessibility | 8〜9 |
| B3 | 通し契約/履歴非依存、query/回帰/静的検査、ブラウザの配色/幅/keyboard | 3〜4 |

- **file ownership**: B1はdesignのBackend行、B2はFrontend src行、B3は統合・性能・ブラウザ検証。単体テストは対応する実装担当が所有し、B3と同じtestファイルを同時編集しない。hook抽出中はConsoleもB2が単独所有する。
- **依存順**: B1のAPI/値型・snapshot契約 → B1実装とB2 DTO/state → hookと表示 → mutation/回復接続 → B3。reviewも契約、状態遷移、画面、統合の順に行う。
- **integration task**: read APIとDTOのfixtureを一致させ、current更新・preview失効・操作結果保持を一つのjourneyで確認する。回復対象と停止制約は別の統合単位に分ける。
- **Rationale**: 40件未満で、独立rolloutを要する別成果・新しい補償状態機械・上流基盤追加がない。複数境界は同じ画面へ一方向に接続できる。Tasksで件数を再計測し、1〜3時間を超える単位は分ける。

## Risks & Mitigations

- DBとLINEを原子的に読むことはできない — 前後のdefault照合と最終DB fenceを行い、観測時点を示す。以降の変更は更新・mutation側で再検証する。
- metadataのlegacy provider scope — provider null等を閲覧へ通さず、current serviceで完全一致を必須とする。
- 操作許可と回復対象の重複判定 — 既存repositoryのpredicateを共用し、変更操作の検証をUIへ移さない。
- 大きなConsoleからhookを抽出する回帰 — mutation latch、後着エラー、同一operation追跡、停止回復を先にテスト対象へ固定する。
- 現行データの不正snapshot・廃止版 — 再適用を要求せず判別可能な失敗とする。自動推測・backfillはしない。
- Figmaと現行template catalogの差 — 表示layoutだけを採用し、未提供の6項目templateを作らない。
- パフォーマンス上限は設計値 — 実装時にservice全体のqueryと外部呼出を計測し、超過を無断で上限変更して隠さない。

## Design Review Gate

- **結果**: PASS。元のrequirementsから57件の数値IDを抽出し、traceabilityの欠落0件。4境界節、具体的file plan、componentからfileへの対応、placeholderなし、サイズ判定を確認した。
- **判断レビュー**: 停止中の外部通信禁止、画像失敗と認可失効の分離、既存API互換、回復対象の関係、操作結果とread失敗の分離、旧snapshotの互換性を確認した。
- **修正1回目**: 新safe codeの所有ファイルを補足し、current_typesからservicesへの循環importを禁止した。SDKの実装確認に基づき、画像bytesだけをHTTPXの上限付きstreamへ変更した。再チェックでcoverage・境界・実行可能性はPASS。設計生成の保存gateであり、実装テスト合格や人間のdesign承認ではない。

## 再レビュー修正後のGate

- **対象**: 管理終了の外部影響と、外部変更時の管理終了導線の2指摘。既存契約の維持が今回の修正方針であり、requirementsの承認済み範囲を変更しない。
- **機械確認**: 57件の受入条件を抽出し、traceability 57件との欠落・余剰0を確認した。4境界節、具体的file plan、componentの所有先、placeholderなし、末尾空白なしを確認した。
- **判断確認**: 通常適用中・外部変更・既定なしの候補、unknownとlocal gateの優先、modeによる絞り込み、attention内の対象参照、管理終了の確認・実行・再取得を一続きで確認した。管理終了はLINE変更・削除を行わず、旧mutationの受付条件を維持する。
- **サイズ**: PASS (single-spec)、19〜23件。追加はB1の候補projectionと接続検証であり、成果1件・新規read-only経路1系統・新しい補償フロー0件を維持する。
- **状態**: 保存gate PASS、再レビュー GO。2指摘は既存契約に合う確認文言・対象条件・操作候補・T9へ反映済みで、重大な未解決事項はない。既存の認可fenceと画像部分失敗の分離も維持する。文書・コード照合と機械確認の結果であり、実装テストは未実行。人間のdesign承認は未取得のままとする。

## References

- [承認済みrequirements](requirements.md)、[brief](brief.md)、[設計](design.md)
- [spec-sizing](../../steering/spec-sizing.md)、[LINE steering](../../steering/line-integration.md)
- [ADR 0001](../../../docs/adr/0001-do-not-delete-external-rich-menus.md)、[ADR 0002](../../../docs/adr/0002-confirm-rich-menu-removal-before-channel-deactivation.md)、[ADR 0003](../../../docs/adr/0003-delete-rich-menu-history-with-channel.md)、[ADR 0004](../../../docs/adr/0004-record-rich-menu-replacement-causality.md)
- 新しい不可逆な業務判断はなく、既存ADRを維持する。CONTEXTの履歴画面・停止中実状態の定義は承認済みrequirementsに合わせて同期する。

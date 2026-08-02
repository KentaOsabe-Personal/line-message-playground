# Research & Design Decisions

## Summary

- **Feature**: `line-rich-menu-foundation`
- **Discovery Scope**: Complex Integration（フルディスカバリ）
- **Key Findings**:
  - 既存Backendにはowner/providerのtransaction fence、exact-origin CSRF、チャネル`updated_at` revision、用途別資格情報復号、operation ID＋fingerprint＋CAS、reference probeがある。ただし既存admin repositoryはprovider未設定legacy行もscopeへ含めるため、本機能はprovider完全一致の専用snapshot portを必要とする。
  - LINEリッチメニューはcreate、画像upload、default設定の複数外部作用から成り、mutation用retry keyは公式契約にない。各段階を永続化し、結果不明時はlist/get/image/defaultの観測から同じoperationへ収束させる必要がある。
  - 画像binaryは保存せず、固定Pillow・固定日本語font・固定テンプレートから毎回生成する。確認値はsnapshot fingerprintとpreview固有nonceを結び、完全URLはoperation受付後のowner専用履歴にだけ保存する。

## Research Log

### 既存Backendの統合境界

- **Context**: owner、provider、チャネルrevision、資格情報、冪等性、削除参照を新appで再実装せず利用できるか確認した。
- **Sources Consulted**:
  - `backend/lineaccounts/admin_authorization.py`
  - `backend/lineaccounts/authentication.py`
  - `backend/lineaccounts/csrf.py`
  - `backend/linechannels/admin_repositories.py`
  - `backend/linechannels/reference_fence.py`
  - `backend/delivery/confirmation.py`
  - `backend/delivery/repositories.py`
  - `backend/delivery/gateway.py`
- **Findings**:
  - `DjangoOwnerOperationFence.lock_active`はowner、session、identity、providerを同一transactionで再証明する。
  - `DjangoAdminChannelRepository`はowner providerでscopeしたチャネルview、復号済みaccess tokenを含む一時snapshot、`updated_at` revision lockを提供するが、既存管理画面の互換性のため`provider_id IS NULL`のlegacy行もscopeへ含める。リッチメニュー要件のprovider完全一致にはそのまま利用できない。
  - 既存gatewayはaccess tokenをcall境界へ明示的に渡し、SDK retry 0、bounded timeout、safe result縮約を行う。rich menu gatewayも同じchannel-scoped call contextを必要とする。
  - Deliveryはoperation IDの一意制約、canonical fingerprint、外部I/O前の予約、外部I/O後のfirst-terminal CAS、`unknown`非再送を実装済みである。
  - `ChannelReferenceFence`は参照作成とチャネル削除を直列化し、`ChannelReferenceProbe`は削除可否をapp横断で合成する。
- **Implications**:
  - `linechannels`はprovider完全一致、active、revision、復号済みaccess tokenを一つのredacted snapshotとして返し、戻り時に同じ軸をlockする専用public portを追加する。新規`linerichmenus` appは既存Modelへ直接依存せず、このportをconstructor injectionする。
  - 単一pushのDelivery modelは流用せず、多段operation、段階、管理資源、観測結果を専用永続状態として持つ。
  - foundationはreference probeとrollback-only history purge契約を公開する。`linechannels` composition rootへの登録と削除orchestrationは下流Specへ残すが、統合完了まではfoundationのmutation受付をread-only rollout gateで閉じる。

### LINEリッチメニューAPI契約

- **Context**: 外部作用の順序、観測可能性、画像制約、rate limit、アプリ外資源の識別限界を確認した。
- **Sources Consulted**:
  - [Messaging API reference: Rich menu](https://developers.line.biz/en/reference/messaging-api/#rich-menu)
  - [Rich menus overview](https://developers.line.biz/en/docs/messaging-api/rich-menus-overview/)
  - [Get rich menu list rate-limit change](https://developers.line.biz/en/news/2026/05/26/get-rich-menu-list-rate-limit-change/)
- **Findings**:
  - 基本順序は`POST /v2/bot/richmenu`、`POST api-data.line.me/.../content`、`POST /v2/bot/user/all/richmenu/{id}`である。画像downloadも`api-data.line.me`を使う。
  - create/deleteは各100回/時、listは10回/秒、validate・upload・download・get・default操作は2,000回/秒である。429は自動再試行の根拠にしない。
  - 画像はJPEG/PNG、幅800〜2500、高さ250以上、aspect比1.45以上、1MB以下で、設定後に差し替えできない。
  - API作成資源は最大1,000件。listはMessaging APIで作成した資源だけを返し、Official Account Manager作成資源は返さない。
  - default取得の403は別チャネル等で設定されたdefault、404はMessaging API defaultなしを表す。default設定は既存API defaultを置換する。
  - 公式rich-menu mutation契約にはmessage送信のようなretry key headerが定義されていない。これは公式request header一覧からの推論であり、local idempotencyと観測照合を必須とする。
- **Implications**:
  - gatewayは`Accepted | Rejected | Unknown`へ縮約し、timeout、5xx、429、応答解釈不能を自動再試行しない。
  - create前にoperation固有の暗号学的random ownership markerを永続化し、rich menu `name`へ埋める。create結果不明時はlistのmarker完全一致が一件だけなら既存operationへ採用する。
  - upload結果不明は画像downloadをdecodeしてcanonical pixel digestを比較する。default設定・解除はdefault ID、deleteはget/list/defaultの複合観測で確認する。
  - Official Account Manager defaultは内容・所有権を取得できない外部状態として扱い、設定時の置換可能性だけをpreviewへ返す。

### 決定的画像生成と日本語font

- **Context**: Python 3.14で固定日本語fontを使い、環境差とglyph欠落を防げる依存を選定した。
- **Sources Consulted**:
  - [Pillow Python support](https://pillow.readthedocs.io/en/stable/installation/python-support.html)
  - [Pillow 12.3.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)
  - [Noto Sans CJK 2.004 release](https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004)
  - [Noto CJK license](https://github.com/notofonts/noto-cjk/blob/Sans2.004/Sans/LICENSE)
  - [Pinned Japanese subset font](https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/SubsetOTF/JP/NotoSansJP-Regular.otf)
- **Findings**:
  - Pillow 12系はPython 3.14を公式サポートし、2026-07-01時点の安定版は12.3.0である。
  - Noto Sans CJK 2.004はSIL Open Font License 1.1で配布され、日本語subset OTFを同梱できる。
  - `NotoSansJP-Regular.otf`の固定URLから取得したSHA-256は`dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073`である。
- **Implications**:
  - `Pillow==12.3.0`、font file、OFL本文、font digestをrepositoryへ固定し、startup validationとtestで差し替えを検出する。
  - content digestはPNG圧縮byteではなく、template ID/version、寸法、canonical RGBA pixel列からSHA-256を計算する。encoder metadataを付けず、固定PNG optionで1MB以下を検証する。
  - cmapで全入力code pointのglyph存在を生成前に検証し、未対応文字はfallbackせず項目エラーにする。

### 仕様矛盾の修正

- **Context**: 4.3は確認値の返却を必須とし、旧10.7は確認値を全owner応答から禁止していた。
- **Sources Consulted**: `requirements.md` 4.3、10.7
- **Findings**: 両者は同一preview応答で両立しないため、ユーザー承認により10.7へ「確認済みプレビュー応答を除く」を追加した。
- **Implications**: 確認値はpreview応答と直後のapply requestにだけ存在し、URL、履歴、ログ、安全なエラー、operation/status応答へ残さない。

### Design validation指摘の統合確認

- **Context**: 初回design validationで、recovery operationの自己排他と永続relation不足、gatewayのチャネル資格情報context不足、下流probe統合前のチャネル削除リスクがNO-GO要因となった。
- **Sources Consulted**:
  - `.kiro/specs/line-rich-menu-foundation/design.md`
  - `.kiro/specs/line-rich-menu-admin-lifecycle/brief.md`
  - `backend/linechannels/admin_repositories.py`
  - `backend/linechannels/admin_services.py`
  - `backend/linechannels/container.py`
  - `backend/linechannels/reference_fence.py`
- **Findings**:
  - recheck／cleanupを独立operationとして公開するなら、元blockerと対象資源をfingerprintだけでなく永続relationとして持ち、blockerと実行中operationを別pointerにする必要がある。
  - gateway methodだけでは複数チャネルのtokenを選べず、既存admin repositoryのprovider-null互換scopeは1.4のfail-closed条件に一致しない。
  - 現行channel deleteはcomposition rootへ登録済みprobeだけを確認して即時削除するため、foundation mutationを先に有効化するとrich menu参照を検出できない。
- **Implications**:
  - `subject_operation_id`／`target_resource_id`、`blocking_operation_id`／`active_operation_id`、atomic recovery handoffをdata/state contractへ追加する。
  - exact-provider `OwnerChannelOperationPort`と明示的`RichMenuGatewayContext`を追加する。
  - 下流がprobe／rollback-only purgeを統合するまでmutation readinessをread-onlyに保つ。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|--------|-------------|-----------|---------------------|---------|
| Modular monolith + ports/adapters + persisted saga | 新規Django appにdomain stateを置き、既存owner/channel portとLINE gatewayを合成 | 既存構造に一致し、多段外部作用とunknownを永続追跡できる | 状態遷移とCASの設計・テスト量が多い | 採用 |
| DeliveryAttempt拡張 | 既存配信operationへrich menu段階を追加 | 冪等性実装を直接再利用できる | push固有状態と資源sagaが混在し責任境界を破る | 不採用 |
| Background job / automatic retry | queueで段階を自動進行・再試行 | owner待ち時間を短縮できる | 現行runtimeにqueueがなく、mutation重複と要件の自動再試行禁止に反する | 不採用 |
| LINE状態のみをsource of truthにする | DB状態を最小化し毎回list/getする | schemaが小さい | 所有権、operation binding、history、unknownを証明できない | 不採用 |

## Design Decisions

### Decision: 一つのチャネル集約と多段operationへ一般化する

- **Context**: 適用、置換、解除、管理終了、再確認、cleanupは同じ排他・所有権・観測規則を共有する。
- **Alternatives Considered**:
  1. 操作種別ごとの独立service/model
  2. `OperationKind`と`OperationStage`を持つ単一集約
- **Selected Approach**: チャネルごとの`RichMenuChannelState`を排他rootとし、`RichMenuOperation`、`ManagedRichMenu`、append-only transitionを一つのrepository/state machineで管理する。公開service methodは操作種別ごとに分ける。
- **Rationale**: 共通の競合防止とunknown収束を一箇所に保ちつつ、公開interfaceは用途別に型安全にできる。
- **Trade-offs**: 状態機械は大きくなるが、重複するlock・CAS実装を避けられる。
- **Follow-up**: 全許可遷移、禁止遷移、外部I/O前後CASをtable-driven testで固定する。

### Decision: 既存境界と公式SDKを採用し、domain sagaだけを構築する

- **Context**: owner認可、資格情報、revision、CSRF、HTTP clientを新規実装する必要はない。
- **Alternatives Considered**:
  1. LINE endpointをHTTPXで直接実装
  2. 既存`line-bot-sdk==3.25.0`の`MessagingApi`／`MessagingApiBlob`をgateway内で利用
- **Selected Approach**: 既存fence/repositoryと公式SDKを採用し、retryを0に固定する。SDK型と例外はgateway外へ出さない。
- **Rationale**: 依存追加を抑え、既存gatewayのtimeout・safe classification patternに一致する。
- **Trade-offs**: SDK更新で生成modelや例外shapeが変わる可能性があるためgateway contract testが必要である。
- **Follow-up**: 3.25.0の全rich-menu method、2ホスト、timeout設定を実装開始時にsmoke testする。

### Decision: stateless preview instance確認値と履歴snapshotを分ける

- **Context**: previewでは全入力を確認へbindする一方、完全URLはowner専用履歴以外へ保持できない。
- **Alternatives Considered**:
  1. preview rowへ全入力を10分保存
  2. applyで入力を再送し、署名済みfingerprintと完全一致検証
- **Selected Approach**: 確認値はpurpose、version、issued time、128-bit以上のpreview nonce、canonical snapshot fingerprintだけを署名する。applyは入力を再送し、owner/provider/channel revision/default/template/input/pixel digestを再計算する。受付成功時だけtoken全体のdigestをconfirmation usage keyとして一意予約し、operation history snapshotへ完全URLを保存する。
- **Rationale**: tokenとpreview storageへ秘密性の高い入力を置かず、確認後差し替えを拒否できる。
- **Trade-offs**: apply時に再renderと現状態照合が必要になる。
- **Follow-up**: 同じtokenの別operation再利用を拒否し、同じsnapshotから新しく発行した別tokenは独立して一度だけ使用できることを検証する。

### Decision: recoveryをsubjectへ結び付いた独立operationとして記録する

- **Context**: 再確認とcleanupは独自の操作識別子と履歴を必要とする一方、元operationのunknown／cleanup blockerを解消するためだけに許可される。
- **Alternatives Considered**:
  1. 元operationを直接`rechecking`へ遷移し、再確認要求をtransitionだけに記録する
  2. `subject_operation_id`と`target_resource_id`を持つ独立recovery operationを作る
- **Selected Approach**: recheck／cleanupは独立`RichMenuOperation`とし、対象となるblockerを`subject_operation_id`、必要な管理資源を`target_resource_id`へ保存する。channel stateは`blocking_operation_id`と`active_operation_id`を分け、blockerを保持したまま一件のrecoveryだけをatomic claimする。
- **Rationale**: 8.7の元operation更新と10.1のrecheck／cleanup固有履歴を両立し、crash後もfingerprintの逆算に頼らず対象を復元できる。
- **Trade-offs**: 状態機械に親子整合とhandoff規則が増える。
- **Follow-up**: recheck成功時の元operation再開、cleanup unknown時のblocker移譲、異channel／循環subject拒否をrepository concurrency testで固定する。

### Decision: provider完全一致snapshotとscoped LINE gatewayを使う

- **Context**: 複数チャネル環境でgatewayが資格情報を暗黙選択すると、provider境界とrevision fenceをinterfaceで保証できない。
- **Selected Approach**: `linechannels`の専用portがexact provider、active、revision、credentialを一つの非serialization snapshotへ閉じ込める。`linerichmenus`はsnapshotから`RichMenuGatewayContext`を作り、全gateway callへ明示的に渡し、応答採用前に同じportでowner/provider/revisionを再lockする。
- **Rationale**: provider未設定legacy行をfail closedにし、tokenの選択と外部I/O後fenceを型で追跡できる。
- **Trade-offs**: `linechannels`へ既存admin契約を変えない追加portが必要になる。
- **Follow-up**: provider null、provider変更、inactive、revision変更、credential unreadableをLINE call 0件で拒否するcontract testを追加する。

### Decision: mutation rolloutを下流reference統合完了まで閉じる

- **Context**: foundationの資源作成後、rich menu probe未登録の既存channel delete APIがチャネルを削除すると、外部資源と履歴が孤立する。
- **Selected Approach**: readinessを`read_only | recovery_only | enabled`に閉じる。foundation単独の`read_only`は全mutationを拒否する。下流Specがreference probe登録とrollback-only purgeをchannel delete transactionへ組み込む同一releaseで`enabled`にし、rollback時はprobe/purgeを残した`recovery_only`でapplyだけを止める。
- **Rationale**: foundationとadmin-lifecycleの責任分割を維持しつつ、安全性をrelease順の暗黙前提にしない。
- **Trade-offs**: foundation単独導入時はmutation contractをテストできるがruntimeでは利用できない。
- **Follow-up**: probe未登録状態では外部mutation 0件、`recovery_only`／`enabled`ではprobe／purge integration markerが揃わない限りstartup validationを通さないことを確認する。

### Decision: 初期テンプレートを固定geometryの1・2・3リンクに限定する

- **Context**: 3種類を実装可能な版付き定義へ落とす必要がある。
- **Selected Approach**: `jp-link-one`、`jp-link-two`、`jp-link-three`のversion 1を2500×843 PNGで定義する。areaは横方向に1分割、2等分、`834/833/833`の3分割とし、各areaは表示名とHTTPS URIを一組だけ要求する。表示名はtrim＋NFC後20 Unicode code point、URLはtrim後1000 code pointを上限とする。
- **Rationale**: URI actionだけで3種類を差分少なく提供し、将来の意味変更は新versionへ分離できる。
- **Trade-offs**: 自由配置、色、font、action追加は扱わない。
- **Follow-up**: 固定palette、文字wrap、padding、chat bar text、golden pixel digestをtemplate testで確定する。

### Decision: 明示再確認に観測quorumを使う

- **Context**: list/getの一時的非観測だけで作成失敗・削除済みを確定できない。
- **Selected Approach**: create unknownはmarker一致一件のみ採用し、不在はunknown維持。upload unknownはdownload画像のpixel digest一致だけを成功とする。default変更はdefault ID一致、不一致または403/404の意味を分類する。delete unknownは認証済みget 404、list marker不在、default非一致の複合観測が揃った場合だけ削除済みへ収束する。
- **Rationale**: 追加mutationなしで可能な最も強い観測を使い、単独の非観測を成功へ読み替えない。
- **Trade-offs**: 外部状態によってはownerがunknownを解消できない場合がある。
- **Follow-up**: 観測の一部失敗、複数marker、429、403をすべてunknown維持として試験する。

### Decision: 単一app・同期実行・binary非永続化へ単純化する

- **Context**: 現行はDocker Compose上の同期Djangoで、queue/object storageを持たない。
- **Selected Approach**: 一つの`linerichmenus` app、同期gateway、一回のowner操作につき一つの外部段階、明示的status/recheckを採用する。画像file/binary、background job、event bus、cacheを追加しない。
- **Rationale**: 現行stackと要件を満たす最小構成であり、自動再試行を構造的に避けられる。
- **Trade-offs**: 多段applyは複数の安全な内部stepを同期で進めるが、timeout時はownerの再確認が必要になる。

## Design-stage Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 39件
- **Independent responsibility seams**: 5（template/renderer、confirmation/preview、LINE gateway/observation、state/persistence/workflow、owner/headless/reference契約）
- **Workstreams and dependency order**:
  1. Pillow/font assetとtemplate/renderer contract
  2. domain types、state machine、schema、repository lock/CAS
  3. confirmation/previewとLINE gateway/observation
  4. apply/unlink/release/recheck/cleanup orchestrationとhistory
  5. owner API、headless/reference/rollback-only purge、rollout readiness契約、cross-boundary validation
- **File ownership / review order**: workstream 3が`linechannels/admin_types.py`と`admin_repositories.py`のexact port、および`linerichmenus/gateway.py`を所有する。その他は`linerichmenus`内の非重複moduleを所有し、types/state契約レビュー後にrepository/gateway、最後に`services.py`／`container.py`／API統合をレビューする。
- **Validation strategy**: unit/golden → migration/repository concurrency → gateway classification → service saga → API/headless/reference/security/performanceの順で収束を確認する。
- **Rationale**: 30〜39件のreview attention帯だが、全seamは「確認済み設定を追跡可能なチャネル既定資源へ一意に収束させるBackend能力」という単一成果へ依存順付きで収束する。exact-provider portは既存の資格情報／gateway integration、recovery relationは既存のstate/schema、readiness guardは既存のreference/purge integration taskを具体化したもので、独立workstreamを追加しない。Frontendとチャネル状態変更orchestrationは下流Specへ分離済みで、file owner、contract、integration順、validationが明確なためbounded review可能である。

### Tasks-stage Size Exception

- **Date**: 2026-08-02
- **Reviewed executable tasks**: 46件
- **Exception decision**: ユーザーは、過大なタスクを隠さず分割した結果として40件を超えることを理解したうえで、単一Specの継続を明示的に承認した。
- **Continuation rationale**: 独立task-graph sanity reviewは具体的な依存不足、順序、replay条件、検証タスクの粒度を指摘できており、単一レビュー範囲として機能している。指摘は局所修正で収束可能で、requirements/designの矛盾や責任境界の未決定は検出されていない。roadmap分割による契約重複と統合負荷の方が実装・レビューを難しくするため、`spec-sizing.md` の明示例外として継続する。
- **Accepted risks**: 実装期間とレビュー量は通常の単一Specより大きい。workstream順、`_Depends:_`、`_Boundary:_`、独立検証タスクを維持し、実装時はタスク単位のreview gateを省略しない。

## Risks & Mitigations

- LINE mutationにretry keyがない — operation予約、ownership marker、段階永続化、明示recheckで重複作用を防ぐ。
- listは10回/秒かつ一時非観測があり得る — 自動pollingをせず、明示操作一回だけ観測し、不在単独では確定しない。
- 画像差し替え不可 — upload前検証を完了し、upload後の変更は新資源としてのみ扱う。
- 日本語glyph/encoder差 — Pillow、font、digest、geometryを固定し、cmap validationとgolden pixel testを行う。
- 完全URL・token・binaryの漏洩 — redacted型、safe error、ログcanary、token payload inspection、history scope testを行う。
- 外部I/O中のowner/channel変更 — transaction外通信後にowner/provider/revision/operation stageを再lockし、stale結果はrecheckable unknownへ収束させる。
- provider未設定legacy行とtoken誤選択 — exact-provider snapshot portとgateway call contextでLINE call前後をfail closedにする。
- recovery対象の喪失／自己排他 — subject/target FK、blocking/active pointer分離、atomic claim/handoffで復元可能にする。
- history-only削除の部分完了 — transaction-required purgeは失敗時にtransactionをrollback-onlyにし、下流統合完了までmutation rollout gateを閉じる。
- 39 taskのreview負荷 — workstream ownerと契約依存順を固定し、Tasks phaseで40件以上ならDiscoveryへ戻す。

## References

- [LINE Messaging API reference](https://developers.line.biz/en/reference/messaging-api/#rich-menu)
- [LINE rich menus overview](https://developers.line.biz/en/docs/messaging-api/rich-menus-overview/)
- [Pillow Python support](https://pillow.readthedocs.io/en/stable/installation/python-support.html)
- [Pillow 12.3.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)
- [Noto Sans CJK 2.004 release](https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004)
- [SIL Open Font License 1.1 for Noto CJK](https://github.com/notofonts/noto-cjk/blob/Sans2.004/Sans/LICENSE)

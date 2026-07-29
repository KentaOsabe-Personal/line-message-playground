# Research & Design Decisions

## Summary

- **Feature**: `line-channel-admin-ui`
- **Discovery Scope**: Complex Integration（既存機能拡張として light discovery を開始し、owner 認可・秘密情報・外部 API・削除競合を含むため full discovery へ拡張）
- **Key Findings**:
  - `line-channel-foundation` の型、検証、暗号化、transaction 境界は再利用できるが、管理用のowner provider投影、欠損資格情報の修復、状態競合、削除は追加契約が必要である。
  - 接続確認は既存 `line-bot-sdk==3.25.0` の `MessagingApi.get_bot_info()` で送信なしに実現できる。SDK retry を無効にし、bot user ID だけを比較して安全な分類へ縮約する。
  - 配信、Webhook、friendship、interaction の監査はチャネル UUID を FK なしで保持するため、削除側の再確認だけでは参照作成との race を閉じられない。全 writer と削除が共有するチャネル行 lock 契約が必要である。
  - `OwnerProtectedAPIView` のrequest開始時認証だけではunlinkと一覧・詳細のraceを閉じず、接続確認もtoken・bot user ID・revisionの同一snapshotと完了時再検証が必要である。
  - 初期プロダクト境界はowner identityとMessaging APIチャネルを同一providerに限定する。別provider管理は本specの対象外である。

## Research Log

### 既存チャネル基盤の拡張点

- **Context**: 登録、更新、有効化、無効化、資格情報置換を新しい HTTP 境界から安全に再利用できるか確認した。
- **Sources Consulted**:
  - `backend/linechannels/types.py`
  - `backend/linechannels/validators.py`
  - `backend/linechannels/services.py`
  - `backend/linechannels/repositories.py`
  - `backend/linechannels/models.py`
  - `.kiro/specs/line-channel-foundation/design.md`
- **Findings**:
  - `PublicChannelSummary`、`RegisterLineChannel`、`UpdateLineChannel`、秘密値 wrapper、入力 validator、暗号化後の原子的保存を再利用できる。
  - 現行 `DjangoLineChannelDirectory` は active かつ provider 設定済みの連携候補だけを返すため、管理画面の同一provider＋legacy NULL一覧には使えない。
  - 現行 `update_locked()` は資格情報行への `UPDATE` が 0 件なら失敗する。欠損資格情報行を完全な新規 pair で修復するには、チャネル行 lock 下の create-or-replace が必要である。
  - 現行 `set_active()` は同一状態への要求を成功扱いする。複数タブや結果不明後の再操作を安全に扱うには `updatedAt` を revision とする期待値比較が必要である。
  - `provider_id=NULL` は legacy backfill seam である。管理投影では `null` を明示し、既存 non-null 値の変更を拒否しつつ、一度だけの legacy backfill はowner identityと同じproviderに限定できる。
  - roadmapは異なるprovider間の本人統合を対象外とし、最初の管理対象を同一provider配下へ限定している。providerの数字形式とimmutable検証だけでは、本人連携へ利用できない別providerチャネルをUIから作成できる。
- **Implications**:
  - 管理用 repository/service を `linechannels` 内へ追加し、既存の配信・Webhook用 projection と資格情報利用契約を変更しない。
  - 資格情報設定状態は秘密列の内容を返さず、「行が存在し両暗号文が非空か」だけから導出する。復号可能性は有効化または接続確認時に fail closed で判定する。
  - owner/session lock下でDBから取得したidentity providerをproofとし、一覧・詳細は同一providerまたはlegacy NULLだけ、登録とNULL backfillは同一providerだけを許可する。

### owner session と exact-origin CSRF

- **Context**: owner 限定 UI と、操作中に unlink または session 失効が発生する競合を確認した。
- **Sources Consulted**:
  - `backend/lineaccounts/authentication.py`
  - `backend/lineaccounts/permissions.py`
  - `backend/lineaccounts/csrf.py`
  - `backend/lineaccounts/repositories.py`
  - `backend/lineaccounts/unlink_services.py`
  - `backend/lineaccounts/views.py`
  - [Django REST framework: Authentication](https://www.django-rest-framework.org/api-guide/authentication/)
  - [Django 6.0: CSRF protection](https://docs.djangoproject.com/en/6.0/ref/csrf/)
- **Findings**:
  - `OwnerProtectedAPIView` は session 認証、active owner permission、unsafe method の exact HTTPS Origin と CSRF cookie/header を serializer より先に検証する。
  - DRF の `SessionAuthentication` は認証済み unsafe request に CSRF token を要求する。既存 exact-origin guard は、現行 proxy が内部 request を secure と判定しない構成でも Origin 欠落を許さない。
  - View 開始時の認可だけでは、その後に `OwnerAccount` が unlink pending へ遷移する競合を閉じられない。既存 unlink は `OwnerAccount` singleton を lock root にしている。
  - mutationだけでなく一覧・詳細も、request開始時認証後にunlinkが先行すると失効後の情報を返し得る。
- **Implications**:
  - 一覧・詳細は `OwnerAccount` → `OwnerSession` の順でlockし、safe projection materializeまでの短いtransactionをunlinkとの線形化点にする。
  - 全ローカル mutation は `OwnerAccount` → `OwnerSession` → `LineChannel` の順で lock し、同じ transaction 内で session ID、identity、provider、active state、有効期限を再照合する。
  - 接続確認は外部通信中に DB lock を保持しない。開始前とLINE応答後に短いowner/session transactionを持ち、再検証失敗時は接続結果を表示しない。

### write-only serializer と Frontend 秘密値境界

- **Context**: 資格情報を入力として受け付けつつ、応答、再表示、状態 snapshot へ混入させない方法を確認した。
- **Sources Consulted**:
  - [Django REST framework: Serializer fields](https://www.django-rest-framework.org/api-guide/fields/)
  - `frontend/src/httpApi.ts`
  - `frontend/src/accountDto.ts`
  - `frontend/src/accountApi.ts`
  - `frontend/src/deliveryState.ts`
- **Findings**:
  - DRF の `write_only=True` field は create/update 入力には使えるが serialized representation から除外される。
  - 既存 Frontend は `unknown` 応答の exact-key 検証、typed API client、discriminated union state、request generation による stale 応答破棄を採用している。
  - React reducer や retry payload に秘密値を含める必要はない。uncontrolled form から submit 時だけ値を読み、要求完了または失敗時に `form.reset()` できる。
- **Implications**:
  - アクセストークンとチャネルシークレットは明示的 `write_only` field とし、safe field error は値を引用せず `credentialPair` の修正だけを示す。
  - Frontend の永続 storage、URL、reducer、operation history、DOM 属性へ秘密値を置かない。自動 retry を実装せず、network unknown 時は一覧または詳細の明示再取得へ収束させる。

### LINE への送信なし接続確認

- **Context**: 保存済みアクセストークンだけで、メッセージ送信や設定変更なしに接続と bot identity を確認する。
- **Sources Consulted**:
  - [LINE Messaging API reference: Get LINE Official Account bot info](https://developers.line.biz/en/reference/messaging-api/nojs/#get-bot-info)
  - [LINE Bot SDK for Python: MessagingApi](https://github.com/line/line-bot-sdk-python/blob/master/linebot/v3/messaging/docs/MessagingApi.md)
  - `backend/requirements.txt`
  - `backend/delivery/gateway.py`
- **Findings**:
  - `GET /v2/bot/info` は Bearer channel access token を使い、bot の `userId` を返す read-only endpoint である。公式 reference の rate limit は 2,000 requests/second である。
  - SDK v3 は `MessagingApi.get_bot_info()` を提供する。`ApiException` は status、headers、body を保持するため、例外そのものを応答や通常ログへ渡してはならない。
  - 既存送信 gateway は retries=0 と timeout/network error の安全な縮約パターンを持つが、送信責務と接続確認責務は分離すべきである。
  - 既存 `CredentialRepository.get_access_token()` は inactive channel を拒否する。登録済み inactive channel も確認対象なので、管理用途専用の active 状態に依存しない access-token 取得契約が必要である。
  - access tokenだけを返すportでは、別queryで得たbot user IDとの組合せが同じchannel revision由来であることを保証できない。外部call中のmetadata/credential更新も旧結果を現在設定の結果に見せ得る。
- **Implications**:
  - 新しい `LineBotInfoGateway` は一回だけ `get_bot_info()` を呼び、401・403 を `authentication_failed`、429 を `rate_limited`、timeout・connection・その他4xx・5xx・不定形応答を `line_unavailable` に縮約する。
  - 開始時にaccess token、保存済みbot user ID、`updated_at`を単一queryの非直列化snapshotとして取得する。取得した `userId` はsnapshot bot user IDと比較する。
  - LINE応答後にowner/sessionとchannel revisionをlock下で再検証し、revision変更時はLINE分類を破棄して`stale_channel`とする。raw body、headers、request ID は保持も返却もしない。

### Webhook URL の導出

- **Context**: request Host や proxy header を信頼せず、各チャネルの完全 URL を一覧・詳細・コピーで一致させる。
- **Sources Consulted**:
  - `backend/config/public_origin.py`
  - `backend/config/settings.py`
  - `backend/config/urls.py`
  - `backend/linewebhooks/urls.py`
  - [LINE Developers: Build a bot](https://developers.line.biz/en/docs/messaging-api/building-bot/)
- **Findings**:
  - `NGROK_DOMAIN` は canonical host として起動時検証され、`build_trusted_https_origin()` から単一 HTTPS origin を生成できる。
  - Webhook path の既存公開契約は `/api/line/webhooks/{channel-public-id}/` である。
  - LINE Developers Console への URL 設定は手動操作であり、Messaging API channel につき webhook endpoint は一つである。
- **Implications**:
  - Backend presenter が検証済み origin、Django `reverse()`、canonical UUID から URL を一度だけ生成して DTO へ含める。Frontend のコピーはこの DTO 値だけを使う。

### 参照整合性と削除競合

- **Context**: 参照確認後に新しい履歴が作成される race を含め、未使用チャネルだけを物理削除する。
- **Sources Consulted**:
  - `backend/lineaccounts/models.py`
  - `backend/delivery/models.py`
  - `backend/linewebhooks/models.py`
  - `backend/linefriendships/models.py`
  - `backend/lineinteractions/models.py`
  - 各 app の repository と container
- **Findings**:
  - `DeliveryRecipient.line_channel` は `PROTECT` FK だが、`DeliveryAttempt`、`WebhookEventReceipt`、`FriendshipSyncAudit`、`InteractionAudit` は監査 lifecycle 分離のため UUID snapshot を保持する。
  - 削除 transaction で参照を二回問い合わせても、UUID snapshot writer が同時に insert できるため 8.4 を保証できない。
  - チャネル row を全参照作成と削除の共通 lock root にすれば、writer 先行時は削除が参照を観測し、削除先行時は writer が channel 不在として insert を中止する。
  - 既存writerの結果型は統一されておらず、特に`DeliveryAttempt`のaccept結果にはchannel消失を表す型がない。fence結果をrepositoryへ注入するだけでは、delete先行時のservice/API動作を確定できない。
- **Implications**:
  - `ChannelReferenceFence` を `linechannels` の公開契約として追加する。全5 writer は自分の既存 transaction 内で、参照 insert より前に同じチャネル行を `select_for_update()` する。
  - 削除は owner lock、channel lock、revision 確認、全参照 probe、資格情報行、チャネル行の順で同一 transaction 内に完了する。各 app は自分の Model を読む `ChannelReferenceProbe` を公開し、`linechannels` は注入された composite contract だけへ依存する。監査 table を FK 化せず、既存 audit lifecycle と app 間の依存規則を維持する。
  - writerごとに`channel_not_found`とstorage失敗のsafe result型・service/API写像を定義する。いずれもrecordと未開始のLINE push/reply/actionを開始せず、transactionをrollbackする。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Decision |
|--------|-------------|-----------|---------------------|----------|
| 既存 Django app 内の vertical slice + ports/adapters | `linechannels` が管理 API、service、repository、LINE gateway を所有し、Frontend は DTO/API/state/UI を分離する | 既存境界と命名規則に一致し、新規 app や依存を増やさない | 参照 fence の下流統合を明示的にレビューする必要がある | 採用 |
| Django Admin 拡張 | ModelAdmin で管理する | CRUD は少量 | owner session/LIFF導線、write-only契約、安全な画面状態、接続確認に不適合 | 不採用 |
| 監査 UUID をすべて FK 化 | DB 制約で削除を拒否する | 参照整合性が直接的 | audit lifecycle と既存 spec のデータ所有を変更し、大規模 migration が必要 | 不採用 |
| 削除時だけ再確認 | delete 直前に全 table を問い合わせる | 実装量が少ない | 同時 insert race を閉じられず 8.4 を満たさない | 不採用 |
| 新規 Frontend router/state library | 専用 route と global store を導入する | 大規模 UI では拡張しやすい | 現行 flat 構造と規模に対して過剰、新規依存が必要 | 不採用 |

## Design Decisions

### Decision: mutation revision を共通の競合契約にする

- **Context**: 状態変更競合、複数タブ、network unknown 後の再送で stale write を防ぐ。
- **Alternatives Considered**:
  1. `expectedActive` だけを比較する。
  2. `updatedAt` を `expectedUpdatedAt` として全既存チャネル mutation で比較する。
- **Selected Approach**: 2。サーバーが返した timezone-aware `updatedAt` を revision とし、更新、状態変更、削除は lock 後の値と完全一致するときだけ進める。
- **Rationale**: schema 追加なしで metadata、credential、state の全変更を同じ contract で検出できる。
- **Trade-offs**: network unknown 後は同じ要求を盲目的に再送できず、明示再取得が必要になる。これは 9.7、9.10 と一致する。
- **Follow-up**: MySQL `datetime(6)` と JSON ISO 8601 の round-trip を repository/API test で固定する。

### Decision: owner read/mutation fence と channel reference fence を分離する

- **Context**: owner unlink 競合とチャネル参照作成競合は lock root と利用者が異なる。
- **Selected Approach**: owner fence は `OwnerAccount` と `OwnerSession` を検証してidentity provider proofを返し、一覧・詳細・mutationで共有する。reference fence は `LineChannel` だけを lock する。管理 lock 順序は owner → session → channel と固定する。
- **Rationale**: 下流の匿名 Webhook writer に owner 依存を持ち込まず、必要最小限の共有契約で race を閉じる。
- **Trade-offs**: 既存5 writer の composition と concurrency test が必要になる。
- **Follow-up**: 新規の channel UUID writer が追加された場合は reference fence 利用を review checklist に含める。

### Decision: 管理対象をowner identityと同一providerに限定する

- **Context**: roadmapは異なるprovider間の本人統合を対象外とし、連携・配信もprovider一致を前提にしている。
- **Selected Approach**: `OwnerOperationFence` がDBから返すidentity providerを唯一の比較元とし、一覧・詳細は同一providerまたはlegacy NULL、登録とNULL backfillは同一providerだけを許可する。
- **Rationale**: UI上は正常に見えても本人連携や配信へ利用できない別providerチャネルの作成を防ぎ、初期プロダクト境界と一致させる。
- **Trade-offs**: 別providerチャネルの一元管理はできない。必要になった場合はidentity統合と認可境界を別specで設計する。
- **Follow-up**: provider不一致をrequest値のfield validationだけで判断せず、owner lock下のproofを使うcontract testを追加する。

### Decision: 接続確認は同一revision snapshotに対する非永続・一回・read-only確認とする

- **Context**: 接続確認履歴と自動監視は対象外であり、結果不明の自動再実行は禁止される。
- **Selected Approach**: request ごとにtoken・bot user ID・revisionを単一snapshotで取得して `get_bot_info()` を一回だけ実行する。完了時にowner状態とrevisionを再検証し、一致時だけ分類と `checkedAt` を返す。DB model、retry queue、監視 job は追加しない。
- **Rationale**: 要求を満たす最小構成で、送信 gateway や監査 lifecycle と責任を混ぜない。
- **Trade-offs**: 過去結果は再表示できず、画面 reload 後に消える。
- **Follow-up**: 将来履歴が必要になった場合は別 spec で保持期間と秘密非露出を再設計する。

### Decision: 秘密値は uncontrolled form の一時入力に限定する

- **Context**: reducer、retry snapshot、永続 storage、DOM 属性への残留を禁止する。
- **Selected Approach**: 資格情報欄は既存値、placeholder、属性へ値を埋めず、submit handler が一度だけ読み取る。要求終了時は成否にかかわらず form を reset する。
- **Rationale**: React の長寿命 state へ秘密値を載せず、再試行時の完全 pair 再入力を構造化できる。
- **Trade-offs**: validation failure 後も秘密値を再入力する必要がある。
- **Follow-up**: UI test で input value、DOM attribute、API error、state action に canary が残らないことを検証する。

## Risks & Mitigations

- 下流 writer が reference fence を呼び忘れると削除 race が再発する — 公開 Protocol、全既存 writer の integration test、削除との実並行 test で固定する。
- fence失敗を既存writer結果へ誤写像すると500や外部作用が起きる — writer別mapping表、result型、service/API contract testで固定する。
- owner lock と channel lock の順序不一致で deadlock が起きる — 管理read/mutation/connection completionは owner → session → channel、下流 writer は channel のみとし、retryable DB error へ安全に分類する。
- 外部call中のchannel更新で古い接続結果を表示する — 開始snapshotと完了時revision lock比較でstaleへ収束させる。
- request providerを信頼すると別providerを登録できる — DB由来owner provider proofとの完全一致をserviceで検証する。
- 外部 SDK 例外が token、body、header を漏らす — 例外を文字列化せず status/type だけを安全な union へ変換し、canary test を追加する。
- `updatedAt` revision の精度差で誤 conflict が起きる — timezone-aware ISO 8601 と DB microsecond round-trip を contract test で検証する。
- 大きなチャネル数で一覧・参照確認 query が増える — 一覧は資格情報値を取得しない単一 projection query、削除参照確認は存在判定 query を各 table 一回に限定し、初期スコープでは pagination や cache を追加しない。

## Design-stage Spec Size Assessment

- **Verdict**: `PASS (single-spec)`（30〜39件の review attention 帯）
- **Projected executable tasks**: 36〜39件
- **Independent responsibility seams**: 6（安全な管理投影・mutation、owner fence、参照 fence、接続確認 gateway、HTTP contract、Frontend DTO/API/state/UI）
- **Independently deliverable outcomes**: 1（認証済み owner が一つの画面でチャネルを安全に管理する成果）
- **External/state workflows**: LINE read-only 接続確認1件、チャネル lifecycle mutation1件。接続確認は非永続で、別 rollout や補償状態機械を持たない。
- **Integration ownership and order**:
  1. `linechannels` の公開型、repository、owner/reference fence contract
  2. 下流5 writer の reference fence統合とwriter別safe result mapping
  3. 管理 service、LINE gateway、HTTP API
  4. Frontend DTO/API/state/UI
  5. 並行性、秘密非露出、回帰、build の統合検証
- **Rationale**: 39件以下で一つの owner 成果へ収束する。read線形化、接続snapshot、writer別mappingを独立taskへ展開しても40件未満である。共有 lock 契約は複数 app に触れるが、単一 file owner、同一 contract、明示した統合順序で bounded review が可能であり、別々の rollout／rollbackや独立状態機械は重ならない。

## References

- [LINE Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/#get-bot-info) — bot info、Bearer token、rate limit
- [LINE Bot SDK for Python MessagingApi](https://github.com/line/line-bot-sdk-python/blob/master/linebot/v3/messaging/docs/MessagingApi.md) — `get_bot_info()` 契約
- [Django REST framework authentication](https://www.django-rest-framework.org/api-guide/authentication/) — session auth と unsafe method の CSRF
- [Django REST framework serializer fields](https://www.django-rest-framework.org/api-guide/fields/) — `write_only` field
- [Django 6.0 CSRF protection](https://docs.djangoproject.com/en/6.0/ref/csrf/) — Origin 検証と unsafe request
- [LINE Developers Build a bot](https://developers.line.biz/en/docs/messaging-api/building-bot/) — Webhook URL の手動設定

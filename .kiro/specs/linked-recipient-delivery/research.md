# 調査・設計判断

## Summary

- **Feature**: `linked-recipient-delivery`
- **Discovery Scope**: Complex Integration。既存配信、owner認証、チャネル資格情報、recipient状態、LINE push、検証済みpostback actionをまたぐ拡張
- **Key Findings**:
  - 現行配信の `processing` 先行記録、transaction外の外部通信、operation IDによるLINE retry key、条件付き終端更新は再利用できる。一方、確認・冪等性・状態照会へownerとtargetを追加しなければ、対象差し替えと所有者横断照会を防げない。
  - upstreamは必要な安全境界を既に提供する。`CredentialRepository`はactive確認済みのredacted access tokenを返し、interaction dispatcherは検証済みchannel、recipient、`webhookEventId`、不透明payloadだけを登録済みactionへ渡す。
  - LINEの200は受付であり到達保証ではない。5xx、timeout、通信切断、応答解釈不能は配信済みの可能性を排除できないため `unknown` とし、自動再送しない。
  - 受取確認は通常textと短いButtons templateを同じpush requestへ含める。消えやすいquick reply、2操作が必須のConfirm template、過剰なFlex Messageは採用しない。
  - unlink後も残るsingleton owner slotを状態照会の安定principalとし、削除されるidentity UUIDは送信時監査snapshotとconfirmation fenceへ限定する。
  - receipt capabilityはaccept前のmemory上でcandidateを作り、勝者のdigestだけを新規attemptと同じtransactionで保存する。競合敗者の生値は永続化・送信しない。

## Research Log

### 既存配信の拡張点

- **Context**: 固定チャネル・固定宛先の配信保証を、選択した登録済みrecipientへ維持できるかを確認した。
- **Sources Consulted**:
  - `backend/delivery/models.py`
  - `backend/delivery/services.py`
  - `backend/delivery/confirmation.py`
  - `backend/delivery/gateway.py`
  - `backend/delivery/views.py`
  - `frontend/src/deliveryState.ts`
- **Findings**:
  - `DeliveryAttempt` は `target_mode=fixed_user` のみで、owner、channel、recipient、受取確認を保存しない。
  - 現行確認tokenはmessage fingerprintだけを署名し、有効期限を持たない。
  - 同一operation IDは同じcontent fingerprintへ収束し、処理中contentは一意制約で抑止される。外部通信はcommit後、終端結果は `status=processing` の条件付き更新で最初の確定結果を維持する。
  - status APIはoperation IDだけで検索するため、owner scopeを追加する必要がある。
  - Frontend reducerはdiscriminated unionで編集、確認、送信、結果不明、状態照会、終端を表現しており、targetと受取確認を同じ編集snapshotへ追加できる。
- **Implications**:
  - 既存状態機械を置換せず、request fingerprintの範囲と監査snapshotを拡張する。
  - `content_fingerprint` と `active_content_fingerprint` はデータを保持したままrequest単位の名称へrenameする。
  - confirmationはTimestampSigner相当のDjango signingを使い、owner、target revision、message fingerprint、受取確認有無、有効期限を結び付ける。

### owner・channel・recipientの照合

- **Context**: 任意ID入力、異なるprovider、異なるownerの対象を存在漏洩なしで拒否する必要がある。
- **Sources Consulted**:
  - `backend/lineaccounts/models.py`
  - `backend/lineaccounts/repositories.py`
  - `backend/lineaccounts/recipient_services.py`
  - `backend/lineaccounts/authentication.py`
  - `backend/linechannels/repositories.py`
- **Findings**:
  - active ownerはsessionから `identity_public_id` として取得できる。recipient所有関係はowner identity、provider、channel、recipientの完全一致で表せる。
  - 配信可能条件は `channel.is_active && recipient.enabled && recipient.friendship_state == friend` で既に統一されている。
  - 現行account channel projectionは管理用途で、recipient表示名とtarget revisionを持たない。配信専用projectionの方が公開項目と再検証契約を限定できる。
  - `CredentialRepository.get_access_token(channel_public_id)` はchannel active、資格情報完全性、復号失敗を型付き結果へ縮約し、別チャネルへfallbackしない。
  - `OwnerSessionAuthentication`の検証済みauth contextは、削除されるidentity UUIDに加えてunlink後も残る`OwnerSessionView.owner_slot`を持つ。status認可はこのslotを利用できる。
  - `LineChannel`と`DeliveryRecipient`は`updated_at`を持ち、既存のactive／enabled／friendship mutationは変更時に明示更新する。既存projectionはrevisionを公開しないため、delivery adapterが単一のcanonical builderを所有する必要がある。
- **Implications**:
  - `lineaccounts` にdelivery専用adapterを置き、他appがaccount Modelを直接読むことを避ける。
  - 一覧用の安全なsummaryと、送信直前用のredacted `LineSubject` を含むlive targetを別契約にする。
  - missing、owner mismatch、provider mismatch、channel-recipient mismatchは公開応答で同じ `target_not_available` に縮約する。
  - target revisionはversion prefix、owner identity、channel/provider/active/updated_at、recipient/enabled/friendship/updated_atの長さprefix付きcanonical encodingをSHA-256化する。UTC microsecondへ正規化し、状態が変わって元へ戻った場合も古いpreviewを無効にする。
  - credential rotationとidentity display name更新は配信可否を変えないためrevision外とし、preview／sendの全境界で同じbuilderを再利用する。

### LINE push契約と結果分類

- **Context**: 単一recipient送信、受取確認UI、retry key、結果不明の扱いを最新の公式契約で確認した。
- **Sources Consulted**:
  - [Send push message](https://developers.line.biz/en/reference/messaging-api/#send-push-message)
  - [Send messages](https://developers.line.biz/en/docs/messaging-api/sending-messages/)
  - [Text character count](https://developers.line.biz/en/docs/messaging-api/text-character-count/)
  - [Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)
  - [Status codes](https://developers.line.biz/en/reference/messaging-api/#status-codes)
- **Findings**:
  - pushは単一 `to` と最大5 message objectsを受け付ける。本仕様は保存済みuser recipient一件だけを `to` に設定する。
  - text上限は5000 UTF-16 code unitsである。既存formatterの計数方法はこの契約に一致する。
  - 同一requestに複数message objectsを含めても、送信数はrecipient数で数える。
  - `X-Line-Retry-Key` はhexadecimal UUIDで、LINE側の管理期間は初回requestから24時間。同一keyの再requestが既受付なら409と `X-Line-Accepted-Request-Id` が返る。
  - 200はLINE受付であり、block、非友だち、存在しないuser等への端末到達を保証しない。
  - 5xxやtimeoutでも配信済みの可能性がある。4xxは明示的拒否として扱えるが、429は原因を生応答から公開せず `rate_limited` に縮約する。
- **Implications**:
  - SDK・HTTP clientの暗黙retryを無効化し、初回requestからoperation IDをretry keyとして付ける。
  - 200と正当な409は `succeeded`、明示4xxは `failed`、5xx・timeout・通信切断・解釈不能は `unknown` とする。
  - ownerによる同一operation再requestでもLINEを再呼び出さず、保存済み状態を返す。

### 受取確認のmessage表現

- **Context**: 整形済みtextを維持しながら「受け取りました」操作を付ける最小のLINE message表現を比較した。
- **Sources Consulted**:
  - [Message types](https://developers.line.biz/en/docs/messaging-api/message-types/)
  - [Template messages](https://developers.line.biz/en/reference/messaging-api/#template-messages)
  - [Postback action](https://developers.line.biz/en/reference/messaging-api/#postback-action)
  - [Use quick replies](https://developers.line.biz/en/docs/messaging-api/using-quick-reply/)
- **Findings**:
  - Buttons templateは1〜4 actionsを持て、短い固定textと一つのpostback actionを表せる。
  - postback `data` は最大300文字、labelはButtons templateで最大20文字である。
  - quick replyは新しいmessage等で消えるため、有効期限中に後から確認する操作には弱い。
  - Confirm templateは2 actions必須、Flex Messageは30KB上限や表示差を伴い、一操作のためには過剰である。
- **Implications**:
  - 受取確認なしは通常text一件、ありは通常textとButtons template一件を同じpush requestで送る。
  - Buttons templateは固定文言と `受け取りました` labelだけを持ち、postback `data` は `v1:delivery.received:<opaque>` とする。
  - 不透明値は256-bit random capabilityとし、生値を保存せずSHA-256 digestだけを配信記録へ保持する。改変はdigest不一致として拒否する。

### postback action統合

- **Context**: Webhook検証・利用者照合を再実装せず、配信固有の受取確認だけを所有する方法を確認した。
- **Sources Consulted**:
  - `backend/lineinteractions/parsing.py`
  - `backend/lineinteractions/types.py`
  - `backend/lineinteractions/registries.py`
  - `backend/lineinteractions/services.py`
  - `backend/linewebhooks/container.py`
  - [Postback event](https://developers.line.biz/en/reference/messaging-api/#postback-event)
- **Findings**:
  - parserは `v1:<action>:<opaque payload>` を受け付け、登録済みhandlerへ検証済みchannel、provider、identity、recipient、`webhookEventId`を渡す。
  - account directoryはaction時にenabled/friendshipを要求しない。関係が残るdisabled・`not_friend` recipientはhandlerへ到達し、unlink・delete済みrecipientはhandler前に除外される。
  - action pathはreply tokenをhandlerへ渡さず、自動replyを実行しない。interaction監査はaction名とsafe outcomeだけを保存し、payloadを保存しない。
  - webhook event台帳は同一 `webhookEventId` の再配送を抑止するが、別event IDでの再操作はdelivery側で冪等化する必要がある。
- **Implications**:
  - production composition rootへ `delivery.received` handlerを明示登録するだけに留める。
  - handlerはtoken digest、expiry、channel、recipient、receipt requested、delivery statusを一つの条件付き更新境界で検証する。
  - 初回の `confirmed_at` と `webhook_event_id` を保持し、再操作は `ActionNoChange`、不一致・期限切れ・failedは `ActionRejected` に縮約する。

### 監査保持とmigration

- **Context**: recipient/identityの物理削除後も配信snapshotを保持し、既存fixed recordを壊さない必要がある。
- **Sources Consulted**:
  - `backend/lineaccounts/unlink_services.py`
  - `backend/lineaccounts/repositories.py`
  - `backend/delivery/migrations/0001_initial.py`
- **Findings**:
  - 全unlinkはrecipientとidentityを物理削除し、owner slotをvacantへ戻す。
  - 配信記録をrecipient/identityの必須FKにするとunlink workflowと監査保持が競合する。
  - 既存recordにはowner/targetの帰属情報がなく、安全に完全backfillできない場合がある。
- **Implications**:
  - linked deliveryはowner principal slot、送信時owner identity UUID、channel UUIDとlabel、recipient UUID、active/enabled/friendshipを非FK snapshotとして保存する。LINE subjectとrecipient表示名は保存しない。
  - owner向けstatus認可はunlink後も残るsingleton OwnerAccount slotを使い、identity UUIDを認可キーにしない。同じownerが再連携してidentity UUIDが変わっても過去attemptを照会できる。
  - legacy fixed rowsは `target_mode=fixed_user` と確定済み状態を保持し、owner principal slotを1へbackfillする。active ownerが一意に存在する場合だけ送信時identity snapshotもbackfillし、解決不能rowは削除しない。
  - owner適格条件を別人物へ変更してsingleton slotを再割当てする運用は現行scope外であり、その場合は過去監査の隔離・移管・認可を再設計する。
  - 新規linked rowsだけはownerとtarget snapshotをDB check constraintで必須化する。

### Frontend境界と検証

- **Context**: 対象変更による確認無効化、秘密非露出、既存unknown flowを型安全に拡張する必要がある。
- **Sources Consulted**:
  - `frontend/src/DeliveryForm.tsx`
  - `frontend/src/deliveryState.ts`
  - `frontend/src/deliveryDto.ts`
  - `frontend/src/deliveryApi.ts`
  - `frontend/src/httpApi.ts`
- **Findings**:
  - API境界は `unknown` からexact-key parserで検証し、相対URL、session cookie、CSRF、401 callbackを共通clientが扱う。
  - reducerは送信中の連打、operation一致、network error後のstatus確認、新しいoperation開始を既に表現する。
- **Implications**:
  - editing snapshotへchannel、recipient、subject、body、receipt requestedを追加する。
  - channel変更はrecipientを必ずclearし、5つの入力軸の変更はいずれもpreview tokenを破棄する。previewから戻る操作は入力snapshotを保持する。
  - target list、preview summary、delivery statusの全応答をexact-key parserで検証し、LINE user ID、token、secret、receipt capabilityを型にも含めない。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| 既存delivery垂直拡張 | delivery appをaggregate ownerとし、upstreamのtyped portを利用する | 現行状態機械・API・UIを維持し、単一成果へ収束する | delivery appの責任が増えるためfile境界が必要 | 採用 |
| 新規linked-delivery app | fixed deliveryと別app・別recordを作る | 新旧の実装分離が明確 | 状態・監査・API・UIが二重化し、移行後も共有所有が残る | 不採用 |
| Webhook側へ受取確認を保存 | interaction appがdelivery recordを更新する | handler登録が短い | 配信状態の所有が分散し、下流固有責任が上流へ漏れる | 不採用 |
| 非同期worker導入 | pushとpostbackをqueueで処理する | 重い処理へ拡張可能 | 現行runtimeに存在せず、学習用単一配信には過剰 | 不採用 |

## Design Decisions

### Decision: delivery aggregateをownerとする垂直拡張

- **Context**: target選択、確認、送信、結果、受取確認が一つのowner体験と同じ配信記録へ収束する。
- **Alternatives Considered**:
  1. fixed deliveryとは別のDjango app・tableを作る
  2. interaction appへ受取確認状態を持たせる
- **Selected Approach**: 既存 `delivery` appが新しいlinked target mode、状態、監査、受取確認を所有し、upstreamはtyped directoryとaction dispatchだけを提供する。
- **Rationale**: 状態の単一所有を維持し、既存冪等性とunknown flowを再利用できる。
- **Trade-offs**: app内はdomain types、ports、services、adapters、composition、HTTPへ分割し、単一fileへの集中を避ける。
- **Follow-up**: task生成時にfile ownerとintegration orderを維持する。

### Decision: live target projectionとsnapshotを分離する

- **Context**: 送信前には最新状態が必要だが、送信後の監査はunlinkに影響されてはならない。
- **Alternatives Considered**:
  1. DeliveryAttemptからrecipient/channelへ必須FKを張る
  2. 送信後も毎回live joinして表示する
- **Selected Approach**: 一覧・preview・送信直前はdelivery target directoryからlive projectionを取得し、accept時に非FK snapshotをDeliveryAttemptへ保存する。
- **Rationale**: live validationと監査不変性を両立し、unlink sagaを変更しない。
- **Trade-offs**: label等の変更は過去recordへ反映されないが、送信時点監査として意図した挙動である。
- **Follow-up**: query budgetと存在非開示をrepository/APIテストで固定し、revision各軸、状態変更後の復元、UTC正規化、無関係なcredential／display name更新をadapter testで検証する。

### Decision: confirmationは短期署名snapshotとする

- **Context**: target、内容、receipt option、状態versionの差し替えを検出する。
- **Alternatives Considered**:
  1. DBへpreview recordを保存する
  2. message fingerprintだけを署名する
- **Selected Approach**: Django signingでowner principal slot、送信時owner identity、channel/recipient ID、target revision、message fingerprint、receipt option、receipt expiryを結び付け、短いmax ageを検証する。
- **Rationale**: 新しいpreview tableを作らず、既存パターンを拡張できる。PIIや本文はpayloadへ含めない。
- **Trade-offs**: target更新versionが変わると配信可否が同じでも再previewを要求する。誤送信防止側へ倒す。
- **Follow-up**: clock境界、tamper、expiry、他owner使用、状態が戻る変更をテストする。

### Decision: request fingerprintをtarget込みへ一般化する

- **Context**: contentだけの一意性では別targetの正当な送信を衝突させ、同じoperationでtarget差し替えを検出できない。
- **Alternatives Considered**:
  1. operation IDだけで重複排除する
  2. targetごとの別table lockを作る
- **Selected Approach**: owner principal、送信時owner identity、channel、recipient、message fingerprint、receipt optionからversioned request fingerprintを作る。operation IDとactive request fingerprintの既存二段階制約を維持する。
- **Rationale**: 一つの基礎能力として「外部作用requestの同一性」を一般化し、実装範囲は単一recipient配信に限定できる。
- **Trade-offs**: 同じ内容でもtarget/optionが違えば別requestとなる。
- **Follow-up**: operation reuse、並行同一request、別target同文面、終端CASをMySQLで検証する。

### Decision: receipt capability candidateとattempt作成を原子的に結ぶ

- **Context**: postbackには300文字以内の相関値が必要だが、operation/target IDや再利用可能な値を露出・記録したくない。
- **Alternatives Considered**:
  1. operation IDを署名してpostbackへ入れる
  2. operation IDを平文で入れる
- **Selected Approach**: receipt requestedのsubmitごとにaccept前のmemory上で256-bit random candidateとSHA-256 digestを生成する。repositoryへはdigestとexpiryだけを渡し、新規attemptのinsertと同じtransactionで保存する。`AttemptAccepted`の勝者だけが対応する生値をLINE requestへ渡し、既存operationまたはactive fingerprint競合の敗者は生値を直ちに破棄する。
- **Rationale**: receipt requested rowが作成時からdigest必須というDB制約、新規attemptだけが外部callを行う冪等性、capability生値非保存を一つのaccept境界で両立できる。
- **Trade-offs**: 競合requestでも未使用candidateのrandom生成は発生するが、外部作用でも永続的なcapability発行でもない。生値を失った後の同一LINE request再構成はできないが、本仕様は同一operationを自動再送しない。
- **Follow-up**: winner digestだけが保存され、loser raw candidateがgateway、DB、API、通常ログ、例外へ渡らないことをbarrier付き競合testとcanaryで検証する。

### Decision: 受取確認はButtons templateで付加する

- **Context**: 整形済みtextの5000 UTF-16上限を維持しつつ、期限中に利用者が操作できるボタンが必要である。
- **Alternatives Considered**:
  1. quick reply
  2. Confirm template
  3. Flex Message
- **Selected Approach**: 受取確認ありの場合だけ、通常textの後に一操作のButtons templateを同じpush requestへ追加する。
- **Rationale**: quick replyより残存性があり、Confirm/Flexより単純で、単一recipient・単一operationを維持する。
- **Trade-offs**: LINE上は二つのmessage objectsとして表示されるが、一つのpush requestと一つのrecipientである。
- **Follow-up**: SDK 3.25.0のmessage object形状、300文字上限、receiptなしでtemplateが付かないことをgateway testで固定する。

### Decision: build-vs-adoptと簡素化

- **Context**: 新しい依存や汎用frameworkを増やさず要件を満たす必要がある。
- **Alternatives Considered**:
  1. 新しいJWT/token library、queue、汎用workflow engineを導入する
  2. 既存Django signing、Python secrets/hashlib、LINE SDK、静的action registryを採用する
- **Selected Approach**: 既存・標準機能を採用し、新規外部依存を追加しない。target directory、delivery service、receipt handlerの三つの業務境界へ絞る。
- **Rationale**: 現行stackと運用に適合し、仮想的な複数recipientやcampaign抽象を持ち込まない。
- **Trade-offs**: 将来のbatch deliveryは別specで契約を再評価する。
- **Follow-up**: implementationで単一実装しかない不要なinterfaceを増やさない。

## Design-stage Spec Size Assessment

- **Policy verdict before exception**: `SPLIT_REQUIRED`。内訳の合計範囲は35〜42件で、上限が40件以上へ到達する。
- **Effective verdict**: `PASS (single-spec, user-approved size exception)`。2026-07-23にユーザーがサイズ超過リスクを受容し、単一Spec継続を明示した。
- **Projected executable tasks**: 35〜42件。schema/migration 4〜5、target directory/API 5〜6、confirmationとrequest identity 4〜5、push gatewayとdelivery state 6〜7、receipt action 5〜6、Frontend 6〜7、境界横断・security・performance検証 5〜6を想定する。
- **Independent responsibility seams**: 5（target projection、確認、配信aggregate/冪等性、選択channel gateway、receipt action）。
- **Independently deliverable outcomes**: 2（linked recipientへの選択配信、当該配信の受取確認）だが、後者は前者が作る同一DeliveryAttemptと同じstatus API/UIへ従属する。
- **External/stateful workflows**: 2（LINE push、検証済みpostback）。delivery statusとreceipt statusを直交させ、別々の列と条件付き更新が所有境界を分ける。
- **File ownership / dependency order**: `domain・migration → upstream target adapter → confirmation・repository → gateway・delivery service → receipt handler登録 → HTTP/Frontend → integration validation`。
- **Exception rationale**: 上限42件によるレビュー負荷と実装期間増加を受容しつつ、pushとreceiptを同じDeliveryAttempt、status API、Frontend体験、同時rolloutへ収束させる単一Specを維持する。タスク数を隠すための不自然な結合は行わない。
- **Accepted risks**: task graphレビューの負荷、境界横断integration taskの増加、実装中の手戻り、単一rolloutの検証時間増加。workstreamごとのfile owner、contract、依存順、integration checkpointをtasksへ明記して緩和する。
- **Revalidation threshold**: ユーザー承認例外は現在の5 seamsと2 external workflowsに限る。汎用複数recipient、非同期worker、別rolloutが必要なreceipt ledger、owner再割当て、または新しい独立成果が追加された場合は例外範囲外として `$kiro-discovery` へ戻す。

## Design Review Gate Record

- **Pass 1 findings**: 新規／変更fileの区別、componentとprimary fileの明示、active fingerprint競合時のcanonical processing stateへの収束、push直前の二回目target再検証、component内の要件ID range表記を修正した。
- **Interactive validation repair**: owner status scopeを削除されるidentity UUIDから安定したowner principal slotへ分離し、receipt candidate digestをattempt insertと同じtransactionで保存するaccept契約へ修正した。
- **Size exception record**: 内訳の正しい上限42件を記録し、policy上の`SPLIT_REQUIRED`と2026-07-23のユーザー承認例外を分離した。例外はサイズだけに適用し、境界・要件・実装可能性の欠落を免除しない。
- **Pass 2 mechanical result**:
  - requirements.mdから抽出した数値IDは71件、design draftのtraceability欠落は0件
  - `This Spec Owns`、`Out of Boundary`、`Allowed Dependencies`、`Revalidation Triggers` はすべて具体化済み
  - File Structure Planは新規／変更pathと全9 componentのprimary owner fileを明示し、境界外Modelのdelivery app直接参照を含まない
  - 未確定マーカーは0件、designは875行で1000行警告閾値未満
  - design-stage size evidenceは35〜42 task、5 seams、2 external workflows、integration order、ユーザー承認例外を記録済み
- **Judgment result**: requirements coverage、architecture readiness、boundary readiness、executabilityは修正後`PASS`。サイズだけはpolicy閾値を超える可能性があり、明示的ユーザー例外で継続する。
- **Final verdict**: `PASS (single-spec, user-approved size exception)`。Tasks phaseでは実数を隠さず、workstream別task graph sanity reviewを必須とする。

## Risks & Mitigations

- target再検証後から外部callまでの短い競合窓 — live targetをaccept直前に取得し、資格情報repositoryでもchannel activeを再確認する。外部通信をtransactionへ入れず、競合窓と期待結果を統合テストで明示する。
- 5xx・timeout後の二重送信 — `unknown`へ確定し、同じoperationも新しいoperationも自動送信しない。ownerへstatus確認導線だけを返す。
- receipt token漏洩 — 生値をDB・API・UI・通常ログへ保存せず、digest lookup後にchannel/recipient/expiry/statusを再照合する。
- receipt candidateのaccept競合 — candidate digestを新規attempt insertと同じtransactionで保存し、競合敗者の生値を永続化・送信しない。
- unlinkと監査保持の競合 — 配信targetを非FK snapshotにし、LINE subjectとrecipient表示名を保存しない。
- unlink後のowner照会 — 残存するsingleton owner slotを認可scope、削除されるidentity UUIDを監査snapshotに分離する。ownerを別人物へ再割当てする場合は監査認可を再設計する。
- processing中receiptと送信結果の競合 — receiptは `processing|succeeded|unknown` にだけ条件付き記録し、delivery finalizeはreceipt列を更新しない。明示的failed responseと有効receiptが同一attemptで成立しない外部契約をintegration testで監視する。
- Buttons templateのSDK差分 — pinned `line-bot-sdk==3.25.0` の生成requestをgateway unit testで検証し、新規依存を追加しない。
- 35〜42件のユーザー承認例外によるレビュー不安定化 — taskを不自然に結合せず、workstreamごとのfile owner、contract、依存順、integration checkpointをtasksへ引き継ぐ。

## References

- [LINE Messaging API: Send push message](https://developers.line.biz/en/reference/messaging-api/#send-push-message)
- [LINE Messaging API: Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)
- [LINE Messaging API: Text character count](https://developers.line.biz/en/docs/messaging-api/text-character-count/)
- [LINE Messaging API: Template messages](https://developers.line.biz/en/reference/messaging-api/#template-messages)
- [LINE Messaging API: Postback action](https://developers.line.biz/en/reference/messaging-api/#postback-action)
- [LINE Messaging API: Postback event](https://developers.line.biz/en/reference/messaging-api/#postback-event)
- [LINE Messaging API: Verify webhook signature](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
- `.kiro/steering/product.md`
- `.kiro/steering/tech.md`
- `.kiro/steering/structure.md`
- `.kiro/steering/spec-sizing.md`

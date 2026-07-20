# 調査・設計判断

## Summary

- **Feature**: `line-webhook-ingress`
- **Discovery Scope**: Complex Integration（外部公開 HTTP、署名検証、複数チャネル資格情報、並行重複排除、同期 handler 契約）
- **Key Findings**:
  - LINE の署名は受信本文そのものを対象とする HMAC-SHA256/Base64 であり、JSON 解析、文字列変換、改行・エスケープの正規化より前に raw bytes で検証する必要がある。
  - `webhookEventId` はイベントの一意識別子で、再送順は発生順と一致せず、`deliveryContext.isRedelivery` は重複判定キーではない。DB 一意制約を受付の線形化点にする。
  - LINE は Webhook オブジェクトへのフィールド追加、プロパティ順変更、列挙値追加を互換変更として扱う。入口は共通必須属性だけを検証する tolerant reader とし、未知種別を `unsupported` として受け付ける。
  - 既存 `CredentialRepository` は Webhook secret を安全に返すが、同一 snapshot の bot user ID を返さない。`linechannels` に Webhook 用途限定の typed repository を追加し、Ingress が ORM・暗号文へ直接依存することを避ける。
  - 2秒応答を同期処理で成立させるため、署名済み raw body 256 KiB、1 request 10 event、登録 handler 1件100 msという明示的な実行上限を設ける。

## Research Log

### LINE Webhook の真正性契約

- **Context**: 公開 URL、署名、`destination` のどこを信頼境界とするかを確定する必要がある。
- **Sources Consulted**:
  - [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/)
  - [Verify webhook signature](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
  - [Verify webhook URL](https://developers.line.biz/en/docs/messaging-api/verify-webhook-url/)
- **Findings**:
  - `x-line-signature` は channel secret を鍵、受信本文を入力とする HMAC-SHA256 digest の Base64 表現である。ヘッダー名は大文字・小文字を区別せず取得する。
  - request body を復号、deserialize、format、normalize してから署名検証してはならない。
  - payload の `destination` は受信した bot の user ID であり、`events` は複数件または疎通確認時の空配列になり得る。
- **Implications**:
  - 公開 UUID は候補選択にだけ使い、raw bytes の署名成功後に `destination` と保存済み bot user ID を照合する。
  - 空配列も同じ真正性・構造検証を通し、台帳作成なしで 200 を返す。
  - channel secret は redacted wrapper から HMAC adapter の直前だけで取り出し、DTO、例外、ログ、永続層へ渡さない。

### 再送、重複、応答時間

- **Context**: 並行到着時に handler を一度だけ呼び、LINE の再送を自動再実行にしない受付方式が必要である。
- **Sources Consulted**:
  - [Receiving messages](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
  - [Check webhook error statistics](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/)
- **Findings**:
  - 同一イベントは複数回配信され得るため、LINE は `webhookEventId` で重複排除するよう案内している。再送で変化するのは原則 `deliveryContext.isRedelivery` で、発生時刻は元の値を保持する。
  - 再送順序はイベント発生順と異なり得る。再送回数・間隔は非公開で、未受信イベントの完全な配信保証ではない。
  - 2 秒以内に応答できない要求は `request_timeout`、20x 以外は `error_status_code` として扱われる。LINE は非同期処理を推奨する。
- **Implications**:
  - `webhookEventId` のグローバル UNIQUE を受付の線形化点とし、`isRedelivery` と発生時刻は初回受付値から更新しない。
  - queue が対象外の本仕様では、台帳 commit 後の同期 handler を軽量処理に限定する。handler 失敗は台帳へ安全に分類して 200 を返し、重複では再実行しない。
  - 初回受付 commit 後、handler 完了前に process が停止した場合は `processing` が残る。自動 replay は行わず、後続仕様が queue/recovery を導入する場合の再検証事項とする。

### 将来互換な payload validation

- **Context**: 不正構造を要求全体で拒否しつつ、LINE の互換的な拡張を許容する必要がある。
- **Sources Consulted**:
  - [Development guidelines](https://developers.line.biz/en/docs/messaging-api/development-guidelines/)
  - [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/)
- **Findings**:
  - LINE はフィールド追加、プロパティ順変更、`type` を含む列挙値追加を予告なしの互換変更としている。
  - 共通イベント属性は `type`、ミリ秒 UNIX `timestamp`、ULID 形式の `webhookEventId`、`deliveryContext.isRedelivery` である。
- **Implications**:
  - top-level object、`destination`、`events` array と共通イベント属性だけを厳格に検証する。未知フィールドは保持し、未知 enum/event type は要求エラーにしない。
  - `line-bot-sdk` の型付き event parser は入口契約に使わない。raw bytes 署名検証は Python 標準 `hmac`、`hashlib`、`base64`、JSON 基本検証は標準 `json` で構成する。
  - handler に渡す event data は検証後に immutable JSON value へ変換し、raw body や検証前 object と識別する。

### Django/DRF の raw body と transaction

- **Context**: DRF parser が署名検証順序を崩さず、MySQL の並行 insert を安全に収束させる必要がある。
- **Sources Consulted**:
  - [Django HttpRequest.body](https://docs.djangoproject.com/en/6.0/ref/request-response/#django.http.HttpRequest.body)
  - [DRF Requests](https://www.django-rest-framework.org/api-guide/requests/)
  - [Django unique field](https://docs.djangoproject.com/en/6.0/ref/models/fields/#unique)
  - [Django transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/)
- **Findings**:
  - `HttpRequest.body` は raw bytes を返す。DRF `request.data` は parser 済みで、不正 JSON では署名検証前に例外化し得る。
  - `unique=True` は DB 一意制約と index を作る。`atomic()` 内で DB 例外を握り潰さず、内側 transaction の外で `IntegrityError` を分類するのが Django の推奨である。
  - 既存 `delivery.DeliveryService` も一意制約、短い受付 transaction、transaction 外の外部作用、条件付き完了更新を採用している。
- **Implications**:
  - Webhook View は parser/authentication/permission を明示的に空とし、`request.data` を参照せず raw body を一度だけ取得する。
  - 全 event の検証後、短い batch transaction で新規受付・重複判定を確定し、commit 後だけ handler を呼ぶ。
  - MySQL 実接続の `TransactionTestCase` と独立 connection で、同時 insert が新規一件へ収束することを検証する。

### `line-channel-foundation` との統合契約

- **Context**: URL の public UUID から、有効状態、bot user ID、channel secret を整合した用途限定契約で取得する必要がある。
- **Sources Consulted**:
  - `.kiro/specs/line-channel-foundation/design.md`
  - `backend/linechannels/types.py`
  - `backend/linechannels/repositories.py`
  - `backend/linechannels/container.py`
- **Findings**:
  - `CredentialRepository.get_channel_secret()` は active/unknown/incomplete/unreadable を安全に分類し、channel secret 列だけを復号する。
  - 既存 `LineChannelDirectory` は account linking 用の非秘密 projection で、意図的に bot user ID を返さない。
  - directory と secret を別 read にすると、その間の無効化・bot ID/credential pair 更新で snapshot が分裂し得る。
- **Implications**:
  - 既存 directory を拡張せず、`WebhookCredentialRepository.get()` を `linechannels` に追加する。1 read で active、bot user ID、secret ciphertext を取得し、secret だけを復号した `WebhookChannelAvailable` を返す。
  - Ingress は `linechannels.models`、暗号文、Cipher に直接依存しない。既存 channel/credential の ownership と schema は変更しない。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| 独立 Django app + purpose-specific ports | `linewebhooks` が HTTP、検証、受付台帳、handler port を所有 | 既存構造と整合し、下流 handler と資格情報基盤を疎結合にできる | app 間 contract が増える | 採用 |
| `linechannels` app に Webhook を実装 | 資格情報と受付を同じ app に置く | channel read は簡単 | 外部 HTTP、イベント台帳、下流 dispatch が資格情報所有へ漏れる | 不採用 |
| LINE SDK parser を入口に採用 | 署名と event mapping を SDK に委譲 | 実装量が少ない | raw bytes 順序と未知 event/enum の tolerant reader 契約が SDK 型へ依存する | 不採用 |
| queue/worker による非同期処理 | 受付後すぐ 200、handler を非同期実行 | latency と recovery に強い | 現行 runtime と明示スコープ外、独立運用責務を追加する | 将来候補 |

## Design Decisions

### Decision: 署名検証と payload validation を分離する

- **Context**: 検証前 JSON を認証判断へ混入させず、未知 event を許容する必要がある。
- **Alternatives Considered**:
  1. SDK parser で署名と event mapping を一括実行する。
  2. DRF serializer/parser で JSON を先に構造化する。
- **Selected Approach**: raw bytes の HMAC adapter、署名後の tolerant payload validator、verified envelope factory を明示的に分離する。
- **Rationale**: 信頼レベルの遷移が明確で、署名順序と将来互換性を個別に検証できる。
- **Trade-offs**: 共通 event 属性の validation を所有するが、暗号 primitive と JSON parser は標準ライブラリを採用する。
- **Follow-up**: LINE が署名方式または共通属性を変更した場合に設計を再検証する。

### Decision: Webhook 用資格情報を単一 typed projection で取得する

- **Context**: bot user ID と channel secret を別 query で読む snapshot race を避ける必要がある。
- **Alternatives Considered**:
  1. account-linking 用 directory を広げる。
  2. Ingress が `linechannels.models` と Cipher を直接読む。
  3. directory と既存 secret repository を同一 transaction で呼ぶ。
- **Selected Approach**: `linechannels` に Webhook 用 `WebhookCredentialRepository` を追加し、bot user ID と secret を1つの用途限定結果で返す。
- **Rationale**: 暗号化 ownership と snapshot 整合性を upstream に保ち、既存 projection の最小権限を崩さない。
- **Trade-offs**: upstream contract を1つ追加するが、既存 contract/schema は変更しない。
- **Follow-up**: bot user ID、active、credential pair の更新規則が変わる場合は Ingress を再検証する。

### Decision: 台帳 commit を handler 実行権の線形化点にする

- **Context**: 並行再送で handler を重複実行せず、DB transaction 中に下流作用を行わない必要がある。
- **Alternatives Considered**:
  1. handler 成功後に受付行を作る — 並行重複を止められず、失敗も監査できない。
  2. handler を受付 transaction 内で呼ぶ — lock 長期化と rollback 後の作用重複を招く。
- **Selected Approach**: `webhook_event_id` UNIQUE の受付行を batch transaction で commit し、新規 `processing` 行だけを transaction 外で handler へ渡し、結果を条件付き更新する。
- **Rationale**: at-most-once dispatch と短い DB transaction を両立し、既存 delivery pattern と整合する。
- **Trade-offs**: commit 後の process 停止では `processing` が残る。重複再送から自動 replay しないことを明示的な保証とする。
- **Follow-up**: recovery/queue を導入する場合は ownership、再実行条件、外部作用の冪等性を別仕様で設計する。

### Decision: event payload は ephemeral verified envelope にだけ保持する

- **Context**: 下流処理は event 固有データを必要とするが、Ingress 監査はユーザーデータを保存してはならない。
- **Alternatives Considered**:
  1. raw payload を台帳へ JSON 保存する。
  2. event type ごとの DTO を Ingress が所有する。
- **Selected Approach**: 検証後 event object を immutable JSON として envelope に載せ、同期 handler 呼出し中だけ保持する。台帳は共通メタデータと分類だけを保存する。
- **Rationale**: 下流拡張性を保ちながらデータ最小化と boundary separation を満たす。
- **Trade-offs**: process 停止後に payload を復元・再処理できない。
- **Follow-up**: 下流仕様が固有データを保存する場合は目的、保持期間、削除方法を所有する。

### Decision: 必要最小限の同期 handler registry を採用する

- **Context**: queue は対象外だが、後続仕様が署名を再検証せず event type ごとの処理を登録する seam が必要である。
- **Alternatives Considered**:
  1. Ingress service に follow/message/postback 分岐を埋め込む。
  2. 汎用 event bus を新設する。
- **Selected Approach**: event type から単一の `VerifiedEventHandler` を解決する in-process registry を定義し、未登録 type は `unsupported` とする。
- **Rationale**: 下流固有ロジックを含めず、現行単一 process に必要な最小 interface だけを提供する。
- **Trade-offs**: handler は同期で、標準 container で1 event 100 ms以下かつ外部 I/O なしという登録条件を守る必要がある。
- **Follow-up**: handler 登録、外部通信、状態更新を追加する各下流仕様で、最大10 event の latency と failure contract を再検証する。

### Decision: 同期受付の入力上限と実行予算を固定する

- **Context**: 8.1 の2秒応答を、event 数無制限かつ同期 handler 逐次実行のまま保証できない。
- **Alternatives Considered**:
  1. queue/worker を導入して handler を応答後へ分離する。
  2. event 数を無制限のまま handler の軽量性だけに依存する。
  3. request と handler に測定可能な上限を設ける。
- **Selected Approach**: 署名済み raw body 256 KiB以下、events 0〜10件、登録 handler 1件100 ms以下を valid request の運用 contract とする。Ingress 内部目標は1,500 ms、外部契約は2,000 msとする。
- **Rationale**: 新しい runtime を追加せず、3.6 の複数 event と8.1の2秒契約を測定可能な条件で両立できる。上限判定は署名後・JSON parse前または共通 payload validation 内で行い、内容を認証根拠にしない。
- **Trade-offs**: 上限超過は安全な payload rejection となる。将来の外部 I/O handler はこの契約に収まらない可能性が高い。
- **Follow-up**: 1件、5件、10件の性能 test、1,500 ms warning、2,000 ms deadline audit を固定し、上限や外部 I/Oを変える下流仕様では queue/timeout を再設計する。

## Synthesis Outcomes

- **Generalization**: 空 event、未知 event、既知 event、重複は、すべて「検証済み request を event 単位の受付結果へ分類する」同じ ingress use case として扱う。event 固有処理は registry の外側へ分離する。
- **Build vs. Adopt**: HMAC/constant-time comparison、JSON parsing、DB UNIQUE/transaction は標準機能を採用する。SDK 固有 event mapping、custom crypto、汎用 event bus は採用しない。
- **Simplification**: request audit table、payload archive、queue、retry scheduler、複数 handler fan-out、event type 固有 serializer を追加しない。受付台帳1 model、application service1境界、同期 registry1契約に限定する。

## Risks & Mitigations

- raw body が parser/middleware に先読みされる — View の parser を空にし、`request.data` 非参照と raw bytes 検証順序を HTTP test で固定する。
- channel metadata と secret の split snapshot — Webhook 用 purpose-specific repository の単一 read/decrypt 結果で回避する。
- 並行 insert による二重 handler — DB UNIQUE、短い transaction、IntegrityError の外側分類、新規行だけの dispatch で防ぐ。
- handler 完了前の process 停止 — `processing` を保持し、自動再実行しない。運用で識別できる安全な状態とする。
- 多数 event または遅い handler による 2 秒超過 — raw body 256 KiB、10 event、handler 100 msの上限、1,500 ms warning、2,000 ms deadline audit、上限 batch のperformance testで検出する。
- event data の accidental persistence/log — model field whitelist、immutable envelope の safe repr、canary security test、raw exception 非記録で防ぐ。
- 未知 enum/event で要求全体を拒否 — 共通属性だけの tolerant validator と未登録 type の `unsupported` 分類で防ぐ。

## Design-stage Spec Size Assessment

- **Policy verdict**: `SPLIT_REQUIRED`。厳密な1〜3時間単位へ分けると18〜22件を見込み、`linechannels` の用途限定 projection と、それを利用する `linewebhooks` 受付を同一 Spec で変更するため、`.kiro/steering/spec-sizing.md` の上流基盤拡張基準にも該当し得る。
- **Recorded exception**: 2026-07-20、ユーザーがタスク生成直前の再分割コストと多少のサイズ超過を理解したうえで、`line-webhook-ingress` を単一 Spec のまま継続することを明示的に承認した。
- **Effective decision**: `CONTINUE (user-approved single-spec exception)`。
- **Independent responsibility seams**: 実装 owner は `linechannels` の用途限定 read projection と `linewebhooks` の受付境界に分かれるが、projection はこの Ingress だけの integration prerequisite であり、独立 API・schema・workflow を追加しない。
- **Independent workflows/state machines**: 外部受付 workflow 1件、receipt state machine 1件。follow/unfollow projection、message/postback 解釈、reply、queue/recovery は別仕様のまま維持する。
- **Continuation rationale**: 既に3 Specへ分割済みで、本仕様をさらに分けると署名・destination・一意受付の統合保証を複数承認単位へまたがせる。利用者はこの調整コストを避ける判断をした。
- **Risks and mitigations**: review量と cross-app coordination が増えるため、タスクは各 component/file owner と integration task を分離し、1〜3時間粒度、独立 task-graph review、最終 security/performance gateを維持する。件数を隠すための結合や検証省略は行わない。

## References

- [LINE Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/)
- [Verify webhook signature](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
- [Verify webhook URL](https://developers.line.biz/en/docs/messaging-api/verify-webhook-url/)
- [Receiving messages](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
- [Check webhook error statistics](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/)
- [Development guidelines](https://developers.line.biz/en/docs/messaging-api/development-guidelines/)
- [Django HttpRequest.body](https://docs.djangoproject.com/en/6.0/ref/request-response/#django.http.HttpRequest.body)
- [Django transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/)
- [Django unique field](https://docs.djangoproject.com/en/6.0/ref/models/fields/#unique)
- [DRF Requests](https://www.django-rest-framework.org/api-guide/requests/)

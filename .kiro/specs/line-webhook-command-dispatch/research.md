# 調査・設計判断ログ

## Summary

- **Feature**: `line-webhook-command-dispatch`
- **Discovery Scope**: Complex Integration（既存 Webhook 受付への外部 reply 作用追加）
- **Key Findings**:
  - 既存 `line-webhook-ingress` は登録 handler を「1 event 100 ms 以下・外部 I/O なし」に限定している。本仕様の同期 LINE reply は、その再検証トリガーに明示された上流契約変更である。
  - 現行 `VerifiedEventHandler.handle(event)` と `VerifiedWebhookEvent` は request 全体の deadline／残予算を渡さない。さらにViewはservice構築後の`ingest()`内で計時を始めるため、最大10イベント時に Requirement 7.1〜7.3 の「request受信起点の期限」と「残り時間で開始可否」を実装できない。
  - event 正規化・利用者照合・allowlist、deadline-aware な ingress 実行基盤、一回限りの reply 外部作用は独立して提供・変更・レビューでき、設計時点の実行可能タスク見積りは22〜26件となる。

## Research Log

### 既存 Webhook handler 契約と実装可能性

- **Context**: Requirement 7 は、最大10イベントを含む request でも2秒以内に HTTP 200を返し、reply 開始前に request 全体の残り時間を判断することを要求する。
- **Sources Consulted**:
  - `.kiro/specs/line-webhook-ingress/design.md`
  - `backend/linewebhooks/types.py`
  - `backend/linewebhooks/handlers.py`
  - `backend/linewebhooks/services.py`
  - `backend/linewebhooks/tests/test_performance.py`
- **Findings**:
  - Ingress 設計は handler を1件100 ms以下・外部 I/O なしに限定し、外部通信、event 上限、応答 deadline の変更を再検証トリガーとしている。
  - 現行 handler port は `handle(event)` だけで、request 開始時刻、絶対 deadline、残予算、後続 event 数を受け取らない。現行Viewはrequestごとにserviceを構築した後で`ingest()`を呼び、`ingest()`冒頭から計時するためcomposition時間も期限外になる。
  - Ingress は最大10件を payload 順に同期実行し、2秒超過を事後監査するだけで、外部要求を開始前に止める budget 制御を持たない。
  - receipt の一意 insert が handler 実行権を線形化し、finalize 失敗後も再送では handler を再実行しない。この性質は reply の二重送信防止に再利用できるが、deadline-aware contract の追加が先に必要である。
- **Implications**:
  - View入口のrequest clock、handler context、Ingress dispatch、期限skip専用receipt結果、performance contract、既存 friendship handler の回帰検証を上流成果として再設計する必要がある。
  - 外部 reply を追加する下流実装だけで Requirement 7 を満たしたことにはできない。

### 既存 app 境界と統合候補

- **Context**: message／postback の意味解釈、連携済み利用者照合、資格情報取得、安全な監査をどこへ配置できるかを確認した。
- **Sources Consulted**:
  - `.kiro/steering/structure.md`
  - `backend/linefriendships/`
  - `backend/lineaccounts/friendship_repositories.py`
  - `backend/linechannels/repositories.py`
  - `backend/linechannels/container.py`
  - `backend/delivery/gateway.py`
- **Findings**:
  - `linefriendships` は downstream app が `VerifiedEventHandler` を実装し、account-owned repository adapter と channel directory を composition root で合成する先行例である。
  - `linechannels.CredentialRepository.get_access_token(channel_public_id)` は active channel だけの復号済み typed token を返し、別チャネルや環境固定値への fallback を行わない。
  - `lineaccounts` の owner／identity／recipient は完全一致照合に必要なデータを持つが、interaction 用 read port は未定義である。設計では要件に列挙された既存 recipient の存在までを照合条件とし、`enabled` と friendship state は別責任として追加しない。
  - 既存 `delivery.LINEGateway` は push と固定環境資格情報向けで、reply token、一回利用、request 全体の deadline を扱わないため再利用対象ではない。
- **Implications**:
  - action dispatch は独立 downstream app、account 照合は account-owned adapter、access token は既存 typed credential port という依存方向が候補になる。
  - reply gateway は push gateway と分離する必要がある。

### LINE Messaging API の外部契約

- **Context**: reply token、reply API、postback data、文字数、Webhook 応答期限が設計判断へ与える制約を確認した。
- **Sources Consulted**:
  - [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/)
  - [Receive messages](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
  - [Character counting in a text](https://developers.line.biz/en/docs/messaging-api/text-character-count/)
  - [Webhook error statistics](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/)
  - [LINE Bot SDK for Python](https://github.com/line/line-bot-sdk-python)
- **Findings**:
  - reply token は一回だけ使用でき、Webhook 受信後できるだけ早く使う必要がある。再送 Webhook でも同じ token が含まれ、元 request で使用済みなら再利用できない。
  - reply は `POST https://api.line.me/v2/bot/message/reply` へ channel access token、reply token、最大5 messages を送る契約である。本仕様はそのうち text 1件だけへ狭める。
  - postback action の `data` は最大300文字、text message は最大5,000文字であり、Messaging API の通常の文字数は UTF-16 code unit で数える。
  - LINE は Webhook 応答が2秒を超えた場合を `request_timeout` として扱う。Webhook redelivery は同一 `webhookEventId` を再送し得るため重複排除が必要である。
- **Implications**:
  - transport retry を無効にし、request 開始後の timeout／通信中断／応答解釈不能を `unknown` として token を再利用しない設計が必要である。
  - Python の `len()` ではなく UTF-16 code unit による境界検証を契約化する必要がある。
  - 2秒は個別 reply timeout ではなく request 全体の絶対 deadline として上流から伝播させる必要がある。

### 設計で固定する契約

- **Context**: 設計レビューで未確定だった wire format、固定値、照合条件、監査 owner を、要件の安全境界を狭める具体化として固定した。
- **Sources Consulted**:
  - `.kiro/specs/line-webhook-command-dispatch/requirements.md`
  - `.kiro/specs/line-webhook-command-dispatch/brief.md`
  - `.kiro/steering/spec-sizing.md`
- **Findings**:
  - postback data は `v1:<action-name>:<opaque-payload>` の一形式だけを受理する。最初の2個の `:` だけを区切りとし、payload は復号・JSON parse・URL decode せず残り全体を不透明値として渡す。action 名は safe registry identifier と完全一致させる。
  - 初期固定 command は `/ping`、固定 reply は `pong`、監査識別子は `connectivity_ping_v1` とする。受信 text 自体は監査しない。
  - 操作許可は要件どおり active owner、同一 provider の identity、対象 channel の既存 recipient の存在で決める。`enabled` と friendship state は配信・友だち状態の別責任であり、この照合条件へ追加しない。
  - `lineinteractions` app が PII-free interaction audit を所有する。監査保存失敗は handler failure とし、既存 receipt の at-most-once 実行権により reply／action を再実行しない。
  - Requirement 5.7 の収束先は「初回 receipt の保存済み状態」と解釈する。finalize 失敗で `processing` が残っても再実行せず、外部作用の成否を推測しない。
- **Implications**:
  - wire format、固定値、監査 schema の変更は後続 `linked-recipient-delivery` を含む再検証トリガーになる。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
| --- | --- | --- | --- | --- |
| 単一Spec・内部3境界 | deadline、dispatch、reply を別 component／file／task boundary として一つの Spec で順次実装 | ユーザーが望む一括成果を保ち、責任ごとのレビューも可能 | 22〜26タスクと広い回帰範囲 | 現行サイズ方針の通常範囲として採用 |
| 上流 budget + action dispatch + reply の3 Spec | deadline-aware ingress を先行し、外部 I/O なしの action dispatch と reply を段階追加 | 契約変更、純粋 dispatch、外部作用を独立検証できる | Spec 間の契約管理と進行 overhead | 原則案だが今回は不採用 |
| queue／worker 化 | HTTP 応答と業務処理を非同期分離 | 2秒応答を守りやすい | reply token の即時性、追加基盤、現行 scope と不整合 | 対象外 |

## Design Decisions

### Decision: レビュー可能性を基準に単一Specを継続する

- **Context**: 旧size gateでは20件以上を原則 `SPLIT_REQUIRED` としていたが、実際の目的は大規模Specでreview修正が収束しないことの防止であり、23件規模は十分レビュー可能だとユーザーが明示した。
- **Alternatives Considered**:
  1. 現行 Spec のまま設計する — 既存 brief の9〜11タスク見積りを採用する。
  2. 上流実行予算、action dispatch、command reply へ分割する。
- **Selected Approach**: 更新後のサイズ方針に基づき、通常の `PASS (single-spec)` として継続する。設計・file plan・task boundary は3責任を分離し、依存順に実装する。
- **Rationale**: 生成された23タスクは通常の単一Spec候補に収まり、3責任は一つの利用者成果へ収束する。task planは1回の局所修正後に独立sanity reviewで`PASS`となり、bounded review内で安定した。
- **Trade-offs**: 1回の design／task review が広くなり、Ingress 回帰と reply 安全性を同時に確認する必要がある。一方、Spec 間の metadata／contract 同期は不要になる。
- **Follow-up**: task 生成では `deadline contract → dispatch → reply/audit → signed integration` の順序を崩さず、境界横断タスクを隠して件数を減らさない。

### Decision: deadline を reply gateway 内の固定 timeout だけで補わない

- **Context**: 最大10イベントの同期 dispatch では、固定 per-call timeout は request 全体の残予算を表現しない。
- **Alternatives Considered**:
  1. reply gateway に固定 timeout を設定する。
  2. Ingress が絶対 deadline を handler context へ渡し、開始可否と transport timeout を同じ budget から決める。
- **Selected Approach**: 2を本Spec内の先行ワークストリームとして実装する。deadline起点は`ingest()`内ではなくWebhook View入口とし、cached service取得を含むHTTP境界全体へ伝播する。
- **Rationale**: Requirement 7.1〜7.3 を request 全体で検証でき、composition、後続 event、receipt finalize、HTTP response用の余裕を明示できる。
- **Trade-offs**: `VerifiedEventHandler` の互換性と既存 handler の再検証が必要になる。
- **Follow-up**: handler context、外部要求開始 cutoff、transport timeout、receipt finalize reserve を設計・性能テストで固定する。

### Decision: 残 dispatch 数を含む per-event budget を Ingress が配分する

- **Context**: 全 event に同じ absolute deadline だけを渡すと、先行 reply が時間を使った後の parser、audit、receipt finalize、HTTP response の予算を予約できない。
- **Alternatives Considered**:
  1. 各 reply に固定600 msを与え、最後に固定200 msだけを残す。
  2. Ingress が現在位置と残 dispatch 数から将来の local completion を先に予約し、deadline-managed external handler にだけ external I/O cutoff を渡す。
- **Selected Approach**: 2を採用する。各 dispatch に local handler 100 ms、receipt finalize 20 ms、request に HTTP response reserve 200 msを予約し、残余を最大600 msの external budget とする。100 msのcancellation／close reserveと最低200 msのtransport watchdogを確保できない300 ms未満ではreplyを開始しない。
- **Rationale**: 最大10件の後続 no-op／audit／finalize を budget 上に残し、reply 成功数より2秒応答を優先できる。
- **Trade-offs**: 遅い先行処理があると後続 reply は `deadline_exceeded` になる。同期処理の強制中断はできないため、local handler 100 ms contract と container 性能テストが前提になる。
- **Follow-up**: registry execution profile、dispatch-closed latch、fake clockによる cutoff testを実装する。

### Decision: deadline skipを専用receipt／audit結果として確定する

- **Context**: local completion reserve不足時にhandlerを開始しないだけでは、既存`handler_failed`から期限skipを区別できず、InteractionAuditも存在しない。
- **Selected Approach**: receipt failure codeとSafeWebhookAuditへevent-scopedな`dispatch_deadline_exceeded`を追加する。未dispatch eventではInteractionAuditを作らず、receiptとIngress auditでhandler未開始、reply未実行、期限超過を区別する。
- **Rationale**: raw eventを再解釈せずRequirement 6.1・7.3の観測可能性を満たし、receiptのat-most-once実行権を維持できる。
- **Trade-offs**: `linewebhooks` model／migration／repository／audit契約が広がる。既存`handler_failed`とのCHECKとrollback条件を再検証する必要がある。
- **Follow-up**: dispatch-closed以降の全created receipt、finalize failure、redelivery非再実行をmodel／repository／service／concurrency testで検証する。

### Decision: reply transport はHTTPX AsyncClientとtotal watchdogを採用する

- **Context**: 一回限りの reply を request 全体の残予算内で開始し、自動 retry を禁止する必要がある。
- **Alternatives Considered**:
  1. 既存 `line-bot-sdk` の generated client を使う。
  2. 既存依存の HTTPX で reply endpoint 一つを typed gateway に閉じ込める。
- **Selected Approach**: sync-facing gateway portの内部でHTTPX 0.28.1 `AsyncClient`をprivate async routineとして実行し、client lifecycle全体を`asyncio.timeout()`で囲む。retry transportは追加せず、phase timeoutもwatchdog以下に設定する。
- **Rationale**: HTTPXのconnect/read/write/pool timeoutは各phaseの待機上限でありrequest全体のwall-clock上限ではない。cancellable total watchdogならtimeout時にtaskを残さず`unknown`へ収束し、SDK objectや例外をdomainへ漏らさない。
- **Trade-offs**: requestごとに短命event loop／AsyncClientを作るため小さなoverheadがあり、reply request／responseの最小schema validationも自前で保守する。最大500 ms watchdogと100 ms cancellation／close reserveで吸収する。
- **Follow-up**: official reply contract、async `MockTransport`、controlled delayed transport、container内loopback slow serverでaccepted／rejected／unknown／単一call／wall-clock／task cleanupを検証する。

## Synthesis Outcomes

- **Generalization**: text command と postback action は「検証済み interaction を有限 registry へ完全一致で渡す」共通 pipeline として一般化できる。ただし reply は command 固有の外部作用として分離する。
- **Build vs. Adopt**: 既存 HTTPX 0.28.1 と LINE 公式 reply HTTP 契約を採用する。独自 transport、retry、汎用 Messaging API client は構築しない。
- **Simplification**: queue、worker、汎用 chatbot、動的 dispatch、action payload の業務検証は導入しない。単一Spec内でも3責任の component／file 境界だけを維持し、追加の汎用 event bus は作らない。

## Spec Size Assessment

- **Verdict**: `PASS (single-spec)`
- **Projected executable tasks**: 22〜26件（生成結果23件）
- **Independent seams**:
  1. `line-webhook-ingress` の deadline-aware handler 実行基盤と最大10イベント性能再検証
  2. message／postback 正規化、連携済み利用者照合、allowlist、型付き action handler 契約
  3. channel 別 reply、一回利用、`accepted`／`rejected`／`unknown`、reply 監査
- **Evidence**:
  - 上流 deadline／handler 契約変更と回帰: 4〜5タスク
  - parser、利用者照合、command/action registry: 6〜7タスク
  - reply gateway、timeout、結果状態・監査: 5〜6タスク
  - migration、composition、競合・再送、security、performance、signed integration: 7〜8タスク
- **Rationale**: 23件は現行方針の通常範囲であり、複数境界だけでは分割しない。3ワークストリームのowner、依存順、統合・検証タスクが明示され、task reviewがbounded pass内で収束したため単一Specを維持する。
- **Required internal workstreams**:
  1. handler execution budget
  2. interaction dispatch
  3. command reply and audit

## Design Review Gate

- **Latest result**: `REVISION APPLIED — REVALIDATION PENDING`（2026-07-22のvalidationはNO-GO）
- **Critical finding 1 remediation**: View入口でrequest clockを採取し、startupで構築・検証したcached compositionの取得を含めてabsolute deadlineを`ingest()`へ渡す契約へ変更した。
- **Critical finding 2 remediation**: handler未開始のdeadline skipを`dispatch_deadline_exceeded` receipt／safe auditへ確定し、generic `handler_failed`から区別した。
- **Critical finding 3 remediation**: sync HTTPX phase timeout依存をやめ、private async routine全体のcancellable watchdog、100 ms cleanup reserve、controlled slow transportのwall-clock testを設計した。
- **Additional consistency repair**: postback action handlerを別枠100 msとせずinteraction local portion全体へ内包し、`brief.md`のsize assessmentを22〜26タスク／3 seamへ同期した。
- **Next gate**: 修正後の`$kiro-validate-design line-webhook-command-dispatch`でGO判定を得るまでtasks生成へ進まない。

## Risks & Mitigations

- deadline を下流だけで扱うとcompositionや10イベント処理で2秒を超える — View入口のabsolute deadline、残件数を含むhandler context、cached startup composition、performance contractを確定する。
- HTTPX phase timeoutだけではtotal wall-clockを制限できない — private async routine全体をcancellable watchdogで囲み、slow loopback transportを含むView起点の実測を必須にする。
- deadlineで未dispatchとなったeventがgeneric failureへ埋没する — receipt／safe auditの`dispatch_deadline_exceeded`でhandler未開始とreply未実行を区別する。
- reply 成功後の監査／receipt finalize 失敗で結果を再現できない — 外部作用後は再送せず、保存済み receipt と安全な unknown／processing を区別する契約を明示する。
- postback grammar の曖昧さが未知 action の誤解釈につながる — versioned な一意文法と重複 field 拒否を requirements で定義する。
- PII／token が例外や監査へ漏れる — sensitive value 型、safe `repr`、whitelist audit、canary test を3ワークストリーム共通の必須条件とする。

## References

- [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/) — reply token、reply endpoint、postback action の公式契約
- [Receive messages](https://developers.line.biz/en/docs/messaging-api/receiving-messages/) — Webhook redelivery と `webhookEventId` の重複排除
- [Character counting in a text](https://developers.line.biz/en/docs/messaging-api/text-character-count/) — UTF-16 code unit による文字数計算
- [Webhook error statistics](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/) — 2秒応答と `request_timeout`
- [LINE Bot SDK for Python](https://github.com/line/line-bot-sdk-python) — Python SDK の reply API と HTTP metadata 取得
- `.kiro/specs/line-webhook-ingress/design.md` — 現行 handler、deadline、receipt 契約
- `.kiro/steering/spec-sizing.md` — 設計段階の分割判定基準

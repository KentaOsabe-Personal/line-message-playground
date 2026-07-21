# 調査・設計判断

## Summary

- **Feature**: `line-friendship-sync`
- **Discovery Scope**: Complex Integration（既存拡張だが、公開Webhook、個人識別情報、同時更新、100ミリ秒目標を含むため完全調査を適用）
- **Key Findings**:
  - 既存の`line-webhook-ingress`は署名、destination、共通イベント属性、最大10件、`webhookEventId`重複を検証し、immutableな`VerifiedWebhookEvent`をreceipt transaction外の同期handlerへ渡す。`follow`／`unfollow`のsource固有検証と状態投影だけを新しいhandlerへ閉じ込められる。
  - `DeliveryRecipient`は`enabled`と`friendship_state`を既に分離し、`identity × line_channel`一意制約を持つが、最後に適用したイベントの順序キーを保持しない。`created_at`を登録境界とし、nullableな最終イベント時刻・IDを追加する必要がある。
  - Djangoの`transaction.atomic()`とMySQL InnoDBの行ロックを採用し、既存account mutationと同じowner→recipientのロック順で、状態・順序情報・安全な同期監査を原子的に確定できる。新規外部依存やLINEへの照会は不要である。
  - `line-account-linking`はprovider updateをlegacy channelへのbackfillとして設計したが、現行serviceは設定済みproviderの異値変更も許す。providerをset-onceに補強し、Webhook署名検証後に照合先providerが切り替わるTOCTOUを防ぐ。
  - 100ミリ秒は`line-webhook-ingress`が登録handlerへ課す標準性能budgetであり、意図的に事前保持されたDB lockの強制中断時間ではない。非事前競合の性能試験と、競合時の収束試験を分離する。

## Research Log

### 既存Webhook受付とhandler契約

- **Context**: 検証済みイベントからどの情報を受け取り、どこまでを本仕様が再検証するかを確定する必要がある。
- **Sources Consulted**:
  - `backend/linewebhooks/types.py`
  - `backend/linewebhooks/verification.py`
  - `backend/linewebhooks/services.py`
  - `backend/linewebhooks/handlers.py`
  - `backend/linewebhooks/container.py`
- **Findings**:
  - ingressは署名検証後、`webhookEventId`、`type`、UNIXミリ秒`timestamp`、`deliveryContext.isRedelivery`を検証し、raw eventを`FrozenJsonObject`として保持する。
  - handlerは`VerifiedWebhookEvent`を1件ずつ同期処理し、`HandlerSucceeded`または`HandlerFailed`を返す。handler呼出しはreceipt受付transaction外である。
  - `StaticHandlerRegistry`は現在空であり、同一handler instanceを`follow`と`unfollow`へ登録できる。
  - `WebhookEventReceipt`はevent IDを全体一意にし、再送をhandlerへ再dispatchしない。receiptは共通受付監査を保持するが、友だち同期固有の`applied`、`state_maintained`、`stale`、`unlinked`、`invalid`等は保持しない。
- **Implications**:
  - 本仕様は署名、destination、共通属性を再実装せず、source、userId、`follow.isUnblocked`だけを厳格に解釈する。
  - `linewebhooks.container`だけをcomposition pointとして変更し、ingress serviceと公開API契約は維持する。
  - 受付receipt／安全ログと、新設する同期監査を組み合わせて、重複・失敗を含む処理結果を追跡する。

### LINE follow／unfollowイベント契約

- **Context**: イベント順序、再送、`isUnblocked`の意味をプラットフォーム契約に合わせる必要がある。
- **Sources Consulted**:
  - [LINE Messaging API reference — Follow event](https://developers.line.biz/en/reference/messaging-api/#follow-event)
  - [LINE Messaging API reference — Common properties](https://developers.line.biz/en/reference/messaging-api/#common-properties)
  - [LINE Developers — Webhook redelivery](https://developers.line.biz/en/docs/messaging-api/receiving-messages/#webhook-redelivery)
- **Findings**:
  - `follow`は友だち追加またはブロック解除、`unfollow`はブロックを表す。`follow.isUnblocked`は追加と解除を補助的に区別するが、完全な正確性は保証されない。
  - `timestamp`はイベント発生時刻のUNIXミリ秒であり、再配信時刻ではない。
  - `webhookEventId`は再送でも同じで、`deliveryContext.isRedelivery`だけが変わる。再送時はイベント到着順が発生順と異なり得る。
  - 公式はULIDの辞書順をイベント発生順として利用する契約を示していない。
- **Implications**:
  - 状態はevent typeのみから決め、`isUnblocked`は監査補助情報に限定する。
  - 新旧判定は要件で定義された`(occurred_at_ms, webhook_event_id)`だけを使用する。`isRedelivery`と到着順は使用しない。
  - 同一時刻のID辞書順比較は本仕様の決定規則であり、LINE ULIDの時系列性には依存しない。

### LINE identity・channel・recipient照合

- **Context**: 未連携userの自動作成と、異なるprovider／channelへの誤反映を防ぐ必要がある。
- **Sources Consulted**:
  - `backend/lineaccounts/models.py`
  - `backend/lineaccounts/repositories.py`
  - `backend/lineaccounts/recipient_services.py`
  - `backend/linechannels/models.py`
  - `backend/linechannels/repositories.py`
  - `backend/linechannels/services.py`
  - `.kiro/specs/line-account-linking/design.md`
  - `.kiro/specs/line-account-linking/tasks.md`
- **Findings**:
  - identity自然キーは`(provider_id, subject)`、recipient自然キーは`(identity, line_channel)`である。
  - channelは不透明な`public_id`とnullableな`provider_id`を持つ。provider未設定時は安全に照合できない。
  - `line-account-linking`設計とtaskはprovider updateを既存legacy channelへのbackfillとして導入した一方、現行`DefaultLineChannelService.update`はnon-null providerから別のnon-null providerへの変更を拒否していない。
  - account mutationはowner rowを先に、recipient rowを後にロックする。個別解除はrecipientを削除し、全解除はrecipient、session、identityを同一transactionで削除する。
  - 再登録は削除後に新しいrecipient rowを作成するため、`created_at`が新しい関係の境界になる。既存recipientへの冪等registerは行を作り直さない。
- **Implications**:
  - 照合はchannel public IDから得たprovider、source userId、対象channelをすべて条件に含める。
  - providerは`NULL → validated value`のbackfill後に不変とし、異なるnon-null値への更新をchannel row lock下で拒否する。これにより署名検証とhandler directory readの間でproviderが別値へ差し替わらない。
  - 同期処理はidentity、owner、recipientを作成またはupsertしない。
  - `DeliveryRecipient.created_at`のUNIXミリ秒切捨て値を登録境界とし、その値以下のイベントを適用しない。
  - 解除との競合はowner→recipientの既存ロック順と「更新のみ、作成なし」によって最終削除へ収束させる。

### Django／MySQLのtransactionとロック

- **Context**: 順序逆転・同時follow/unfollow・解除競合で単一の最終状態へ収束し、部分確定を防ぐ必要がある。
- **Sources Consulted**:
  - [Django 6.0 — Database transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/)
  - [Django 6.0 — `select_for_update()`](https://docs.djangoproject.com/en/6.0/ref/models/querysets/#select-for-update)
  - [Django 6.0 — MySQL database notes](https://docs.djangoproject.com/en/6.0/ref/databases/#isolation-level)
  - [MySQL 8.4 — InnoDB locking](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)
  - [MySQL 8.4 — Locks set by statements](https://dev.mysql.com/doc/refman/8.4/en/innodb-locks-set.html)
  - [MySQL 8.4 — InnoDB error handling](https://dev.mysql.com/doc/refman/8.4/en/innodb-error-handling.html)
- **Findings**:
  - `transaction.atomic()`はblock成功時にcommitし、例外時にrollbackする。`select_for_update()`は必ずtransaction内で評価する必要がある。
  - InnoDBの一意キー完全一致は対象index recordへロック範囲を限定できる。非一意・範囲検索ではより広いロックが発生し得る。
  - deadlockとlock wait timeoutは通常運用でも起こり得る。transactionを短くし、複数rowのロック順を統一し、失敗を安全なretryable／storage failureへ分類する必要がある。
  - 実際のcommitと独立connectionの競合検証には`TransactionTestCase`が適する。
- **Implications**:
  - owner→recipientの順でlocking readを行い、比較・状態更新・同期監査insertを1つの短い`atomic()`へ閉じる。
  - deadlock／lock timeoutをhandler失敗へ変換し、ingress receiptの失敗分類に委ねる。本仕様内で自動再実行しない。
  - concurrency試験は独立connectionとbarrierを用いるMySQL統合試験とする。

### 個人情報保護と監査

- **Context**: LINE user IDを露出せず、処理結果を後から分類できる必要がある。
- **Sources Consulted**:
  - `.kiro/steering/product.md`
  - `.kiro/steering/tech.md`
  - `backend/linewebhooks/audit.py`
  - `backend/linewebhooks/models.py`
- **Findings**:
  - `SafeWebhookAuditLogger`はchannel UUID、event ID、event type等だけを記録し、LINE user IDを含めない。
  - 既存receiptは安全な共通属性とterminal statusを保持するが、projection固有の正常な非更新理由を表現できない。
  - LINE user IDは照合に必要だが、既存`LineSubject`と同様にrepr／例外／公開DTOへ出してはならない。
- **Implications**:
  - 新しい同期監査はchannel UUID、event ID、event type、発生時刻、安全な結果、`isUnblocked`だけを保持し、identity／recipientへのFKやuser IDを保持しない。
  - recipientの状態・順序更新と同期監査insertを同一transactionにし、監査確定失敗時は状態更新もrollbackする。
  - DB transaction自体が失敗した場合は`HandlerFailed`を返し、既存receiptの`failed/handler_failed`を失敗監査として使用する。

### 性能とquery境界

- **Context**: 1イベント100ミリ秒以内、最大10イベントを含むWebhookの2秒以内応答を維持する必要がある。
- **Sources Consulted**:
  - `backend/linewebhooks/tests/test_performance.py`
  - `backend/linewebhooks/services.py`
  - `backend/config/settings.py`
  - `.kiro/specs/line-webhook-ingress/design.md`
  - `.kiro/specs/line-webhook-ingress/research.md`
- **Findings**:
  - ingressは最大10イベントを同期・逐次dispatchし、handlerをreceipt受付transaction外で実行する。
  - upstream ingress設計はvalid requestを最大10event、登録handlerを1件100ミリ秒以下として2秒契約を構成し、2秒以上はdeadline auditで観測する。実装は2秒到達時に実行中handlerを強制中断しない。
  - 現行性能試験は実MySQL経路のelapsed timeとquery数の線形増加を検証する。
  - Django `select_for_update()`は競合rowが解放されるまで待機する。`nowait`は即時failureにできるが、現行ingressはfailed receiptを再dispatchしないため、本機能で採用すると競合eventがprojectionされずRequirement 4.7の収束を損なう。
  - 同期処理は外部I/Oを必要とせず、channel／owner／recipientの限定検索、最大1件のrecipient更新、監査insertで完結できる。
- **Implications**:
  - cache、queue、worker、新規SDKは導入しない。
  - 標準性能条件を「計測開始前から別transactionが対象owner／recipient lockを保持していない」と明示し、1イベントのhandler elapsed timeを100ミリ秒未満、10件request全体を2秒未満として実MySQL統合試験で計測する。
  - 同時event／unlinkは別のconcurrency suiteで正しさと有限時間の完了を検証する。意図的なlock保持時間へ100ミリ秒の強制timeoutを適用せず、DB deadlock／lock timeoutだけをsafe failureへ変換する。
  - query budgetを固定し、event数に対する線形増加を回帰検知する。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Decision |
|---|---|---|---|---|
| 既存`lineaccounts`へ全ロジックを追加 | account serviceがWebhook parsing、投影、監査まで所有 | ファイル数が少ない | Webhook固有責務が登録・解除責務へ混入し、境界が曖昧 | 不採用 |
| `linewebhooks`がrecipientを直接更新 | ingress handlerがORMでaccount modelを更新 | 接続が直接的 | ingressとprojectionの所有が混在し、下流状態機械が上流へ漏れる | 不採用 |
| 専用`linefriendships` app＋公開adapter | parsing、projection orchestration、同期監査を専用appが所有し、account adapter経由で既存aggregateを更新 | 境界、型、テスト、将来レビューが明確。ingress契約を維持 | appとcomposition fileが増える | 採用 |
| 外部queue／worker | 受付後に非同期投影 | 応答時間分離 | 復旧、再試行、順序保証という別責務を追加し、現行要件を超える | 不採用 |

## Design Decisions

### Decision: 検証済みenvelopeを専用projection handlerへ接続する

- **Context**: 署名・destination・共通属性の再実装を避けつつsource固有の不正入力を安全に分類する。
- **Alternatives Considered**:
  1. ingress validatorへfollow固有validationを追加する。
  2. 専用handlerで`FrozenJsonObject`をtyped commandへ変換する。
- **Selected Approach**: `linefriendships`のparser／serviceがsourceと`isUnblocked`を検証し、`linewebhooks.container`が同一handlerを`follow`と`unfollow`へ登録する。
- **Rationale**: 共通受付の責務を変更せず、イベント固有の進化を下流へ隔離できる。
- **Trade-offs**: handlerはraw event shapeを解釈するが、入力は署名・共通検証済みのimmutable objectに限定される。
- **Follow-up**: 未知追加fieldを無視し、必須fieldの型だけを厳密に検証する単体試験を用意する。

### Decision: channel provider bindingをlegacy backfill後は不変にする

- **Context**: ingressはchannel UUIDに対応するcredentialで署名を検証するが、現行envelopeはproviderを保持せず、handlerは後からdirectoryでproviderを解決する。設定済みproviderを処理途中で別値へ変更できると、検証時と投影時のidentity境界が一致しない。
- **Alternatives Considered**:
  1. credential取得時のprovider snapshotを`VerifiedWebhookEvent`へ追加し、ingress共通contractを拡張する。
  2. `LineChannel.provider_id`をlegacy backfill後はset-onceとし、既存envelope／directory contractを維持する。
- **Selected Approach**: 新規channelはprovider必須、legacy `NULL`は1回だけbackfill可能、設定済みproviderの同値指定は冪等、異値指定はchannel row lock後に`invalid_transition`として拒否する。
- **Rationale**: LINE channelが属するprovider自体は固定関係であり、既存`line-account-linking`の「legacy updateではbackfill可能」という承認済み意図を実装へ強制できる。ingress共通envelopeへ新fieldを追加せずTOCTOUを閉じられる。
- **Trade-offs**: 誤ったproviderを設定した場合は通常updateで修正できず、recipient／identity整合を確認する明示的なmigrationまたは再登録判断が必要になる。
- **Follow-up**: backfill、同値、異値拒否のservice testと、Webhook処理中の異provider update競合testを追加する。

### Decision: recipient rowを友だち状態projectionの線形化点にする

- **Context**: 異なるイベントの同時処理でも最大順序キーへ収束させる。
- **Alternatives Considered**:
  1. 到着順のlast-write-wins。
  2. event tableを全件集計して現在状態を再計算する。
  3. recipientへ最終適用順序を保持し、row lock下で比較する。
- **Selected Approach**: `DeliveryRecipient`へnullableな`last_friendship_event_occurred_at_ms`と`last_friendship_webhook_event_id`を追加し、owner→recipient lock後にtuple比較する。
- **Rationale**: 外部I/Oや全履歴集計なしでO(1)比較でき、既存aggregateと解除lock順に整合する。
- **Trade-offs**: recipientにprojection metadataが増えるが、現在状態の根拠を同じaggregate内で原子的に保持できる。
- **Follow-up**: DB check constraintで2fieldの同時null／同時non-nullと非負時刻を保証する。

### Decision: `created_at`を登録・再登録境界として再利用する

- **Context**: 削除前の古いイベントを新しいrecipientへ適用してはならない。
- **Alternatives Considered**:
  1. 専用registration generation UUIDを追加する。
  2. 専用baseline timestamp fieldを追加しbackfillする。
  3. 既存のimmutableなrow作成時刻を使用する。
- **Selected Approach**: `floor(created_at UNIX milliseconds)`をbaselineとし、`occurred_at_ms <= baseline_ms`をstaleとする。
- **Rationale**: recipient削除・再作成の既存lifecycleがそのまま新しい境界を生成し、追加fieldやbackfillを不要にする。
- **Trade-offs**: LINE timestampがミリ秒精度なので同一ミリ秒は保守的に旧関係として扱う。
- **Follow-up**: migration後の既存recipientと、削除・再登録直後の境界試験を行う。

### Decision: 同期監査と状態更新を同一transactionにする

- **Context**: 状態・順序だけ、または監査だけが部分確定することを防ぐ。
- **Alternatives Considered**:
  1. best-effort application logだけを使用する。
  2. ingress receiptへprojection固有fieldを追加し、handler後に更新する。
  3. 専用のsafe audit rowをprojection transaction内で作成する。
- **Selected Approach**: identity／recipientへのFKを持たない`FriendshipSyncAudit`を新設し、状態・順序更新と同じ`atomic()`で保存する。共通重複と失敗は既存receipt／safe webhook auditと合成する。
- **Rationale**: LINE user IDを保持せずunlink後も診断でき、ingress ownershipを変更せず原子性を保証できる。
- **Trade-offs**: 共通受付とprojection結果は2つの監査surfaceに分かれるため、event IDで相関する。
- **Follow-up**: audit insert failure時のrecipient rollbackと、ログ／repr／例外へのuser ID非露出を検証する。

### Decision: 標準transaction機能を採用し独自同期基盤を作らない

- **Context**: build-vs-adoptと単純化を評価する。
- **Alternatives Considered**:
  1. application-level mutexやMySQL advisory lockを追加する。
  2. Django `atomic()`、`select_for_update()`、既存一意制約を採用する。
- **Selected Approach**: Django／InnoDBの標準transaction機能を採用し、追加library、queue、cache、workerを導入しない。
- **Rationale**: 対象は単一DB内の既存aggregateであり、標準row lockが要件を満たす。新しい運用責務を増やさない。
- **Trade-offs**: lock競合はDB待機になるためtransactionを短く保ち、deadlock／timeoutを失敗へ分類する必要がある。
- **Follow-up**: 実MySQLのconcurrency／performance試験で検証する。

### Decision: handler性能budgetとlock競合correctnessを別条件で検証する

- **Context**: 1event 100ミリ秒のhandler budgetと最大10event 2秒のingress契約を維持しつつ、同時event／unlinkはrow lockで直列化する必要がある。
- **Alternatives Considered**:
  1. `select_for_update(nowait=True)`で100ミリ秒未満のfailureを優先する。
  2. 標準性能計測を非事前競合条件へ限定し、競合時はblocking lockによる収束を優先する。
  3. queue／worker／failed event再dispatchを追加する。
- **Selected Approach**: 性能testでは開始前から別transactionが対象lockを保持していないことを保証し、100ミリ秒／2秒budgetを測定する。concurrency testでは同じlock順による最終収束と有限時間完了を検証し、人為的なlock保持時間を100ミリ秒budgetへ含めない。
- **Rationale**: `nowait` failure後を再dispatchしない現行ingressでevent lossを起こさず、upstream ingressが定義したregistered handler budgetの計測条件も明確にできる。
- **Trade-offs**: 異常に長い外部lock競合では100ミリ秒を保証しない。DB deadlock／lock timeoutはsafe failure、2秒超過は既存deadline auditで観測し、成功や自動再実行を推測しない。
- **Follow-up**: performance fixtureの非事前競合条件、concurrency suite、synthetic deadline audit回帰を分離して固定する。

### Decision: 一般化は「順序付き状態projection」interfaceまでに限定する

- **Context**: followとunfollowは同じ照合・順序・監査手順のtarget state違いである。
- **Alternatives Considered**:
  1. event typeごとに独立handler／repositoryを作る。
  2. typedなtarget stateを持つ1つのprojection commandへ正規化する。
- **Selected Approach**: parserがfollow／unfollowを`friend`／`not_friend`のtyped commandへ変換し、1つのserviceとrepository contractを共有する。
- **Rationale**: 重複実装を避けつつ、実装対象は現在の2イベントに限定できる。
- **Trade-offs**: 将来のgroup／roomや別状態を先取りして抽象化しない。
- **Follow-up**: registryも現在は2event typeだけを登録する。

## Risks & Mitigations

- **不正userIdの露出** — `U`＋32桁小文字hexの境界validatorとredacted value objectを使用し、repr、audit、例外、公開DTOへ値を渡さない。
- **署名検証後のprovider差し替え** — providerをlegacy backfill後はset-onceとし、設定済み値から別値への変更をrow lock下で拒否する。provider未設定中は`unresolvable`として投影しない。
- **解除とのdeadlock** — 既存account mutationと同じowner→recipientのlock順を固定し、transaction内に外部I/Oを入れない。
- **lock timeout／deadlock** — retryable／storage failureを`HandlerFailed`へ変換し、本仕様では自動再実行しない。ingressの失敗receiptで追跡する。
- **同一時刻の非決定性** — event IDをASCII辞書順で比較する要件規則を明示し、DB collationやULID時系列性へ依存しない。
- **audit肥大化** — 本仕様では診断可能性を優先して全処理結果を保持する。保持期間・削除jobは要件外であり、将来導入時はprivacy／downstream revalidation対象とする。
- **性能budgetと競合待機の混同** — 非事前競合のperformance suiteでquery budgetと100ミリ秒／2秒を測定し、lock競合は別のconcurrency suiteで収束を検証する。2秒超過は既存deadline auditで観測する。

## Design-stage Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 11〜13件（provider set-once補強、app／型とparser、audit model migration、recipient順序field migration、account projection adapter、service transaction、composition、単体試験、MySQL concurrency、security／audit、performance／ingress統合）
- **Independent responsibility seams**: 1（検証済みfollow／unfollowを既存recipientへ順序付きで投影し、安全な結果を同時確定する状態projection）
- **Independent deliverables**: 1。source validation、永続化、監査、ingress接続は単独では利用価値を持たず、同じprojection成果を構成する。
- **External/state workflows**: 外部サービス呼出し0、独立状態機械1。Webhook受付、identity登録、unlink saga、配信は既存／別仕様の責務として維持する。
- **Rationale**: 20件未満であり、複数の独立提供境界、補償workflow、反復するcross-boundary統合を含まない。provider set-once補強は新しい基盤能力ではなく、承認済み`line-account-linking`のlegacy backfill契約に対するenforcement gap修正であり、本projectionの安全な照合前提として同時に検証する。設計段階でも単一Specを維持できる。

## References

- [LINE Messaging API reference](https://developers.line.biz/en/reference/messaging-api/) — follow／unfollow、共通イベント属性、source契約
- [LINE webhook redelivery](https://developers.line.biz/en/docs/messaging-api/receiving-messages/#webhook-redelivery) — 再送と順序逆転
- [Django 6.0 database transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/) — `atomic()`とrollback
- [Django 6.0 QuerySet API](https://docs.djangoproject.com/en/6.0/ref/models/querysets/#select-for-update) — locking readとtransaction前提
- [Django 6.0 MySQL notes](https://docs.djangoproject.com/en/6.0/ref/databases/#isolation-level) — MySQLのtransaction特性
- [MySQL 8.4 InnoDB locking](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html) — record／gap／next-key lock
- [MySQL 8.4 InnoDB error handling](https://dev.mysql.com/doc/refman/8.4/en/innodb-error-handling.html) — deadlockとlock wait timeout

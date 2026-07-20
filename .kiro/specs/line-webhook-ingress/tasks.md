# 実装計画

> すべての テスト定義には、入力・操作を示す日本語の `テストケース:` コメントと、観測可能な結果を示す `期待値:` コメントを付ける。

- [x] 1. Webhook 受付の実行基盤と永続化契約を整える
- [x] 1.1 Webhook app の起動基盤を追加する
  - Django が受付 app、migration、test package を標準 runtime で認識できる最小構成を用意する
  - app を Backend 設定へ登録し、新しい外部依存 package、Docker service、環境変数を追加しない
  - 後続の URL と composition はこの段階で公開せず、app 起動責務だけに限定する
  - 完了時には既存 Backend の system check と test discovery が新しい app を認識して成功する
  - _Requirements: 1.1_
  - _Boundary: Linewebhooks App Foundation_

- [x] 1.2 内容非露出の共通 value contract を定義する
  - 検証済み payload と event、受付結果、handler 結果、監査 entry を immutable な型付き契約として定義する
  - 検証済み envelope から raw body、署名、シークレット、destination、検証前 object、生例外を構造的に排除する
  - safe 表現は event data を表示せず、deadline 以外の監査結果では経過時間を持たせない
  - 契約の import に DB、暗号、HTTP、logging の副作用を持ち込まない
  - 完了時には immutable 性、安全な表現、許可された結果分類が境界単位のテストで確認できる
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 7.3, 7.4, 8.1_
  - _Boundary: linewebhooks.types_

- [x] 1.3 Webhook 用途限定のチャネル資格情報結果を定義する
  - 有効チャネルの公開識別子、bot user ID、redacted なチャネルシークレットだけを返す typed result を用意する
  - unknown、inactive、資格情報欠落・破損、保存層障害を既存の安全な利用不可分類へ統一する
  - account-linking 用 directory と既存 credential contract を拡張せず、Webhook 専用の最小権限を維持する
  - 完了時には成功結果へ不要なアクセストークンや内部状態を格納できず、失敗分類を安全に識別できる
  - _Requirements: 1.2, 1.3, 1.4, 2.1, 2.4_
  - _Boundary: WebhookCredentialRepository_

- [x] 1.4 Webhook 検証材料を整合した snapshot で取得する
  - canonical な公開識別子に対応する active、bot user ID、シークレット暗号文を一回の整合した読取りで取得する
  - チャネルシークレット列だけを復号し、平文利用を署名検証境界へ渡すまでの redacted wrapper 内に限定する
  - unknown、inactive、incomplete、corrupt、DB failure を秘密値や内部状態のない結果へ変換する
  - 既存 schema を変更せず、用途限定 repository を既存 composition から構築できるようにする
  - 完了時には有効チャネルだけが bot ID と正しいシークレットを同一 snapshot で返し、全失敗経路と非復号列が integration test で確認できる
  - _Requirements: 1.2, 1.3, 1.4, 2.1, 2.4_
  - _Boundary: WebhookCredentialRepository_

- [x] 1.5 最小限のイベント受付台帳と状態制約を作成する
  - webhookEventId の全体一意制約、非秘密チャネル識別子、event metadata、初回受付時刻、処理分類だけを保持する
  - processing、processed、failed、unsupported と完了時刻・安全な失敗分類の整合制約、運用時系列 index を設ける
  - event payload、署名、destination、ユーザー識別子、reply token、チャネル外部キーを保存対象から除外する
  - DB は一意性と状態整合だけを保証し、metadata 不変性を後続 repository の限定更新で守れる schema にする
  - 完了時には空 DB へ migration を適用でき、不正な状態組合せと webhookEventId 重複が DB で拒否される
  - _Requirements: 4.1, 4.4, 4.5, 5.5, 6.1, 6.2, 7.1, 7.3_
  - _Boundary: WebhookEventReceipt_

- [x] 2. 未加工要求から検証済みイベントへ信頼を昇格する
- [x] 2.1 未加工本文の署名を JSON 解析前に検証する
  - 欠落または厳密 Base64 として不正な署名を一律に拒否する
  - 対象チャネルのシークレットで raw bytes の HMAC-SHA256 を計算し、constant-time 比較で一致を判定する
  - 本文の decode、正規化、JSON 解析を行わず、本文・署名・シークレットを例外や安全表現へ含めない
  - 正当署名、本文一 byte 変更、別シークレット、欠落・形式不正を unit test で固定する
  - 完了時には raw bytes が完全一致する正当要求だけが verified となり、その他は内容非露出の rejected へ収束する
  - _Requirements: 2.1, 2.2, 2.3, 7.3, 7.4, 8.4_
  - _Boundary: RawSignatureVerifier_

- [x] 2.2 署名済み payload の受付上限と基本構造を検証する
  - 署名成功後、JSON parse 前に raw body が 256 KiB 以下であることを検証する
  - JSON root、destination、0〜10件の events と各 event の webhookEventId、種別、発生時刻、再送表示を全件検証する
  - 一件でも基本契約を満たさない、または上限を超える場合は request 全体を拒否し、一件も通過させない
  - 空、1件、10件、11件と各必須属性の境界値を unit test で固定する
  - 完了時には受付上限と基本契約を満たす共通 event 列だけが後続変換へ渡る
  - _Requirements: 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.7, 8.1, 8.4_
  - _Boundary: WebhookPayloadValidator_

- [x] 2.3 検証済み event を将来互換かつ immutable に変換する
  - 受付上限内の未知 field、field 順序、列挙値、event type を拒否せず保持する
  - event object を再帰的に immutable 化し、検証後の値と検証前 object の共有を断つ
  - raw request、署名、シークレット、destination、検証前 object を検証済み event から排除する
  - 既知・未知 event の混在、複数 event、変換後の改変試行を unit test で固定する
  - 完了時には tolerant reader が内容非露出の immutable な検証済み event tuple を返す
  - _Requirements: 3.5, 3.6, 5.2, 5.3, 7.3_
  - _Boundary: WebhookPayloadValidator_

- [x] 3. 一意な受付、handler 解決、安全な監査を実装する
- [x] 3.1 (P) 検証済み event type を単一の同期 handler へ解決する
  - 起動時に event type ごと最大一件を登録し、request 処理中は registry を変更しない
  - 登録済み type だけに検証済み envelope を渡し、未登録 type は handler を呼ばず unsupported と判定する
  - registry は handler contract を保持するが、具体 handler の100 ms性能検証は登録側仕様と最終 performance gate に委ねる
  - event 固有作用、fan-out、retry を registry に持ち込まず、重複登録を拒否する
  - 完了時には登録済み type の一意解決、未登録 type、重複登録がテストで固定される
  - _Requirements: 3.5, 5.1, 5.4, 5.5, 6.4_
  - _Boundary: HandlerRegistry_
  - _Depends: 1.2_

- [x] 3.2 (P) 内容を受け取れない安全な Webhook 監査を実装する
  - channel、署名、payload の拒否、空受付、新規・重複・未対応、handler 成功・失敗、保存層障害、deadline 超過を whitelist 分類する
  - outcome、観測時刻、許可された非秘密 metadata だけを構造化記録する
  - deadline 超過時だけ非負の elapsed milliseconds を記録し、他 outcome では経過時間を保持しない
  - raw body、署名、秘密値、event data、生例外、任意 context、traceback を監査入口と通常ログから排除する
  - 完了時には各運用結果を内容なしで識別でき、禁止データ canary と deadline entry の形がテストで確認できる
  - _Requirements: 3.4, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1_
  - _Boundary: SafeWebhookAuditLogger_
  - _Depends: 1.2_

- [x] 3.3 (P) event batch の新規受付権を原子的に確定する
  - request 内の全 candidate を短い単一 transaction で受付し、保存層障害時は新規行を全 rollback する
  - webhookEventId の一意制約を線形化点として、同一 request と別 request の重複を既存 receipt へ収束させる
  - candidate と同じ件数・順序の判定を返し、同一 request 内では最初の occurrence だけに新規処理権を与える
  - 重複時に初回 metadata、発生時刻、再送表示、support 判定、保存済み状態を変更しない
  - 完了時には複数 event の新規・重複判定が順序どおり返り、失敗時の全 rollback と台帳一行への収束が repository test で確認できる
  - _Requirements: 3.3, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 5.5, 7.1, 8.5_
  - _Boundary: EventReceiptRepository_
  - _Depends: 1.2, 1.5_

- [x] 3.4 handler 結果を台帳へ単調に確定する
  - processing の receipt だけを processed または handler_failed 付き failed へ条件付き更新する
  - 更新対象を status、failure code、completed time、updated time に限定し、初回 metadata を書き換えない
  - terminal または unsupported の receipt、重複観測、競合した確定処理では保存済み分類を維持する
  - 確定保存に失敗した processing receipt を維持し、duplicate acceptance へ新規 dispatch 権を返さない
  - 完了時には成功・失敗・競合・保存失敗が単調な状態遷移となり、metadata 不変性と duplicate に新規 dispatch 権がないことを repository test で確認できる
  - _Requirements: 4.3, 4.5, 4.6, 6.1, 6.2, 6.3, 8.3, 8.5_
  - _Boundary: EventReceiptRepository_

- [x] 4. 検証、受付、dispatch を公開 HTTP 境界へ統合する
- [x] 4.1 request 全体の信頼遷移と batch 受付を順序制御する
  - canonical な公開識別子から資格情報を解決し、署名成功前に payload を解析せず、全 payload 検証完了前に receipt を作成しない
  - 空 events は台帳と handler を使わず安全に受付監査し、非空 batch は support 判定後に原子的な受付へ渡す
  - channel、credential、signature、destination、受付上限、payload、受付保存の失敗を安全な request 結果へ分類する
  - controlled rejection では一件も受け付けず、予期しない下位例外を内容非露出の結果へ置き換える
  - 完了時には untrusted raw から verified batch までの順序が service test で固定され、部分受付や検証前 handler 呼出しが発生しない
  - _Requirements: 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 7.2, 7.3, 7.4, 8.4_
  - _Boundary: WebhookIngressService Integration_
  - _Depends: 2.3, 3.1, 3.2, 3.3_

- [x] 4.2 新規受付 event だけを dispatch して結果と deadline を分類する
  - commit 済みの新規 processing event だけから検証済み envelope を作り、handler を transaction 外で payload 順に呼び出す
  - unsupported と全重複では handler を呼ばず、保存済み分類と初回 metadata を維持する
  - handler の安全な失敗と生例外を handler_failed へ置換し、失敗後も後続 event の分類を継続する
  - handler failure の確定後は accepted、確定保存失敗では processing を残して storage unavailable とする
  - monotonic clock で全体を測定し、1,500 msはテストで評価する内部目標、2,000 ms以上だけを deadline audit として記録する
  - 完了時には成功・失敗・未対応・重複・確定失敗・deadline が設計どおりの台帳状態、監査、request 結果になる
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.5_
  - _Boundary: WebhookIngressService Integration_

- [x] 4.3 channel 別の匿名 POST と安全な HTTP 応答を実装する
  - 専用 URL の path parameter を候補選択にだけ渡し、request body を一度だけ raw bytes として取得する
  - owner 認証、permission、body parser を受付境界で無効化し、request data を参照しない
  - POST 以外は service を呼ばない固定405とし、acceptedを空200、payload/destinationを400、signatureを401、channel/credentialを404、storage/unexpectedを503へ写像する
  - 400・401・404は同じ安全な rejection body、503は固定 unavailable bodyとし、内部分類や受信内容を露出しない
  - 完了時には正しい URL の匿名 POST だけが service を呼び、method、status、body、parser 順序が HTTP 境界 test で観測できる
  - _Requirements: 1.1, 1.3, 1.4, 1.5, 2.1, 2.2, 2.5, 3.4, 3.7, 7.4, 8.1, 8.2, 8.3, 8.4_
  - _Boundary: WebhookAPIView_
  - _Depends: 4.2_

- [x] 4.4 concrete component を合成して公開 route へ接続する
  - credential、署名、payload、台帳、registry、監査、clock、service を composition root だけで合成する
  - app-local route を root API prefix へ接続し、既存 owner API の認証設定へ影響を与えない
  - 空 registry を既定とし、上限内の valid non-empty event を unsupported として安全に受け付ける
  - 新規依存、service、環境 secret を追加せず既存 Docker Compose/ngrok 導線を利用する
  - 完了時には標準 Backend runtime で migration 後に route が解決され、signed empty/unsupported request が公開入口から期待 status へ到達する
  - _Requirements: 1.1, 3.4, 5.4, 5.5, 8.1, 8.2_
  - _Boundary: Composition Integration_
  - _Depends: 1.4, 4.3_

- [x] 5. 公開境界の受付、競合、安全性、応答時間を検証する
- [x] 5.1 (P) service の受付・dispatch 分類を統合検証する
  - 空、既知、未知、複数、重複 event と handler 成功・安全な失敗・生例外・確定保存失敗を通して台帳、監査、呼出回数、request 結果を確認する
  - channel、credential、signature、destination、受付上限、payload、受付保存の失敗で全件拒否と handler 未呼出しを確認する
  - 一つの handler 失敗または確定失敗後も後続 event の分類を継続することを確認する
  - 完了時には service の全成功・拒否・失敗分岐が receipt 状態、監査結果、handler 呼出回数として再現できる
  - _Requirements: 3.4, 3.5, 3.6, 3.7, 4.3, 5.1, 5.2, 5.5, 6.1, 6.2, 6.3, 8.2, 8.3, 8.4, 8.5_
  - _Boundary: WebhookIngressService_
  - _Depends: 4.2_

- [x] 5.2 (P) 公開 HTTP 契約を統合検証する
  - exact route の POST だけが service を呼び、GET・PUT・DELETE・OPTIONS は固定405となることを確認する
  - owner session と CSRF に依存せず、body parser が署名前に動かないことを確認する
  - malformed/unknown/inactive/credential unavailable が同じ404、signature が401、destination/payload/上限超過が400、storage が503になることを確認する
  - empty、duplicate、unsupported、handler failed が空200となり、各 rejection で receipt と handler がゼロであることを確認する
  - 完了時には公開 API の status、body、auth、parser 契約が観測可能な HTTP test として再現できる
  - _Requirements: 1.1, 1.3, 1.4, 1.5, 2.1, 2.2, 2.5, 3.3, 3.4, 3.7, 7.4, 8.1, 8.2, 8.3, 8.4_
  - _Boundary: WebhookAPIView_
  - _Depends: 4.4_

- [x] 5.3 MySQL 上の同一イベント並行受付を検証する
  - 独立 connection と同時開始 barrier で同じ webhookEventId を service から受付する
  - receipt 一件、新規判定一件、handler 一回へ収束することを確認する
  - 初回 metadata と状態が競合側の入力で上書きされないことを確認する
  - 完了時には InnoDB の実 transaction で並行再送の at-most-once dispatch が再現可能に証明される
  - _Requirements: 4.2, 4.3, 4.4, 4.5, 8.5_
  - _Boundary: WebhookIngressService, EventReceiptRepository Integration_
  - _Depends: 3.3, 4.2_

- [x] 5.4 部分共通 batch と確定競合を MySQL 上で検証する
  - 一部だけ共通 ID を持つ複数 batch で各固有 ID を一度ずつ受け付け、共通 ID を一行へ収束させる
  - candidate 順序と新規・重複判定の対応、初回 metadata の不変性を確認する
  - CAS finalize と duplicate read の競合で terminal 状態が processing へ戻らないことを確認する
  - failed event の重複で handler 再実行権が生じないことを確認する
  - 完了時には batch atomicity、単調状態遷移、failed duplicate 非再実行が実 MySQL 競合 test で再現できる
  - _Requirements: 3.6, 4.2, 4.3, 4.4, 4.5, 6.1, 6.2, 6.3, 8.5_
  - _Boundary: EventReceiptRepository_

- [x] 5.5 禁止データの非露出を境界横断で検証する
  - raw body、署名、シークレット、user/source ID、reply token、message/postback 内容、生例外の canary を用意する
  - envelope、safe 表現、model、通常ログ、監査、公開 response を走査し、canary が残らないことを確認する
  - rejection、handler exception、storage exception でも内部状態と traceback が公開・通常監査へ出ないことを確認する
  - この task を security integration gate として明示し、各 component の保存・表示責務を変更しない
  - 完了時には全禁止データと生例外の非露出が自動 security test 結果として観測できる
  - _Requirements: 5.3, 7.1, 7.2, 7.3, 7.4, 7.5_
  - _Boundary: Security Integration_
  - _Depends: 4.4, 5.4_

- [x] 5.6 受付上限内の2秒応答と query 予算を検証する
  - signed 1件、5件、10件 request と empty、duplicate、unsupported path を標準 Backend/MySQL runtime で測定する
  - 1 event 100 ms以下の stub handler で内部目標1,500 ms未満、外部契約2,000 ms未満を確認する
  - query 数が event 数に対して線形で、receipt transaction へ handler 時間が含まれないことを確認する
  - 2,000 ms以上で内容非露出の deadline audit が elapsed milliseconds 付きで残ることを確認する
  - 完了時には主要 path の応答時間、query 特性、deadline 監査が自動 performance test 結果として観測できる
  - _Requirements: 3.6, 8.1, 8.2, 8.3_
  - _Boundary: Performance Integration_
  - _Depends: 4.4, 5.5_

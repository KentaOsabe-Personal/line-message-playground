# 調査・設計判断記録

## Summary

- **Feature**: `line-message-delivery`
- **Discovery Scope**: Complex Integration（既存の React / Django / MySQL 基盤へ、取り消せない外部作用を伴う LINE Messaging API を統合）
- **Key Findings**:
  - LINE の push API は `X-Line-Retry-Key` による重複実行防止を提供するが、保持期間は24時間であり、アプリケーション側の永続的な操作ID一意制約を置き換えない。
  - タイムアウトと5xxは LINE 側で受理済みの可能性がある。自動再送を行わず「結果不明」を成功・確定失敗と区別する状態が必要である。
  - `line-bot-sdk 3.25.0` は Python 3.14 を分類対象に含み、保守対象の `linebot.v3` からレスポンスヘッダーを取得できる。`push_message_with_http_info` を利用すると `X-Line-Request-Id` を監査記録へ保存できる。
  - Frontend の画面状態だけでは「確認していない内容を送らない」契約を API 直呼び出しに対して保証できない。Backend が発行する署名付き確認トークンを最終送信時に検証する。

## Research Log

### 既存アーキテクチャと統合点

- **Context**: 新しい配信機能を既存の疎通確認責務へ混在させず、実装可能なファイル境界を特定する必要があった。
- **Sources Consulted**: `backend/config/settings.py`、`backend/config/urls.py`、`backend/health/`、`frontend/src/`、`compose.yaml`、`.kiro/steering/product.md`、`.kiro/steering/tech.md`、`.kiro/steering/structure.md`
- **Findings**:
  - 現在の経路は Browser → Vite proxy → Django REST API → MySQL であり、LINE の秘密値は Backend コンテナだけへ注入済みである。
  - Django app-local URLConf、DRF `APIView`、`APITestCase` が既存パターンである。Frontend は `App.tsx` を画面ルートとする小規模構成で、配信フォームの慣例はまだない。
  - MySQL は `utf8mb4`、Django は timezone-aware datetime を利用するため、日本語・絵文字・受付/完了時刻を追加設定なしで保持できる。
- **Implications**:
  - Backend は独立した `delivery` app とし、`health` app は変更しない。
  - Frontend は `src/delivery/` に配信固有の型、API client、画面状態を閉じ込め、`App.tsx` は機能を合成するだけにする。
  - 配信試行の一意制約と状態遷移は Django model を正本とし、Frontend のボタン無効化は補助制御とする。

### LINE push API の契約と制約

- **Context**: メッセージ長、成功の意味、レート制限、レスポンス識別子を設計契約へ反映する必要があった。
- **Sources Consulted**:
  - [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/)
  - [Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)
- **Findings**:
  - push API は1宛先へ最大5メッセージを送信できる。本機能は通常テキスト1件だけを使い、整形後テキストを5,000文字以下に制限する。LINEの文字数規則ではサロゲートペア等が複数文字として数えられるため、PythonのUnicode code point数ではなくUTF-16コード単位で境界を判定する必要がある。
  - HTTP 200 は LINE Platform による受理を表し、ブロック済みなどでは実端末へ届かない場合がある。
  - `X-Line-Request-Id` は各要求の追跡に使える。同一 retry key が受理済みの場合は409と `X-Line-Accepted-Request-Id` が返る。
  - 429はエンドポイントのレート超過だけでなく、同一ユーザーへの集中や月間上限でも発生する。
- **Implications**:
  - UI の成功文言は「LINEが送信要求を受け付けた」とし、端末到達を保証しない。
  - 400、401、403、409、429、5xx、timeout を固定の内部失敗種別へ変換し、外部レスポンス本文は保存・表示・ログ出力しない。
  - 409に `X-Line-Accepted-Request-Id` がある場合だけ既受理の成功へ正規化し、それ以外の409は外部競合として失敗にする。

### SDK・ランタイム互換性

- **Context**: 既存の Python 3.14 / Django 6 と保守対象 SDK の互換性、およびヘッダー取得方法を確認した。
- **Sources Consulted**:
  - [line-bot-sdk on PyPI](https://pypi.org/project/line-bot-sdk/)
  - [LINE Bot SDK for Python](https://github.com/line/line-bot-sdk-python)
  - [Django 6.0 release notes](https://docs.djangoproject.com/en/6.0/releases/6.0/)
- **Findings**:
  - 2026-07-12時点の `line-bot-sdk 3.25.0` は Python `>=3.10` を要求し、Python 3.14 classifier を持つ。Django 6.0 は Python 3.12–3.14 をサポートする。
  - SDK は `linebot.v3` のみを保守対象としている。3.x は2.xと非互換である。
  - `MessagingApi.push_message_with_http_info` はステータス、ヘッダー、データを含む応答を返す。`ApiException` からも status と headers を取得できる。
- **Implications**:
  - `backend/requirements.txt` に `line-bot-sdk==3.25.0` を固定し、旧 `linebot` API を禁止する。
  - SDK固有型と例外は `line_client.py` 内に閉じ込め、サービス層へは型付きの成功/失敗結果だけを返す。
  - チャネルシークレットは push 送信には不要であり、本機能はアクセストークンと固定ユーザーIDだけを参照する。

### 冪等性、並行実行、結果不明

- **Context**: UI連打、HTTP再送、同一キーの内容差し替え、外部タイムアウトを同時に安全に扱う必要があった。
- **Sources Consulted**: 要求 4.x、5.x、6.x、LINE retry guide、Django model/transaction の既存利用条件
- **Findings**:
  - retry key は初回要求から指定し、同じ宛先・内容にだけ再利用する必要がある。LINE側の保持期間は24時間である。
  - 外部通信中にDB transactionや行ロックを保持すると、長時間ロックと障害時の回復を複雑化する。
  - MySQL の nullable unique column は複数のNULLを許容するため、処理中だけ値を持つ `active_content_fingerprint` で同一内容の並行送信を排除できる。
- **Implications**:
  - 短いtransactionで30秒の期限を持つ `processing` 行を確定してから、connect 3秒・read 10秒の有限timeoutでLINEを呼び、完了時に `active_content_fingerprint` をNULLへ戻す。
  - `operation_id` を永続的一意キーかつ LINE retry key として用いる。同一ID・同一fingerprintは既存結果を返し、同一ID・異なるfingerprintは409で拒否する。
  - timeoutは `unknown`、予期しない例外は `failed` へ確定し、どちらも自動再送しない。処理中のままプロセスが停止した記録は、同一operation IDによる状態再確認時に期限切れなら `unknown` へ確定し、LINE呼出しを再実行しない。

### 送信前確認のBackend強制

- **Context**: Frontendだけの確認フラグでは、送信APIへ未確認の本文を直接渡せる。
- **Sources Consulted**: 要求 2.1–2.4、Django signing API、既存 `DJANGO_SECRET_KEY` 設定
- **Findings**:
  - preview API がBackendの正規フォーマッタで検証・整形し、内容fingerprintへ署名したトークンを返せる。
  - 最終送信APIは件名・本文からfingerprintを再計算し、署名と一致しない場合は LINE を呼ばずに拒否できる。
- **Implications**:
  - 確認トークンには秘密値や本文を含めず、formatter version と SHA-256 fingerprint だけを署名する。
  - 入力変更、署名不正、formatter version変更、`DJANGO_SECRET_KEY` 変更は再確認を要求する。
  - previewは永続化せず、送信試行の記録は最終送信受付時にだけ作る。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Django app内の層分離 | View/serializer、application service、model、LINE adapterを単一app内で分離 | 既存構造に適合し、境界が明確でテストしやすい | 規律がないとserviceが肥大化する | 採用 |
| 完全なHexagonal構成 | repository portや複数adapterを導入 | 外部差し替えに強い | 現時点で実装が1種類しかなく過剰 | 不採用 |
| ViewからSDKを直接呼出し | 最小ファイル数 | 初期実装が短い | 冪等性、監査、例外変換、テスト境界が混在 | 不採用 |
| 非同期job/queue | API応答と外部通信を分離 | 将来の再送・大量配信に向く | 要求対象外で、結果確認と運用が複雑化 | 不採用 |

## Design Decisions

### Decision: 二層の冪等性を採用する

- **Context**: 4.1–4.5 はブラウザ連打とネットワーク再送の双方を扱う。
- **Alternatives Considered**:
  1. Frontendのボタン無効化のみ — 並行HTTP要求を防げない。
  2. LINE retry keyのみ — 24時間後の再利用と内容差し替えをアプリ側で検出できない。
  3. DB一意制約とLINE retry key — 両境界で重複を防ぐ。
- **Selected Approach**: `operation_id` のDB一意制約、処理中fingerprintの一意制約、同じUUIDによる `X-Line-Retry-Key` を併用する。
- **Rationale**: アプリ受付と外部受理の異なる競合点を、それぞれが所有する仕組みで保護できる。
- **Trade-offs**: 状態遷移と競合応答が増えるが、取り消せない作用に必要な明示性を得る。
- **Follow-up**: MySQL上の並行テストで一意制約の挙動を確認する。

### Decision: 署名付き確認トークンを自前の小さな境界として構築する

- **Context**: 2.3–2.4 をBackend境界でも保証する必要がある。
- **Alternatives Considered**:
  1. Frontend stateのみ — API直接呼び出しを防げない。
  2. previewをDBへ保存 — 確認だけで永続データが増える。
  3. Django signingでfingerprintへ署名 — DB不要で改変を検出できる。
- **Selected Approach**: Django標準signingを利用し、正規化内容fingerprintとformatter versionへ署名する。
- **Rationale**: 既存依存だけで確認と最終送信を契約上分離できる。
- **Trade-offs**: `DJANGO_SECRET_KEY` 変更時に未送信previewは無効になる。これは安全側の挙動として許容する。
- **Follow-up**: 改変、入力変更、version変更の拒否テストを実装する。

### Decision: 外部SDKを採用し、repository抽象は作らない

- **Context**: build-vs-adopt と簡素化を同時に評価した。
- **Alternatives Considered**:
  1. HTTP clientを直接実装 — 認証、schema、例外処理を重複実装する。
  2. 公式SDK v3 — OpenAPI生成型と保守対象APIを利用できる。
  3. ORM repository interface — 将来のstorage差し替えには有効だが現要求では実装が1つだけ。
- **Selected Approach**: LINE境界は公式SDKをadapterで包み、永続化はDjango ORMをapplication serviceから利用する。
- **Rationale**: 外部契約は隔離しつつ、仮想的な差し替えのための層を増やさない。
- **Trade-offs**: serviceはDjango ORMへ依存するが、単一Django appの責務内に収まる。
- **Follow-up**: SDK v3の実シグネチャを固定版コンテナ内で確認するcontract testを置く。

### Decision: 自動再送を実装しない

- **Context**: LINEは500/timeoutのretryを案内するが、4.5は利用者の新しい操作なしの自動再送を禁止する。
- **Alternatives Considered**:
  1. SDK呼出しを自動retry — LINE推奨に沿うが要件違反となる。
  2. 初回からretry keyのみ付与し再送しない — 将来の明示的回復に備えつつ現要件を守る。
- **Selected Approach**: 外部呼出しは1回だけ行い、timeoutを `unknown`、5xxを `failed` として記録する。
- **Rationale**: 本仕様は安全な学習用最小機能であり、回復workflowは対象外である。
- **Trade-offs**: 一時障害から自動回復しない。利用者は結果を確認して新しい操作を開始する。
- **Follow-up**: 将来retryを追加する場合は、24時間制約と同一payload保証を含む別仕様で再設計する。

### Decision: 滞留したprocessingを状態再確認でunknownへ収束させる

- **Context**: `processing` をcommitした後、LINE呼出し中またはterminal更新前にプロセスが停止すると、送信結果を確定できず同一内容のactive fingerprintも解放されない。
- **Alternatives Considered**:
  1. 永久にprocessingとして保持 — 外部作用を推測しないが、結果表示と後続の明示操作を永久に妨げる。
  2. background jobで再送 — 自動再送禁止に反し、worker運用も現スコープを超える。
  3. 有限期限後の同一ID状態再確認でunknownへ確定 — 外部送信を増やさず、結果不明を明示してactive fingerprintを解放できる。
- **Selected Approach**: gatewayの最大待機時間より十分長い30秒を `processing_expires_at` とし、単一operation IDのstatus POST時に期限切れprocessingをcompare-and-setで `processing_expired` のunknownへ確定する。試行が存在しない場合は404とし、状態確認自体から送信を開始しない。404は受付有無を断定せず、利用者が明示した場合だけ元と同じoperation ID/payloadで同一送信操作を再試行する。
- **Rationale**: 利用者の明示的な状態確認で監査状態をterminalへ収束させつつ、LINEへの再送と成功推測を避けられる。
- **Trade-offs**: backgroundで自動収束はしない。gateway完了との競合では先に成立したterminal遷移を正本とし、後続更新は上書きしない。
- **Follow-up**: `TransactionTestCase` と独立DB connectionで、期限切れ確定とgateway完了の競合を検証する。

## Risks & Mitigations

- LINE 200でも実端末へ届かない可能性 — 成功を「LINE受理」と表現し、到達保証と誤認させない。
- timeout時に実際は受理済みの可能性 — `unknown` として記録し、自動再送しない。
- 同一内容の並行要求がDB競合する — nullable unique fingerprintを最終防壁にし、競合を安全な状態応答へ変換する。
- SDK例外に秘密値や外部本文が含まれる可能性 — raw exception/bodyをモデル、API、通常ログへ渡さず、固定enumと安全な概要だけを扱う。
- 署名キー変更でpreviewが無効化される — 最終送信を拒否し、再確認を促す。
- プロセス停止で `processing` が残る可能性 — 同一IDの状態再確認時に期限切れを `processing_expired` のunknownへ確定し、自動再送しない。

## References

- [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/) — push契約、文字数、status、request ID、rate limit
- [Messaging APIリファレンス](https://developers.line.biz/ja/reference/messaging-api/) — サロゲートペア等を含む文字数の数え方
- [Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/) — retry key、24時間、409、timeout/5xxの意味
- [LINE Bot SDK for Python](https://github.com/line/line-bot-sdk-python) — `linebot.v3` の保守方針とヘッダー取得
- [line-bot-sdk on PyPI](https://pypi.org/project/line-bot-sdk/) — 3.25.0、Python要件、ライセンス
- [Django 6.0 release notes](https://docs.djangoproject.com/en/6.0/releases/6.0/) — Python 3.14互換性

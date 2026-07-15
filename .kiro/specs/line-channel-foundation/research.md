# 調査・設計判断

## Summary

- **Feature**: `line-channel-foundation`
- **Discovery Scope**: Extension / Light Discovery（既存のComplex Integration設計を、rotation開始契約とtask実行可能性に絞って再検証）
- **Key Findings**:
  - 既存配信は `delivery.LINEGateway` が環境変数のアクセストークンを直接参照するため、本仕様では変更せず、独立した `linechannels` app と用途別資格情報 repository を並置する。
  - `cryptography==49.0.0` の Fernet/MultiFernet は Python 3.14 に対応し、認証付き暗号、先頭鍵での新規暗号化、複数鍵での復号、再暗号化を提供する。Fernet に AAD がない制約は、チャネル公開 UUID と資格情報種別を認証対象の plaintext envelope に含めて復号後に照合することで補う。
  - ローテーションは資格情報行ごとの短い transaction と `select_for_update()` で実行し、現用鍵単独での再検証後にだけ2暗号文を同時更新する。最終全件検証が成功するまで旧鍵撤去可を報告しない。
  - 設計検証で明らかになった境界を補強し、Serviceがtransaction、`DjangoLineChannelRepository`がlocked persistence、context managerがadvisory lockの明示解放を所有する。
  - Django DEBUG query captureは暗号文をSQL parameterとして保持し得るため、資格情報基盤を有効にするBackendは `DEBUG=False` を起動条件とし、MySQL general query logも禁止する。
  - keyring raw valueはcanonical Fernet keyの厳密なASCII comma-separated grammarに固定し、decode後の鍵bytesで重複判定する。
  - raw keyringはDjango settingsへ登録せず、専用runtime loaderが環境変数から直接読んで検証済みprivate stateだけを共有する。これにより`diffsettings`の設定列挙から鍵を除外する。
  - Backend testは明示的な`config.test_settings`がbase settings import前にephemeral keyを生成し、`AppConfig.ready()`より前に安全なtest環境を確立する。
  - rotation開始条件はopaque keyringをserviceへ公開せず、`CredentialCipher` の非秘密なtyped readinessをDB access前に判定する。単一鍵では安全な `configuration_required` とし、複数鍵だけが走査を開始する。
  - task境界を明確にするため、rotationのadvisory lock、行repository、1pair暗号処理、batch orchestrationと、対話prompt、command dispatchをそれぞれ単一責務へ分離する。

## Research Log

### 既存アーキテクチャと統合境界

- **Context**: 新基盤をどこへ置き、既存配信へどこまで影響させるかを決定する必要があった。
- **Sources Consulted**: `backend/config/settings.py`、`backend/delivery/gateway.py`、`backend/delivery/services.py`、`backend/delivery/models.py`、`compose.yaml`、`.kiro/steering/structure.md`、`.kiro/specs/line-channel-foundation/brief.md`
- **Findings**:
  - Backend は Django app 単位で責務を分け、View/Serializer、Service、Model、Gateway を分離している。
  - 現行 `LINEGateway` は `LINE_CHANNEL_ACCESS_TOKEN` と固定 `LINE_USER_ID` を設定から取得する。既存テストはチャネルシークレットを配信設定へ取り込まないことも検証している。
  - `compose.yaml` と `.env.example` には `LINE_CHANNEL_SECRET` が存在するが、Django settings と現行配信は参照していない。
  - 既存に資格情報モデル、repository、management command、暗号ライブラリはない。
  - `channels` という app 名は一般的すぎるため、LINE 固有境界を表す `linechannels` が適する。
- **Implications**:
  - 新 app は API/Frontend を持たず、モデル、暗号、repository、application service、管理コマンドを所有する。
  - `delivery` app と公開配信 API は変更しない。後続 `linked-recipient-delivery` が repository 利用へ移行する。
  - DB 初期登録後、未使用の `LINE_CHANNEL_SECRET` は本仕様で Compose/example/local environment から撤去する。`LINE_CHANNEL_ACCESS_TOKEN` は既存配信互換のため一時的にDBと重複保持し、後続配信移行後に撤去する。
  - Django Admin へ資格情報モデルを登録せず、暗号文を表示する経路を作らない。

### LINE チャネルと公式アカウントの識別情報

- **Context**: チャネルを一意に識別し、後続 Webhook が受信先を照合できる非秘密情報を確定する必要があった。
- **Sources Consulted**:
  - [Messaging API reference: Get LINE Official Account bot info](https://developers.line.biz/en/reference/messaging-api/nojs/#get-bot-info)
  - [Receive messages webhook](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
  - [Issue channel access token v2.1](https://developers.line.biz/en/docs/messaging-api/generate-json-web-token/)
- **Findings**:
  - Messaging API channel ID は LINE Developers Console で取得するチャネル識別子である。
  - bot user ID は Webhook の `destination` と bot info の `userId` に現れ、公式アカウントを機械的に識別できる。
  - Basic ID は利用者向け検索情報だが、後続 Webhook の照合には bot user ID が直接必要である。
- **Implications**:
  - `messaging_api_channel_id` と `bot_user_id` を一意な非秘密識別情報として保持する。
  - 内部連番とは別に UUID v4 の `public_id` を発行し、後続 URL/DTO の不透明な識別子にする。
  - Basic ID の管理は現要件に必須でないため、本仕様には追加しない。

### 認証付き暗号と Python 3.14 互換性

- **Context**: DB に平文を残さず、改変検出と複数鍵読取を満たす保守済みライブラリが必要だった。
- **Sources Consulted**:
  - [cryptography 49.0.0 on PyPI](https://pypi.org/project/cryptography/)
  - [Fernet and MultiFernet documentation](https://cryptography.io/en/latest/fernet/)
  - [cryptography changelog](https://cryptography.io/en/stable/changelog/)
- **Findings**:
  - `cryptography==49.0.0` は Python 3.14 をサポートし、CPython 3.14 wheel を提供する。
  - Fernet は機密性と完全性を提供し、MultiFernet は先頭鍵で暗号化し、設定順に復号を試み、先頭鍵への rotate を提供する。
  - Fernet key は URL-safe base64 の32 byte鍵であり、漏えい時は読取・偽造、紛失時は復号不能となる。
  - Fernet token には生成時刻が平文で含まれるが、本仕様では資格情報更新日時自体が非秘密なので許容できる。
  - Fernet は AAD を直接受け取らないため、正当な暗号文を別チャネルや別資格情報列へ差し替える攻撃は、値だけを暗号化した場合には検出できない。
- **Implications**:
  - 各秘密を別々に、`format_version`、`channel_public_id`、`credential_kind`、`value` を持つ決定的 schema の envelope として Fernet 暗号化する。
  - 復号後に期待する公開 UUID と種別を照合し、不一致を完全性エラーへ変換する。これにより用途別取得では必要な一方だけを復号しつつ、差し替えも検出する。
  - 暗号文は索引不要の `BinaryField(editable=False)` とし、文字照合やフォーム表示の対象にしない。

### AES-GCM envelope との比較

- **Context**: AAD を直接使える AEAD を採用すべきか、Fernet の recipes API を使うべきかを比較した。
- **Sources Consulted**:
  - [Authenticated encryption primitives](https://cryptography.io/en/stable/hazmat/primitives/aead/)
  - [Fernet and MultiFernet documentation](https://cryptography.io/en/latest/fernet/)
- **Findings**:
  - AES-GCM は AAD を直接認証できる一方、nonce 一意性、envelope format、key ID、複数鍵選択、rotate をアプリケーション側で設計・実装する必要がある。
  - MultiFernet は本仕様の小さな秘密値と少数鍵のローテーションに必要な機能を高水準 API として提供する。
- **Implications**:
  - 現スコープでは Fernet/MultiFernet と認証済み context envelope を採用する。
  - 将来、鍵数増加による復号コスト、暗号アルゴリズムの変更、明示的 key ID が必要になった場合は envelope version を上げ、AES-GCM/KMS adapter を再評価する。

### 起動時の鍵設定検証

- **Context**: 無効な鍵設定で WSGI/ASGI を起動せず、資格情報操作前に失敗させる必要があった。
- **Sources Consulted**:
  - [Django 6.0 application initialization](https://docs.djangoproject.com/en/6.0/ref/applications/)
  - [Django system check framework](https://docs.djangoproject.com/en/6.0/topics/checks/)
- **Findings**:
  - `AppConfig.ready()` は WSGI/ASGI と management command の Django 初期化で呼ばれる。
  - `ready()` 内の DB query は migration 前や全 command 起動時に問題を起こすため避ける必要がある。
  - system check はすべての WSGI 起動経路で自動実行される保証がないため、単独では fail-fast 境界にならない。
- **Implications**:
  - `LINE_CHANNEL_CREDENTIAL_KEYS` をカンマ区切りの「現用鍵、旧鍵…」として環境へ設定し、`AppConfig.ready()` から専用runtime loaderと純粋な parser/validator を呼ぶ。raw値をDjango settingsへ登録しない。
  - 未設定、空要素、不正な Fernet key、重複鍵を `ImproperlyConfigured` の安全な分類で拒否する。値や一部文字列もエラーへ含めない。
  - `SECRET_KEY` の参照、暗黙生成、DB query は行わない。検証処理は複数回呼ばれても同じ結果となる。

### 対話式秘密入力

- **Context**: bootstrap/update command が端末や process list に秘密を残さない入力方法が必要だった。
- **Sources Consulted**:
  - [Python 3.14 getpass](https://docs.python.org/3.14/library/getpass.html)
  - [Django custom management commands](https://docs.djangoproject.com/ja/6.0/howto/custom-management-commands/)
- **Findings**:
  - `getpass.getpass()` は既定で入力を表示しないが、非表示入力を利用できない環境では `GetPassWarning` を出して stdin にフォールバックする場合がある。
  - Django management command は `self.stdout`/`self.stderr` を使うことで出力テストが可能である。
- **Implications**:
  - 秘密は CLI option/argument では受け付けず、`getpass` のみで入力する。
  - `GetPassWarning` をエラーとして扱い、非表示を保証できない環境では保存前に中止する。
  - stdout/stderr は公開 ID、有効状態、設定済み状態、時刻、安全な結果分類だけを出力する。

### 原子性、並行性、中断可能なローテーション

- **Context**: 2資格情報の部分更新、通常更新との競合、中断後の新旧混在を安全に扱う必要があった。
- **Sources Consulted**:
  - [Django database transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/)
  - [MySQL 8.4 locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)
  - [MySQL 8.4 deadlock guidance](https://dev.mysql.com/doc/refman/8.4/en/innodb-deadlocks.html)
- **Findings**:
  - `transaction.atomic()` は例外時に対象変更を rollback する。
  - InnoDB の `SELECT ... FOR UPDATE` は transaction 完了まで行更新を直列化する。
  - 長い transaction はロック競合を増やすため、ローテーション全件を1 transaction に含めるべきでない。
- **Implications**:
  - 登録と資格情報ペア更新は1 transaction とし、暗号化に失敗した値を保存しない。
  - ローテーションは公開 ID の安定した順序で1行ずつ lock し、両方の旧値を復号、rotate、現用鍵単独で復号・context 照合してから同一 UPDATE で保存する。
  - 現用鍵で両方を検証できる行は再実行時に skip し、失敗行は一切変更しない。最終 sweep が全件成功した場合だけ完了とする。
  - command の二重起動は MySQL advisory lock で拒否し、通常更新との競合は行 lock で直列化する。

### MySQL の暗号文保存とログ

- **Context**: 可変長暗号文を安全に保存し、運用ログへ暗号文を出さない必要があった。
- **Sources Consulted**:
  - [MySQL 8.4 string storage requirements](https://dev.mysql.com/doc/refman/8.4/en/storage-requirements.html)
  - [Django model fields](https://docs.djangoproject.com/en/6.0/ref/models/fields/)
  - [Django database logging](https://docs.djangoproject.com/ja/6.0/ref/logging/#django-db-backends)
- **Findings**:
  - 暗号文は検索・一意判定・並び替えの対象ではないため、索引不要である。
  - Django database debug logging は SQL parameters を含み、暗号文をログへ出す可能性がある。
- **Implications**:
  - 暗号文列は非 null・非空の unindexed binary storage とし、資格情報ペアを同一行へ保持する。
  - `django.db.backends` の SQL parameter debug logging を資格情報操作の通常運用で有効にしない。DB general query log も秘密データと同等にアクセス制御する。

### 設計検証後のRepository・transaction ownership補強

- **Context**: 初版設計では `LineChannelRepository` がP0依存でありながらinterface、transaction owner、advisory lock解放契約、concrete componentの組立方法が未定義だった。
- **Sources Consulted**: `.agents/skills/kiro-validate-design/rules/design-review.md`、`backend/delivery/services.py`、`.kiro/steering/structure.md`、MySQL 8.4 locking functions documentation
- **Findings**:
  - 既存Backendではapplication serviceがtransactionを所有する。repository層は既存標準ではないため、新設する境界を設計で固定する必要がある。
  - MySQL `GET_LOCK()` はtransaction commit/rollbackで解放されず、同一connectionでの明示 `RELEASE_LOCK()` またはsession終了が必要である。
- **Implications**:
  - 通常mutationは `LineChannelService`、rotationの各行transactionは `CredentialRotationService` が所有し、repositoryのlocked methodはtransaction内だけで使用する。
  - advisory lockは同一connectionのcontext managerが `finally` で明示解放し、正常・busy・例外・割込み後の再取得をMySQL integration testで検証する。
  - `container.py` をcomposition rootとし、validated keyringからconcrete cipher/repository/serviceをconstructor injectionで構築する。

### DEBUG query captureと暗号文非露出

- **Context**: Requirement 7.1は暗号文のデバッグ出力も禁止するが、既存Composeは `DJANGO_DEBUG=true` を既定としていた。
- **Sources Consulted**: Django 6.0 logging documentation、Django database query inspection documentation、`backend/config/settings.py`、`compose.yaml`
- **Findings**:
  - Djangoは `DEBUG=True` のときSQLとparameterをdebug cursor、`django.db.backends`、`connection.queries`へ保持し得る。BinaryFieldへの暗号文write/readも対象になる。
  - logger levelだけを上げてもdebug cursorのquery captureを設計上無効化したことにはならない。
- **Implications**:
  - `LineChannelsConfig.ready()` は `DEBUG=True` をDB access前に拒否し、Compose既定をfalseへ変更する。
  - `django.db.backends` は `WARNING` 以上・非伝播、MySQLは `general_log=OFF` を明示し、資格情報DBでquery logを有効にする運用をサポートしない。
  - startupとcanary integration testで、query履歴、log、例外、command outputの非露出を検証する。

### Keyring raw grammar

- **Context**: 初版設計は先頭primaryと複数鍵の意味を定義したが、単一環境変数の直列化・正規化規則が不足していた。
- **Sources Consulted**: Fernet key format documentation、Docker Compose environment interpolation、`.env.example`
- **Findings**:
  - Fernet keyのURL-safe Base64 alphabetはcommaを含まないため、ASCII commaを曖昧性のない区切りとして利用できる。
  - trimや寛容なBase64 decodeは設定ミスや表記違いの重複を隠す可能性がある。
- **Implications**:
  - raw grammarを `FERNET_KEY(,FERNET_KEY)*` に固定し、whitespace、quote、空要素、非canonical encodingを拒否する。
  - 重複はdecode後の32 byte keyで判定し、test keyは実行時生成してsubprocess環境へ注入する。
  - raw値は`os.environ`から専用runtime loaderだけが読み、検証後はimmutable keyringだけをprivate process stateへ保持する。`settings.LINE_CHANNEL_CREDENTIAL_KEYS`は定義しない。

### 設計再検証後のkeyring非露出とtest bootstrap補強

- **Context**: 初版設計はraw keyringをDjango settingsへ保持し、test setupでephemeral keyを生成するとしていたが、標準management commandの出力経路とDjango初期化順序が未解決だった。
- **Sources Consulted**:
  - [Django `diffsettings` documentation](https://docs.djangoproject.com/en/6.0/ref/django-admin/#diffsettings)
  - [Django 6.0 `diffsettings` source](https://github.com/django/django/blob/stable/6.0.x/django/core/management/commands/diffsettings.py)
  - [Django application initialization](https://docs.djangoproject.com/en/6.0/ref/applications/#initialization-process)
  - `.kiro/steering/tech.md` のBackend標準test command
- **Findings**:
  - `diffsettings`はDjango settingsの追加・変更値を`repr()`で列挙するため、raw keyringを大文字settingへ置くと通常の管理操作で鍵がstdoutへ露出する。
  - `AppConfig.ready()`はmanagement commandのDjango setup中に実行され、test class/setupより先に到達する。test setup内の鍵生成では親`manage.py test`のstartup検証を通過できない。
- **Implications**:
  - raw keyringはDjango settingsから除外し、`linechannels.runtime`が環境から直接読み、検証済みkeyringだけをprivate stateとして`apps.py`と`container.py`へ渡す。
  - `backend/config/test_settings.py`を明示的なtest settings moduleとして追加し、base settings import前に毎回ephemeral Fernet keyを生成して`DJANGO_DEBUG=false`とともに環境へ設定する。本番settingsにはtest key生成やfallbackを追加しない。
  - 標準Backend test commandを`python manage.py test --settings=config.test_settings`へ変更し、startup failure testは`config.settings`を使う子subprocessへ個別envを渡す。
  - canary keyを使った`diffsettings`とsettings属性列挙の非露出を回帰テストに追加する。

### 設計再検証後の利用・更新・削除契約補強

- **Context**: safe wrapperから平文を利用する唯一の境界、資格情報置換と再enableの同時指定順序、OneToOneの削除方針が実装者判断に残っていた。
- **Findings**:
  - 後続LINE SDK/HMAC adapterは最終的にraw stringを必要とするため、暗黙変換を禁止するだけでは利用契約が完結しない。
  - 破損した旧pairを新pairへ置換しながら再enableする復旧操作では、旧pairを先に検証すると正当な復旧を拒否する。
  - `OneToOneField.on_delete`を未指定のままにすると、物理削除を提供しない境界とcredential保持方針が実装へ反映されない。
- **Implications**:
  - `AccessToken`/`ChannelSecret`は`reveal_for_use()`だけを明示的な平文境界とし、SDK/HMAC adapter直前以外での利用・保持・serializationを禁止する。
  - 新pair＋`is_active=True`は新pairをprimary encrypt・primary-only検証した後、pairとstateを同一transactionで保存する。新pairなしのenableだけが保存済みpairを検証する。
  - `LineChannelCredential.line_channel`は`on_delete=PROTECT`とし、将来の物理削除にはcredentialの明示破棄設計を必須とする。

### タスク生成レビュー後のrotation開始条件と責務分割

- **Context**: task graph sanity reviewで、rotationは「現用鍵と旧鍵1件以上」を開始条件とする一方、opaqueな`RuntimeKeyring`を受け取れるのはCipher constructorだけで、`CredentialRotationService`が開始可否を観測できないことが判明した。加えてrotation repository/advisory lock/行暗号処理/orchestrationと、TTY prompt/command dispatchが同一責務に集約され、1–3時間の実装単位へ分割しにくかった。
- **Sources Consulted**: 既存`design.md`のRuntimeKeyring、CredentialCipher、CredentialRotationService、CompositionRoot契約、`backend/config/settings.py`、`compose.yaml`、`.env.example`、task graph sanity review結果、`.agents/skills/kiro-spec-design/rules/design-review-gate.md`
- **Findings**:
  - primary-only keyringは通常の登録・取得には正当なので、app startup自体を失敗させてはならない。
  - serviceやcommandにraw environmentまたはkeyring countを読ませると、秘密設定のownershipと依存方向が崩れる。
  - rotation開始判定に必要なのは「旧鍵が1件以上あるか」という非秘密の能力分類だけで、鍵値、鍵ID、正確な鍵数は不要である。
  - `backend/config/settings.py`が`"linechannels.apps.LineChannelsConfig"`登録とDB loggerを所有し、Compose/.env/READMEはcontainer runtime configurationとして別の統合境界にすると、起動hook実装とのファイル競合を避けられる。
- **Implications**:
  - `CredentialCipher.rotation_readiness()` は `ready` / `old_key_missing` だけを返し、Rotation ServiceがDB接続とadvisory lockより前に判定する。単一鍵では変更ゼロの`configuration_required`を返す。
  - CompositionRootは同じCipher instanceをitem processorとRotation Serviceへ注入するだけで、readiness policyを判断しない。
  - `rotation_repository.py`、`rotation_lock.py`、`rotation_item.py`、`rotation.py`へ責務を分離する。item processorは通常の`process()`と、旧鍵fallbackや更新を行わないfinal sweep専用`verify_with_primary()`を分ける。
  - prompt処理は`management/prompts.py`へ分け、CompositionRootが標準getpass adapterを構築してDjango管理commandへ注入する。test moduleも同じ境界で分割する。
  - 要件変更は不要であり、4.6、6.1、6.8、7.1の既存意図を実装可能な契約へ閉じる設計修正として扱う。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| 独立 Django app + layer 分離 | Model、Crypto、Repository、Service、Command を `linechannels` 内で分離 | 既存構造と整合し、後続機能が repository 契約だけへ依存できる | 新しい管理コマンド規約が必要 | 採用 |
| 汎用 encrypted model field package | field access 時に透過復号する | コード量が少ない | 不要な秘密まで復号しやすく、用途別取得、safe repr、複数鍵、検証済み rotation の責務が隠れる | 不採用 |
| 完全な hexagonal architecture | port/adapter をすべて抽象化する | 交換可能性が高い | 単一 Django/MySQL 実装には抽象化が過剰 | repository と cipher 契約だけを明示 |
| AES-GCM custom envelope | AAD と key ID を明示する | context binding と O1 鍵選択 | nonce/keyring/rotation format を自作する必要 | 将来の versioned migration 候補 |

## Design Decisions

### Decision: 独立した `linechannels` app が基盤を所有する

- **Context**: 既存配信を壊さず、後続配信/Webhook が共通境界を使う必要がある。
- **Alternatives Considered**:
  1. `delivery` app へ追加する — Webhook と管理の資格情報所有権が配信へ漏れる。
  2. `config` へ追加する — 業務データとユースケースを project 設定へ混在させる。
- **Selected Approach**: `linechannels` がチャネル、暗号文、用途別 repository、管理 command、rotation を所有する。
- **Rationale**: project の service-first/Django app 境界と、後続仕様の共通 upstream という位置づけに合う。
- **Trade-offs**: app 間の契約を新設するが、既存 `delivery` は変更しない。
- **Follow-up**: 後続仕様は model を直接 import せず repository の typed contract を利用する。

### Decision: 資格情報は別暗号文・同一 credential 行で保持する

- **Context**: 用途別に一方だけを復号しつつ、登録・置換は完全なペアとして原子的に扱う必要がある。
- **Alternatives Considered**:
  1. token と secret を1 envelope にする — 取得時に不要な秘密も復号する。
  2. 別テーブルにする — 部分状態と transaction 管理が複雑になる。
- **Selected Approach**: 1対1 `LineChannelCredential` の同一行に2つの独立 Fernet token を保持する。
- **Rationale**: 最小の schema で 2.1–2.6 と 3.1–3.2 を同時に満たす。
- **Trade-offs**: 1資格情報だけの更新は提供せず、credential update は常にペア置換になる。
- **Follow-up**: 後続 UI も資格情報置換を write-only pair として扱う。

### Decision: 認証済み context envelope を採用する

- **Context**: Fernet の MAC だけでは正当な token の行/用途間差し替えを検出できない。
- **Alternatives Considered**:
  1. 値だけを Fernet 暗号化する — 差し替えを検出できない。
  2. AES-GCM + AAD + key ID を自作する — 現スコープには複雑すぎる。
- **Selected Approach**: version、公開 UUID、credential kind、秘密値を含む envelope を Fernet 認証し、復号後に context を必須照合する。
- **Rationale**: MultiFernet の安全な recipes API を維持しながら context binding を実現する。
- **Trade-offs**: 鍵選択は設定順の O鍵数であり、明示 key ID はない。
- **Follow-up**: envelope version を migration seam として保持する。

### Decision: keyring は単一の順序付き専用設定とする

- **Context**: 新規 write を現用鍵へ固定し、旧暗号文を移行中だけ読める必要がある。
- **Alternatives Considered**:
  1. 現用鍵と旧鍵を別変数にする — 順序と複数旧鍵の扱いが分散する。
  2. Django `SECRET_KEY` を利用する — 責務分離と撤去手順に反する。
- **Selected Approach**: `LINE_CHANNEL_CREDENTIAL_KEYS` をcanonical Fernet keyの厳密な `FERNET_KEY(,FERNET_KEY)*` とし、先頭を現用鍵、後続を read-old 鍵とする。whitespace/quote/空要素/非canonical encodingを拒否し、decode後bytesで重複判定する。raw値はDjango settingsへ登録せず専用runtime loaderが環境から直接読み、検証済みprivate stateだけを共有する。
- **Rationale**: MultiFernet の契約と一致し、設定と rotate の意味が一意になる。
- **Trade-offs**: カンマを含まない Fernet key format に依存する。
- **Follow-up**: README にgrammar、one-shot生成、配布、再起動、rotation、最終検証、旧鍵撤去、backup の順序を記録する。test keyは明示的な`config.test_settings`がbase settings import前に実行時生成し、`diffsettings`非露出を回帰検証する。

### Decision: Serviceがtransaction、Repositoryがlocked persistenceを所有する

- **Context**: repository abstractionを新設する一方、原子性とlock lifecycleの責任が曖昧だと並行更新・中断再実行の実装が分岐する。
- **Alternatives Considered**:
  1. repository methodごとにtransactionを内包する — 複数aggregate操作のatomic boundaryが分断される。
  2. commandがORMとtransactionを直接扱う — application service境界が崩れる。
- **Selected Approach**: 通常mutationは `LineChannelService`、rotationは `CredentialRotationService` がtransactionを所有し、`DjangoLineChannelRepository` はtransaction内限定のlocked persistenceを提供する。advisory lockは同一connectionのcontext managerが明示解放する。
- **Rationale**: application use case単位の原子性と、MySQL固有lock処理の隔離を両立する。
- **Trade-offs**: repository methodにtransaction内preconditionが生じる。
- **Follow-up**: transaction外失敗、pair rollback、全advisory lock終了経路をMySQL integration testで検証する。

### Decision: 資格情報基盤ではDjango DEBUGを禁止する

- **Context**: Requirement 7.1は暗号文のdebug出力も禁止するが、Django `DEBUG=True` はSQL parameterをquery captureへ保持し得る。
- **Alternatives Considered**:
  1. logger levelだけを上げる — `connection.queries`/debug cursorを無効化できない。
  2. custom DB backendで暗号文だけredactする — 全ORM経路の正しさと将来互換性を負う複雑さが大きい。
- **Selected Approach**: `LineChannelsConfig.ready()` で `DEBUG=True` をfail closedとし、Compose既定をfalse、DB loggerを非伝播、MySQL general logをOFFにする。
- **Rationale**: 最小で検証可能な仕組みで、暗号文のdebug artifact残留を防ぐ。
- **Trade-offs**: 資格情報基盤を有効にしたBackendではDjango debug page/query inspectionを利用できない。
- **Follow-up**: 起動失敗とquery/log/exception/outputのcanary非露出を回帰テストへ追加する。

### Decision: ローテーションは行単位 commit と最終 sweep を分離する

- **Context**: 中断可能性と、完了時の強い保証を両立する必要がある。
- **Alternatives Considered**:
  1. 全件1 transaction — 中断時は戻せるが長時間 lock となり進捗を保持できない。
  2. 検証なしの一括 update — 破損や誤鍵でデータを失う。
- **Selected Approach**: 行単位で復号・rotate・現用鍵検証・commit し、最後に全件を現用鍵単独で再検証する。
- **Rationale**: 中断後も新旧 keyring で読め、再実行で安全に収束する。
- **Trade-offs**: 最終 sweep 中の通常更新と競合し得るが、通常 write も現用鍵を使うため保証を弱めない。
- **Follow-up**: MySQL 実環境の `TransactionTestCase` で中断・競合・再実行を検証する。

### Decision: Cipherの非秘密readinessでrotationをpre-DB gateする

- **Context**: opaque keyringを維持しながら、Rotation Serviceが複数鍵の開始条件を判定できる契約が必要である。
- **Alternatives Considered**:
  1. RuntimeKeyringが鍵数またはfallback一覧を公開する — keyring consumerと秘密設定の観測面が拡大する。
  2. CompositionRootが単一鍵ではRotation Service構築を拒否する — 通常の登録・取得に有効なprocessでrotation commandだけを安全に拒否できず、policyがfactoryへ漏れる。
  3. Rotation Serviceが環境変数を再読込する — runtime ownershipと起動時検証済みstateを迂回する。
- **Selected Approach**: Cipherが`RotationReadiness = Literal["ready", "old_key_missing"]`を返し、Rotation ServiceがDB/lockより先に判定する。`old_key_missing`は`RotationSummary.status="configuration_required"`、全count 0、failure空、`old_keys_removable=False`へ写像する。
- **Rationale**: 鍵素材、鍵ID、鍵数を露出せず、`types → crypto → rotation → command`の依存方向とuse-case policy ownershipを維持できる。
- **Trade-offs**: Cipher contractに暗号操作以外の能力照会が1つ増えるが、rotationに必要な最小情報に限定される。
- **Follow-up**: 単一鍵でrepository/lock未呼出し、複数鍵でのみ走査開始、readinessのrepr/log非露出を独立testで検証する。

### Decision: 設計を必要最小限の基盤へ限定する

- **Context**: 後続 UI/Webhook/配信の要望を先取りすると ownership が曖昧になる。
- **Alternatives Considered**:
  1. チャネル CRUD API、Admin、接続確認 API も同時追加する。
  2. 既存配信 gateway を直ちに repository へ移行する。
- **Selected Approach**: typed repository と対話コマンドまでを公開し、HTTP/UI/LINE API 呼出しは追加しない。
- **Rationale**: 現要件を満たす最小構成で、後続仕様の revalidation seam が明確になる。
- **Trade-offs**: 初期操作は CLI に限られる。
- **Follow-up**: 後続仕様ごとに repository contract と public ID の互換性を再確認する。

### Decision: 既存 LINE 資格情報環境変数は段階的に撤去する

- **Context**: 新基盤は token/secret をDBへ保存する一方、既存固定配信は access token 環境変数を直接読む。
- **Alternatives Considered**:
  1. 本仕様で token/secret の両環境変数を即時削除する — 既存配信が設定不足で停止する。
  2. 両方を無期限に残す — 未使用 secret の露出面と移行負債が残る。
- **Selected Approach**: 未使用の `LINE_CHANNEL_SECRET` はDB登録確認後に本仕様で削除し、`LINE_CHANNEL_ACCESS_TOKEN` は後続 `linked-recipient-delivery` の repository 移行完了まで暫定維持する。
- **Rationale**: 既存配信の回帰を避けながら、不要になった秘密から順に環境変数の露出面を減らせる。
- **Trade-offs**: 本仕様完了から後続配信移行まで access token がDBと環境変数に重複する。
- **Follow-up**: 後続配信仕様の完了条件に Compose、`.env.example`、operator のローカル `.env` から `LINE_CHANNEL_ACCESS_TOKEN` を削除する確認を含める。

## Risks & Mitigations

- 鍵を失うと復号不能 — DB 外 backup、rotation 完了前の旧鍵保持、backup 保持期間と鍵撤去を同期する。
- DB 暗号文の差し替え — 認証済み envelope の公開 UUID/credential kind 照合で拒否する。
- `getpass` の echo fallback — `GetPassWarning` を失敗として保存前に中止する。
- 暗号文や秘密値の accidental repr/log — safe wrapper、明示 `__str__`、raw exception の分類、Admin/API 非公開、DB parameter debug log 禁止で抑止する。
- rotation と通常更新の lost update — 同一 credential 行を `select_for_update()` し、lock 後の値だけを処理する。
- 複数 rotation process の異なる keyring — command scope の MySQL advisory lock と同一設定配布手順で拒否する。
- advisory lockの解放漏れ — 同一connectionのcontext managerと `finally`、全終了経路後の再取得testで防ぐ。
- 起動 fail-fast が migration/test も止める — Compose と test command に実行時生成の専用鍵と `DEBUG=False` を明示注入し、既定鍵は作らない。
- raw keyringのsettings列挙露出 — raw値をDjango settingsへ登録せず、専用runtime loaderと`diffsettings` canary testで防ぐ。
- DEBUG禁止で開発時のdebug pageが使えない — safe error/resultと対象testを診断境界にし、秘密を含むSQL inspectionを優先しない。
- Fernet が同一 context の過去の正当な暗号文 replay を検出しない — 外部単調 counter/KMS は対象外とし、DB backup/restore を運用境界として管理する。
- Backend process/host の侵害では平文と鍵が露出し得る — 本仕様は DB at-rest と通常出力の保護を対象とし、host/KMS security は対象外と明記する。

## References

- [cryptography 49.0.0](https://pypi.org/project/cryptography/) — Python 3.14 対応と配布版
- [Fernet and MultiFernet](https://cryptography.io/en/latest/fernet/) — 認証付き暗号、複数鍵、rotation 契約
- [Django application initialization](https://docs.djangoproject.com/en/6.0/ref/applications/) — `AppConfig.ready()` の起動特性
- [Django database logging](https://docs.djangoproject.com/ja/6.0/ref/logging/#django-db-backends) — DEBUG時のSQL/parameter logging
- [Django transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/) — `atomic()` の原子性
- [Python getpass](https://docs.python.org/3.14/library/getpass.html) — 非表示入力と fallback warning
- [MySQL locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html) — `FOR UPDATE` の排他制御
- [MySQL locking functions](https://dev.mysql.com/doc/refman/8.4/en/locking-functions.html) — advisory lockのconnection scopeと明示解放
- [LINE Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/#get-bot-info) — bot user ID と Basic ID

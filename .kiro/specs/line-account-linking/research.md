# 調査・設計判断記録

## Summary

- **Feature**: `line-account-linking`
- **Discovery Scope**: Complex Integration / Full Discovery
- **Key Findings**:
  - LINE IDトークンにはprovider ID claimがない。`aud`でLINE Loginチャネルを確定し、そのチャネルへ事前設定したproviderメタデータからproviderを導出する必要がある。
  - 現行実装にはチャネル単位のproviderメタデータと、安全な公開情報だけを列挙するread-only contractがない。`line-channel-foundation`designのDownstream Extension Contractを根拠に、本仕様が実装タスクを所有する。
  - LIFFの友だち状態はFrontend申告を信用できない。Backendが操作時のLIFF access tokenを検証し、LINEのfriendship status APIを呼ぶ。access tokenは永続化しない。
  - LIFF Endpoint URLはLINE Developers Consoleへ`https://${NGROK_DOMAIN}/liff`を設定し、LIFF URLは`VITE_LIFF_ID`から`https://liff.line.me/${VITE_LIFF_ID}`を導出する。外部loginのredirect URIは`${window.location.origin}/liff`から導出し、公開環境でEndpoint URLと一致させる。
  - 全連携解除はLINE deauthorizeを含める。「成功確認済み」はLINE 204と`local_deletion_pending`マーカのcommitが両方完了した状態と定義し、その後はLINEを再呼出しない。marker commit前の障害は結果不確定として再認証から再開する。
  - deauthorize POSTは不可逆な外部作用としてin-request自動retryせず、MySQL session advisory lockで複数端末からの実行をsingle-flightにする。pending attemptごとの`unlink_generation`で再linkをまたぐstale requestを拒否する。preview tokenは削除対象snapshotへ結合し、fence設定と同じOwnerAccount lock下で再検証する。
  - LINE Login secretとowner digestはDjango settingsへ載せず、`lineaccounts.runtime`がDB接続前にfail closed検証する。owner digestはsubjectを表示しないlocal management commandで準備する。
  - `line-account-linking`は`linechannels`の資格情報契約を変更せず、非秘密`provider_id`とread-only directoryの下流拡張実装・移行・テストを所有する。

## Research Log

### 既存コードと統合境界

- **Context**: 既存のFrontend、配信API、チャネル基盤へ認証・配信先管理を追加する変更範囲を確定するため。
- **Sources Consulted**: `frontend/src/App.tsx`、`frontend/src/deliveryApi.ts`、`frontend/src/deliveryDto.ts`、`frontend/src/deliveryState.ts`、`backend/delivery/`、`backend/linechannels/`、`backend/config/settings.py`、`backend/config/urls.py`、`compose.yaml`、`.kiro/steering/product.md`、`.kiro/steering/tech.md`、`.kiro/steering/structure.md`、`.kiro/steering/roadmap.md`
- **Findings**:
  - FrontendはDTO検証、API client、純粋な状態遷移、UIを分離している。認証もLIFF adapter、境界DTO、API client、認証状態、Auth Gateへ同じ方向で分けられる。
  - BackendはView/Serializer、Service、Model/Repository、GatewayをDjango app内で分離している。新規`lineaccounts` appを同じ依存方向で追加できる。
  - `backend/delivery/views.py`の共通Viewが認証・permissionを明示的に無効化している。ここへowner認証境界を適用すれば、入力検証とLINE送信より先に未認証者を拒否できる。
  - `DeliveryAttempt`はLINE identityや配信先への外部キーを持たない。全連携解除で既存配信監査を削除しない構造を維持できる。
  - Django session、CSRF middleware、AuthenticationMiddlewareは導入済みだが、公開origin向けSecure Cookie、trusted origin、HTTPS proxy認識、DRFの認証・permission既定値は未設定である。
  - Vite proxyは`changeOrigin: true`で`/api`をBackendへ転送する。同一公開originを維持しつつ、元のOriginをDjango CSRFの完全一致許可リストで検証する必要がある。
- **Implications**:
  - `lineaccounts`がidentity、owner grant、端末別owner session、recipientを別ライフサイクルとして所有する。
  - 配信の既存HTTP成功契約、確認、冪等性、結果状態、監査は変更せず、共通View境界だけを保護する。
  - 未認証のセッション確立POSTはDRF SessionAuthenticationだけではCSRF検査されないため、CSRF cookie発行GETと明示的なCSRF保護が必要である。

### LINE IDトークン検証とprovider導出

- **Context**: 2.1、2.2、3.1、4.2、4.8、5.4の本人性とprovider境界を実装可能な契約にするため。
- **Sources Consulted**: [LINE IDトークン検証](https://developers.line.biz/en/docs/line-login/verify-id-token/)、[LINE Login API reference](https://developers.line.biz/en/reference/line-login/)、[LINE provider設計](https://developers.line.biz/en/docs/line-login/getting-started/)、[LIFFプロフィール情報の安全な利用](https://developers.line.biz/en/docs/liff/using-user-profile/)
- **Findings**:
  - Frontendは`liff.getIDToken()`のraw tokenだけを本人証明としてBackendへ渡す。`getDecodedIDToken()`や`getProfile()`の結果を認証根拠にしない。
  - 最小構成はLINEのverify endpointへraw ID tokenと期待LINE Login channel IDを送り、成功応答でも`iss`、`aud`、`exp`、非空`sub`を防御的に再確認する方式である。
  - IDトークンの標準claimにprovider IDはない。`aud`と一致した設定済みLINE Loginチャネルからproviderを導出する。
  - 同一provider内ではLINE LoginとMessaging APIのuser IDが一致するが、異なるproviderでは一致しない。
  - `liff.login()`にはアプリ指定nonceがない。検証済みでないnonceをリプレイ防止根拠にしない。
- **Implications**:
  - identityの自然キーは内部限定の`provider_id + subject`とする。subjectは公開DTO、画面、通常ログ、reprへ出さない。
  - owner適格条件は設定済みproviderとprovider-scoped subjectの不可逆digestを用い、未設定・形式不正時はfail closedとする案が適合する。
  - remote verifyを採用すればJWT/JWKライブラリは不要である。LINE障害時にdecode-onlyへフォールバックしない。

### Messaging APIチャネルのprovider情報と公開directory

- **Context**: 5.1、5.3、5.4、5.9、6.4、6.6が依存するチャネル情報を調査するため。
- **Sources Consulted**: `backend/linechannels/models.py`、`backend/linechannels/types.py`、`backend/linechannels/repositories.py`、`backend/linechannels/services.py`、`.kiro/specs/line-channel-foundation/design.md`
- **Findings**:
  - `LineChannel`は`public_id`、Messaging API channel ID、bot user ID、label、active stateだけを持ち、provider IDを持たない。
  - 既存`LineChannelRepository`はtransaction内mutation用、`CredentialRepository`は秘密取得用である。active channelを非秘密projectionとして一覧・解決するquery contractはない。
  - 既存`PublicChannelSummary`はMessaging API channel IDとbot user IDを含むため、そのまま公開APIへ返せない。
  - foundation設計は識別情報と公開contractの変更を再検証triggerとしている。
- **Implications**:
  - foundationへ非秘密`provider_id`と、`public_id`、label、provider、active stateだけを返すread-only directory contractを追加する必要がある。
  - これは`line-channel-foundation`の上流境界拡張であり、同specと後続`line-webhook-interaction`、`linked-recipient-delivery`、`line-channel-admin-ui`の再検証対象である。
  - provider未設定の既存チャネルをaccount linkingの登録候補へ出してはならない。backfillと制約強化の段階をmigration strategyで定義する必要がある。

### 友だち状態の信頼境界

- **Context**: 5.7、5.8、6.5の友だち状態と配信可否を安全に決めるため。
- **Sources Consulted**: [LINE公式アカウント連携](https://developers.line.biz/en/docs/line-login/link-a-bot/)、[LINE Login API reference](https://developers.line.biz/en/reference/line-login/)
- **Findings**:
  - LINE Loginチャネルにリンクできる公式アカウントは1つだけである。
  - `liff.getFriendship()`のFrontend結果は改変可能であり、Backendの永続状態の根拠にできない。
  - Backendはrecipient登録操作中だけLIFF access tokenを受け取り、token verify endpointで期待client IDと正の有効期間を確認後、friendship status APIを呼ぶ。
  - `friendFlag=false`は未友だちとブロック済みを区別しない。ドメイン値は`friend`、`not_friend`、`unknown`で十分である。
  - LIFFに直接紐づかないチャネルは`unknown`とし、後続Webhook仕様だけが検証済みイベントから更新する。
- **Implications**:
  - access tokenは操作中のgateway引数に限定し、DB、Django session、プロフィール、通常ログへ保存しない。
  - recipient作成要求は、LIFF直結チャネルのときだけwrite-only access tokenを要求する。LINE user IDを含む未知fieldをstrictに拒否する。
  - 配信可否はrecipient active、friendship=`friend`、channel activeの論理積としてquery時に導出し、重複保存しない。

### LIFF起動とFrontend状態遷移

- **Context**: 1.1から1.6、2.10のLIFFブラウザ・外部ブラウザ共通導線を設計するため。
- **Sources Consulted**: [LIFF API reference](https://developers.line.biz/en/reference/liff)、[LIFFアプリ開発](https://developers.line.biz/en/docs/liff/developing-liff-apps)、[LIFF release notes](https://developers.line.biz/en/docs/liff/release-notes)
- **Findings**:
  - 本環境のLIFF Endpoint URLは`https://${NGROK_DOMAIN}/liff`とし、LINE Developers ConsoleのLIFF設定へ登録する。
  - 利用者が開くLIFF URLは`https://liff.line.me/${VITE_LIFF_ID}`であり、Endpoint URLとは役割が異なる。
  - 外部browserからのloginでは`redirectUri`を`${window.location.origin}/liff`から導出し、公開環境ではEndpoint URLと同じ`https://${NGROK_DOMAIN}/liff`になる。
  - ページを開くたびに`liff.init()`を完了させ、完了前に認証情報を含み得る初期URLを変更・送信しない。
  - LIFFブラウザはログイン済みとなる。外部ブラウザは`isLoggedIn()`を確認し、未ログイン時に利用者操作の`liff.login({redirectUri})`を提供する。
  - 自動外部ブラウザログインは環境によって失敗・ループするため、要件の再試行・取消状態には明示ボタンが適する。
  - `@line/liff@2.29.1`が調査時点の現行SDKである。
- **Implications**:
  - source of truthは`VITE_LIFF_ID`と`NGROK_DOMAIN`だけとし、`VITE_LIFF_URL`は追加しない。Frontendは`NGROK_DOMAIN`を直接受け取らずBrowser locationからredirect URIを導出する。
  - 固定entry path `/liff`とHTTPSをFrontend起動前に検証し、exact originはVite allowed hostとBackend trusted origin/CSRFで検証する。ngrok domain変更時は環境変数とConsoleのEndpoint URLを同時に更新するが、LIFF IDが同じならLIFF URLは変わらない。
  - Frontend認証状態は`initializing`、`login_required`、`verifying`、`authenticated`、`anonymous`、`error`のdiscriminated unionで表す。
  - `authenticated`になるまで配信・管理Componentをmountしない。401受信時は現在の認証状態を破棄して再認証導線へ戻す。
  - SDKはtyped adapterに隔離し、UIテストではfakeを注入する。

### Django session、CSRF、公開HTTPS origin

- **Context**: 2.4から2.11、3.5から3.8、8.1から8.8を一貫して満たすため。
- **Sources Consulted**: [Django 6 CSRF](https://docs.djangoproject.com/en/6.0/ref/csrf/)、[Django 6 settings](https://docs.djangoproject.com/en/6.0/ref/settings/)、[DRF authentication](https://www.django-rest-framework.org/api-guide/authentication/)、`compose.yaml`、`frontend/vite.config.ts`
- **Findings**:
  - 同一originのVite `/api` proxy構成はDjango sessionに適する。
  - Secure、HttpOnly、SameSite Laxのsession cookieと、Secure、SameSite LaxのCSRF cookieが必要である。SameSite StrictはLINEからの戻り導線を壊し得る。
  - trusted originはngrokのexact HTTPS originだけを環境から構成する。
  - 現行Vite proxyは外部入力のforwarded headerを除去・再設定する保証を持たないため`SECURE_PROXY_SSL_HEADER`を設定せず、偽`X-Forwarded-Proto`を信頼しない。
  - 匿名login POSTにもCSRFを明示適用する。通常のAPI test clientではなく`enforce_csrf_checks=True`でOrigin、cookie、headerを検証する。
- **Implications**:
  - Django sessionにはopaqueなowner-session IDだけを保存し、DBの端末別session ledgerで有効期限、owner、失効状態を照合する案がidentity/owner/sessionの別ライフサイクルに適する。
  - Backendは内部HTTP requestをsecureと判定しないため、DjangoのReferer fallbackだけへ依存せず、全unsafe APIでexact HTTPS Origin headerを必須検証してからCSRF cookie/header tokenを検証する。
  - login成功時にsession keyをrotateし、端末logoutは現在のledgerだけを削除してsessionをflushする。全連携解除はidentity配下の全ledgerを同一transactionで削除する。
  - DRFのglobal defaultをowner認証・owner permissionとし、healthとlogin bootstrapだけを明示的に公開する。既存配信APIはView handler前に拒否される。

### LINE API障害と秘密情報の非露出

- **Context**: 1.5、2.2、8.4から8.7の安全な失敗を設計するため。
- **Sources Consulted**: [LINE Login API reference](https://developers.line.biz/en/reference/line-login/)、[DRF throttling](https://www.django-rest-framework.org/api-guide/throttling/)、既存`backend/delivery/gateway.py`、`backend/linechannels/types.py`
- **Findings**:
  - LINE Login APIは400系、429、500系を返し、rate limit閾値は公開していない。
  - read-only verify/profile/friendshipとstateless token発行は、400系を再試行せず、429、5xx、timeoutだけを短い上限付きretry候補にできる。不可逆なdeauthorize POSTは結果不確定窓を広げないため自動retry対象から除外する。既存owner sessionはLINE検証障害だけで失効させない。
  - raw LINE error、token、subject、プロフィールを公開応答・通常ログへ出さず、安全な分類へ変換する。
  - DRF throttlingはDoS防御の完全な境界ではない。
- **Implications**:
  - gateway resultは`invalid_proof`、`provider_unavailable`、`rate_limited`、`unexpected`等のtyped safe failureに変換する。
  - ログ可能なのはoperation、HTTP status、latency、存在する場合の`x-line-request-id`、safe result codeに限定する。
  - raw tokenを保持する型はserialization不能かつredacted reprとし、serializer fieldはwrite-onlyにする。

### 全連携解除とLINE deauthorizeの収束

- **Context**: 7.1から7.15をLINE公式の利用終了要件と整合させ、外部APIとDBの非原子性から収束させるため。
- **Sources Consulted**: [LIFF development guidelines](https://developers.line.biz/en/docs/liff/development-guidelines/)、[LINE deauthorize API](https://developers.line.biz/en/reference/line-login/)、[Channel access token](https://developers.line.biz/en/docs/basics/channel-access-token/)、[Stateless channel token](https://developers.line.biz/en/reference/messaging-api/#issue-stateless-channel-access-token)、`requirements.md` 7.1–7.15
- **Findings**:
  - LINE公式ガイドは、利用者がサービスを退会・利用終了するときにdeauthorize APIでアプリ権限を解除することを要求している。
  - `POST /user/v1/deauthorize`はLINE Login channel access tokenとfreshなLIFF user access tokenを要求し、204だけを成功として返す。
  - 単一owner・低頻度ではLINE Login channel ID/secretから`POST /oauth2/v3/token`で15分のstateless channel tokenを都度発行する構成が最小である。
  - deauthorizeの400は既解除と単なるuser token無効を区別できず、正式な冪等成功として扱えない。429、500、timeoutの後は結果不確定になり得る。
  - access tokenを永続化せず、外部APIとMySQLを単一transactionにできないため、明示的なpending stageと再認証resumeが必要である。
- **Implications**:
  - user tokenを検証後、ownerを`deauthorization_pending`へ遷移させ、全通常保護操作を拒否するdeletion fenceを設定する。
  - 複数端末の同時resumeはowner slot単位のMySQL session advisory lockでsingle-flightにし、lock非取得requestはLINEを呼ばず409 `unlink_in_progress`へ収束させる。lockは外部call中にDB transactionを保持せず、connection喪失時にはMySQLが解放する。
  - 204後は`line_deauthorized_at`と`local_deletion_pending`を同一transactionでcommitする。このcommit完了が「認可取消成功確認済み」の線形化点であり、以後はLINEを再呼出せずローカル削除だけをretryする。
  - 400、429、500、timeout、204後のmarker commit失敗は、外部成功を耐久性ある状態で証明できないため完了扱いにせず、同一request内で再送せずfreshなLINE再認証からresumeする。既解除済みなら再同意後に改めてdeauthorizeして収束する。
  - outbox方式はuser token永続化とローカル先行削除を必要とするため採用しない。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| App-local layered boundary | `lineaccounts`内でHTTP、service、repository、LINE gatewayを分離し、foundationの公開directory contractへ依存する | 既存構造と一致し、task境界とテスト責任が明確 | foundationのquery seam拡張が先行必須 | 採用 |
| Django auth Userへidentityを統合 | Django標準User/sessionをowner本人として使う | 標準SessionAuthenticationを直接利用できる | identity、owner権限、端末sessionのライフサイクルが結合し、将来の非owner identity拡張を阻害する | 不採用候補 |
| Owner session ledger | Django sessionにはopaque IDを置き、`OwnerSession`をidentity/owner grantとは別モデルで検証する | 端末単位logout、複数端末、全失効を直接表現できる | custom authenticatorとCSRF境界の厳密な実装が必要 | 採用 |
| Frontend friendship申告 | `liff.getFriendship()`結果をrequestで保存する | 実装が小さい | 改ざん可能で信頼境界を満たさない | 不採用 |
| Remote ID token verify | LINE verify endpointを認証時に呼ぶ | 署名鍵管理とJWT依存を追加しない | LINE障害時に新規sessionを確立できない | 採用、fail closed |
| Pending saga | owner stateを削除フェンスにしてdeauthorizeとローカル削除を段階化する | token非永続化、通常操作遮断、再開可能 | 不確定時に再認証が必要 | 採用 |
| Local delete plus outbox | ローカル削除後に暗号化tokenを保持してdeauthorizeを再試行する | ローカル削除が早い | token保持、owner不在retry、部分状態 | 不採用 |
| Local JWK verification | JWK cacheとES256固定でローカル検証する | verify endpointへの依存を減らせる | 鍵更新、cache、JWTライブラリ、安全なalgorithm固定が増える | 現スコープでは不採用 |

## Design Decisions

### Decision: LIFF URLはLIFF IDから導出し、Endpoint URLとredirect URIを単一契約にする

- **Context**: LINE Developers Consoleへ登録するEndpoint URL、利用者が開くLIFF URL、外部login後のredirect URIは役割が異なり、個別設定するとdomain変更時に不整合が起きる。
- **Alternatives Considered**:
  1. `VITE_LIFF_URL`、Endpoint URL、redirect URIをそれぞれ独立して管理する。
  2. `VITE_LIFF_ID`と`NGROK_DOMAIN`をsource of truthとし、3つのURLを導出する。
- **Selected Approach**: 2を採用する。LIFF URLは`https://liff.line.me/${VITE_LIFF_ID}`、Endpoint URLは`https://${NGROK_DOMAIN}/liff`とし、redirect URIは`${window.location.origin}/liff`から導出して公開環境でEndpoint URLと一致させる。
- **Rationale**: 重複設定をなくし、固定entry pathとexact originを実装・テスト・運用手順で一貫して検証できる。
- **Trade-offs**: ngrok domain変更時には環境変数に加えてLINE Developers ConsoleのEndpoint URL更新が必要である。LIFF IDが同じなら利用者向けLIFF URLは変更しない。
- **Follow-up**: READMEへConsole設定、domain変更手順、同一LINE Loginチャネル配下のLIFF IDを使う確認手順を記載する。

### Decision: identity、owner grant、端末session、recipientを分離する

- **Context**: 現在は単一ownerだが、将来は非owner identityと配信先参加を追加し得る。
- **Alternatives Considered**:
  1. `LineIdentity.is_owner`とDjango sessionだけで表す。
  2. identity、単一slotのowner grant、端末別owner session、recipientを別モデルにする。
- **Selected Approach**: 2を採用する。単一slotの`OwnerAccount`をlock rootとし、identity、session、recipientを別entityにする。
- **Rationale**: owner認可、検証済みidentity、端末session、チャネル別recipientの異なる削除・更新単位を明示できる。
- **Trade-offs**: modelとrepositoryは増えるが、2.6–2.11、3.4、6.8–6.9、7.4–7.7を無理なく表現できる。
- **Follow-up**: implementationでMySQL row lockと競合fault testを確認する。

### Decision: provider metadataと安全なdirectoryをfoundationへ追加する

- **Context**: IDトークンにprovider claimがなく、既存foundationにもprovider情報がない。
- **Alternatives Considered**:
  1. 全チャネル同一providerという運用前提だけで比較を省略する。
  2. account linking側にチャネルprovider対応表を重複保持する。
  3. foundationの`LineChannel`とread-only projectionへproviderを追加する。
- **Selected Approach**: 3を採用する。`linechannels`を長期的なデータ所有者としたまま、`line-account-linking`がfoundationの下流拡張契約に基づき`LineChannel`とread-only projectionへproviderを追加する。
- **Rationale**: チャネルのproviderはチャネルmetadataの責務であり、Webhook、配信、管理UIも再利用できる。5.4を検証可能にする。
- **Trade-offs**: 実装済みupstream specとmigrationの再検証が必要である。
- **Follow-up**: `line-channel-foundation`designのDownstream Extension Contractを統合境界とし、migration、backfill、directory、regression testのタスクは本仕様から生成する。既存CredentialRepository契約の回帰を同タスクで検証する。

### Decision: LINE本人証明と友だち状態を別の検証契約にする

- **Context**: ID tokenはsession確立に使い、friendshipはrecipient登録時点のcurrent stateが必要である。
- **Alternatives Considered**:
  1. 認証時のfriendship結果をsession期間中再利用する。
  2. recipient登録時だけfresh LIFF access tokenを検証しfriendship APIを呼ぶ。
- **Selected Approach**: 2を採用する。
- **Rationale**: access tokenを永続化せず、5.7の「現在」を登録操作へ近づけられる。
- **Trade-offs**: LIFF直結チャネルの登録requestだけwrite-only access tokenを運ぶ必要がある。
- **Follow-up**: tokenのstrict input、safe logging、LINE障害分類をAPI contractへ明記する。

### Decision: 全連携解除をdeletion fence付きpending sagaで収束させる

- **Context**: 公式deauthorizeと7.5の非部分状態を、外部APIとDBの非原子性の中で整合させる必要がある。
- **Alternatives Considered**:
  1. ローカルデータ削除だけを全連携解除とする。
  2. ローカル削除先行とtoken outboxでdeauthorizeを後追いする。
  3. deletion fence後にdeauthorizeし、LINE確認後にローカル削除する。
- **Selected Approach**: 3を採用する。owner stateは`active`、`deauthorization_pending`、`local_deletion_pending`、`vacant`を持つ。LINE 204と`local_deletion_pending`マーカのcommitの両方が完了した状態だけを成功確認済みと定義する。deauthorizeはowner slot単位のMySQL session advisory lockでsingle-flightにし、POST自体は自動retryしない。pendingごとの`unlink_generation`をmarker更新/finalizeのexpected valueにしてABAを防ぐ。preview confirmationはidentityとrecipient snapshotへ結合し、fence設定と同じOwnerAccount lock下で再検証する。
- **Rationale**: tokenを永続化せず、LINE未確認中の配信・管理を拒否し、耐久性ある成功marker後のローカル失敗ではLINEを重複実行せず再開できる。
- **Trade-offs**: timeoutや204直後のmarker commit障害では結果を断定できず、freshなLINE再認証と場合によっては再同意が必要になる。これは外部APIとMySQLに共通transactionがないことによる明示的な不確定窓である。single-flight中の別端末requestは待たずに409を受け取り、statusを再取得する。
- **Follow-up**: 400、429、500、timeout、204後marker commit失敗、marker commit後のlocal delete失敗、二重端末resume、lock connection喪失、preview後snapshot変更をfault injectionし、それぞれ再認証resumeとlocal-only retryへ収束することを検証する。

### Decision: account runtime secretをDjango settingsから分離する

- **Context**: LINE Login channel secretとowner digestをsettings属性へ載せると、`diffsettings`や設定列挙経路へ露出し、既存`linechannels`の秘密境界とも不整合になる。
- **Alternatives Considered**:
  1. `config.settings`で全環境変数を読み、serviceがsettingsを参照する。
  2. `lineaccounts.runtime`だけがraw環境変数を読み、AppConfigでDB接続前に検証済みredacted objectへ変換する。
- **Selected Approach**: 2を採用する。channel secretとowner digestをsettings、DB、container DTOへ載せず、immutable runtime objectだけを依存注入する。owner digest未設定はstartup crashではなく`OwnerEligibilityUnavailable`としてloginだけをfail closedにし、その状態でもsubject非echoのlocal management commandでdigestを生成できるようにする。unlink confirmationの署名に使う`DJANGO_SECRET_KEY`は明示設定・32文字以上・既知default以外をstartupで要求する。
- **Rationale**: 既存`linechannels.runtime`と同じfail-closed・非露出パターンを再利用でき、owner事前許可のsetup手順も実装可能になる。
- **Trade-offs**: startup subprocess testと専用management commandが増える。DB上のlinked channel/provider整合はAppConfigでは確認せず、最初のaccount operationでfail closed検証する。
- **Follow-up**: settings/diffsettings/log canary、DB接続前停止、digest command stdout/stderr非露出、linked channel不一致をテストする。

### Decision: generalize the boundary, not the implementation

- **Context**: 将来の非owner identityとWebhook friendship更新を妨げず、現specを過剰設計しないため。
- **Alternatives Considered**:
  1. すべてのidentityをowner、すべてのrecipientをowner本人として単一modelへ統合する。
  2. interfaceはidentity、authorization、session、recipientを分離し、実装は単一ownerに限定する。
- **Selected Approach**: 2を採用する。
- **Rationale**: 将来拡張に必要なseamを保ちつつ、複数owner、RBAC、event基盤、workerを実装しない。
- **Trade-offs**: modelは4entityになるが、不要なrepository層やevent busは追加しない。
- **Follow-up**: task生成時に各entity/fileのboundary annotationへ反映する。

## Risks & Mitigations

- provider情報をglobal前提だけで済ませると異providerチャネルを登録前に拒否できない — foundationにチャネル単位providerとquery contractを追加する。
- 初回owner登録の競合で複数ownerが成立する — 単一slot行とDB unique constraint、row lock、競合テストを組み合わせる。
- LIFF token、subject、session情報がrepr、serializer error、request logへ漏れる — redacted型、write-only field、body非記録、secret canary testを適用する。
- DRFの匿名login POSTがCSRF検査を通らない — bootstrap GETでtokenを発行し、login view自体へCSRF保護を明示する。
- 匿名session keyがowner認証後も維持されsession fixation境界が曖昧になる — 初回、追加端末、pending再認証の成功ごとに`cycle_key()`相当を必須化し、旧key拒否をテストする。
- LIFF直結channel判定が環境変数の隠れ依存になる — 起動時検証済み`LiffLinkedChannelPolicy`を`RecipientService`へ注入する。
- DRF標準Serializerが未知fieldを無視する — `StrictRequestSerializer`が入力key集合を明示検証し、user IDやtoken aliasを拒否する。
- proxy HTTPS誤認またはwildcard trusted originでcookie/origin保護が崩れる — 現行構成では`SECURE_PROXY_SSL_HEADER`を未設定とし、exact origin、Secure Cookie、偽forwarded headerを信頼しない設定テストで固定する。
- LIFF URL、Endpoint URL、redirect URIを個別管理してdomain変更時に不整合になる — `VITE_LIFF_ID`と`NGROK_DOMAIN`から導出し、Console設定とruntimeの完全一致を検証する。
- LINE verify/friendship API障害で誤って認証・配信可能化する — fail closed、安全な障害分類、read-only operationだけに上限付きretryを許可する。
- deauthorizeが自動retryや複数端末から重複実行される — POSTは自動retryせず、owner slot単位のsession advisory lockでsingle-flightにする。
- 完了済み旧requestが再link後の新unlink attemptをfinalizeする — pendingごとの`unlink_generation`一致をmarker更新とfinalizeで必須にする。
- preview後に削除対象が変わる — identity/recipient snapshotへ結合した短期confirmationをOwnerAccount lock下で再検証してからfenceを設定する。
- LINE Login secretやowner digestがsettings列挙へ露出する、または既知Django secretでconfirmationが署名される — `lineaccounts.runtime`だけがraw LINE環境を読み、DB接続前にredacted objectへ変換し、Django secretの明示設定と既知default拒否を検証する。
- deauthorizeとローカル削除が部分状態になる — pending stage、通常操作拒否、204とmarker commitの成功確認線形化点、確認済み後のlocal-only retryで収束させる。

## References

- [LIFF API reference](https://developers.line.biz/en/reference/liff) — LIFF初期化、login、token取得
- [LIFFアプリ開発](https://developers.line.biz/en/docs/liff/developing-liff-apps) — Endpoint URL、redirect、初期化制約
- [LIFF development guidelines](https://developers.line.biz/en/docs/liff/development-guidelines/) — 利用終了時のdeauthorize
- [LINE IDトークン検証](https://developers.line.biz/en/docs/line-login/verify-id-token/) — Backend検証方法
- [LINE Login API reference](https://developers.line.biz/en/reference/line-login/) — verify、friendship、deauthorize、エラー契約
- [LINE provider設計](https://developers.line.biz/en/docs/line-login/getting-started/) — provider-scoped user ID
- [LINE公式アカウント連携](https://developers.line.biz/en/docs/line-login/link-a-bot/) — linked OAとfriendship制約
- [Channel access token](https://developers.line.biz/en/docs/basics/channel-access-token/) — stateless tokenの性質と有効期間
- [Messaging API channel token reference](https://developers.line.biz/en/reference/messaging-api/#issue-stateless-channel-access-token) — LINE Login channel ID/secretからのstateless token発行契約
- [Django 6 CSRF](https://docs.djangoproject.com/en/6.0/ref/csrf/) — Origin、cookie/header検証
- [Django 6 settings](https://docs.djangoproject.com/en/6.0/ref/settings/) — Secure Cookie、proxy HTTPS設定
- [DRF authentication](https://www.django-rest-framework.org/api-guide/authentication/) — session認証とCSRF
- [HTTPX](https://www.python-httpx.org/) — typed同期HTTP clientと明示timeout

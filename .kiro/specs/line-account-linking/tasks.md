# Implementation Plan

- [x] 1. 実装に必要な依存・runtime・公開origin設定を整える
- [x] 1.1 Backendへ固定HTTP client依存を追加する
  - LINE remote API用の同期HTTP clientを設計指定バージョンで固定する
  - 既存Backend testが依存追加後も起動できることを確認する
  - 完了時には、Backendコンテナで固定バージョンをimportできる
  - _Requirements: 2.1, 7.11_

- [x] 1.2 LINE Login用runtime設定をfail closedで読み込む
  - チャネルID・secret、provider、LIFF直結チャネルID、owner digestをBackend専用の不変なruntimeへ変換する
  - 必須値の欠落・空値・非canonical値を秘密情報なしの起動エラーにし、owner digest未設定だけは認証拒否用sentinelへ変換する
  - 完了時には、DB接続前に設定不備が検出され、raw secretとowner digestがDjango settingsや例外へ載らない
  - _Requirements: 2.1, 3.1, 3.2, 3.3, 3.8, 7.9, 7.10, 7.11, 8.4, 8.5, 8.6_

- [x] 1.3 Django署名secretを厳密に検証する
  - Django署名secretの明示設定・最小長・既知default禁止を起動時に検証する
  - 不正値をsecret長・断片・既知値なしの起動エラーへ変換する
  - 完了時には、安全なsecretだけがunlink confirmation署名へ利用できる
  - _Requirements: 7.1, 8.4, 8.5, 8.6_

- [x] 1.4 Frontend設定processへLIFF IDとexact allowed hostを分離する
  - clientへ公開するLIFF IDと、config processだけが読む公開hostを別の環境境界として扱う
  - 公開hostからexact allowed hostを構成し、重複するLIFF URLやallowed-host環境値を作らない
  - 完了時には、Frontend bundleがLIFF IDだけを参照し、Backend秘密値や公開host設定値をclient変数として含まない
  - _Requirements: 1.1, 1.3, 3.8, 8.1, 8.4, 8.5_

- [x] 1.5 test起動用の安全なruntime bootstrapを用意する
  - base settings読込前にprocess固有のDjango secretとLINE Login test secretを供給する
  - syntheticなhost・channel・provider・UUIDをtest用に注入し、owner digestはtestごとに明示できるようにする
  - 完了時には、repositoryへ固定secret canaryを保存せず、全Backend testが本番相当の起動時検証を通過できる
  - _Requirements: 2.1, 3.2, 3.8, 8.1, 8.4, 8.5, 8.6_

- [x] 1.6 owner適格条件を秘密値なしで生成する
  - providerと非echo入力された本人識別情報からcanonicalなowner digestだけを導出する
  - owner digest未設定のruntimeでも生成処理を許可し、本人識別情報・入力長・断片を出力やログへ残さない
  - 完了時には、標準出力へdigestだけが返り、その値を設定すると事前許可ownerを照合できる
  - _Requirements: 3.1, 3.2, 3.3, 3.8, 8.4, 8.5, 8.6_

- [x] 1.7 Frontendへ固定LIFF SDK依存を追加する
  - LIFF SDKを設計指定バージョンで固定し、再現可能なlockfileへ組み込む
  - 既存Frontend testとproduction buildが依存追加後も起動できることを確認する
  - 完了時には、Frontendコンテナで固定SDKをimportできる
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 1.8 公開hostを厳密に検証する
  - 公開hostは単一ASCII hostnameだけを許し、scheme・port・path・wildcard・空白を拒否する
  - 検証済みhostからexact HTTPS trusted originを導出する
  - 完了時には、不正hostが設定段階で拒否され、安全なoriginだけが後続のCSRF設定へ渡る
  - _Requirements: 1.1, 1.3, 8.1, 8.2, 8.3_

- [x] 2. provider付きチャネル参照の上流契約を拡張する
- [x] 2.1 provider識別子の共通検証契約を追加する
  - providerを1〜64文字のASCII数字列として扱い、trim・整数化・leading zero除去を行わない
  - 新規チャネル入力ではproviderを必須とし、既存チャネル更新では安全なbackfillを許可する
  - 完了時には、LINE Login runtimeとMessaging APIチャネルが同じ完全一致規則で検証される
  - _Requirements: 5.3, 5.4, 6.4, 8.5_

- [x] 2.2 既存チャネルを維持するnullable provider migrationを追加する
  - 既存チャネルを削除・変更せずprovider列と検索索引を追加する
  - legacy未設定値を許容しながら、新しいチャネルでは検証済みproviderを保存できるようにする
  - 完了時には、migration適用後も既存配信チャネルが利用でき、provider未設定を明示的に識別できる
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.4, 6.6_

- [x] 2.3 チャネル管理操作へprovider backfillを追加する
  - 対話入力と非対話入力の両方でproviderを安全に登録・更新できるようにする
  - 表示時にprovider以外の秘密credentialを混在させず、未設定チャネルを明確に判別する
  - 完了時には、既存チャネルへproviderをbackfillし、後続のaccount link候補へ移行できる
  - _Requirements: 5.1, 5.3, 5.4, 6.4, 6.6, 8.4, 8.5_

- [x] 2.4 秘密を含まないread-onlyチャネルdirectoryを提供する
  - activeかつprovider設定済みのチャネルだけを登録候補として返す
  - 既存recipient用にはinactiveチャネルも不透明ID・名称・provider・状態だけで取得できるようにする
  - 完了時には、Messaging API内部ID・bot user ID・credential状態を含まないsafe projectionだけをaccount境界へ返せる
  - _Requirements: 5.1, 5.3, 5.4, 5.9, 6.4, 6.6, 8.5_

- [x] 2.5 LIFF直結チャネルをserver-side policyとして確立する
  - 設定済みチャネルIDをdirectoryで解決し、存在・provider binding・LINE Login provider一致を確認する
  - policy未確立時は全account operationを安全な設定エラーで拒否する
  - 完了時には、検証済みの1チャネルだけをLIFF直結と判定でき、serviceが環境変数を直接参照しない
  - _Depends: 1.2, 2.4_
  - _Requirements: 5.4, 5.7, 5.8, 6.4, 7.9, 7.10_

- [x] 3. accountデータと安全な境界型を構築する
- [x] 3.1 owner・identity・session・recipientの不変条件を定義する
  - 単一ownerの4状態、provider単位のidentity一意性、端末別session、identityとチャネルのrecipient一意性を表現する
  - unlink generationと認可取消確認時刻の組合せを制約し、不可能な解除stageを拒否する
  - 完了時には、重複owner・identity・recipientと矛盾した解除状態がmodel validationで検出される
  - _Requirements: 2.4, 2.6, 3.4, 4.1, 4.2, 4.8, 5.2, 5.6, 6.1, 6.3, 7.2, 7.4, 7.12, 7.14, 7.15_

- [x] 3.2 account schemaとowner singleton seedをmigrationする
  - provider付きチャネルmigrationへ明示依存し、identity・owner・session・recipient schemaを作成する
  - owner singletonをseedし、配信監査へidentity外部キーを追加しない
  - 完了時には、空DBからmigrationでき、owner slotが1行だけ存在し、既存配信監査が独立して保持される
  - _Depends: 2.2_
  - _Requirements: 3.4, 4.2, 5.6, 7.2, 7.3, 7.4, 7.8_

- [x] 3.3 LINE credentialをredactedな値境界へ閉じ込める
  - ID token、user access token、channel access token、本人識別情報をimmutableかつserialization不能として扱う
  - repr・例外・通常ログへraw値が展開されないようにする
  - 完了時には、秘密値canaryが値の表示・serialization・例外出力へ現れない
  - _Requirements: 2.2, 2.3, 3.8, 4.5, 4.7, 8.4, 8.5, 8.6_

- [x] 3.4 strict request境界を作る
  - 未知field、user ID、profile object、token aliasをmutation前に拒否する
  - field errorへcredentialや入力値をechoしない
  - 完了時には、定義済みfieldだけがdomain処理へ渡る
  - _Requirements: 2.3, 4.5, 5.5, 8.5_

- [x] 3.5 safe errorの共通変換境界を作る
  - 下位例外とLINE raw errorを安全なcode・概要・field errorへ変換する
  - token・subject・session ID・secretを成功応答とエラー応答の双方から除外する
  - 完了時には、公開APIが定義済み安全形式以外のエラーbodyを返さない
  - _Requirements: 2.2, 3.6, 3.8, 8.4, 8.5, 8.6_

- [x] 4. LINE Platform gatewayを実装する
- [x] 4.1 LINE read-only通信の共通transport policyを実装する
  - verify・profile・friendship・stateless token発行へredirect禁止とconnect・read・write・pool timeout上限を適用する
  - 400系を再送せず、429・5xx・transport timeoutだけを最大2回の短いjitter付きretryへ限定する
  - 完了時には、read-only LINE通信が共通のbounded execution結果へ収束する
  - _Requirements: 2.1, 5.7, 7.11, 8.6_

- [x] 4.2 ID tokenの本人証明をremote検証する
  - LINE verify応答のissuer・audience・expiry・subject・nameを防御的に確認する
  - 対象チャネル・provider不一致、期限切れ、改変、scope不足をsession作成前の安全な失敗へ変換する
  - 完了時には、有効なraw ID tokenだけが検証済みidentityとなり、Frontend profileだけでは認証できない
  - _Requirements: 2.1, 2.2, 2.3, 4.1, 4.3, 8.6_

- [x] 4.3 user access tokenとprofileの本人bindingを検証する
  - tokenのclient ID・有効期限・必要scopeを確認し、profile subjectを保存identityとconstant-timeで比較する
  - 無効token・本人不一致・対象チャネル不一致を外部作用前に拒否する
  - 完了時には、現在owner本人のfresh tokenだけがrecipient登録または全解除の証明として利用できる
  - _Requirements: 5.4, 5.7, 7.9, 7.10, 8.4, 8.5, 8.6_

- [x] 4.4 LIFF直結チャネルの友だち状態を取得する
  - 検証済みuser tokenでfriendshipを取得し、boolean以外の応答を安全なdependency errorへ変換する
  - read-only呼出しへredirect禁止・明示timeout・限定retryを適用する
  - 完了時には、directチャネルだけがLINE確認済みfriendshipを取得でき、非directチャネルはLINEを呼ばない
  - _Requirements: 5.7, 5.8, 5.9, 6.2, 6.5, 8.6_

- [x] 4.5 stateless channel tokenと認可取消結果を扱う
  - Backend内で短期channel tokenを発行し、検証済みuser tokenを認可取消へ送る
  - 204だけを外部成功応答とし、400・429・5xx・timeout・接続切断を拒否または結果不確定へ分類する
  - 完了時には、認可取消POSTが同一request内で自動再送されず、channel tokenとsecretも保存されない
  - _Requirements: 7.5, 7.11, 7.13, 7.14, 7.15, 8.4, 8.5, 8.6_

- [x] 5. account aggregateの永続化境界を実装する
- [x] 5.1 owner lockとidentity bindingを永続化する
  - owner singletonをlock rootとして取得し、適格identityの初回bindingと再認証時の表示名更新をtransaction化する
  - providerとsubjectの自然一意性を保ち、異provider identityを自動統合しない
  - 完了時には、並行初回認証でも単一identityだけがownerへbindされる
  - _Requirements: 2.4, 3.1, 3.3, 3.4, 4.1, 4.2, 4.3, 4.8_

- [x] 5.2 端末別owner session ledgerを永続化する
  - 端末ごとにopaque session IDと期限を作成・検索・削除する
  - 期限切れと現在端末logoutを他端末sessionへ影響なく処理する
  - 完了時には、追加端末sessionが併存し、1端末のlogout・expiryで他端末が維持される
  - _Requirements: 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11_

- [x] 5.3 recipient mutationをowner lock上で永続化する
  - recipientの一覧・作成・有効状態変更・対象削除をactive ownerのidentity一致時だけ許可する
  - duplicate作成を既存projectionへ収束させ、対象不在では他行を変更しない
  - 完了時には、並行登録でも一意性が保たれ、解除fence後のrecipient mutationが拒否される
  - _Requirements: 5.2, 5.6, 6.1, 6.3, 6.7, 6.8, 6.9, 6.10, 7.12_

- [x] 5.4 unlink snapshotとgeneration fenceを永続化する
  - owner lock下で一貫した解除snapshotを取得し、activeから新generation付きpendingだけを開始する
  - snapshot検証とfence開始を同一transactionへ置く
  - 完了時には、stale snapshotではpendingが作られず、成功時だけ新generationが耐久化される
  - _Requirements: 7.1, 7.5, 7.12, 7.13_

- [x] 5.5 LINE認可取消成功markerを永続化する
  - expected generation一致時だけLINE成功時刻とlocal deletion pendingを同一commitへ保存する
  - stale generationとmarker保存失敗を既存pending状態へ収束させる
  - 完了時には、stale requestが再link後の新attemptへ成功markerを適用できない
  - _Requirements: 7.5, 7.13, 7.14, 7.15_

- [x] 5.6 unlinkのローカルfinalizeを原子的に永続化する
  - local deletion pendingとexpected generationを確認してから全recipient・全session・owner binding・identityを削除する
  - statement失敗時は全変更をrollbackし、markerと再試行可能stageを保持する
  - 完了時には、成功時だけownerがvacantへ戻り、失敗時に個人データの部分削除が残らない
  - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.14, 7.15_

- [x] 6. owner session・recipient・保護APIを実装する
- [x] 6.1 事前許可ownerの適格性とidentity bindingを実装する
  - 検証済みprovider・subjectのdigestを事前許可値とconstant-timeで照合する
  - vacantでは適格identityだけをbindし、activeでは同じprovider・subjectだけを許可する
  - 完了時には、未設定・不一致・別identityが同じ安全な拒否へ収束し、最初の訪問者がownerを先取りできない
  - _Depends: 1.2, 4.2, 5.1_
  - _Requirements: 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.8_

- [x] 6.2 端末別sessionの確立・更新・終了を実装する
  - 初回・追加端末・通常再認証・pending再認証ごとに新しいsession ledgerを作る
  - 表示名更新、現在端末logout、expiryを他端末・identity・recipientへ影響なく処理する
  - 完了時には、同一ownerの複数端末sessionが併存し、pending再認証でもowner状態をactiveへ戻さない
  - _Depends: 4.2, 5.1, 5.2, 6.1_
  - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 4.3, 4.6, 4.7, 7.6, 7.7, 7.12_

- [x] 6.3 owner session認証と状態別permissionを実装する
  - Django sessionのopaque IDをledger・期限・owner状態と毎request照合する
  - active ownerだけへ通常操作を許可し、pendingではstatus・現在端末logout・unlink再開だけを許可する
  - 完了時には、未認証・期限切れ・非owner・pendingの要求がhandlerと入力検証より先に拒否される
  - _Requirements: 2.5, 2.10, 2.11, 3.5, 3.6, 3.7, 3.8, 7.6, 7.12, 8.7_

- [x] 6.4 exact OriginとCSRFによる状態変更保護を実装する
  - 検証済み公開hostから導出した単一のexact HTTPS Originを全unsafe APIで必須化する
  - missing・複数値・null・scheme/host/port差異とcookie/header token不備をmutationなしで拒否する
  - 完了時には、loginを含む状態変更が正規originとCSRF tokenの両方を満たす場合だけ実行される
  - _Depends: 1.8_
  - _Requirements: 8.1, 8.2, 8.3, 8.7_

- [x] 6.5 session APIへservice・認証・CSRFを統合する
  - 匿名・認証済み・解除pendingの状態確認、raw ID token login、現在端末logoutを公開する
  - login成功時にsession keyをrotateしてからowner session IDを保存し、保存失敗を認証済みとして返さない
  - 完了時には、表示名と連携状態だけを返すsession APIがCSRF cookie bootstrapと端末別logoutを提供する
  - _Depends: 3.4, 3.5, 6.2, 6.3, 6.4_
  - _Requirements: 1.1, 1.4, 1.5, 1.6, 2.4, 2.5, 2.7, 2.10, 3.5, 3.6, 4.4, 4.5, 7.12, 8.5, 8.7_

- [x] 6.6 (P) recipient登録候補一覧を実装する
  - provider一致のactiveチャネルと既存recipientチャネルをsafe projectionへ統合する
  - inactive既存チャネルを残し、配信可否を秘密情報なしで導出する
  - 完了時には、ownerが登録候補と既存リンクを名称・状態・不透明IDだけで一覧できる
  - _Boundary: RecipientService Channel Listing_
  - _Depends: 2.4, 2.5, 5.3_
  - _Requirements: 5.1, 5.3, 5.4, 5.9, 6.6_

- [x] 6.7 recipient登録を本人bindingとfriendshipで実装する
  - directチャネルでは検証済みtokenからfriendshipを保存し、非directではLINEを呼ばずunknownを保存する
  - channel存在・active・provider一致をmutation前に確認し、duplicateを既存projectionへ収束させる
  - 完了時には、user ID入力なしで重複しないrecipientを登録できる
  - _Depends: 4.3, 4.4, 5.3, 6.6_
  - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9_

- [x] 6.8 recipientの無効化と再有効化を実装する
  - disableでは関係を保持し、enableではchannel active・provider一致を再検証する
  - 配信可否をrecipient enabled・friendship friend・channel activeの積として導出する
  - 完了時には、対象recipientの有効状態だけが変わり、unknownやinactiveは配信不可のままになる
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.9, 6.10_

- [x] 6.9 recipientのチャネル別解除を実装する
  - owner lock下で対象recipientだけを削除し、identity・他recipient・全sessionを維持する
  - 対象不在では他の永続状態を変更しない
  - 完了時には、選択チャネルのリンクだけが一覧から消える
  - _Requirements: 6.7, 6.8, 6.9, 6.10_

- [x] 6.10 recipient APIへserviceとowner保護を統合する
  - 一覧・登録・有効状態変更・対象解除を不透明IDとstrict requestだけで公開する
  - domain結果を安全なsuccess projectionと404・409・422・503へ対応付ける
  - 完了時には、active ownerだけがLINE user IDなしでrecipient全操作を実行できる
  - _Depends: 3.4, 3.5, 6.3, 6.4, 6.6, 6.7, 6.8, 6.9_
  - _Requirements: 3.5, 3.6, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.9, 6.1, 6.3, 6.4, 6.7, 6.10, 8.5, 8.7_

- [ ] 7. 全連携解除sagaを実装する
- [ ] 7.1 unlink対象snapshotと短期confirmationを作る
  - owner slot・identity UUID・表示名・sorted recipient UUID・各channel UUID・recipient数・監査保持flagから秘密値なしのcanonical fingerprintを作る
  - 専用purpose・version・5分期限で署名し、改変・期限切れ・snapshot変更・再link後tokenを拒否する
  - 完了時には、ownerが削除範囲を確認でき、stale confirmationではfenceが設定されない
  - _Depends: 1.3, 5.4_
  - _Requirements: 7.1, 7.5, 7.8, 7.9, 7.10_

- [ ] 7.2 deauthorize実行をMySQL advisory lockでsingle-flightにする
  - owner slot由来の非秘密lock名をwaitなしで取得し、競合要求をLINE呼出し前に拒否する
  - 通常の成功・失敗・例外では取得した同一connectionからreleaseし、connection喪失時はMySQLの自動解放後に別connectionから再取得できるようにする
  - 完了時には、複数端末の同時要求のうち1つだけが認可取消へ進める
  - _Requirements: 2.6, 7.12, 7.13, 7.14, 7.15_

- [ ] 7.3 fresh本人証明からunlink fenceを開始する
  - confirmation事前検証とfresh user token remote検証をDB transaction外で行う
  - owner lock下でsnapshotを再検証し、同一transactionで新generation付きdeauthorization pendingへ遷移する
  - 完了時には、有効なconfirmationと現在owner本人のtokenが揃った場合だけ通常操作がfenceされる
  - _Depends: 4.3, 5.4, 7.1_
  - _Requirements: 7.5, 7.9, 7.10, 7.12, 7.13_

- [ ] 7.4 LINE認可取消を成功markerへ収束させる
  - advisory lock取得後にstateとexpected generationを再確認し、stale attemptを隔離する
  - LINE 204後にmarker commitできた場合だけlocal deletion pendingへ進め、失敗・不確定・marker保存失敗はidentityを保持する
  - 完了時には、外部成功と耐久markerの両方が成立した場合だけ認可取消確認済みになる
  - _Depends: 4.5, 5.5, 7.2, 7.3_
  - _Requirements: 7.5, 7.11, 7.12, 7.13, 7.14, 7.15_

- [ ] 7.5 local-only finalizeを実装する
  - local deletion pendingではLINEを再呼出しせず原子的finalizeだけを再試行する
  - 削除失敗をmarker保持済みのlocal deletion pendingへ戻す
  - 完了時には、成功時だけ全端末sessionと個人データが消え、失敗時は部分削除が残らない
  - _Depends: 5.6, 7.4_
  - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.14, 7.15_

- [ ] 7.6 pending unlinkの再認証・再開を実装する
  - deauthorization pendingではfresh再認証を要求し、検証後に同generationの認可取消へ再合流する
  - local deletion pendingではtokenなしでlocal finalizeへ再合流し、旧session・旧generationを新attemptへ適用しない
  - 完了時には、各pending stageが許可された再開経路だけを返す
  - _Depends: 6.2, 7.4, 7.5_
  - _Requirements: 7.5, 7.6, 7.7, 7.12, 7.13, 7.14, 7.15_

- [ ] 7.7 unlink preview・実行・再開APIを統合する
  - active owner向けpreview、初回実行、fresh再認証resume、local-only retryをstrictに区別する
  - completed・deauthorization pending・local deletion pendingを安全なretry action付きunionへ変換する
  - 完了時には、Frontendがblind retryせず現在stageに許可された解除操作だけを呼び出せる
  - _Depends: 3.4, 3.5, 6.3, 6.4, 7.1, 7.3, 7.4, 7.5, 7.6_
  - _Requirements: 7.1, 7.2, 7.5, 7.9, 7.10, 7.12, 7.13, 7.14, 7.15, 8.5, 8.6, 8.7_

- [ ] 7.8 unlink pendingを個人識別子なしで観測できるようにする
  - deauthorization pendingとlocal deletion pendingの件数・経過時間を安全な内部指標として取得する
  - token・subject・表示名・session IDをmetricや通常ログへ含めない
  - 完了時には、解除停滞をsafe stateと経過時間だけで運用確認できる
  - _Requirements: 7.5, 7.12, 7.13, 7.15, 8.4, 8.5, 8.6_

- [ ] 8. LIFF認証Frontendを構築する
- [ ] 8.1 (P) LIFF URLと固定entry pathの設定契約を実装する
  - LIFF IDからLIFF URLを、現在originと固定pathからendpoint・redirect URIを一意に導出する
  - 非HTTPS origin・path不一致・空LIFF IDをSDK初期化前に拒否する
  - 完了時には、query・fragmentを保持しつつ安全性判定がoriginと`/liff`だけで行われる
  - _Boundary: LiffRuntimeConfig_
  - _Depends: 1.4_
  - _Requirements: 1.1, 1.3, 1.5, 8.1_

- [ ] 8.2 (P) LIFF SDKをtyped adapterへ隔離する
  - SDK初期化、browser種別、login状態、明示login、raw token取得を小さな外部境界へ閉じ込める
  - decoded tokenとFrontend profileをBackend認証根拠として使用しない
  - 完了時には、認証controllerがSDK実装へ直接依存せず、失敗とtoken欠落を安全な結果として受け取れる
  - _Boundary: LinePlatformLiffAdapter_
  - _Depends: 1.7_
  - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 2.3, 8.4_

- [ ] 8.3 (P) same-origin sessionとCSRFを扱うHTTP clientを実装する
  - same-origin credentialを使い、unsafe要求はCSRF cookieとheaderが揃う場合だけ送信する
  - 401をtoken再送で隠さず認証状態へsession失効を通知する
  - 完了時には、Frontendの全保護要求が共通cookie・CSRF・safe error契約を通る
  - _Boundary: ProtectedHttpClient_
  - _Depends: 6.4_
  - _Requirements: 2.5, 2.10, 8.1, 8.2, 8.3, 8.5, 8.7_

- [ ] 8.4 session APIのstrict DTOとclientを実装する
  - 匿名・認証済み・解除pendingの応答unionを未知field拒否で検証する
  - session bootstrap・raw ID token login・logoutを共通HTTP client経由で呼び出す
  - 完了時には、表示名と連携状態だけを認証controllerへ渡し、曖昧な応答を認証済みとして扱わない
  - _Depends: 6.5, 8.3_
  - _Requirements: 1.1, 2.5, 2.7, 2.10, 4.4, 4.5, 7.12, 8.5_

- [ ] 8.5 LIFF認証の純粋な状態遷移を実装する
  - initializing・login required・verifying・anonymous・authenticated・unlinking・errorをイベントから一意に遷移させる
  - 外部browser login復帰・取消・init失敗・Backend検証失敗・401 expiryをfail closedで扱う
  - 完了時には、各イベント列から保護UIの可否と安全な再試行導線が決定できる
  - _Depends: 8.1, 8.2, 8.4_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.10, 7.12_

- [ ] 8.6 認証gateと端末logout UIを実装する
  - 認証中・未認証・取消・失敗・unlinkingでは配信画面とaccount管理画面をmountしない
  - login・再試行・現在端末logoutを状態controllerへ接続する
  - 完了時には、authenticated ownerだけが表示名付き保護consoleへ進み、LINE user IDは表示されない
  - _Depends: 8.5_
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 2.7, 2.10, 3.5, 4.4, 4.5_

- [ ] 9. account管理Frontendを構築する
- [ ] 9.1 account APIのstrict DTOとclientを実装する
  - channel・recipient・unlink preview・unlink結果を未知field拒否のsafe unionへ変換する
  - 不透明ID・confirmation・write-only tokenだけを共通HTTP client経由で送信する
  - 完了時には、不正応答を描画せず、安全な成功・pending・errorだけをUIへ渡せる
  - _Depends: 6.10, 7.7, 8.3_
  - _Requirements: 4.4, 4.5, 5.1, 5.2, 5.5, 5.9, 6.1, 6.3, 6.7, 7.1, 7.9, 7.10, 8.5_

- [ ] 9.2 recipient一覧と状態変更UIを実装する
  - チャネル名称・リンク状態・friendship・配信可否をLINE user IDなしで表示する
  - 登録・disable・enable・チャネル別解除の進行中・成功・安全な失敗を対象単位で表示する
  - 完了時には、ownerがrecipient全操作を行い、unknown friendshipとinactiveチャネルを配信不可として確認できる
  - _Depends: 9.1_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10_

- [ ] 9.3 unlink previewと初回確認UIを実装する
  - 表示名・対象チャネル名称・recipient数・配信監査保持説明を確認画面へ表示する
  - confirmationとfresh access tokenが揃った場合だけ初回実行を送信する
  - 完了時には、ownerが秘密値なしで削除範囲を確認し、token不足・期限切れを再認証へ戻せる
  - _Depends: 9.1_
  - _Requirements: 7.1, 7.8, 7.9, 7.10_

- [ ] 9.4 (P) unlink pending専用のrecovery UIを実装する
  - deauthorization pendingではfresh再認証、local deletion pendingではtokenなしlocal retryだけを提示する
  - 競合時はsession状態を再取得し、blindなLINE再送を行わない
  - 完了時には、各pending stageに許可された唯一の再開操作が表示され、通常管理UIは表示されない
  - _Boundary: UnlinkRecoveryPanel_
  - _Depends: 8.5, 9.1_
  - _Requirements: 7.5, 7.10, 7.12, 7.13, 7.15_

- [ ] 9.5 recipient管理・unlink・認証状態をaccount consoleへ統合する
  - activeではrecipient管理とunlink preview、pendingではrecoveryだけを表示する
  - unlink completed時は認証状態をanonymousへ更新し、全通常操作をunmountする
  - 完了時には、account stateごとに許可された画面だけが表示され、完了と未完了を誤認しない
  - _Depends: 8.6, 9.2, 9.3, 9.4_
  - _Requirements: 1.4, 4.4, 4.5, 5.9, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.12, 7.13, 7.14, 7.15_

- [ ] 10. 既存システムと公開HTTPS構成へ統合する
- [ ] 10.1 account Backendの依存構成・設定・URLを統合する
  - runtime・directory policy・gateway・repository・services・認証境界を明示的に合成する
  - account APIを共通URL配下へ接続し、Secure cookie・safe exception・exact trusted originを適用する
  - 完了時には、全account endpointが同じ安全な既定値と依存構成で起動・応答する
  - _Depends: 1.2, 1.3, 1.8, 2.5, 6.5, 6.10, 7.7, 7.8_
  - _Requirements: 2.5, 3.5, 3.6, 7.12, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

- [ ] 10.2 既存配信APIをowner先行認可で保護する
  - 無認証overrideを共通owner保護へ置き換え、未認証・非owner・pendingを入力検証とLINE呼出しより先に拒否する
  - active ownerでは本文確認・冪等性・結果状態・監査の既存契約を変更しない
  - 完了時には、不正配信がLINEへ到達せず、ownerの既存201・200・202契約が維持される
  - _Depends: 6.3, 6.4, 10.1_
  - _Requirements: 3.7, 3.8, 7.8, 7.12, 8.4, 8.5, 8.6, 8.7, 8.8_

- [ ] 10.3 既存配信Frontendを保護HTTPと認証失効へ接続する
  - 配信要求を共通HTTP clientとCSRF headerへ移行する
  - 401・logout・pendingで配信状態を破棄し、認証gateへ制御を戻す
  - 完了時には、active ownerの既存配信UIを保ちながら未認証時の送信操作が表示されない
  - _Depends: 8.3, 10.2_
  - _Requirements: 2.10, 3.7, 7.12, 8.2, 8.3, 8.7, 8.8_

- [ ] 10.4 `/liff`の認証済みconsoleへ管理画面と配信画面を統合する
  - 固定entry pathで同じSPAを描画し、認証gate内にaccount consoleと配信画面を配置する
  - 認証・recipient・unlink pendingを判別できる最小限の視覚状態を整える
  - 完了時には、LINEアプリ内と外部browserの両方でownerだけが統合consoleを利用できる
  - _Depends: 8.6, 9.5, 10.3_
  - _Requirements: 1.1, 1.3, 1.4, 1.6, 3.5, 4.4, 4.5, 5.9, 7.12, 8.7, 8.8_

- [ ] 10.5 Composeの環境注入と単一公開originを統合する
  - LIFF IDだけをFrontendへ、LINE Login secret・owner digestをBackendだけへ注入する
  - 同じ公開domainからVite allowed hostとBackend trusted originを構成し、既知secret fallbackを除去する
  - 完了時には、安全な環境例からComposeを構成でき、Frontend bundleへBackend秘密値が含まれない
  - _Depends: 1.2, 1.3, 1.4, 1.8, 10.1_
  - _Requirements: 1.1, 1.3, 3.2, 3.8, 7.11, 8.1, 8.2, 8.4, 8.5_

- [ ] 11. 自動検証と通常経路のLIFF受入を完了する

> 以降の全追加test定義直前には、日本語の`テストケース:`と`期待値:`コメントを記載する。

- [ ] 11.1 (P) LINE Login runtime loaderを自動検証する
  - 必須設定・canonical provider・UUID・owner未設定sentinelの正常異常系を検証する
  - raw secretとowner digestがsettings・例外・reprへ載らないことを確認する
  - 完了時には、runtime loaderのfail-closed testが単独で成功する
  - _Boundary: Runtime Loader Tests_
  - _Depends: 1.2, 1.5_
  - _Requirements: 2.1, 3.1, 3.2, 3.3, 3.8, 8.4, 8.5, 8.6_

- [ ] 11.2 (P) provider validationとmodel規則を自動検証する
  - opaque providerの完全一致validationと新規必須規則を検証する
  - nullable legacy値とprovider索引のmodel metadataを確認する
  - 完了時には、provider validation・model境界のtestが成功する
  - _Boundary: LineChannel Provider Tests_
  - _Depends: 2.1, 2.2_
  - _Requirements: 5.1, 5.3, 5.4, 6.4, 6.6_

- [ ] 11.3 (P) ID token verify境界を自動検証する
  - issuer・audience・expiry・subject・name・scopeの成功失敗をfakeで検証する
  - decoded profileだけでは認証できずraw errorも公開されないことを確認する
  - timeoutと限定retryを含むID token verifyの実行上限を確認する
  - 完了時には、ID token verifyのgateway testが成功する
  - _Boundary: ID Token Gateway Tests_
  - _Depends: 4.1, 4.2_
  - _Requirements: 2.1, 2.2, 2.3, 4.1, 4.3, 8.6_

- [ ] 11.4 (P) account modelとDB制約を自動検証する
  - owner singleton・identity・session・recipient・unlink stateのvalid/invalid組合せを検証する
  - unique・check・FK規則と配信監査の独立性を確認する
  - 完了時には、account model不変条件のtestが成功する
  - _Boundary: Account Model Tests_
  - _Depends: 3.1, 3.2_
  - _Requirements: 2.4, 2.6, 3.4, 4.2, 5.2, 5.6, 7.2, 7.4, 7.8_

- [ ] 11.5 (P) owner適格性と初回bindingを自動検証する
  - owner digest未設定・不一致・一致とvacant・active identity判定を検証する
  - 異provider identityを統合せず単一ownerを保つことを確認する
  - 完了時には、owner確立serviceのtestが成功する
  - _Boundary: Owner Establishment Tests_
  - _Depends: 6.1_
  - _Requirements: 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.8_

- [ ] 11.6 (P) recipient一覧と登録を自動検証する
  - active・inactive・unbound・provider不一致・duplicateの分岐を検証する
  - direct friendshipと非direct unknownを確認する
  - 完了時には、recipient一覧・登録serviceのtestが成功する
  - _Boundary: Recipient Registration Tests_
  - _Depends: 6.6, 6.7_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9_

- [ ] 11.7 unlink confirmationとfenceを自動検証する
  - confirmation改変・期限切れ・snapshot変更・replay・fresh token不備をmutationなしで拒否する
  - 表示名と件数が同じでもrecipient UUIDまたはchannel UUIDが差し替わればstaleとして拒否する
  - begin_unlinkがsnapshot検証と同一transactionでgenerationを設定することを確認する
  - 完了時には、stale confirmationと旧generationが新attemptへ影響しないtestが成功する
  - _Depends: 7.1, 7.3_
  - _Requirements: 7.1, 7.5, 7.9, 7.10, 7.12, 7.13_

- [ ] 11.8 unlink認可取消とmarker障害をfault injectionで自動検証する
  - LINE 400・429・5xx・timeout・204後marker失敗を決定的に再現する
  - deauthorizeがrequestごとに1回だけで、結果不確定がdeauthorization pendingへ留まることを確認する
  - 完了時には、外部結果とmarker遷移のsaga testが成功する
  - _Depends: 7.2, 7.4_
  - _Requirements: 7.5, 7.11, 7.12, 7.13, 7.14, 7.15_

- [ ] 11.9 session API・Origin・cookie保護を自動検証する
  - 全unsafe endpointで不正OriginとCSRF欠落を403かつmutationなしにする
  - session cookieのSecure・HttpOnly・SameSite Lax、CSRF cookieのSecure・SameSite Lax・JS-readable、HTTPへの認証cookie非送信を確認する
  - 偽`X-Forwarded-Proto`がBackendのHTTPS判定を変更せず、proxy trust設定が追加されていないことを確認する
  - 完了時には、session APIの認証・rotation・Origin・CSRF・cookie testが成功する
  - _Depends: 10.1_
  - _Requirements: 2.4, 2.10, 3.5, 3.6, 7.6, 7.12, 8.1, 8.2, 8.3, 8.7_

- [ ] 11.10 既存配信APIのowner保護と監査を回帰検証する
  - 未認証・非owner・pendingの不正payloadがvalidationとgatewayより先に拒否されることを確認する
  - active ownerの確認・冪等性・結果状態と全解除前後の監査不変を確認する
  - 完了時には、既存配信契約を保つBackend回帰testが成功する
  - _Depends: 10.2_
  - _Requirements: 3.7, 3.8, 7.8, 7.12, 8.4, 8.5, 8.6, 8.7, 8.8_

- [ ] 11.11 MySQL実接続でownerとrecipient競合を検証する
  - 同時初回ownerとduplicate recipientを同期させる
  - owner lockとunique制約が単一結果へ収束させることを確認する
  - 完了時には、単一ownerとrecipient一意性の並行testが成功する
  - _Depends: 6.1, 6.7_
  - _Requirements: 2.4, 2.6, 2.11, 3.4, 5.6, 6.10_

- [ ] 11.12 session状態とチャネル一覧のquery効率を検証する
  - session statusとchannel・recipient一覧へ固定query上限を設ける
  - recipient件数に比例するN+1とcredential table joinがないことを確認する
  - 完了時には、代表データ量でquery上限testが成功する
  - _Depends: 6.5, 6.6, 10.1_
  - _Requirements: 2.5, 5.1, 5.9, 8.4, 8.5_

- [ ] 11.13 (P) LIFF URL設定を自動検証する
  - LIFF URL・endpoint・redirect URI導出とorigin/path不一致を検証する
  - query・fragmentを保持しつつ安全性判定から除外することを確認する
  - 完了時には、LIFF設定のFrontend unit testが成功する
  - _Boundary: LiffRuntimeConfig Tests_
  - _Depends: 8.1_
  - _Requirements: 1.1, 1.3, 1.5, 8.1_

- [ ] 11.14 (P) 認証状態とgateを自動検証する
  - login required・redirect復帰・取消・verifying・authenticated・error・401 expiryを検証する
  - 非authenticatedで保護UIがmountされず、logoutで匿名へ戻ることを確認する
  - 完了時には、LIFF認証stateとgateのFrontend testが成功する
  - _Boundary: LiffAuthController Tests_
  - _Depends: 8.4, 8.5, 8.6_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.10, 4.4, 4.5, 8.7_

- [ ] 11.15 recipient管理Frontendを自動検証する
  - 一覧・登録・disable・enable・対象解除の表示更新を確認する
  - malformed DTO・safe error・unknown friendship・inactive channel表示を確認する
  - 完了時には、recipient管理UIのFrontend testが成功する
  - _Depends: 9.1, 9.2, 10.4_
  - _Requirements: 4.4, 4.5, 5.1, 5.2, 5.9, 6.1, 6.2, 6.3, 6.7, 6.8, 6.9, 8.5, 8.7_

- [ ] 11.16 unlink recovery panelを自動検証する
  - 両pending stageの異なるretry actionと競合後の状態再取得を確認する
  - recovery中に通常操作が表示されないことを確認する
  - 完了時には、recovery panelのfake応答testが成功する
  - _Depends: 9.4, 10.4_
  - _Requirements: 7.5, 7.12, 7.15, 8.5, 8.7_

- [ ] 11.17 (P) 既存配信Frontendの認証統合を回帰検証する
  - 配信要求のCSRF、401・pending時のunmount、active owner時の既存表示を確認する
  - session失効時にtoken再送せず認証gateへ戻ることを確認する
  - 完了時には、配信Frontendの認証統合回帰testが成功する
  - _Boundary: DeliveryForm Tests_
  - _Depends: 10.3, 10.4_
  - _Requirements: 2.10, 3.7, 7.12, 8.2, 8.3, 8.7, 8.8_

- [ ] 11.18 Django署名secret検証を自動検証する
  - secret未設定・短い値・既知defaultと安全な値を検証する
  - errorへsecret値・長さ・断片が出ないことを確認する
  - 完了時には、Django署名secretの起動時回帰testが成功する
  - _Depends: 1.3_
  - _Requirements: 7.1, 8.4, 8.5, 8.6_

- [ ] 11.19 owner digest生成を自動検証する
  - digest未設定runtime、非echo入力、既存Backend専用入力の各経路を検証する
  - subject・入力長・断片がstdout・stderr・logへ現れないことを確認する
  - 完了時には、canonical digestだけを返すcommand testが成功する
  - _Depends: 1.6_
  - _Requirements: 3.1, 3.2, 3.3, 3.8, 8.4, 8.5, 8.6_

- [ ] 11.20 provider backfill管理操作を自動検証する
  - 対話・非対話入力、legacy更新、新規必須、invalid providerを検証する
  - 管理出力へcredentialが混在しないことを確認する
  - 完了時には、provider管理service・prompt・command testが成功する
  - _Depends: 2.3_
  - _Requirements: 5.1, 5.3, 5.4, 6.4, 6.6, 8.4, 8.5_

- [ ] 11.21 safe channel directoryを自動検証する
  - active bound一覧、inactive既存取得、unbound拒否を検証する
  - public projectionへ内部ID・bot user ID・credential状態が入らないことを確認する
  - 完了時には、channel directory query testが成功する
  - _Depends: 2.4_
  - _Requirements: 5.1, 5.3, 5.4, 5.9, 6.4, 6.6, 8.5_

- [ ] 11.22 LIFF直結チャネルpolicyを自動検証する
  - 対象チャネルの存在・provider binding・LINE Login provider一致を検証する
  - 未設定・unbound・不一致でsession状態確認を含む全account操作がfail closedになることを確認する
  - 完了時には、LIFF直結policyのconfiguration testが成功する
  - _Depends: 2.5_
  - _Requirements: 5.4, 5.7, 5.8, 6.4, 7.9, 7.10_

- [ ] 11.23 user token・profile・friendship gatewayを自動検証する
  - token client ID・expiry・scope、profile subject binding、friendship booleanをfakeで検証する
  - read-only retryとbounded timeout、non-direct時の未呼出しを確認する
  - 完了時には、本人bindingとfriendshipのgateway testが成功する
  - _Depends: 4.1, 4.3, 4.4_
  - _Requirements: 5.4, 5.7, 5.8, 5.9, 7.9, 7.10, 8.6_

- [ ] 11.24 stateless token・deauthorize gatewayを自動検証する
  - token発行と204・400・429・5xx・timeout・connection切断の分類をfakeで検証する
  - deauthorize自動再送禁止とchannel secret非露出を確認する
  - 完了時には、認可取消gatewayのbounded execution testが成功する
  - _Depends: 4.5_
  - _Requirements: 7.11, 7.13, 7.14, 7.15, 8.4, 8.5, 8.6_

- [ ] 11.25 owner・identity repositoryを自動検証する
  - owner lock・identity一意性・表示名更新・異provider分離を検証する
  - deadlockとlock timeoutが安全なretryable resultへ変換されることを確認する
  - 完了時には、owner・identity repository testが成功する
  - _Depends: 5.1_
  - _Requirements: 2.4, 3.4, 4.1, 4.2, 4.3, 4.8_

- [ ] 11.26 recipient repositoryを自動検証する
  - create・duplicate収束・enabled変更・対象削除・owner fenceを検証する
  - 対象不在とrollbackが他recipientへ影響しないことを確認する
  - 完了時には、recipient repository testが成功する
  - _Depends: 5.3_
  - _Requirements: 5.2, 5.6, 6.1, 6.3, 6.7, 6.8, 6.9, 6.10, 7.12_

- [ ] 11.27 unlink snapshot・fence repositoryを自動検証する
  - snapshot一貫性・active precondition・generation作成を検証する
  - stale snapshotとstale generationがmutationなしで拒否されることを確認する
  - 完了時には、unlink snapshot・fence repository testが成功する
  - _Depends: 5.4_
  - _Requirements: 7.1, 7.5, 7.12, 7.13_

- [ ] 11.28 device session lifecycleを自動検証する
  - 初回・追加端末・通常再認証・pending再認証・logout・expiry・表示名更新を検証する
  - session rotation保存失敗と他端末維持を確認する
  - 完了時には、端末別session service testが成功する
  - _Depends: 6.2, 6.5_
  - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 4.3, 7.12_

- [ ] 11.29 recipient状態遷移を自動検証する
  - disable・enable・inactive channel・unknown friendship・対象解除の分岐を検証する
  - 他recipient・identity・sessionが維持されることを確認する
  - 完了時には、recipient状態変更service testが成功する
  - _Depends: 6.8, 6.9_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10_

- [ ] 11.30 unlink local retry・再認証resume・ABA防止を自動検証する
  - local deletion失敗後のlocal-only retryとdeauthorization pendingのfresh再認証resumeを検証する
  - 旧generation要求が再link後の新identity・session・recipientへ触れないことを確認する
  - 完了時には、pending再開・完了・ABA防止のsaga testが成功する
  - _Depends: 7.5, 7.6_
  - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.12, 7.13, 7.14, 7.15_

- [ ] 11.31 recipient APIのstrict inputとsafe responseを自動検証する
  - unknown field・user ID・resource不在・provider不一致を検証する
  - active owner以外の拒否とsafe channel・recipient projectionを確認する
  - 完了時には、recipient APIのHTTP契約testが成功する
  - _Depends: 6.10, 10.1_
  - _Requirements: 3.5, 3.6, 4.5, 5.5, 6.10, 8.5, 8.7_

- [ ] 11.32 公開境界のsecret canary非露出を自動検証する
  - ID token・access token・subject・channel secret・session ID・LINE errorへcanaryを埋める
  - response・log・repr・DB非許可列へcanaryが現れないことを確認する
  - 完了時には、公開境界と永続化のsecret non-exposure testが成功する
  - _Depends: 3.3, 3.5, 10.1_
  - _Requirements: 3.8, 4.5, 4.6, 4.7, 8.4, 8.5, 8.6_

- [ ] 11.33 MySQL実接続でunlink single-flightとfence競合を検証する
  - unlink対recipient mutation・二重unlink・advisory lock競合を同期させる
  - 競合要求がLINEを呼ばず、fence後のrecipient mutationが拒否されることを確認する
  - 完了時には、single-flightとdeletion fenceの並行testが成功する
  - _Depends: 7.2, 7.6, 11.27_
  - _Requirements: 2.6, 6.10, 7.5, 7.12, 7.13, 7.14, 7.15_

- [ ] 11.34 LIFF SDK adapterを自動検証する
  - browser種別・init・login・raw token取得・取消・token欠落を検証する
  - decoded profileを認証根拠に使用しないことを確認する
  - 完了時には、LIFF adapterのFrontend unit testが成功する
  - _Depends: 8.2_
  - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 2.3, 8.4_

- [ ] 11.35 Protected HTTP clientを自動検証する
  - same-origin credential・CSRF cookie/header・safe error parser・401通知を検証する
  - CSRF欠落時にunsafe requestを送信しないことを確認する
  - 完了時には、保護HTTP clientのFrontend unit testが成功する
  - _Depends: 8.3_
  - _Requirements: 2.10, 8.1, 8.2, 8.3, 8.5, 8.7_

- [ ] 11.36 unlink preview・completedのaccount consoleを自動検証する
  - previewの削除範囲・監査保持説明・fresh token不足を検証する
  - completed後のanonymous化と通常操作unmountを確認する
  - 完了時には、unlink初回確認・完了UIのFrontend testが成功する
  - _Depends: 9.3, 9.5, 10.4_
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.8, 7.9, 7.10, 8.5, 8.7_

- [ ] 11.37 unlink marker・local finalize repositoryを自動検証する
  - expected generation markerとlocal finalizeのpreconditionを検証する
  - marker保存失敗とfinalize statement失敗が全transactionをrollbackすることを確認する
  - 完了時には、unlink marker・atomic deletion repository testが成功する
  - _Depends: 5.5, 5.6_
  - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.14, 7.15_

- [ ] 11.38 unlink APIのstrict inputとpending responseを自動検証する
  - unknown field・profile・token alias・stale confirmation・stale attemptを検証する
  - pending permissionとstage別retry action unionを確認する
  - 完了時には、unlink APIのHTTP契約testが成功する
  - _Depends: 7.7, 10.1_
  - _Requirements: 3.5, 3.6, 7.1, 7.5, 7.10, 7.12, 8.5, 8.6, 8.7_

- [ ] 11.39 公開hostのBackend・Vite共通fixtureを自動検証する
  - canonical hostnameとscheme・port・path・wildcard・whitespaceを同じfixtureで検証する
  - Backend trusted originとVite allowed hostがexact値だけになることを確認する
  - 完了時には、公開hostの両runtime回帰testが成功する
  - _Depends: 1.4, 1.8_
  - _Requirements: 1.1, 1.3, 8.1, 8.2, 8.3_

- [ ] 11.40 test runtime bootstrapを自動検証する
  - process固有secretとsynthetic host・channel・provider・UUIDがbase settings前に供給されることを確認する
  - owner digestをtestごとに明示でき、固定secret canaryをsourceへ保存しないことを確認する
  - 完了時には、test runtime bootstrapの回帰testが成功する
  - _Depends: 1.5_
  - _Requirements: 2.1, 3.2, 3.8, 8.4, 8.5, 8.6_

- [ ] 11.41 nullable provider migrationを自動検証する
  - migration前後で既存チャネルの表示情報とcredential参照が維持されることを確認する
  - legacy provider未設定行がlink候補へ出ないことを確認する
  - 完了時には、provider migrationの前方・後方互換testが成功する
  - _Depends: 2.2_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.4, 6.6_

- [ ] 11.42 owner session repositoryを自動検証する
  - 端末session CRUD・expiry lookup・lazy cleanupを検証する
  - 1端末の削除が他端末sessionとidentityへ影響しないことを確認する
  - 完了時には、owner session repository testが成功する
  - _Depends: 5.2_
  - _Requirements: 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11_

- [ ] 11.43 advisory lock connection喪失とstale generationを検証する
  - lock保持connection喪失後に別connectionから再取得できることを確認する
  - 旧generationのmarker更新・finalizeが再link後のidentityへ触れないことを確認する
  - 完了時には、connection recoveryとABA防止のMySQL並行testが成功する
  - _Depends: 7.2, 7.6, 11.27, 11.37_
  - _Requirements: 2.6, 7.2, 7.5, 7.12, 7.13, 7.14, 7.15_

- [ ] 12. 全自動検証と実LIFF環境の技術受入を完了する
- [ ] 12.1 Compose環境でmigration・全自動test・production buildを完走させる
  - provider backfill後のmigration整合、Backend test、Frontend test、production buildを同じCompose構成で実行する
  - production bundleへBackend秘密値とsecret canaryが含まれないことを確認する
  - 完了時には、全自動検証とbuildが成功し、認証済みconsoleを有効化できる
  - _Requirements: 1.1, 1.3, 2.4, 2.10, 3.5, 4.5, 5.1, 7.12, 8.1, 8.4, 8.5, 8.7, 8.8_

- [ ] 12.2 実LIFF受入に必要な公開HTTPS前提を検証する
  - operator提供済みのLINE Login credential・LIFF ID・公開domain・linked channel IDが安全な環境から読み込めることを確認する
  - 既存チャネルproviderをbackfillし、owner digestを生成してBackendへ設定する
  - operatorが設定済みのLINE Developers Console Endpoint `/liff`、`openid profile` scope、LIFF・Login・Messaging provider整合を照合する
  - 完了時には、秘密値をFrontendへ出さず実LIFF URLからauthenticated sessionを開始できる前提が揃う
  - _Depends: 12.1_
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 3.1, 3.2, 5.4, 5.7, 7.9, 7.11, 8.1, 8.4, 8.5_

- [ ] 12.3 公開HTTPS originでLIFF技術受入を確認する
  - LIFF browser認証と外部browser login復帰を画面・session API結果で確認する
  - 不正path・host、login取消、検証失敗で保護UIと状態変更が拒否されることを確認する
  - recipient全操作と通常のunlink成功後のanonymous化をLINE user ID表示なしで確認する
  - 実LINEがpendingを返した場合はfresh再認証resumeを実機確認し、発生しない障害stageは11.8・11.16・11.30の決定的test結果で確認する
  - 完了時には、通常経路の期待状態とpending recoveryの検証証跡を個別に確認してすべて合格と判定できる
  - _Depends: 12.2_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.4, 2.6, 2.10, 3.5, 4.4, 4.5, 5.1, 5.2, 5.7, 5.8, 5.9, 6.1, 6.2, 6.3, 6.7, 7.1, 7.2, 7.4, 7.6, 7.14, 8.1, 8.2, 8.3, 8.5, 8.7, 8.8_

# Implementation Plan

- [x] 1. 資格情報を扱う安全な Backend 基盤を整える
- [x] 1.1 暗号依存と隔離されたテストランタイムを準備する
  - Python 3.14 で利用する認証付き暗号ライブラリのバージョンを固定し、コンテナ内で再現可能にする
  - 本番設定の読込前にプロセスごとの一時 Fernet 鍵と `DEBUG=false` を注入する明示的なテスト設定を用意する
  - Backend 全テストの標準実行が明示的なテスト設定を選び、固定鍵や暗黙の既定鍵をソースへ持ち込まないようにする
  - 完了時には、実鍵をリポジトリへ保存せず、隔離された設定で Django のテスト初期化が成功する
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.1_

- [x] 1.2 秘密値を露出しない共通契約と境界検証を実装する
  - アクセストークン、チャネルシークレット、暗号文ペア、公開結果、失敗分類を、暗黙の文字列化やシリアライズで値を出さない型として表現する
  - Messaging API channel ID、bot user ID、運用者向け名称、公開 UUID、秘密入力の形式と上限を永続化前に検証する
  - 登録時と資格情報更新時は token と secret の完全なペアだけを受け付け、片側不足や空入力を拒否する
  - 完了時には、canary の平文・暗号文・鍵が共通結果の `str`、`repr`、安全な検証エラーへ現れず、不正入力が DB や暗号処理へ到達しない
  - _Requirements: 1.1, 1.2, 2.6, 3.7, 7.1, 7.2, 7.3_

- [x] 1.3 専用 keyring を厳密に読み込む private runtime state を実装する
  - 専用環境変数だけを raw 入力とし、comma 区切り、canonical URL-safe Base64、32 byte、空要素・空白・quote・改行・重複拒否を補正なしで検証する
  - 先頭を現用鍵、後続を読取専用旧鍵として保持し、鍵値・鍵ID・鍵数を列挙、表示、シリアライズできない immutable state にする
  - 初回ロードを冪等にし、未初期化取得や異なる raw 値での再初期化を秘密なしの設定エラーとして拒否する
  - Django settings、DB、`SECRET_KEY`、既定鍵生成、遅延した環境再読込を利用しない
  - 完了時には、未指定・空・不正・重複 keyring が値を含まないエラーで失敗し、正しい keyring だけが process-private state として取得できる
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 7.1_

- [x] 1.4 チャネルと用途に束縛した認証付き暗号を実装する
  - access token と channel secret を別 envelope・別暗号文として現用鍵で暗号化し、format version、公開 UUID、credential kind を認証対象に含める
  - 通常 read は設定済み全鍵、primary 検証は現用鍵だけを使い、旧鍵 read と現用鍵 write の期間を共存させる
  - 改変、欠損、別チャネル・別用途への差し替え、未知 version、serialization 失敗を平文なしの安全な暗号エラーへ置換する
  - 鍵素材や鍵数を公開せず、ローテーション開始可否、再暗号化、primary-only 再検証を提供する
  - 完了時には、正常値だけが期待 context で復号でき、破損・差し替え値から平文や部分値が返らない
  - _Requirements: 2.1, 2.2, 2.4, 2.5, 4.6, 4.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.3_

- [x] 1.5 Django 起動を専用 keyring と安全なデバッグ条件へ接続する
  - 新しいチャネル基盤 app を起動 lifecycle へ登録し、DB/model を読む前に runtime keyring を一度だけ検証する
  - `DEBUG=True` を fail closed で拒否し、DB query logger を SQL parameter を保持・伝播しない設定にする
  - 設定失敗を raw 値や下位例外を含まない `ImproperlyConfigured` 相当へ変換し、migration、server、management command の開始を止める
  - 起動 hook の複数呼出しを冪等にし、app 設定や settings の列挙経路へ raw keyring を保持しない
  - 完了時には、鍵不備または `DEBUG=True` の Backend が DB 接続前に安全に停止し、正しい設定だけが起動を通過する
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.1, 7.3_

- [x] 1.6 コンテナ実行環境を専用 keyring と非露出条件へ統合する
  - 専用 keyring を Backend service だけへ注入し、Backend の既定を `DEBUG=false`、MySQL の general query log を明示的に無効にする
  - 未使用の channel secret 注入を構成例から削除し、既存配信互換の access token と固定 user ID は維持する
  - 安全な環境設定例には空の keyring 変数だけを置き、実鍵・固定 test 鍵・秘密を含む placeholder を保存しない
  - 技術セットアップの一部として、one-shot 鍵生成、厳密な keyring 文法、初期登録後の secret 撤去、rotation・backup・旧鍵撤去順序を runtime 手順へ反映する
  - 完了時には、Compose の解決済み構成で専用 keyring が Backend 以外へ渡らず、`DEBUG=false` と `general_log=OFF` が確認できる
  - _Requirements: 4.2, 4.3, 4.4, 4.5, 7.1_

- [x] 2. チャネルと資格情報の永続化・取得境界を構築する
- [x] 2.1 チャネルと完全な暗号文ペアのデータ整合性を実装する
  - 内部連番と不透明な公開 UUID を分離し、Messaging API channel ID、bot user ID、名称、有効状態、作成・更新日時を保持する
  - channel ID、bot user ID、公開 UUID の一意性と active lookup を DB 制約・index で保証する
  - 資格情報をチャネルへ一対一かつ削除保護で関連付け、2暗号文を null/empty 不可・非index・非編集対象として同じ行に保持する
  - モデル表示は公開 ID、active/configured state だけに制限し、暗号文の表示・Admin/Form 公開・物理削除操作を提供しない
  - 完了時には migration 適用後の DB が複数チャネルと完全な資格情報ペアを保存でき、不完全ペアと一意性違反を拒否する
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.2, 2.3, 2.5, 2.6, 7.1, 7.2, 7.4_

- [x] 2.2 通常登録・更新の locked persistence 境界を実装する
  - 呼出し側 transaction 内で channel と資格情報を同時作成し、更新時は対象 channel を行 lock 後に読み直す
  - 指定された非秘密項目だけを更新し、資格情報置換は検証済み暗号文ペアを単一更新で保存して更新日時を進める
  - transaction 外の locked 操作を安全に拒否し、unique race、deadlock、timeout、接続失敗を SQL や値なしの永続化エラーへ分類する
  - repository 内では暗号化・復号・業務状態判定を行わない
  - 完了時には、作成・更新が同一 transaction で commit/rollback し、競合時に別チャネルや片側資格情報へ部分変更が残らない
  - _Requirements: 1.1, 1.4, 1.5, 1.6, 1.7, 2.3, 2.4, 2.6, 5.4, 5.5, 7.3_

- [x] 2.3 用途別に一方だけ復号する資格情報取得境界を実装する
  - 公開 UUID からチャネルを解決し、不存在、無効、資格情報欠損を復号前に安全な利用不能結果へ分類する
  - 送信用取得では access token 列だけ、Webhook 検証用取得では channel secret 列だけを暗号境界へ渡す
  - 復号・完全性検証の失敗を復旧が必要な利用不能結果へ置換し、raw ORM/crypto 例外を返さない
  - 成功した秘密は用途固有 wrapper で一時返却するだけとし、model、cache、通常ログ、後続要求向け状態へ保存しない
  - 汎用 decrypt、暗号文 read/copy/export、秘密値の serializer や property を公開しない
  - 完了時には、spy 暗号境界で非対象列が一度も復号されず、各状態が契約どおり available/unavailable に分類される
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.7, 7.1, 7.2, 7.3, 7.4_

- [x] 3. 原子的なチャネル管理と安全な対話操作を実装する
- [x] 3.1 新規チャネルを資格情報と同時に原子登録する
  - 不透明な UUID を発行し、非秘密識別情報、名称、初期状態、完全な資格情報ペアを一つの登録要求として扱う
  - 2秘密を DB transaction の開始前に個別暗号化し、両方の成功後だけ channel と credential を同時作成する
  - validation、2件目の暗号化失敗、重複、保存失敗を安全な結果へ分類し、入力値や下位例外を含めない
  - 完了時には、成功時だけ1チャネルと完全な暗号文ペアが作成され、あらゆる失敗時に両 table へ部分行が残らない
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.6, 5.4, 7.3_

- [x] 3.2 指定項目更新と安全な有効状態遷移を実装する
  - 対象チャネルを lock 後、指定されたメタデータ、名称、資格情報ペア、有効状態だけを更新し、公開 UUID を維持する
  - 無効化では資格情報を保持し、保存済みペアだけで再有効化する場合は両方の全 keyring 復号・context 検証を必須にする
  - 新しい資格情報ペアと有効化を同時指定した場合は、新ペアを primary-only で検証して同一 mutation へ保存し、破損した旧ペアからの復旧を可能にする
  - not found、空更新、読取不能、競合、保存失敗では metadata を含む変更全体を rollback し、暗黙 create を行わない
  - 完了時には、対象チャネルだけの更新日時が進み、無効チャネルは取得対象外、検証済み再有効化は同じ識別情報と資格情報で取得対象へ戻る
  - _Requirements: 1.5, 1.6, 1.7, 2.3, 2.4, 2.5, 5.3, 5.4, 5.5, 7.5_

- [x] 3.3 (P) TTY で秘密を隠す対話入力境界を実装する
  - action と非秘密項目は通常 prompt、token と secret の新しいペアは確認付き hidden input だけで収集する
  - 更新時に既存秘密を default、placeholder、表示値として読み戻さず、置換を選んだ場合だけ新ペアを収集する
  - 非 TTY、echo fallback 警告、EOF、割込み、明示取消、不正入力を mutation request へ変換せず安全な cancelled/invalid 結果にする
  - prompt 境界は stdout/stderr や service mutation を所有せず、成功時だけ型付き入力を返す
  - 完了時には、端末収集テストで token/secret が表示されず、取消・入力不能時に mutation 用入力が生成されない
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.7, 7.1, 7.5_
  - _Boundary: ManageLineChannelPrompts_
  - _Depends: 1.2_

- [x] 3.4 管理コマンドから登録・更新・有効化・無効化を安全に実行する
  - 対話入力の action を対応する application operation へ dispatch し、取消・不正入力では service を呼ばない
  - update と状態変更では公開 UUID を必須にし、not found を新規作成へ切り替えない
  - 完了時には公開 UUID、非秘密識別情報、active/configured state、日時、安全な結果分類だけを表示する
  - CLI option、argument、stdout、stderr、例外へ平文・暗号文・鍵を渡さず、資格情報の表示・copy・export action を提供しない
  - 完了時には、登録・更新・状態変更の dispatch テストが公開結果だけを出し、失敗・取消経路で秘密と DB 部分変更が観測されない
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 4. 中断・再実行可能な鍵ローテーションを構築する
- [x] 4.1 (P) ローテーション専用の snapshot と行更新境界を実装する
  - 資格情報を持つ公開 UUID の安定した昇順 snapshot を返し、final sweep では新しい snapshot を取得する
  - 呼出し側の行単位 transaction 内だけで資格情報行を lock 後に読み直し、検証済みペアを単一更新する
  - transaction 外利用、行消失、deadlock、timeout、storage failure を値や SQL なしの安全な分類へ置換する
  - 暗号判定、全件集計、advisory lock lifecycle をこの境界へ混在させない
  - 完了時には、各行の pair が transaction 単位で commit/rollback し、失敗行の元暗号文が保持される
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.3_
  - _Boundary: RotationCredentialRepository_
  - _Depends: 2.1_

- [x] 4.2 (P) 二重ローテーションを防ぐ advisory lock 境界を実装する
  - 同一 MySQL connection で command scope lock を取得し、競合中は例外でなく busy として走査前に返す
  - transaction の commit/rollback を解放とみなさず、正常、busy、storage error、予期しない例外、割込みの全終了経路で明示解放する
  - lock 名、SQL、接続情報を結果・例外・通常ログへ含めない
  - 完了時には、各終了経路の後に別 connection から同じ lock を再取得でき、busy 経路で DB 行が変更されない
  - _Requirements: 6.1, 6.3, 6.5, 7.3_
  - _Boundary: RotationLock_
  - _Depends: 1.5_

- [x] 4.3 (P) 1資格情報ペアの再暗号化と primary 検証を実装する
  - 両値がすでに現用鍵と期待 context で読める場合は暗号文を変更せず verified とする
  - それ以外は全 keyring で両値を復号し、現用鍵へ再暗号化後、primary-only で元値・context 一致を再検証する
  - 片側破損、context 不一致、再暗号化・再検証失敗では新ペアを返さず安全な失敗分類だけを返す
  - final sweep 用検証は旧鍵 fallback や再暗号化を行わず、副作用なしで primary-only の可否だけを返す
  - 完了時には、primary 済み・旧鍵・片側破損・差し替え・再検証失敗の各 pair が期待する verified/rotated/failed へ分類される
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.3_
  - _Boundary: CredentialRotationItemProcessor_
  - _Depends: 1.4_

- [x] 4.4 ローテーション全件処理と最終完了判定を統合する
  - 旧鍵がない場合は repository、DB、advisory lock を呼ぶ前に変更ゼロの configuration-required 結果を返す
  - 準備完了時だけ batch lock を取得し、snapshot の各資格情報を別 transaction で lock・判定・必要時更新する
  - 行失敗は rollback して公開 UUID と安全な code だけを集計し、割込み時は処理中の1行だけを rollback する
  - 走査後に fresh snapshot を primary-only で再検証し、全件成功時だけ complete と旧鍵撤去可能を報告する
  - 再実行では primary 済み行を変更せず、未処理・旧鍵行だけを収束させる
  - 完了時には、complete/incomplete/busy/configuration-required と件数・失敗公開IDだけから成る集計が得られ、失敗が1件でもあれば完了にならない
  - _Requirements: 4.6, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 7.1, 7.2, 7.3_
  - _Depends: 4.1, 4.2, 4.3_

- [x] 4.5 鍵素材を受け取らないローテーションコマンドを実装する
  - process の検証済み keyring だけで全件処理を起動し、鍵を option、argument、stdin から受け付けない
  - complete では件数と旧鍵撤去可否、incomplete では失敗公開 UUID と安全な code、busy/configuration-required では行動可能な非完了結果だけを表示する
  - incomplete、busy、configuration-required を非完了 exit へ写像し、raw 例外を出力へ連結しない
  - 完了時には、全結果経路の stdout/stderr に鍵、鍵数、平文、暗号文がなく、exit status が集計状態と一致する
  - _Requirements: 6.1, 6.3, 6.6, 6.7, 6.8, 7.1, 7.2, 7.3_

- [ ] 5. 各境界を接続し、セキュリティ・競合・回帰を検証する
- [ ] 5.1 検証済み keyring から concrete 依存を一意に組み立てる
  - composition root だけが runtime の検証済み state から暗号境界、通常 repository、用途別 repository、channel service、rotation components、prompt を構築する
  - rotation service と1行 processor は同じ暗号 instance を共有し、command lifecycle ごとに必要な factory を一度だけ呼ぶ
  - factory は raw environment、Django settings、DB query、readiness policy、秘密値へ触れず、future consumer には用途別 repository 契約だけを公開する
  - 管理・rotation command を concrete 構成へ接続し、model/cipher の直接組立てや既存配信 app への依存を持ち込まない
  - 完了時には、両 command と用途別 repository を composition root から構築でき、依存差替えテストでは fake を使って各境界を独立検証できる
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.6, 4.7, 5.1, 5.3, 5.4, 5.5, 5.6, 5.7, 6.1, 6.8, 7.1, 7.3, 7.4_
  - _Depends: 2.3, 3.4, 4.5_

- [ ] 5.2 起動設定と settings 列挙の秘密非露出を検証する
  - 本番設定を使う子 process で、鍵未指定・空・不正・重複、`DEBUG=True`、`SECRET_KEY` だけの起動が DB access 前に失敗することを検証する
  - 正しい canary key で settings 列挙と差分表示を実行し、raw keyring の属性・値が stdout/stderr、例外、ログへ出ないことを検証する
  - 起動 hook の複数呼出しと runtime state の再初期化条件が、秘密値なしで冪等または安全な失敗になることを検証する
  - 完了時には、全 startup subprocess assertion が通り、設定不備経路も秘密値なしで fail closed になる
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.1, 7.3_

- [ ] 5.3 資格情報操作の query・log 非露出を検証する
  - `DEBUG=false` で資格情報の登録、用途別取得、rotation を実行し、canary の平文・暗号文・鍵を観測する
  - `connection.queries`、DB logger、safe exception、command の stdout/stderr に canary が一切存在しないことを検証する
  - DB logger が SQL parameter handler を持たず伝播しないことと、構成上 MySQL general query log が無効であることを確認する
  - 完了時には、登録・取得・rotation の全 canary assertion が query capture と通常ログの両方で通る
  - _Requirements: 3.7, 5.7, 6.8, 7.1, 7.2, 7.3_

- [ ] 5.4 登録・資格情報置換・状態遷移の原子性を検証する
  - duplicate、2件目暗号化失敗、資格情報 pair 置換失敗で channel/credential の作成・更新全体が rollback することを検証する
  - disable 後の暗号文保持と、保存済み完全 pair を使う enable が同じ公開 ID で取得対象へ戻ることを検証する
  - 破損した保存済み pair だけでの enable は全変更を拒否し、新 pair と同時 enable では primary 検証後に metadata と同時 commit して復旧できることを検証する
  - 完了時には、失敗シナリオで対象 channel の metadata・状態・資格情報が一切変わらず、成功時だけ更新日時が進む
  - _Requirements: 1.4, 1.5, 1.6, 1.7, 2.1, 2.3, 2.4, 2.5, 5.4, 5.5, 7.3_

- [ ] 5.5 通常チャネル更新の並行競合を検証する
  - 独立 DB connection と行 lock を使い、同一 channel への並行 metadata・資格情報更新を競合させる
  - lock 後に読み直した最新値へ各更新が収束し、lost update や別 channel への変更が発生しないことを検証する
  - unique race、deadlock、timeout が raw DB 情報なしの conflict/retryable 分類となり、transaction が rollback することを検証する
  - 完了時には、競合テストが対象 channel だけの一貫した最終状態と更新日時を観測し、部分 pair を一件も残さない
  - _Requirements: 1.4, 1.5, 2.3, 2.4, 5.4, 7.3_

- [ ] 5.6 対話管理操作の端末安全性と公開出力を結合検証する
  - 登録、部分更新、有効化、無効化を対話入力から application operation まで通し、hidden pair と指定項目だけが渡ることを検証する
  - 非 TTY、hidden input 警告、EOF、割込み、取消、不正 UUID、not found では service mutation ゼロかつ暗黙 create なしを検証する
  - 成功・失敗出力に公開 summary と安全な code だけが現れ、既存値、新しい平文、暗号文、鍵が現れないことを canary で検証する
  - 完了時には、全 action の command test が結果と exit behavior を再現し、資格情報の表示・copy・export 経路が存在しない
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 5.7 ローテーションの中断・再実行と破損行保持を検証する
  - N件目で強制割込みし、処理中の1行だけが rollback され、commit 済み行と未処理行を新旧 keyring で取得できることを検証する
  - 再実行で primary 済み行を変更せず、残りの旧鍵行だけが現用鍵へ収束することを検証する
  - 破損行は元暗号文のまま保持され、失敗があれば完了非報告、修復後の再実行でのみ完了になることを検証する
  - 完了時には、中断前後の件数・公開 UUID・安全な failure code が一致し、未検証暗号文への上書きが一件もない
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 7.1, 7.2, 7.3_

- [ ] 5.8 ローテーションと通常更新の競合・final sweep を検証する
  - 通常資格情報更新と rotation を独立 connection で競合させ、行 lock 後の最新 pair が失われないことを検証する
  - 初回 snapshot 後に追加・更新された資格情報も fresh snapshot の final sweep で検査されることを検証する
  - final sweep が primary-only 検証だけを行い、旧鍵 fallback、再暗号化、DB 更新を実行しないことを確認する
  - 完了時には、全最新 pair が primary で読める場合だけ旧鍵撤去可能となり、一件でも旧鍵専用または破損 pair があれば incomplete になる
  - _Requirements: 1.5, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.3_

- [ ] 5.9 二重ローテーションと advisory lock の全終了経路を検証する
  - 同時 command の一方だけが lock を取得し、もう一方は busy で走査・変更せず終了することを検証する
  - 正常、busy、行失敗、storage error、予期しない例外、割込みの各経路を発生させる
  - 各経路の終了後に別 connection から同じ advisory lock を再取得でき、lock 名・SQL・接続情報が出力されないことを検証する
  - 完了時には、二重実行で暗号文更新が重複せず、全終了経路で lock 解放と安全な exit/output が観測できる
  - _Requirements: 6.1, 6.3, 6.5, 6.8, 7.1, 7.2, 7.3_

- [ ] 5.10 Backend 全体の migration・設定・既存配信回帰を確認する
  - 新 app の migration 作成状態と system check を検証し、schema 制約と startup gate が標準コンテナ手順で有効になることを確認する
  - Backend 全テストを隔離テスト設定で実行し、各テストの日本語ケース・期待値コメント規約を満たす
  - 既存配信テストを実行し、access token と固定 user ID の環境変数契約が維持され、channel secret 注入削除で配信 behavior が変わらないことを確認する
  - Frontend、公開 API、既存配信実装へチャネル基盤の model/cipher 直接依存が追加されていないことを確認する
  - 完了時には、migration check、Django system check、Backend 全テスト、既存配信回帰が標準コンテナコマンドで成功する
  - _Requirements: 1.1, 2.2, 3.7, 4.1, 4.5, 5.7, 6.8, 7.1, 7.4_

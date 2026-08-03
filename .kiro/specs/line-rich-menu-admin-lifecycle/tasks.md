# Implementation Plan

- [x] 1. 基盤契約と管理操作の提供可否を整える
- [x] 1.1 チャネル無効化状態を永続化する
  - 現在の無効化意図、一意な操作、owner・provider・revision、状態・理由・回復対象・時刻の整合制約を持つ保存領域を追加する。
  - 既存チャネルへのbackfillを行わず、秘密、URL、画像、LINE識別子を保存しない形で導入する。
  - 導入後は不正な状態の組合せが拒否され、チャネル削除時には無効化状態も同じ完了単位で消える。
  - _Requirements: 10.4, 10.6, 10.9, 11.8_
  - _Boundary: ChannelDeactivationRepository_

- [x] 1.2 readinessを反映した実効操作を公開する
  - domainの許可操作、チャネル有効状態、`read_only`・`recovery_only`・`enabled`を合成し、設定不整合は利用不可へ閉じる。
  - 無効チャネルの状態取得ではLINEへ通信せず、保存済みprojectionだけを返す。
  - 状態応答から提供mode、実効操作、安全な利用不可理由を確認できる。
  - _Requirements: 4.8, 9.1, 9.2, 9.3, 12.1, 12.2, 12.3, 12.4, 12.6, 12.7_
  - _Boundary: EffectiveCapabilityProjection_

- [x] 1.3 無効化向けheadless lifecycle契約を整える
  - 解除不要、解除必要、アプリ外既定、再確認必要、後片付け必要、利用不可を不透明なclosed resultで表す。
  - 保存subjectがある再確認と、外部作用を伴わない最新状態の再評価を分離し、呼出側へLINE資源IDを公開しない。
  - 同じ回復操作IDの再送は保存済み結果へ収束し、ownerの明示操作なしに外部作用を開始しない。
  - _Requirements: 10.2, 10.3, 10.5, 10.6, 10.7, 10.8, 10.10, 10.11_
  - _Boundary: RichMenuLifecyclePort_

- [x] 1.4 無効化に伴う解除対象の選定と操作受付を原子的に予約する
  - リッチメニュー状態のlock下で、評価証明、観測状態、管理資源、所有権、チャネルrevisionを再検証する。
  - 呼出側が対象資源を指定せず、対象選定と解除操作受付を同じtransactionで確定する。
  - 古い評価、アプリ外既定、結果不明、後片付け待ちではLINE呼出しを行わず、安全な拒否結果になる。
  - _Requirements: 1.6, 10.3, 10.4_
  - _Boundary: RichMenuLifecyclePort_

- [x] 1.5 runtime readinessと同一release markerをfail closedに合成する
  - 安全な既定値を`read_only`とし、提供modeと統合markerの一致を起動時と操作前に検証する。
  - 統合未完了ではowner向け・headless双方の変更を外部作用前に拒否し、`recovery_only`では回復操作だけを許可する。
  - 構成不整合が安全な利用不可状態としてAPIとsystem checkの両方に現れる。
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_
  - _Boundary: LifecycleReadiness_

- [ ] 2. チャネル無効化と再有効化をリッチメニュー状態へ統合する
- [ ] 2.1 現在の無効化意図とrevision CASを管理する
  - チャネルと現在の無効化状態を固定順でlockし、同一操作の再送と別操作の競合を区別する。
  - 外部結果保存、明示再確認時だけのrevision前進、古いrevisionの記録、無効化完了を一貫した状態遷移として提供する。
  - 完了時は更新前revisionを一度だけ検証し、チャネル無効化と操作完了を同時にcommitして新しいrevisionを返す。
  - _Requirements: 10.4, 10.6, 10.9, 10.10, 11.7, 11.9_
  - _Boundary: ChannelDeactivationRepository_

- [ ] 2.2 無効化開始を解除不要・解除必要・阻止状態へ収束させる
  - owner、provider、有効状態、revision、現在の無効化意図をfenceし、外部I/O中はDB lockを保持しない。
  - 解除不要なら原子的に無効化し、管理対象が現在既定なら同じ意図へ解除を結び付け、アプリ外既定・結果不明・後片付け待ちは確認待ちにする。
  - 処理中もチャネルは有効状態を維持し、重複実行と競合操作が安全に拒否される。
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
  - _Boundary: ChannelDeactivationCoordinator_
  - _Depends: 1.3, 1.4, 2.1_

- [ ] 2.3 明示再確認と古いrevisionからの回復を実装する
  - 保存subjectがある場合だけ元操作を再確認し、subjectがない場合は新しい変更操作を作らず最新状態だけを再評価する。
  - revision競合では部分更新せずチャネルを有効に保ち、ownerの明示再確認時だけ最新revisionへ前進させる。
  - 同じ回復操作の再送は一件の結果、別操作は競合となり、自動再確認は発生しない。
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11_
  - _Boundary: ChannelDeactivationCoordinator_
  - _Depends: 2.2_

- [ ] 2.4 owner向け無効化APIと安全な状態表示を公開する
  - 状態取得、無効化開始、明示再確認をowner・provider・revision・CSRFのfence付きで提供する。
  - チャネル一覧と詳細へ未解決状態を追加し、既存の直接無効化経路からの迂回を拒否する。
  - 応答は安全な状態、理由、操作相関、次の操作だけを返し、秘密や外部資源IDを含まない。
  - _Requirements: 1.4, 1.5, 1.6, 1.7, 10.1, 10.4, 10.6, 10.9, 10.11_
  - _Boundary: ChannelAdminLifecycleAPI_
  - _Depends: 2.2, 2.3_

- [ ] 2.5 再有効化を過去資源の復元なしで統合する
  - チャネルだけを有効化し、完了済みの無効化状態は過去のprojectionとして保持する。
  - 新しいチャネル詳細とリッチメニュー状態の両方を取得できるまで、変更操作を閉じる状態を返す。
  - 過去の管理資源は自動適用せず、観測結果を保存状態との関係または外部変更として分類できる。
  - _Requirements: 9.4, 9.5, 9.6, 11.1, 11.2_
  - _Boundary: ChannelDeactivationCoordinator_
  - _Depends: 2.4_

- [ ] 3. 物理削除と履歴削除を原子的に統合する
- [ ] 3.1 リッチメニュー参照確認をチャネル削除のfenceへ登録する
  - 適用中、処理中、結果不明、後片付け待ち、無効化要確認を削除阻止とし、確定済み履歴だけの状態は参照なしに分類する。
  - 外部LINE変更を行わず、既存のowner・channel lock順序に従って削除直前の状態を再確認する。
  - 削除事前確認から安全な阻止理由または参照なしの結果を観測できる。
  - _Requirements: 11.3, 11.4, 11.5, 11.9, 12.5_
  - _Boundary: AtomicChannelDelete_

- [ ] 3.2 確定済み履歴のrollback-only削除契約を提供する
  - 削除済み、対象なし、阻止中、保存領域利用不可をclosed resultとして返す。
  - 確定済み履歴だけを対象にし、管理資源または未解決操作が残る場合は削除を阻止する。
  - 失敗結果を呼出側が誤って無視しても、同じtransactionをcommitできないことを確認できる。
  - _Requirements: 11.5, 11.6, 11.7, 12.5_
  - _Boundary: AtomicChannelDelete_

- [ ] 3.3 参照再確認・履歴削除・資格情報とチャネル削除を一体化する
  - revisionと全参照を削除直前に再確認し、履歴削除済みまたは対象なしの場合だけ続行する。
  - 履歴、資格情報、チャネル、無効化状態の削除を同じtransactionでcommitする。
  - 新しい阻止参照、競合、履歴削除失敗では全てのlocal stateが残り、部分成功を返さない。
  - _Requirements: 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_
  - _Boundary: AtomicChannelDelete_
  - _Depends: 3.1, 3.2_

- [ ] 3.4 チャネル削除APIへ阻止理由と安全な完了結果を統合する
  - 既存の削除契約を維持しつつ、確認時と失敗時にownerが解消できる安全な阻止理由を返す。
  - 成功応答は非秘密の識別情報だけとし、履歴や資格情報の内容を返さない。
  - owner・provider・revision・CSRF違反や競合では部分削除せず、最新状態の再取得を要求する。
  - _Requirements: 1.4, 1.7, 11.3, 11.5, 11.7, 11.8, 11.9_
  - _Boundary: AtomicChannelDelete_
  - _Depends: 3.3_

- [ ] 4. Frontendの境界契約と画面状態を構築する
- [ ] 4.1 リッチメニューと無効化の応答をexact DTOとして検証する
  - template、状態、操作、履歴、無効化についてkey、UUID、日時、enum、長さをclosed setで検証する。
  - 未知のkey・値や禁止された秘密をprotocol errorにし、以前のprojectionを最新として扱わない。
  - preview以外で確認値、URL、画像を受け入れない検証結果を観測できる。
  - _Requirements: 1.4, 1.5, 1.7, 8.7, 8.8, 12.3_
  - _Boundary: RichMenuAdminClient_

- [ ] 4.2 認証付きHTTP手順と安全なエラー変換を実装する
  - template、preview、状態、操作、履歴、無効化APIをsame-origin sessionとCSRFで呼び出す。
  - GET失敗時は古い状態を表示せず、POSTのnetwork failureでは成功・失敗を推測せず明示的な再取得へ移す。
  - 確認値、URL、画像、内部例外をログ、URL、永続storage、エラーへ渡さない。
  - _Requirements: 1.3, 1.4, 1.5, 1.7, 3.6, 3.7, 3.8, 5.8, 5.9, 7.10, 8.7, 8.8_
  - _Boundary: RichMenuAdminClient_

- [ ] 4.3 一時的なeditor状態とrequest generationの遷移を実装する
  - 読込中、準備済み、読取専用、再取得必要、取得失敗と、editorのclosed stateを区別する。
  - 入力、template、revision、期限の変化でpreviewを無効化し、古いgenerationの応答を採用しない。
  - session失効またはunmountでdraft、確認値、画像参照を破棄し、永続storageやURLへ保存しない。
  - _Requirements: 1.3, 1.5, 1.6, 2.3, 2.7, 2.8, 3.2, 3.5, 3.6, 3.7, 3.8, 3.9, 9.4, 9.5_
  - _Boundary: RichMenuAdminState_

- [ ] 4.4 チャネル詳細・リッチメニュー状態・履歴を一画面へ安全に合成する
  - 同じrequest generationで各取得結果を採用し、読込中、失敗、protocol error時に古い操作を表示しない。
  - 無効チャネルは保存状態だけの読取専用とし、有効チャネルでもserverの実効操作以外は閉じる。
  - session失効、owner利用不能、全連携解除開始時には画面とmemory-only dataが直ちに破棄される。
  - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.7, 3.9, 9.1, 9.2, 9.3, 12.7_
  - _Boundary: RichMenuAdminConsole_
  - _Depends: 4.1, 4.2, 4.3_

- [ ] 5. template編集と期限付きpreviewを提供する
- [ ] 5.1 組み込みtemplateの全項目editorを提供する
  - template名、版、必須項目、入力上限、領域を表示し、表示名と完全なHTTPS URLだけを編集可能にする。
  - 項目検証に失敗した場合はpreview requestを送らず、修正対象と安全な理由を表示する。
  - owner画像、自由layout、custom template、URI以外のactionを提供せず、未適用入力が画面で分かる。
  - _Requirements: 2.1, 2.2, 2.3, 2.9, 2.10_
  - _Boundary: RichMenuEditor_
  - _Depends: 4.3_

- [ ] 5.2 editorと状態境界で未適用入力の消去規則を実装する
  - 未適用入力があるtemplate切替では全入力の消去を確認し、取消時は元のtemplateと入力を保つ。
  - 画面内移動とreload・closeに使う未保存判定と消去処理を提供し、session失効やowner利用不能では確認なしに全一時参照を破棄する。
  - 消去完了時にdraftとpreview参照が空になり、object URLも解放され、再認証後に復元されない。
  - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 3.9_
  - _Boundary: RichMenuEditor, RichMenuAdminState_
  - _Depends: 5.1_

- [ ] 5.3 期限付きpreviewと手動リンク確認を実装する
  - 対象チャネル、template、生成画像、全項目のURL、実状態、置換警告、有効期限を一つの確認表示にする。
  - 表示だけではリンクへ接続せず、ownerが選んだ一件だけをopenerとreferrerを渡さずに開く。
  - 入力、revision、template、実状態の変化または期限切れで適用を閉じ、リンク結果や到達保証を保存しない。
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_
  - _Boundary: RichMenuPreview_
  - _Depends: 4.2, 4.3, 5.1_

- [ ] 6. 状態・操作・回復・履歴の各panelを実装する
- [ ] 6.1 (P) 保存状態・LINE実状態・実効操作を保守的に表示する
  - 管理対象、既定なし、アプリ外、別の管理対象、結果不明と、操作・後片付け状態を同じ概要内で区別する。
  - アプリ外資源の内容や所有権を推測せず、禁止理由と次の明示操作を表示する。
  - readinessと無効状態を含むserverの実効操作だけが画面上で実行可能になる。
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 9.2, 12.1, 12.2, 12.3, 12.4, 12.7_
  - _Boundary: RichMenuStatePanel_
  - _Depends: 1.2, 4.4_

- [ ] 6.2 適用・置換の最終確認と一件の操作追跡を実装する
  - 対象チャネル、template、全項目、現在既定、置換影響を確認し、アプリ外資源自体は削除しないと示す。
  - 再描画前の二重clickを含む重複POSTと競合操作を防ぎ、同じ操作の保存済み状態を再表示する。
  - 成功、失敗、結果不明、競合を推測せず表示し、自動polling、自動再試行、自動再確認を行わない。
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10_
  - _Boundary: RichMenuAdminConsole_
  - _Depends: 5.3, 6.1_

- [ ] 6.3 適用解除と管理終了を外部効果別に明示確認する
  - 適用解除はLINE既定を外し、管理終了はLINE既定を維持したままアプリ管理を終えることを対象と不可逆性と共に示す。
  - ownerが確定した一件の操作だけを開始し、処理中は重複送信と競合するリッチメニュー・チャネル操作を閉じる。
  - 現在既定の差異、成功、失敗、結果不明を保守的に表示し、自動再試行を行わない。
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_
  - _Boundary: RichMenuRecoveryPanel_
  - _Depends: 4.2, 6.1_

- [ ] 6.4 結果不明の再確認と管理資源の後片付けを明示確認する
  - 再確認は元の操作だけ、後片付けは一件の管理対象資源だけを対象にし、段階、不可逆性、禁止中操作を示す。
  - 所有権、元操作との関係、現在既定が一致しない場合や一意に観測できない場合は、削除せず安全な拒否または結果不明を表示する。
  - 重複、利用頻度超過、失敗、結果不明を同じ対象の保存結果へ収束させ、自動再試行や任意資源の採用を行わない。
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10_
  - _Boundary: RichMenuRecoveryPanel_
  - _Depends: 4.2, 6.1, 6.3_

- [ ] 6.5 (P) 上限付きcursor履歴と無効チャネルの読取専用表示を実装する
  - owner、provider、channelで限定した履歴を新しい順に表示し、ownerの明示click時だけ次のpageを一度追加する。
  - 操作時点の項目、資源との関係、後片付け結果を表示し、rollback、画像再利用、統計、予約を提供しない。
  - 取得失敗やprotocol error時は既存pageを完全・最新と断定せず、無効化後や再認証後も保存済み履歴だけを参照できる。
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 9.1, 9.3_
  - _Boundary: RichMenuHistory_
  - _Depends: 4.1, 4.2, 4.4_

- [ ] 7. チャネル管理画面へ各ライフサイクルを統合する
- [ ] 7.1 同一providerのチャネルカードから専用画面へ接続する
  - 対象チャネルだけにmemory-onlyの導線を表示し、対象情報、未適用入力、秘密をURLへ載せない。
  - 未保存判定を使って画面内の戻る操作を確認し、reload・close時は標準`beforeunload`確認を登録・解除する。session失効と全連携解除開始では確認なしに画面をunmountする。
  - editorの消去処理を再実装せず、owner・provider・channel不一致時は対象非開示のconsoleへ戻り、unmount後に画面参照が残らない。
  - _Requirements: 1.1, 1.4, 1.7, 2.6, 2.7, 2.8_
  - _Boundary: RichMenuAdminConsole_
  - _Depends: 4.4, 5.2_

- [ ] 7.2 チャネル無効化画面を同じ無効化意図へ接続する
  - リッチメニュー状態、実状態、進行中操作、後片付け、解除影響を確認して一件の無効化を開始する。
  - 確認中、解除中、確認待ちでは有効表示と競合禁止を維持し、再認証後も同じ無効化意図を表示する。
  - アプリ外既定、結果不明、後片付け待ち、競合では外部解消または明示再確認を案内し、自動再確認しない。
  - _Requirements: 10.1, 10.3, 10.4, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11_
  - _Boundary: ChannelAdminLifecycleAPI, RichMenuAdminConsole_
  - _Depends: 2.4, 6.1, 6.3, 6.4_

- [ ] 7.3 再有効化後の最新状態取得gateを画面へ統合する
  - チャネルの有効化だけを表示し、過去の未適用入力、preview、管理資源を復元しない。
  - 新しいチャネル詳細とリッチメニュー状態の両方の取得が成功するまで、変更操作を閉じる。
  - 観測差を外部変更または結果不明として表示し、過去の成功を再適用の成功として扱わない。
  - _Requirements: 9.4, 9.5, 9.6, 11.1, 11.2_
  - _Boundary: RichMenuAdminConsole, ChannelDeactivationCoordinator_
  - _Depends: 2.5, 4.4_

- [ ] 7.4 チャネル削除確認へ阻止理由と原子的な結果を統合する
  - 削除の不可逆性、現在の参照状態、同時削除される確定済み履歴を確認表示する。
  - 阻止、競合、失敗時は必要な解消操作と再取得を示し、部分削除を成功として表示しない。
  - 成功時だけ非秘密のチャネル概要を表示し、対象画面と一時状態を破棄する。
  - _Requirements: 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_
  - _Boundary: AtomicChannelDelete, RichMenuAdminConsole_
  - _Depends: 3.4, 4.4, 6.5_

- [ ] 8. 単体・統合・安全性・提供modeを検証する
- [ ] 8.1 (P) 実効操作・headless評価・解除予約のBackend単体テストを追加する
  - 提供modeの積集合、無効状態、結果不明、構成不整合と、評価・回復の全closed variantを検証する。
  - 古い評価、対象差替え、所有権差替えで予約がLINE呼出しゼロになることを検証する。
  - 安全なprojection、対象非露出、操作の一件性を観測できる単体テストが通る。
  - _Requirements: 1.6, 4.8, 10.2, 10.3, 10.4, 10.6, 10.7, 10.8, 12.1, 12.2, 12.3, 12.4, 12.6_
  - _Boundary: EffectiveCapabilityProjection, RichMenuLifecyclePort_
  - _Depends: 1.2, 1.3, 1.4_

- [ ] 8.2 (P) 無効化API・統合・並行更新のBackendテストを追加する
  - 解除不要、解除必要、阻止、結果不明、明示再確認、revision競合を二接続の競合を含めて検証する。
  - 外部I/O中のlock非保持、無効化の原子的完了、同一IDの再送、別IDの競合、自動再確認ゼロ件を検証する。
  - 失敗時に有効チャネルと同じ現在意図が残るBackendテスト群が通る。
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11_
  - _Boundary: ChannelDeactivationCoordinator, ChannelAdminLifecycleAPI_
  - _Depends: 2.1, 2.2, 2.3, 2.4, 2.5_

- [ ] 8.3 (P) 参照確認・履歴削除・物理削除・readinessのBackend統合テストを追加する
  - 阻止参照、確定履歴だけ、対象なし、履歴削除失敗、新規参照、deadlockを検証する。
  - チャネル、資格情報、履歴、無効化状態が必ず全commitまたは全rollbackになることを検証する。
  - modeとmarker不整合では変更・LINE呼出しゼロ件となり、`enabled`だけが通常操作を許すテスト群が通る。
  - _Requirements: 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_
  - _Boundary: AtomicChannelDelete, LifecycleReadiness_
  - _Depends: 1.5, 3.1, 3.2, 3.3, 3.4_

- [ ] 8.4 (P) Frontend DTOと状態遷移の単体テストを追加する
  - 全variantのexact parse、未知・余分・禁止応答とrequest generationを検証する。
  - 入力、template、revision、期限によるpreview無効化と、session失効・unmount時のmemory clearを検証する。
  - 未適用入力、確認値、画像が永続storage、URL、エラーへ残らない単体テストが通る。
  - _Requirements: 1.4, 1.5, 1.7, 2.3, 2.7, 2.8, 2.9, 3.2, 3.5, 3.6, 3.7, 3.8, 3.9, 8.7, 8.8_
  - _Boundary: RichMenuAdminClient, RichMenuAdminState_
  - _Depends: 4.1, 4.2, 4.3_

- [ ] 8.5 (P) 導線・editor・previewのComponentフローを検証する
  - 対象チャネルの導線、読込・失敗表示、未適用入力の切替・戻る・`beforeunload`・session失効と全template項目を検証する。
  - previewの期限・無効化・object URL解放、リンク自動接続ゼロ件、手動open一件を検証する。
  - 導線、編集、previewのowner journeyが独立したComponentテスト群で通る。
  - _Requirements: 1.1, 1.2, 1.3, 1.5, 2.1, 2.2, 2.4, 2.5, 2.6, 2.10, 3.1, 3.3, 3.4, 3.10_
  - _Boundary: RichMenuAdminConsole, RichMenuEditor, RichMenuPreview_
  - _Depends: 5.1, 5.2, 5.3, 7.1_

- [ ] 8.6 (P) 操作・回復・履歴のComponentフローを検証する
  - 保存状態と実状態の全分類、適用、置換、解除、管理終了、再確認、後片付けのdialogと安全な阻止理由を検証する。
  - 再描画前の二重clickでもPOST一件、自動polling・再試行ゼロ件、履歴追加・失敗・無効時読取専用を検証する。
  - 状態、操作、回復、履歴のowner journeyが独立したComponentテスト群で通る。
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 7.6, 7.7, 7.8, 7.9, 7.10, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 9.1, 9.2, 9.3_
  - _Boundary: RichMenuStatePanel, RichMenuRecoveryPanel, RichMenuHistory_
  - _Depends: 6.1, 6.2, 6.3, 6.4, 6.5_

- [ ] 8.7 無効化・再有効化・物理削除の画面統合を検証する
  - 無効化確認、確認待ち再開、有効状態維持、同一意図、再有効化後の再取得gate、削除阻止と結果を検証する。
  - アプリ外既定、後片付け待ち、競合、部分削除失敗が安全な次操作を示し、自動再確認ゼロ件となることを検証する。
  - チャネルライフサイクルのowner journeyがBackend契約と結合したテスト群で通る。
  - _Requirements: 9.4, 9.5, 9.6, 10.1, 10.4, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_
  - _Boundary: RichMenuAdminConsole, ChannelAdminLifecycleAPI, AtomicChannelDelete_
  - _Depends: 7.2, 7.3, 7.4_

- [ ] 8.8 秘密非露出とpreviewのno-storeを横断検証する
  - 資格情報、owner識別子、session、確認値、URL、画像、LINE生応答のcanaryが状態、履歴、エラー、ログ、表現、無効化状態へ漏れないことを検証する。
  - previewだけが必要な確認情報を`no-store`で返し、画面破棄後に全一時参照が解放されることを検証する。
  - 秘密非露出を観測可能に確認するsecurityテスト群が通る。
  - _Requirements: 1.7, 2.8, 3.9, 8.8, 11.8_
  - _Boundary: RichMenuAdminClient, RichMenuAdminConsole, ChannelDeactivationCoordinator, AtomicChannelDelete_
  - _Depends: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

- [ ] 8.9 性能budgetとreadiness rolloutの最終回帰を行う
  - 状態・履歴のquery budget、履歴上限50件、自動polling・再試行ゼロ件、外部I/O中のlock非保持、原子的commit・rollbackと競合を検証する。
  - 同一releaseの`enabled`、障害時の`recovery_only`、資源が空の`read_only`へ切替後、再取得した実効操作がruntimeと一致することを検証する。
  - FrontendとBackendのbuild・testが通り、統合成果が各提供modeでfail closedに動作する。
  - _Requirements: 3.4, 3.10, 5.10, 7.10, 8.7, 8.9, 10.11, 11.7, 11.9, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_
  - _Boundary: LifecycleReadiness, RichMenuAdminConsole, ChannelDeactivationCoordinator, AtomicChannelDelete_
  - _Depends: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

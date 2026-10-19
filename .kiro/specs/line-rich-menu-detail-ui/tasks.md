# 実装計画

## 実行方針

各実行タスクは実装とその境界の単体検証を含む1〜3時間の単位とする。コンテナだけの親タスクは実行数に含めない。必須検証のため、延期可能なテストは設けない。既存の完了タスク・blocked・Notesはない。

所有範囲はdesign.mdのFile Structure Planに従い、対応する単体テストも同じ担当が所有する。統合・検証タスクは先行担当から引き継ぎ、同じファイルを並行編集しない。特にhookとそのテスト、Console、repository、serviceはタスク間で逐次引き継ぐ。文書同期は既存差分として維持し、本計画に追加の文書作業は含めない。

原則は番号順に進める。並行候補は2.1・2.2と3の間だけとし、3は1の完了後にrepository作業と並行できる。4以降は両方の完了を待つ。Backendの加算的APIを先に検証し、Frontendの切り替えを10.1で行う。旧API・データ・履歴部品を維持し、Frontendを旧版へ戻せる互換性を最終検証する。

- [x] 1. 閲覧用の値・公開portと検証用fixtureを整える
  - 現在資源、作成元、操作、停止intent、設定内容の比較値と不存在を表現するrequest内snapshot、および閲覧結果を既存モデルから独立したimmutable値として用意する。画像・完全URL・資格情報をreprへ出さない。
  - owner・channel・deactivationの既存公開portを利用し、新しい競合codeを閲覧限定で扱えるようにする。既存serviceやrepositoryへの逆向き依存を持ち込まない。
  - 承認済みHTTP契約の各状態を安全なfixtureにし、既存Django・Vitest/jsdomとfake gatewayで後続検証に使えることを確認する。MySQL並行transaction試験の実行経路も既存構成で確保する。
  - 既存owner設定、keyring、catalog/runtime検証、readinessを再利用し、新依存・環境変数・font変更・DB migrationなしで値とfixtureを利用できる。閲覧のためにmutation modeを変更しない。
  - _Boundary: B1 current_types / types_
  - _Requirements: 2.1, 2.4, 2.6, 2.7, 3.6, 6.7_

- [x] 2. 現在資源の保存関係と競合検出を実装する
- [x] 2.1 現在資源に対応する設定と回復対象を取得する
  - 現在資源から作成元applyへの保存関係を辿り、owner・provider・channelと確定済み適用状態を検証する。履歴順による推測や再適用を必要としない。
  - template ID・正の版・全fields・完全HTTPS URLを検証し、同一版のarea順と既存入力規則に照合する。補助キー欠落の旧snapshot、版廃止、欠落・不正を区別し、値をトリム・正規化しない。
  - 管理終了対象の所有関係は設定の編集可能性から分離する。回復候補は既存predicateを共用してsubject・target・置換関係が有効な組に限定し、履歴一覧を辿らず必要な履歴有無・操作参照を残す。
  - 旧snapshot、異なるowner、履歴最新と異なる現在資源、関係不明の旧資源のテストで、正しい設定と許可対象だけを返せる。
  - _Boundary: CurrentReadRepository_
  - _Depends: 1_
  - _Requirements: 2.1, 2.3, 2.5, 3.5, 6.6, 6.7, 7.3_

- [x] 2.2 取得前後の保存状態を同じ順序で比較する
  - owner → channel → deactivation → rich-menu state → operation/resourceの順序でsnapshotの取得・最終lockを行う。
  - current・active・blocking参照、資源のlifecycle・LINE ID・digest・更新時刻、作成元設定、操作status/stage、停止intent、cleanup関係を比較し、行の新規作成・消滅も検出する。
  - 観測日時だけの更新を競合にせず、設定・所有関係・操作状態の変更は競合として返す。画像や設定を永続化しない。
  - 一致・変更・不存在から作成・削除のrepository試験で、前提を失ったsnapshotが採用されない。
  - _Boundary: CurrentReadRepository_
  - _Requirements: 2.4, 2.7, 3.4, 3.7_

- [x] 3. (P) 元の画像を上限付きで取得・検証する
  - 固定hostと検証済み資源IDを使うHTTPX streamで一度だけ取得し、redirect禁止、上限+1 byteで打ち切り、response/clientの確実なcloseを実装する。
  - 最大1 MiB、PNG/JPEG実decode、幅800〜2500・高さ250以上・比率1.45以上を展開前に検証する。各I/O timeoutに加えて開始から5秒の単一deadlineを適用し、残り時間を超えて読まない。
  - canonical pixel digestと元bytesを返し、再エンコードしない。既存downloadの戻り値・SDK経路は維持してdecode検証だけを共用する。
  - 正常bytes一致、404、timeout、429、5xx、破損、過大bytes/寸法、禁止形式、closeと秘密非露出をgateway試験で確認し、自動retryしない。
  - _Boundary: RichMenuGateway_
  - _Depends: 1_
  - _Requirements: 2.2, 2.6, 2.7, 3.1_

- [x] 4. 閲覧serviceの観測と操作候補を構成する
- [x] 4.1 認可・既定照会・画像取得・最終検証を統合する
  - ownerと厳密provider一致、revisionを短いtransaction内で検証し、activeだけ資格情報を取得する。停止中は資格情報を復号せず、同じlock内の保存情報を返す。
  - transaction外で既定を照会し、管理対象と一致した場合だけ画像を取得する。確定済みcleanup待ちは画像を返せるが、外部・結果不明・進行中では画像や設定を返さない。
  - 画像取得を試みた場合は失敗時も既定を再照会し、digest不一致や既定変化は全体競合、再照会失敗は全体失敗とする。最終認可・snapshot比較が一致した場合だけ観測を記録する。
  - fake gatewayでmanaged/unset/external/attention/inactiveと画像だけの失敗を識別でき、renderer・プレビュー発行・mutation・外部資源内容取得を呼ばない。
  - _Boundary: B1 integration — CurrentMenuService, CurrentReadRepository, RichMenuGateway, owner/channel/deactivation ports_
  - _Depends: 2.2, 3_
  - _Requirements: 1.5, 2.1, 2.2, 2.4, 2.6, 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 7.4_

- [x] 4.2 保存関係と提供モードから許可操作を合成する
  - inactive、停止pending、active/blocking操作、未解決cleanupの順に制限し、制限がない場合だけ設計の観測別候補表を適用する。
  - 通常適用中と既定不一致の適格な管理対象に管理終了候補を補い、currentなし・不正所有関係・unknownを対象にしない。画像失敗・設定不明・版廃止だけでは有効な管理終了を除外しない。
  - 候補を既存readinessで絞り、read_only/recovery_only/enabledを守る。変更前候補と有効操作を返し、回復は有効な対象組だけに限定する。
  - 候補表とmodeのservice試験で禁止操作が追加されず、既存reconciler・旧state endpoint・mutation受付契約を変更しない。
  - _Boundary: CurrentMenuService_
  - _Depends: 2.1_
  - _Requirements: 3.4, 3.5, 6.6, 6.7, 7.3, 7.4_

- [x] 5. 認証済み閲覧APIを既存公開portへ統合する
  - containerで具象adapterを合成し、UUIDとtimezone-aware revision必須の単発GETをappのURLへ登録する。他appのModelや非公開repositoryを閲覧serviceから参照しない。
  - presenterはallowlistで現在結果をJSON化し、停止情報は既存presenterへ委譲する。停止中checkedAtはnullとし、閲覧応答にconfirmationToken・expiresAtを付与しない。
  - 成功と400/401/403/404/409/503のstatus/body、no-store、画像部分失敗をHTTP試験する。非公開情報、画像・完全URL・内部例外をログや安全なエラーへ出さないことをcanaryで確認する。
  - 新endpointだけを加えた状態で既存APIを利用でき、正常閲覧・競合・認可失効がHTTP契約どおりに返る。
  - _Boundary: B1 HTTP integration — ChannelCurrentAPIView, presenters, serializers, urls, container_
  - _Depends: 4.1, 4.2_
  - _Requirements: 2.1, 2.4, 2.6, 2.7, 3.1, 3.2, 3.7_

- [ ] 6. Frontendの閲覧通信境界を追加する
- [ ] 6.1 現在結果の整合性と部分失敗を検証する
  - path対象・revision・richと停止情報のchannel対応、managed資源と観測の一致、回復対象UUID、tagごとの必須項目を検証する。
  - Base64構文・decoded size・形式・寸法・digestを検証し、画像だけの不正は画像失敗へ縮退する。設定だけの不正は編集不可とし、状態・認可・対応関係の不正は全体protocol errorにする。
  - 保存された完全URLを維持し、未知statusを成功にせず、各種不正fixtureと既存DTO互換試験が通る。
  - _Boundary: richMenuAdminDto_
  - _Depends: 5_
  - _Requirements: 2.3, 2.7, 3.1, 3.2, 3.5_

- [ ] 6.2 単発閲覧と安全なエラー変換を追加する
  - 既存protected HTTP clientからchannelとrevisionを指定して読み取り、read用AbortSignalを伝える。
  - 閲覧限定のcurrent_changed、既存stale_channel、認証失効・利用不能を安全に区別し、既存通知経路へ接続する。
  - HTTP mockで単発GET、DTO検証、失効通知、中止を確認する。自動retryや未配置404を旧画面の成功へ置き換えるfallbackを追加しない。
  - _Boundary: richMenuAdminApi_
  - _Requirements: 2.1, 2.4, 2.7, 3.2, 3.7, 8.3_

- [ ] 7. 閲覧・編集・操作結果を純粋な状態遷移へ分離する
- [ ] 7.1 現在取得と編集・確認の不変条件を実装する
  - current read、editor、catalogを直交した状態にし、更新開始時に旧画像・旧操作可否を破棄する。初期閲覧をcatalog取得に依存させない。
  - 明示編集・作成、版一致、area順の全入力、template変更確認、入力エラー、戻る・キャンセルを扱い、暗黙の別template選択を除去する。
  - 入力変更で旧previewを失効させ、期限切れ・競合では認可がある間だけdraftを保持する。戻るでは確認値を捨て、キャンセルではdraftも捨てる。
  - 遷移試験で現在画像と未適用previewの混同を防ぎ、bytes/Base64をstateに残さず、履歴一覧stateを除去できる。
  - _Boundary: richMenuAdminState_
  - _Depends: 6.1_
  - _Requirements: 1.6, 4.1, 4.2, 4.4, 4.5, 4.6, 4.7, 5.2, 5.5, 5.6, 7.1_

- [ ] 7.2 操作結果・回復・認可失効の遷移を分離する
  - submitting/tracking/settled/transport_unknownをcurrent readから独立させ、確定結果を再取得失敗で消さない。
  - Backendの有効操作と回復対象を状態へ反映し、unknown・停止pending・inactive・再有効化直後の禁止条件を維持する。
  - チャネル変更・離脱・認可失効ではチャネル情報・入力・preview・結果を消去し、古い応答から復元しない。
  - 遷移試験で操作失敗と表示未確認を別々に示せ、受付済み操作の追跡だけをサーバー参照から再開できる。
  - _Boundary: richMenuAdminState_
  - _Requirements: 3.4, 3.6, 3.7, 6.3, 6.4, 6.5, 6.7, 6.8, 7.4, 7.6, 8.1, 8.2, 8.3, 8.4_

- [ ] 8. 通信と画像の寿命をhookへ集約する
- [ ] 8.1 初期取得・更新・現在画像の寿命を実装する
  - Consoleの読み取り副作用をhookへ抽出し、検証済みinitial channelからcurrentだけを取得する。更新はchannel取得後にcurrentを読み、history/state/deactivationを重複取得しない。
  - route/session/read世代とchannelを成功・失敗の双方で照合し、readを中止できるようにする。競合後の自動ループを行わない。
  - 採用後だけBlob/Object URLを生成し、現在画像の差替え・更新開始・認可失効・channel変更・unmountでrevokeする。decode失敗は画像だけの失敗とし認可失効を上書きしない。
  - hook試験で旧応答が採用されず、現在画像URLを一つだけ所有し、履歴呼出や永続保存なしで閲覧・更新できる。
  - _Boundary: useRichMenuAdmin（抽出時のConsoleも単独所有）_
  - _Depends: 6.2, 7.2_
  - _Requirements: 1.6, 3.1, 3.7, 7.1, 7.2, 8.1, 8.2, 8.3, 8.5_

- [ ] 8.2 編集開始・preview生成・期限を接続する
  - 明示編集・作成時だけcatalogを取得し、同一ID・版の全fieldsを初期化する。版不明時は理由を示し、明示した作り直しだけ空入力を開始する。
  - editor世代で入力変更・キャンセル後のpreviewを破棄し、field errorではdraftを保持する。失効時はcurrent更新後に同じ入力で再生成できるようにする。
  - previewのURLを現在画像と分けて一つだけ所有し、戻る・キャンセル・期限切れ・認可失効・離脱で確認値とともに破棄する。
  - タイマーと遅延応答のhook試験で期限後の適用を防ぎ、入力保持と完全破棄の各条件を観測できる。
  - _Boundary: useRichMenuAdmin_
  - _Requirements: 4.1, 4.2, 4.4, 4.5, 4.6, 4.7, 5.5, 5.6, 8.1, 8.2, 8.3, 8.5_

- [ ] 8.3 既存操作の一度だけの受付と同一操作追跡を接続する
  - 有効な確認値・draft・revisionと新規operation IDを一度だけ渡し、受付前からlatchで適用・解除・回復の重複を防ぐ。mutationへreadのAbortSignalを渡さない。
  - 通信切断を結果不明にし、保持中IDをGETで追跡する。再訪ではcurrentのactive/blocking/latest参照を使い、POSTを自動再送しない。
  - recheckは同一subject、cleanupは選択された有効な一組、管理終了は許可された現在管理資源だけを渡し、開始直前にもstateと対象を照合する。
  - 操作確定後は結果を保持してchannel/currentを再取得する。hook試験で二重送信ゼロ、対象逸脱ゼロ、再取得失敗時の結果保持を確認する。
  - _Boundary: useRichMenuAdmin_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 8.4_

- [ ] 8.4 チャネル停止・回復・再有効化・削除を接続する
  - 既存チャネルAPIへ停止・停止再確認・再有効化・削除を接続し、停止pendingではそのintentに認められた回復だけを扱う。
  - inactiveでは保存情報を読取専用とし、rich-menuの再確認・cleanupを含む変更を開始しない。再有効化後はdraftを復元せずcurrent取得完了まで変更を禁止する。
  - 削除確認にだけ履歴有無を渡し、既存参照による削除阻止と失効・削除後の通知を維持する。
  - hook試験で停止回復から再有効化までの禁止状態と最新取得順序を確認できる。
  - _Boundary: useRichMenuAdmin_
  - _Requirements: 3.6, 3.7, 6.7, 7.3, 7.4, 7.5, 7.6_

- [ ] 9. 閲覧・編集・確認の表示部品を整える
- [ ] 9.1 現在画像と状態別の代替表示を作る
  - 現在画像は実寸情報と元比率で全体表示し、loading、画像失敗と再取得、確認済み未設定、外部、attention、停止中を区別する。
  - 編集・確認中の現在メニューは展開可能な折りたたみにし、未適用画像と別のチャネル名付きaltを持たせる。
  - 表示試験で過去画像の代用、切抜き、引き伸ばしがなく、画像失敗だけでは許可操作を失わない。
  - _Boundary: RichMenuCurrentMenu_
  - _Depends: 8.1_
  - _Requirements: 1.2, 1.5, 1.6, 3.1, 3.3, 3.6, 4.3, 5.2, 9.4_

- [ ] 9.2 短い状態と禁止理由だけを表示する
  - 既存labelを共用し、通常の保存状態・提供モード・操作状態の一覧と内部状態名を外す。
  - 全体取得失敗、外部変更、結果不明、停止などの理由とBackendが許可する次の操作を必要時だけ示す。
  - 状態表示試験でloading・成功・失敗・要確認を文字とsemantic roleで識別でき、unknownを成功や未設定と表示しない。
  - _Boundary: RichMenuStatePanel_
  - _Requirements: 1.3, 3.2, 3.4, 3.6, 6.7, 9.1, 9.4_

- [ ] 9.3 明示draftによる編集フォームを作る
  - 同一template ID・版とarea順で全入力を表示し、選択肢の値でもID・版を区別する。編集不可理由と明示的な作り直しを表示する。
  - template変更時の入力消去確認、取消時の保持、field errorとlabelの関連、未適用表示、previewとキャンセル操作を接続する。
  - 表示試験で編集時に別templateへfallbackせず、作成時だけ空入力を開始し、長い表示名・完全URLを省略せず扱える。
  - _Boundary: RichMenuEditor_
  - _Depends: 8.2_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 9.3, 9.4_

- [ ] 9.4 未適用の確認内容と明示リンクテストを表示する
  - 対象チャネル、template名・版、全画像、領域番号と全項目・完全URL、未適用を折りたたまず示す。
  - 有効期限日時と残り時間、既定の置換影響、外部資源を編集・削除しないこと、利用者別優先、端末表示非保証を表示する。
  - 編集へ戻る・失効後の再生成へ接続し、リンクテストは選択した保存値だけをnoopener,noreferrerで開く。
  - 表示試験で全文・期限・影響を確認でき、hover・描画・preview生成によるリンク先への自動接続を行わない。
  - _Boundary: RichMenuPreview_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 9.3, 9.4_

- [ ] 9.5 適用操作と直前結果を簡潔に表示する
  - 有効な確認中だけ「LINEに反映」を接続し、処理中は短い表示と競合操作の無効化を行う。
  - 確定結果、結果不明、失敗、現在表示の再取得失敗を別々に表示し、操作IDを常設しない。
  - 表示試験で再取得に失敗しても直前結果が残り、previewを現在画像へ流用せず同一操作の確認へ進める。
  - _Boundary: RichMenuOperationPanel_
  - _Depends: 8.3_
  - _Requirements: 1.3, 6.1, 6.3, 6.4, 6.5, 6.8, 9.1, 9.4_

- [ ] 9.6 解除・管理終了・回復の確認を必要時に表示する
  - 解除は対象、既定解除と管理資源削除、外部表示再開とcleanupの可能性をインライン確認してから実行へ渡す。
  - 管理終了は有効操作に含まれる場合だけ表示し、通常適用中は補助の折りたたみ、外部変更時は要確認案内に配置する。現在既定と資源を変更・削除しないこと、追跡終了後の管理・削除不能と自動復帰なし、履歴保持を確認する。
  - 結果不明は同一subject再確認、cleanupは有効な対象組の選択だけを表示する。関係不明なら安全な案内を残し、任意資源を選べるようにしない。
  - 表示試験で対象・確認取消・許可操作を検証し、禁止mode・停止中・不正関係では操作を提示しない。
  - _Boundary: RichMenuRecoveryPanel_
  - _Depends: 8.3, 8.4_
  - _Requirements: 6.2, 6.5, 6.6, 6.7, 7.4, 9.4_

- [ ] 10. 詳細画面を統合し既存操作との契約を確認する
- [ ] 10.1 閲覧中心の画面構成と履歴非依存の取得へ切り替える
  - Pageから検証済みchannelをhookへ渡し、戻る導線、チャネル名と短い状態、現在メニュー、主操作・補助操作、停止・削除の順にConsoleへ合成する。
  - 通常は編集を主操作、解除・更新を補助操作とし、フォームと全設定は明示開始後だけ表示する。履歴一覧・件数・ページングと取得依存をConsoleから外し、既存履歴部品・DTO・APIは保持する。
  - 共通theme tokenとPageFrameで390pxの縦配置・1440pxの読みやすい幅、全画像・長文折返しを整える。編集・確認・取消のfocusと通常更新時のfocus保持を接続する。
  - API fixtureとDTOを照合した統合試験で二重初期取得がなく、履歴APIを失敗させても表示・更新・編集が使える。API未配置404を成功扱いしない。
  - _Boundary: B2/B3 integration — RichMenuAdminPage, RichMenuAdminConsole, 表示Component, style.css_
  - _Depends: 5, 8.4, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - _Requirements: 1.1, 1.3, 1.4, 4.3, 7.1, 7.2, 9.1, 9.2, 9.3, 9.4_

- [ ] 10.2 閲覧から編集・確認・適用・解除の通常経路を検証する
  - HTTP mock統合で既存適用済み設定の編集、未設定から作成、版廃止から明示作り直し、template変更の承認・取消を通す。
  - preview、戻る、入力変更、期限切れ後の再生成、キャンセル、適用連打を確認し、キャンセルではLINE変更なし、適用は一度だけであることを観測する。
  - 解除確認と操作後のchannel/current更新を通し、現在画像は再取得結果だけから表示する。解除後の外部既定と確定結果＋再取得失敗も検証する。
  - 初期表示・更新・操作後の履歴呼出がすべて0で、全経路を履歴API利用不能でも完了できる。
  - _Boundary: B3 UI integration — RichMenuAdminConsole, Editor, Preview, OperationPanel_
  - _Requirements: 1.5, 2.5, 4.1, 4.2, 4.4, 4.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 6.1, 6.2, 6.3, 6.4, 6.8, 7.1, 7.2_

- [ ] 10.3 回復対象とチャネルライフサイクルを通して検証する
  - currentのactive/blocking/latest参照から同一operationを取得し、unknownの明示再確認、置換関係のある対象だけのcleanupをHTTP mockとfake gatewayで確認する。
  - 関係不明の旧資源、他subjectの候補、停止pendingの禁止操作を拒否し、停止再確認と許可されたcleanupだけを接続する。
  - 停止中の読取専用、再有効化後の取得完了待ち、履歴消失を含む物理削除確認と参照による阻止を検証する。
  - 履歴APIなしで回復・削除確認が継続でき、再取得失敗でも確定結果を保持し、新規POSTの自動再送や過去draftの復元がない。
  - _Boundary: B3 lifecycle integration — useRichMenuAdmin, Console, RecoveryPanel, 既存channel/operation API_
  - _Depends: 8.4, 9.6, 10.1_
  - _Requirements: 3.4, 3.6, 6.5, 6.6, 6.7, 6.8, 7.2, 7.3, 7.4, 7.5, 7.6, 8.4_

- [ ] 10.4 管理終了の候補・確認・既存実行・再取得を通して検証する
  - 通常適用中、外部既定、既定なし、別管理資源への変更で候補と対象を検証する。画像失敗・設定不明・版廃止でも所有関係が正しければ候補を維持する。
  - currentなし、不正所有関係、unknown、active/blocking、cleanup、停止intent、inactiveと各提供modeで禁止条件を検証する。
  - Backendのservice/APIとFrontendのConsole/RecoveryPanelで既存releaseへ現在管理資源を渡し、確認後に追跡終了・履歴保持・再取得へ到達することを確認する。外部IDやcleanup資源への代用を拒否する。
  - LINE変更・削除呼出0、管理終了済み資源の自動再取込・後続cleanup・削除なしを検証する。表示後にrevision・対象・操作・停止intentを変えた場合は既存mutationが拒否する。
  - _Boundary: B3 release integration — CurrentMenuService, ChannelCurrentAPIView, Console, RecoveryPanel, 既存release_
  - _Depends: 4.2, 5, 8.3, 9.6, 10.1_
  - _Requirements: 3.4, 6.4, 6.7, 6.8, 7.3_

- [ ] 11. 競合・性能・画面寿命と回帰を検証する
- [ ] 11.1 外部取得中の競合と認可失効をMySQLで検証する
  - downloadを停止させ、session・owner資格・provider・channel revision・current・operation・停止intentを別transactionから変更する。
  - 初回と最終検証間の行作成・削除、作成元設定変更、cleanup関係変更、既定変化・digest不一致を試験し、画像・設定が応答へ残らないことを確認する。
  - MySQLの並行transactionで既存lock順序と外部I/O中のtransactionなしを確認する。SQLiteの結果だけでlock保証済みとしない。
  - 401/403/404/409と全体失敗を画像だけの失敗へ縮めず、安全な応答と再取得へ収束する。
  - _Boundary: B3 Backend concurrency integration — CurrentMenuService, repository, owner/channel/deactivation ports_
  - _Depends: 5_
  - _Requirements: 2.1, 2.4, 3.2, 3.4, 3.7, 8.2_

- [ ] 11.2 閲覧全体のquery・外部呼出・応答サイズを計測する
  - service全体32 SQL以下を認可・channel・停止情報・最終再検証・観測記録込みで計測する。制御SQLのみ除外し、HTTP認証層は別測定する。
  - 履歴10件/1000件、旧資源1件/50件でquery数が増えず、履歴・transitionの全件走査をしないことを確認する。
  - 通常managedはdefault最大2回＋image1回、画像対象なしはdefault1回、inactiveは0回、list/get-resource/mutationは0回を確認する。既定照会の5秒timeoutと画像全体5秒deadlineを超えた再送がない。
  - 最大画像のBase64 1,398,104文字とstate・設定・回復情報込みの応答サイズを実測し、画像の二重保持や秘密を含む計測ログを残さない。
  - _Boundary: B3 performance integration — CurrentMenuService, repository, gateway, HTTP response_
  - _Depends: 5, 11.1_
  - _Requirements: 2.2, 2.6, 2.7, 3.6, 7.1, 7.2, 7.3_

- [ ] 11.3 離脱・認証失効・後着応答と画像破棄を検証する
  - 成功・失敗の遅延応答を使い、channel変更、離脱、編集取消、入力変更、read置換、認証失効後に旧画像・設定・preview・操作可否が復活しないことを確認する。
  - 更新・差替え・戻る・取消・期限切れ・unmount・全連携解除・owner利用不能で、対応するObject URLがrevokeされ、decode失敗が失効表示を上書きしないことを確認する。
  - Pageとrouteの統合で確認なし離脱、read中止、mutation非中止、再訪・再認証後の同一操作GETとPOST再送なしを確認する。
  - localStorage/sessionStorage/ページURLへdraft・完全URL・画像・確認値を保存せず、再読み込み後にも復元しない。
  - _Boundary: B3 lifetime integration — State, useRichMenuAdmin, RichMenuAdminPage, AppScreenSeparationIntegration_
  - _Depends: 8.1, 8.2, 8.3, 10.1_
  - _Requirements: 3.1, 3.7, 4.7, 5.5, 5.6, 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 11.4 配色・幅・キーボード操作をブラウザで検証する
  - 390px/1440px×light/darkで閲覧・編集・確認・回復・停止を開き、compact/large fixtureの全画像と全操作が横スクロールなしで利用できることを確認する。
  - 長いチャネル名・日本語表示名・完全URLを省略せず参照でき、狭幅で補助操作が縦に並ぶことを確認する。新規6項目templateやデモ画像は追加しない。
  - 戻る・折りたたみ・編集・確認・取消・回復へのkeyboard到達、focus-visible、編集/確認へのfocus移動、取消後の復帰、更新時のfocus保持を検証する。
  - labelとfield error、現在/未適用の別alt、status/alertによる通知が機能する。実LINE mutationを成功条件にせず既存HTTP mock・表示fixtureを使う。
  - _Boundary: B3 browser integration — 全表示Component, style.css, 既存shell_
  - _Depends: 10.2, 10.3, 10.4, 11.3_
  - _Requirements: 1.2, 9.1, 9.2, 9.3, 9.4_

- [ ] 11.5 既存契約の回帰とサービス全体の品質検査を完了する
  - 対象Django test runnerで既存history記録・参照API・purge、管理終了のLINE外部変更0、preview・適用・解除・停止の回帰を実行する。
  - Frontend Vitestで新旧DTO、履歴部品の保持、画面統合を検証する。Backend endpoint追加後にFrontendを切り替える順序と、旧Frontend・旧API・既存データの互換性を確認する。
  - 両サービスのcheck scriptとFrontend production buildを実行し、関連する失敗を修正して完了する。実LINEへのmutationや新しいE2E基盤は不要。
  - テストの日本語「テストケース」「期待値」コメント規約を満たす。未実施・失敗を合格扱いせず、全必須検証の結果を実装完了判定へ反映する。
  - _Boundary: B3 regression integration — Backend/Frontend tests, 既存runtime・品質検査_
  - _Depends: 10.2, 10.3, 10.4, 11.1, 11.2, 11.3, 11.4_
  - _Requirements: 2.5, 2.6, 2.7, 6.4, 6.7, 7.1, 7.2, 7.3, 7.5_

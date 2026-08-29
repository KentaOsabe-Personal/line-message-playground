# 要件文書

## はじめに

本Specは、LINE Message Playgroundのowner向けFrontendを、トップ画面と独立URLを持つ機能画面へ分割する。ownerは共通ナビゲーション、直接アクセス、再読み込み、ブラウザ履歴を利用して目的の機能へ移動でき、現在表示している機能だけのデータを読み込める。既存のowner認証、アカウント連携、チャネル管理、リッチメニュー管理、メッセージ配信の業務・安全性契約は、画面離脱時の破棄確認について本Specが明示する変更を除き維持する。

## 境界コンテキスト

- **対象内**: `/liff`トップ、チャネル管理、アカウント管理、リッチメニューのチャネル選択とチャネル別管理、メッセージ配信、404、共通ナビゲーション、認証状態に応じた画面制御、画面単位のデータ読込み、ページタイトル、フォーカス、responsive表示、accessibility、全画面の視覚的統一
- **対象外**: 新しい業務機能、Backendの業務ロジック、API schema、新規API endpoint、Databaseまたはmigration、Mobile-first設計、LINEアプリ内browserの正式保証、WCAG認証・第三者監査、新しいE2Eまたはvisual regressionの仕組み
- **隣接する期待**: 完了済みの`line-account-linking`、`line-channel-admin-ui`、`line-rich-menu-admin-lifecycle`、`linked-recipient-delivery`が提供する確認、競合制御、二重実行防止、結果不明、回復、秘密情報非露出の契約を利用し、変更しない

## 要件

### Requirement 1: URLとブラウザ遷移

**目的:** ownerとして、各機能を固有のURLで開きたい。それにより、目的の画面へ直接移動し、ブラウザの標準操作を利用できる。

#### 受入条件

1. When ownerが`/`へアクセスする, the LINE Message Playground shall 履歴を置換して`/liff`へ移動する
2. The LINE Message Playground shall トップを`/liff`、チャネル管理を`/liff/channels`、アカウント管理を`/liff/account`、リッチメニューのチャネル選択を`/liff/rich-menus`、チャネル別管理を`/liff/rich-menus/{channelId}`、メッセージ配信を`/liff/deliveries`で提供する
3. When ownerが定義済みURLを直接開く、再読み込みする、またはブラウザの戻る・進むを使う, the LINE Message Playground shall URLに対応する画面を表示する
4. If `/`以外の未定義URLが要求される, the LINE Message Playground shall 404画面と`/liff`へ戻る導線を表示し、自動的にトップへ移動しない
5. If `{channelId}`が存在しない、形式が不正、またはownerが利用できない, the LINE Message Playground shall いずれも同じ「対象が見つからない」表示とチャネル選択画面への導線を示し、存在または権限を推測できる情報を示さない

### Requirement 2: 認証と安全な画面復帰

**目的:** ownerとして、認証状態が変化しても安全な画面へ収束したい。それにより、保護情報や処理を誤って露出・再実行せず作業を再開できる。

#### 受入条件

1. When 未認証のownerが定義済みの保護対象URLへアクセスする, the LINE Message Playground shall そのURLでログインを要求し、認証成功後に要求時の画面を表示する
2. If 認証後の復帰先が定義済み内部URLではない, the LINE Message Playground shall 外部URLまたは任意URLへ移動せず`/liff`を表示する
3. When 表示中にowner sessionが失効する, the LINE Message Playground shall 現在の許可済みURLを維持し、保護対象内容を直ちに非表示にしてログインを要求する
4. When session失効後にownerが再認証する, the LINE Message Playground shall 同じ許可済みURLへ復帰し、最新の保存状態を取得する
5. If session失効前に送信、更新、削除、または外部状態変更が開始されている, the LINE Message Playground shall 再認証を理由にその操作を自動再実行しない
6. While ownerの全連携解除が未完了である, the LINE Message Playground shall どの画面URLからも`/liff/account`へ移動し、回復画面だけを表示して4機能の共通ナビゲーションを表示しない
7. When ownerの全連携解除が完了する, the LINE Message Playground shall 未認証状態へ移行し、再利用時にログインを要求する
8. When ownerが明示的にログアウトする, the LINE Message Playground shall 履歴を置換して`/liff`へ移動し、未保存入力、プレビュー、復帰先、保持中の操作ID、owner別一時情報を消去する
9. When ログアウト後にownerが再認証する, the LINE Message Playground shall 以前の機能画面へ自動復帰せずトップ画面から開始する

### Requirement 3: 共通レイアウトとトップ画面

**目的:** ownerとして、どの通常画面からも現在地を把握して各機能へ移動したい。それにより、目的の操作を見つけやすくなる。

#### 受入条件

1. While 認証済みownerが通常画面を利用している, the LINE Message Playground shall アプリ名、4機能へのナビゲーション、owner表示名、ログアウト操作を持つ共通ヘッダーを表示する
2. The LINE Message Playground shall 共通ナビゲーションとトップ画面の機能カードを、チャネル管理、アカウント管理、リッチメニュー管理、メッセージ配信の順で表示する
3. When ownerが機能画面を表示する, the LINE Message Playground shall 現在の機能を視覚的にも支援技術からも識別可能にする
4. When ownerが共通ナビゲーションを操作する, the LINE Message Playground shall トップ画面を経由せず選択した機能画面へ移動する
5. The LINE Message Playground shall 各画面に可視の`h1`を一つだけ表示し、その見出しを画面名だけで構成する
6. When ownerがトップ画面を表示する, the LINE Message Playground shall 4機能へのカードを表示し、各カード全体を画面遷移リンクとして操作可能にする
7. While トップ画面を表示している, the LINE Message Playground shall 機能データ、機能状態、個別管理操作、配信操作、機能説明文を表示または取得しない

### Requirement 4: チャネル管理画面の責務

**目的:** ownerとして、チャネル管理だけに集中できる画面を利用したい。それにより、他機能と混在せずチャネルの設定と状態を管理できる。

#### 受入条件

1. When ownerがチャネル管理画面を表示する, the LINE Message Playground shall 登録済みチャネル一覧、新規登録、表示情報と識別情報の編集、資格情報の設定・更新、接続確認、有効化、無効化、許可された回復、物理削除を提供する
2. When ownerが対象チャネルのリッチメニュー管理を選ぶ, the LINE Message Playground shall `/liff/rich-menus/{channelId}`へ直接移動する
3. While チャネル管理画面を表示している, the LINE Message Playground shall リッチメニューの編集・適用・解除・履歴、配信先管理、またはメッセージ配信を同画面へ表示しない
4. When ownerが資格情報を入力して操作を完了または中止する, the LINE Message Playground shall 既存のwrite-only表示、明示確認、競合拒否、二重操作防止、結果不明時の再確認、入力消去の契約を維持する

### Requirement 5: アカウント管理画面の責務

**目的:** ownerとして、自分とチャネル別配信先の連携だけを管理したい。それにより、認証・連携状態と回復操作を一つの画面で扱える。

#### 受入条件

1. When ownerがアカウント管理画面を表示する, the LINE Message Playground shall ownerのLINE連携状態、チャネル別配信先登録、配信先の有効化・無効化、チャネル単位の連携解除、全連携解除を提供する
2. While アカウント管理画面を表示している, the LINE Message Playground shall チャネル資格情報管理、リッチメニュー管理、メッセージ配信、または画面固有のログアウト操作を表示しない
3. When ownerが全連携解除またはその回復を操作する, the LINE Message Playground shall 既存の再認証、確認、競合処理、許可された回復、秘密情報非露出の契約を維持する

### Requirement 6: リッチメニュー管理画面の責務

**目的:** ownerとして、対象チャネルを明示してリッチメニューの状態と操作を扱いたい。それにより、チャネルの利用可否に応じた安全な管理または参照ができる。

#### 受入条件

1. When ownerが`/liff/rich-menus`を表示する, the LINE Message Playground shall 利用不可のものを含む全登録済みチャネルと、その状態および利用できない理由を表示する
2. While チャネルが有効かつprovider ID設定済みである, the LINE Message Playground shall そのチャネルの通常のリッチメニュー管理画面へ移動できるようにする
3. While チャネルが無効かつprovider ID設定済みである, the LINE Message Playground shall そのチャネルの実状態と適用履歴を読み取り専用で確認できるようにする
4. If チャネルにprovider IDが設定されていない, the LINE Message Playground shall 選択不可の理由とチャネル管理画面への導線を表示し、管理操作を表示しない
5. While 無効化など競合するライフサイクル処理が進行中である, the LINE Message Playground shall 現在状態と保存状態が許可する回復操作だけを表示する
6. If 登録済みチャネルが0件である, the LINE Message Playground shall 空状態とチャネル管理画面への導線を表示し、新規登録フォームを重複して表示しない
7. When ownerがチャネルを選択する, the LINE Message Playground shall `/liff/rich-menus/{channelId}`を表示し、チャネル選択画面へ戻る導線を常に表示する
8. When ownerがチャネル選択画面からチャネル別管理画面へ移動する, the LINE Message Playground shall ブラウザの戻る操作でチャネル選択画面へ戻れるようにする
9. When ownerがリッチメニューの変更、確認、回復、または管理終了を操作する, the LINE Message Playground shall 既存の期限付きプレビュー、所有権確認、revision競合、二重操作防止、結果不明時の同一操作再確認、read-only制約を維持する

### Requirement 7: メッセージ配信画面の責務

**目的:** ownerとして、既存の安全なテスト配信を独立画面で実行したい。それにより、確認済みの単一対象へ重複なく配信し結果を追跡できる。

#### 受入条件

1. When ownerがメッセージ配信画面を表示する, the LINE Message Playground shall 配信元チャネル1件、登録済み配信先1件、件名、本文、受取確認の選択または入力を提供する
2. When ownerが配信を実行する, the LINE Message Playground shall 送信前プレビュー、確認済み内容の送信、現在操作の処理状態と結果、完了後の新しい配信開始を既存契約どおり提供する
3. If 配信結果が不明である, the LINE Message Playground shall 新規配信として自動再実行せず、同じ操作IDの状態再確認だけを提供する
4. The LINE Message Playground shall 共通ナビゲーションとトップカードでは「メッセージ配信」、画面見出しでは「LINEテスト配信」と表示する
5. While メッセージ配信画面を表示している, the LINE Message Playground shall 一括配信、配信予約、配信履歴一覧、配信テンプレート、または配信分析を提供しない

### Requirement 8: 画面離脱と受付済み処理

**目的:** ownerとして、画面遷移時に入力と受付済み処理がどう扱われるか予測したい。それにより、意図しない復元、重複実行、処理中断を避けられる。

#### 受入条件

1. When ownerが共通ナビゲーション、トップへの移動、画面内リンク、戻る・進む、再読み込み、またはタブを閉じる操作で画面を離れる, the LINE Message Playground shall 破棄確認を表示せず未保存入力とプレビューを破棄し、遷移後に復元しない
2. When ownerが同一画面内で削除、送信、外部状態変更、または入力消去を伴う既存操作を行う, the LINE Message Playground shall その操作に対する既存の確認を維持する
3. While Backendが送信、適用、更新などを受付済みである, the LINE Message Playground shall 処理中の確認なしで別画面へ移動できるようにし、画面移動を理由に処理を中断または自動再送しない
4. When ownerが受付済み処理の元画面へ戻る, the LINE Message Playground shall 保存済みの最新状態と追跡可能な進行中操作を再取得する
5. Where メッセージ配信の受付済み操作を追跡する, the LINE Message Playground shall 不透明な操作IDだけを同一browser session中に保持できる
6. The LINE Message Playground shall 入力本文、チャネル資格情報、リッチメニュー入力、プレビュー内容、LINE user IDを遷移後の復元目的でURLまたはbrowserの永続領域へ保存しない

### Requirement 9: 画面単位のデータ読込みと状態表示

**目的:** ownerとして、現在の画面に必要なデータと状態だけを確認したい。それにより、無関係な機能の読込みや後着応答による誤表示を避けられる。

#### 受入条件

1. When ownerが機能画面を表示する, the LINE Message Playground shall 現在のURLに対応する機能だけを実行状態にし、その機能に必要なデータだけを取得する
2. While トップ画面を表示している, the LINE Message Playground shall 認証確認以外の機能データを取得しない
3. When ownerがデータ取得中に別画面へ移動する, the LINE Message Playground shall 可能な取得を中止し、中止できない取得の後着結果を移動先の状態へ反映しない
4. When ownerが機能画面を再訪する, the LINE Message Playground shall 保存済みの最新状態を取得する
5. While 読込み中または取得エラーが発生している, the LINE Message Playground shall 共通ヘッダーとページ見出しを維持し、ページ内に統一された読込み状態またはエラー状態を表示する
6. If 安全に再試行できる取得処理が失敗する, the LINE Message Playground shall 再試行操作を表示し、更新、配信、適用などの外部作用を再試行対象にしない
7. When 読込み、成功、失敗、または結果不明の状態が変化する, the LINE Message Playground shall 色だけに依存せず状態を表示し、支援技術へ通知する

### Requirement 10: ページタイトルとフォーカス

**目的:** ownerとして、browserと支援技術から現在の画面を識別したい。それにより、画面遷移後の位置と状態を把握できる。

#### 受入条件

1. The LINE Message Playground shall トップのタイトルを`LINE Message Playground`、チャネル管理を`チャネル管理 | LINE Message Playground`、アカウント管理を`アカウント管理 | LINE Message Playground`、リッチメニュー選択を`リッチメニュー管理 | LINE Message Playground`、メッセージ配信を`LINEテスト配信 | LINE Message Playground`、404を`ページが見つかりません | LINE Message Playground`とする
2. When チャネル別リッチメニュー管理画面を表示する, the LINE Message Playground shall ページタイトルを`{チャネル名} | リッチメニュー管理`とする
3. When 画面遷移リンクによってURLに対応する画面が変わる, the LINE Message Playground shall 移動先の`h1`へフォーカスを移す
4. When 同一画面内でデータを再取得する, the LINE Message Playground shall `h1`へフォーカスを移動しない
5. When 入力中にエラーまたは状態変化が発生する, the LINE Message Playground shall 支援技術へ通知し、現在の入力フォーカスを奪わない

### Requirement 11: 統一UIとresponsive表示

**目的:** ownerとして、全画面で一貫した表示と操作を利用したい。それにより、状態や操作の意味を画面ごとに学び直さず利用できる。

#### 受入条件

1. The LINE Message Playground shall 白、淡いグリーン、濃い文字色を基調とし、薄いグレーグリーンのページ背景、白いカード、細い境界線、控えめな影、主要操作とフォーカスのLINEグリーン、危険操作に限定した赤を全画面で一貫して使用する
2. The LINE Message Playground shall 文字、入力欄、ボタン、リンク、余白、角丸、状態表示を全画面で統一し、派手なgradient、強い影、または不要なanimationを使用しない
3. While 広い画面で表示している, the LINE Message Playground shall 共通ナビゲーションを横並び、トップ画面の機能カードを2列で表示する
4. While 狭い画面で表示している, the LINE Message Playground shall 機能ナビゲーション、現在状態、owner表示、ログアウトを折り畳みメニューへ収め、トップ画面の機能カードを1列で表示する
5. The LINE Message Playground shall 対応画面幅でページ全体に不要な横scrollを発生させない
6. The LINE Message Playground shall keyboardだけですべての操作を実行可能にし、keyboard trapを発生させず、可視で隠れないfocus表示を提供する
7. The LINE Message Playground shall 通常文字に4.5:1以上、大きな文字とUI境界に3:1以上のcontrastを提供し、状態を色だけで表現しない
8. The LINE Message Playground shall WCAG 2.2が認める例外を除き、pointer targetを24×24 CSS px以上とし、適切なlabel、heading、landmark、現在状態、status通知を提供する

### Requirement 12: 対応環境と既存契約の維持

**目的:** ownerおよび保守者として、既存の安全性と明示された対応範囲を維持したまま画面分割を利用したい。それにより、画面構造の変更を理由とする業務回帰や秘密露出を避けられる。

#### 受入条件

1. The LINE Message Playground shall Google Chrome 111以降、Chromium版Microsoft Edge 111相当以降、Safari 16.4以降、Firefox 128以降を正式なbrowser対応範囲とする
2. Where SmartphoneまたはLINEアプリ内browserから利用する, the LINE Message Playground shall best effortで表示し、正式な受入保証の対象にしない
3. When 既存のアカウント、チャネル、リッチメニュー、または配信操作を各独立画面から利用する, the LINE Message Playground shall 本Specが明示した画面分割と離脱時確認の変更を除き、既存の入力、表示、確認、競合、回復、二重実行防止、結果不明の契約を維持する
4. The LINE Message Playground shall Backendの業務ロジック、既存データ契約、永続データ構造、または外部サービスとの業務契約を変更せずに本Specの画面挙動を提供する
5. If 既存の公開契約だけでは本Specの画面挙動を満たせないことが判明する, the LINE Message Playground shall 暗黙に契約を拡張せず、未達の要件として保守者へ明示する
6. The LINE Message Playground shall 資格情報、入力本文、プレビュー、LINE user IDをURL、通常ログ、安全なエラー表示、またはownerを越えて共有される一時情報へ追加しない

## Specサイズ評価

- **判定**: PASS (single-spec)
- **予測実行可能タスク数**: 34〜39件
- **独立した責任境界**: 5（URL／認証制御、共通UI、既存機能画面分離、リッチメニュー画面、統一UI／検証）
- **依存順**: URL／認証制御 → 共通UI → 既存機能画面分離とリッチメニュー画面 → 統一UIと横断検証
- **理由**: 30〜39件のreview attention帯だが、すべてが既存Frontendを独立URLへ分割する一つのowner成果へ収束する。新しいBackend状態機械、migration、外部workflow、独立rolloutを持たず、要件の境界と依存順が一つのbounded reviewで収束したため、単一Specとして継続する

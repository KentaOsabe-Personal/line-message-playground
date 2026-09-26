# 調査・設計判断記録

## 概要

- **機能:** `text-judgment-lab`
- **調査日:** 2026年9月20日
- **調査範囲:** 既存システムへの新機能追加と複合外部連携。初回はFull discoveryを実施し、今回の設計修正では既存HTTP境界、失敗回復、公開DTOに絞ったlight discoveryを実施した。コード調査のみ独立したサブエージェントへ委譲し、統合判断は主担当が行った。
- **前提:** `spec.json`の要件承認は確認済み。承認済み`requirements.md`を正とし、初期案に残るAndroid対応や過去の会話例を採用しない。
- **主要知見:** 管理認証はプロフィールと永続owner sessionに依存する。ラボは独立したBearer本人確認を必要とする。Jevは同じ文脈への独立質問をまとめて評価できる。判定の構造検証と会話遷移は分け、部分結果は採用しない。会話をブラウザメモリだけに置けば、DB移行と履歴管理は不要となる。

## 調査記録

### 既存コードとの接続

- **確認元:** `frontend/src/App.tsx`、`AuthGate.tsx`、`appRoutes.ts`、`liffConfig.ts`、`liffClient.ts`、`httpApi.ts`、`PageFrame.tsx`、`backend/config/settings.py`、`backend/config/urls.py`、`backend/lineaccounts/gateway.py`、`views.py`、`runtime.py`、`compose.yaml`。
- `/liff`はowner認証済みshellである。ラボはReact routeとしてowner shellの外側に置きつつ、LIFF SDKのEndpoint URL制約に合わせてURLを`/liff/labs/text-judgment`とする。管理APIや復帰先許可リストへラボの意味を持ち込まない。
- DRFの既定認証・権限とglobal exception handlerはowner専用である。既存handlerは`{error: {code, summary}}`とowner用codeを返すため、ラボの全Viewは専用認証・権限に加え、認証・権限処理中の例外も`{error: {code, message}}`へ変換する専用`LabAPIView`境界を必要とする。
- 既存LINE gatewayは`name`を必須とする。`openid`だけで成立するラボの契約にはそのまま適用できない。
- `liffClient.ts`の既存adapterはID token取得を提供し、再利用できる。SDK singletonのため、異なるLIFF ID間の移動は文書全体のnavigationで行う。
- `httpApi.ts`はcookieとCSRFを必須とし、Authorization headerを受け取らない。ラボBearer通信に合わせて汎用化せず、専用の小さいHTTP adapterを設ける。
- テストはVitest/jsdom、Django/DRFを使用する。新しいE2E基盤は導入しない。

### LINEミニアプリ本人確認

- **確認元:** [Consoleガイド](https://developers.line.biz/ja/docs/line-mini-app/discover/console-guide/)、[認可フロー](https://developers.line.biz/en/docs/line-mini-app/develop/channel-consent-simplification/)、[ID token検証](https://developers.line.biz/en/reference/line-login/#verify-id-token)、[LIFF API](https://developers.line.biz/en/reference/liff/#get-id-token)。
- 開発・審査・本番の内部チャネルは別のLIFF IDを持つ。開発用LIFF IDと、その開発用内部チャネルIDを組として設定する。
- サーバーはLINEの検証APIに`id_token`と設定済み`client_id`を送信し、検証後の`iss`、`aud`、`exp`、`sub`を確認する。表示名、画像、emailは不要である。
- 認証は毎回の保護APIで再検証する。ブラウザは検証済み有効期限も保持し、通信しない選択肢操作を期限到達時に止める。
- LINE上の権限付与、実際のIDの組、本人識別用digest、iPhoneでの再認証は未確認。これらの外部確認はユーザーが任意で行い、実装の完成条件に含めない。公開範囲の拡張や管理権限の変更ではない。

### Jevの外部契約

- **確認元:** [HTTP API](https://docs.typesafe.ai/api)、[Choice](https://docs.typesafe.ai/primitives/choice)、[Score](https://docs.typesafe.ai/primitives/score)、[Noul](https://docs.typesafe.ai/primitives/noul)、[Models](https://docs.typesafe.ai/models)。
- HTTPXで`POST https://api.typesafe.ai/v1/systemone`へ接続し、APIキーをBearer headerへ置く。`state`、`model`、`questions`を送り、`answers`を受け取る。
- Choiceは候補名、候補別確率、confidenceを持つ。Scoreは順序付きの説明配列をcriteriaとし、点数、legend、段階別確率、confidenceを返す。Noulは0〜1の値で、別のconfidenceはない。
- Scoreは0〜2の小数を取り得る。表示値と分岐区分を混同しない。質問は互いの結果を読めないため、候補と条件は同一stateだけで解釈できる形にする。
- 調査時点の固定モデル`jev-1.13.0`を採用する。aliasの自動更新による判定変化を避け、応答modelも詳細へ表示する。日本語の代表例は固定fixtureによる自動統合検証で確認し、正答率目標を設けない。
- SDKの既定再試行は本要件と合わない。429、529、必須質問の欠落、構造違反は全体失敗とする。実アカウントの利用可能性、応答fixtureの照合は未実施である。
- 提供元の応答速度は本構成の実測ではない。LINE本人確認、トンネル、日本語質問を含む端末からの性能値として引用しない。

### タイムアウトと記録

- **確認元:** [HTTPX timeout](https://www.python-httpx.org/advanced/timeouts/)、[Python asyncio timeout](https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout)、[ngrok CLI](https://ngrok.com/docs/gateway/agent/cli)。
- HTTPXのread timeoutは受信chunk間の待ち時間であり、呼出し全体の上限ではない。非同期HTTPX処理を`asyncio.timeout`で囲み、LINE検証4秒、Jev判定8秒の全体上限を設ける。DRF同期境界との接続には既存Django依存のasgirefを使う。
- ブラウザは送信開始から15秒で失敗へ遷移する。ページ中断、世代変更、期限超過は、abortの成功に依存せず結果採用条件で排除する。
- ngrokのinspectionは既定で有効。開発トンネルへ`--inspect=false`を指定し、相談本文やAuthorizationをローカルinspectionへ記録しない。cloud側の本文取得設定はユーザーが有効化前に別途確認する。外部提供元の内部保持をアプリから消去できるとは主張しない。

### 固定iPhone案内

- **確認元:** [通知トラブルの公式ヘルプ](https://help.line.me/line/ios/?contentId=20000276&lang=ja)、[トーク単位の通知設定](https://guide.line.me/ja/account-and-settings/notification-chatroom.html)、[通知の便利な設定](https://help.line.me/line/?contentId=20011381&lang=ja)。
- 全体案内はiPhoneの通知許可とLINEの通知設定、特定トーク案内は該当トークのメニューの通知設定を扱う。設定の自動検知、全原因の診断、アプリ再インストール等は追加しない。
- 旧ガイドのiPhone設定リンクはトップへredirectすることを確認した。製品からは内容を確認できた公式ヘルプとトーク設定ページへリンクする。
- 手順は短い独自の要約として固定し、参照URLと確認日をcontentモジュールへ置く。表示内容は自動UI検証で照合する。

### 依存の保守事項

- 既存固定版はReact 19.2.7、React Router 8.3.1、TypeScript 6.0.3、Vite 8.1.4、LIFF 2.29.1、Django 6.0.7、DRF 3.17.1、HTTPX 0.28.1。実行系はNode 24、Python 3.14である。
- [Django 6.0.8 release notes](https://docs.djangoproject.com/en/6.0/releases/6.0.8/)には6.0.7に対するセキュリティ修正がある。既存基盤の保守事項として残し、ラボ設計の完了を依存更新や脆弱性監査の完了とは扱わない。今回のファイル計画へ一括依存更新は含めない。

## アーキテクチャ候補の評価

| 候補 | 利点 | 制約 | 判断 |
| --- | --- | --- | --- |
| owner sessionを流用 | 既存機能を多く使える | profile必須、管理権限への結合 | 不採用 |
| ラボ専用cookieとサーバーsession | token送信回数が少ない | CSRF、失効管理、保持・移行が増える | 今回は不採用 |
| ラボ専用Bearer確認、クライアント内会話 | 認証・会話・管理の境界が明確、DB不要 | APIごとにLINE照会が必要 | 採用 |
| サーバー内会話エンジンと履歴 | サーバーが会話を一元管理できる | 選択肢も通信、保持・破棄APIが必要 | 不採用 |
| 純粋なフロント状態遷移と判定API | 選択肢がローカルで確定、遅延隔離を検証しやすい | クライアント文脈は認可に使えない | 採用 |

## 設計判断と統合

### 判断: 二つの相談を共通の回答確定と案内選択へ整理する

- **背景:** 範囲質問、急ぎ表示、結果確認は共通し、回避策質問だけが通知不達固有である。
- **選択:** 型付きslot、質問種別、案内ID、終了理由を使う純粋な遷移関数を一つ置く。汎用チャットエンジンや将来の相談登録機構は作らない。
- **不変条件:** 確定した回答は上書きしない。未確定項目の明確な回答だけを一括反映し、次の一問を決める。
- **簡略化:** 複数相談時の新たな抽出候補は保留せず捨てる。選択後に必要な質問を行い、別相談の回答混入を避ける。

### 判断: 判定の正規化と会話の進行を分離する

- **選択:** Backendは固定質問、外部応答の全体検証、閾値による正規化を所有する。Frontendは質問順、回答保持、終了、中断、入力制御を所有する。
- **採用する既存機能:** HTTPX、DRF、React reducer、LIFF adapter、PageFrame、Tailwind token、標準の`details`要素。
- **自作する最小部分:** 機能固有のBearer検証、固定判定質問、有限な会話遷移。新SDK、状態管理ライブラリ、queue、repository、Modelを導入しない。
- **閾値:** Choiceはconfidenceと最大確率がともに0.70以上かつ最大値一意。Scoreはconfidence 0.70以上なら1.5以上を大きな支障、それ未満を追加確認不要とする。明示的な支障の根拠がない場合は要確認とする。Noulは0.80以上を急ぎ、0.20以下を急ぎなし、その間を要確認とする。いずれもラボの初期方針であり、正答保証ではない。

### 判断: 認証と会話の寿命を独立させる

- **選択:** tokenは専用adapterのメモリに閉じ、会話型、判定API応答、ログへ含めない。期限到達では会話を読取専用に保ち、再認証を要求する。
- **帰結:** 同じページ内で再確認できれば会話を維持できる。LINE再認証がページ再読み込みを伴う場合は、要件どおり相談を復元せず新規開始する。
- **制約:** クライアントから送る回答slotは判定材料にすぎない。本人許可、管理操作、Messaging APIへの権限には一切使用しない。

### 判断: ラボ専用HTTP境界でowner契約との混在を防ぐ

- **背景:** DRFのglobal handlerは認証・権限エラーをowner用codeと`summary` schemaへ変換する。ラボViewが本文処理だけを実装すると、認証失敗などView method前の経路でラボ契約を維持できない。
- **選択:** `views.py`内の`LabAPIView`がラボ専用認証・権限、CSRFなしのexact-Origin、例外縮約、全応答の`Cache-Control: no-store`を一元管理する。既存global handlerと`ExactOriginCsrfMixin`は変更しない。
- **帰結:** 認証、権限、parse、method不正、想定外例外を含めて公開schemaが一定となる。owner APIとラボAPIの相互回帰試験が必要となる。

### 判断: LINE照会障害は利用確認と本文再送を分離して回復する

- **背景:** 各保護APIでLINE証明を再検証するため、判定要求中にも一時的なLINE照会障害が起こり得る。会話を停止するだけでは通常入力からの回復要件を満たさない。
- **選択:** 判定中の`access_unavailable`はpending snapshotと入力へ戻し、認証gateを利用不能へ移す。本人は空の利用確認APIだけを明示的に再試行し、成功後に通常送信で本文を再送する。
- **帰結:** 本文の自動再送と二重判定を避けつつ、会話を失わず回復できる。初回・画面復帰時の照会障害も同じ利用確認操作へ統一する。

### 判断: Scoreの公開legendをBackend固定値から生成する

- **背景:** 外部応答のlegend文字列を表示へ流すと、Jev側の文言変更や異常応答で要件上の0・1・2の意味が変わり得る。
- **選択:** 外部応答からscore、確率、confidenceだけを採用し、公開legendはBackendの固定した三段階定義から構築する。外部legendは表示へ使わない。
- **帰結:** 分岐と表示の意味が同じ固定契約へ収束する。契約fixtureで三段階の固定説明を照合する。

## リスクと対策

- 日本語で否定や曖昧さを読み違える可能性がある。固定閾値・選択肢による確認・判定詳細で観察可能にする。
- API全体が成功でも質問が欠ける可能性がある。完全性、数値範囲、候補集合を確認し、一つでも違反すれば失敗させる。
- owner向けglobal exception handlerがラボの認証・権限エラーへ適用される可能性がある。LabAPIViewの専用例外変換と両APIの回帰試験で境界を固定する。
- 判定中のLINE照会障害で会話が回復不能になる可能性がある。snapshot復帰、空の利用確認の手動再試行、本文の手動再送へ分ける。
- 外部Score legendが固定説明と異なる可能性がある。公開legendはBackend固定値から生成する。
- 終了・再開後にレスポンスが届く。相談ID、リクエストID、revision、deadline、認証状態を採用条件にする。
- 一時メモリの利用量制限はプロセス再起動でリセットされる。本人専用の単一Backendプロセスを前提とし、請求額の上限保証とは扱わない。
- 開発用チャネル設定、Jev資格情報、実機の動作は未検証である。外部設定と実機確認はユーザーが任意で行い、実装完了の主張や完成条件に含めない。

## 設計段階のSpecサイズ評価

- **判定:** `PASS (single-spec)`。ドラフトの境界・契約・検証項目から再見積りした。
- **実行可能タスク見込み:** 36件、増分余地を含む範囲は36〜39件。すべて1〜3時間単位で、実装・テスト・統合・自動回帰を含む。
- **内部責任境界:** 5つ。入口・本人確認、外部判定と正規化、会話遷移、画面・案内・観察、統合と検証。
- **独立して提供する成果:** 本人が2種類の相談と3種類の判定を観察できる一つのラボ。
- **外部ワークフロー:** LINE本人確認とJev判定の2系統。会話は単一ページの状態機械で、外部資源作成、補償、DB移行はない。

| 作業境界 | 具体的な1〜3時間単位の作業 | 件数 |
| --- | --- | --- |
| 入口・本人確認 | 設定と契約、LINE gatewayと検証テスト、Bearer認証と権限分離テスト、利用確認API、LIFF gateと期限テスト、独立route配線 | 6 |
| 判定境界 | DTOと境界検証、固定Choice群、Score/Noulと文脈構築、HTTPXと全体deadline、応答完全性と正規化、利用量制限、判定HTTP契約テスト | 7 |
| 会話状態 | 型と初期化、回答確定・質問順、通知不達分岐、設定相談分岐、曖昧回答制御、複数・対象外・訂正、結果と終了、pendingと遅延隔離、横断状態遷移テスト | 9 |
| 画面・観察 | 会話と入力、選択肢と例文、固定案内とリンク、判定詳細、非同期接続、認証復帰と寿命、UI操作・アクセシビリティテスト | 7 |
| 統合・検証 | Composeと秘密注入、通信記録の抑制確認、契約fixture照合、管理認証・route回帰、二相談のUI統合、失敗・中断統合、production buildと自動回帰 | 7 |

- **所有とレビュー順:** 契約と設定を先に確定し、本人確認・判定adapter、純粋な遷移、UI接続、統合・自動回帰の順に検証する。共有設定・route・Composeの変更は統合担当が一括所有する。
- **継続理由:** 複数の内部責務は一つの利用者成果へ収束する。独立rolloutや永続状態機械を持ち込まず、境界を越える接続は定義済みHTTP DTOとUI hookへ限定する。
- **再判定条件:** 新しいsession保存、履歴、外部管理機能、別の状態機械、40件以上の見積り、または2回の修正で解消しない構造問題が判明したら`SPLIT_REQUIRED`とする。Tasks段階では30〜39件の独立task graph reviewが必要である。
- **再検証後の見積り:** LabAPIViewは既存のHTTP契約作業、LINE照会回復は既存の認証復帰・失敗統合作業、固定legendは既存の判定正規化作業へ含まれる。新しい独立成果、状態機械、外部ワークフローは増えず、36〜39件の`PASS (single-spec)`を維持する。

## 設計レビュー記録

- **初回の機械検査:** 受け入れ基準87件からcanonical IDを抽出し、トレーサビリティ表で87件すべてを確認した。欠落・不正ID、TypeScriptの`any`、未記入placeholderはなかった。境界4区分と具体的なファイル計画を確認した。
- **初回の実質レビュー:** 既存コード調査担当による統合可能性確認と、主担当による要件・状態・境界レビューを行った。BearerとDBなしの方針に既存コード上の阻害要因はなかった。
- **修正パス1:** topicを選択肢で確定した後の`impact=unassessed`を回避策確認対象へ追加した。Jevの固定URL・認証header・request/response構造・公開DTOへの写像を追記した。認証状態の変化でcontrollerをunmountしない配置と、期限・失効時のsnapshot復帰を明記した。機能・要件境界は変更していない。
- **最終確認:** `PASS`。修正後の87件のID対応、境界4区分、16コンポーネントの配置、新規34パスと変更6パスの実在状況、placeholder不在を再確認した。コード調査担当も前回指摘の解消を確認した。主担当の契約・遷移・実行可能性レビューは1回の修正で収束し、設計段階のサイズ判定は`PASS (single-spec)`、36〜39件で確定した。
- **確認の範囲:** 文書と現行コード・公式契約の整合性を検証した。実装、実Jev呼出し、LINE Console設定、iPhone動作、依存更新は未実施である。外部設定と実機確認は実装完成条件に含めず、設計生成は人による設計承認を代替しない。
- **再検証指摘:** ラボ固有HTTPエラーと既存DRF global handlerの衝突、判定中のLINE照会障害からの回復不足、外部Score legendへの表示依存を実装開始前の重大課題として確認した。
- **修正パス2:** `LabAPIView`の専用例外・origin・no-store境界、空の利用確認だけを再試行する回復フロー、Backend固定Score legendを設計とテスト戦略へ追加した。要件、成果境界、外部依存は変更していない。
- **再レビュー結果:** `PASS`。3件の重大課題は既存所有ファイルと既存作業境界の局所修正で解消した。設計段階のサイズ判定は`PASS (single-spec)`、36〜39件を維持する。

## 参照資料

上記各調査項目に一次資料を配置した。内部の判断基準は`.kiro/steering/product.md`、`tech.md`、`structure.md`、`spec-sizing.md`、承認済み`requirements.md`、`brief.md`、`TEXT_JUDGMENT_LAB_REQUIREMENTS.md`第11節である。

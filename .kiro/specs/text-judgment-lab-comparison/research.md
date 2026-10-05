# Research & Design Decisions

## Summary

- **Feature**: `text-judgment-lab-comparison`
- **調査日**: 2026年10月5日
- **Discovery Scope**: 既存拡張。`design-discovery-light.md`を適用。
- **主要発見**: API v3と共通結果表示を再利用できる。通常turnだけの履歴を判別可能なunionへ変更する必要がある。認証ゲートの許可後deniedが子をunmountする経路は、比較データの保持要件と合わない。
- 新規外部API・依存・認証方式は導入せず、full discoveryへ広げる要因はない。秘密情報を扱う既存通信の内部は変更しない。

## Research Log

### 既存画面と送信処理

- 根拠: [useTextJudgmentLab.ts](../../../frontend/src/useTextJudgmentLab.ts)、[TextJudgmentLab.tsx](../../../frontend/src/TextJudgmentLab.tsx)、[既存design](../text-judgment-lab/design.md)。
- 通常送信は先にturnを作り、成功・失敗で同じturnを更新する。入力は送信時trim、1,000文字制限、running refで連打を拒否し、15秒timeoutとabortを持つ。
- 比較にも通常turn追加を適用すると対表示と二重になる。比較成功だけで専用entryを追加し、pendingと比較失敗はcomposerで示す。
- 失敗復元は現在trim済みtextを使う。比較は編集内容保持が必要なため、生のdraftと実際に送るtextを別に保存する。UIの文字数判定も同じ純粋遷移の条件へそろえる。
- 既存結果表示は分類・感情・急ぎ、時間・モデル、answersの詳細を一箇所で持つ。共通componentへ抽出し、詳細では保持したresponse全体を表示してmetadataも未丸めで確認できるようにする。

### HTTP・認証・寿命

- 根拠: [API](../../../frontend/src/textJudgmentLabApi.ts)、[DTO](../../../frontend/src/textJudgmentLabDto.ts)、[型](../../../frontend/src/textJudgmentLabTypes.ts)、[AuthGate](../../../frontend/src/TextJudgmentLabAuthGate.tsx)、[Page](../../../frontend/src/TextJudgmentLabPage.tsx)。
- requestはv3とtextのみ。cookieなしのBearer認証であり、owner sessionへ変更しない。APIはDTO検証済み結果を返し、比較用の追加情報は必要ない。
- AuthGateは許可後のinitializing・unavailable・reauthentication_requiredで子を保つが、deniedでは外す。要件5.5の画面存続中の保持を満たすため、初回許可後の子はdeniedでも保持する。初回拒否は従来どおり未mountとする。
- Reactは同じ位置のcomponentに状態を対応付け、削除すると状態が失われる。子の位置とkeyを固定して保持する設計の根拠とした。[React公式資料](https://react.dev/learn/preserving-and-resetting-state)
- job ID照合に加え、完了時の認証・期限と経過時間を確認する。タイマー遅延やabort非対応のモックでも後着を採用しない契約を明示した。
- 本設計ではBackendを変更しない。固定3質問とJev入力の分離は既存specの契約として依存し、Frontendの送信引数を完全一致で検証する。実Jevへの接続は本調査では実施していない。

### 表示・確認・検証環境

- 根拠: [style.css](../../../frontend/src/style.css)、[既存UIテスト](../../../frontend/test/TextJudgmentLab.test.tsx)、[認証テスト](../../../frontend/test/TextJudgmentLabAuthGate.test.tsx)、[package.json](../../../frontend/package.json)。
- ラボ外枠は最大46rem、assistantは最大36rem、userは85%。比較を既存assistant内へ入れると二列の幅が不足するため、比較articleをチャット直下で全幅にする。
- Vitestとjsdomは既に導入済み。現行テストに送信回数、timeout後の成功破棄、失効後の自動再送なしがあり、比較ケースへ拡張できる。CSS・実フォーカス・改行の最終判定は実ブラウザで行う。
- ブラウザ標準confirmは承認をbooleanで返し、抑止時はfalseとなる。trueだけで置換する設計とした。[MDN confirm資料](https://developer.mozilla.org/en-US/docs/Web/API/Window/confirm)
- リポジトリ内にも`RichMenuEditor.tsx`で入力破棄時のconfirm採用例がある。独自modalやdialogライブラリは導入しない。LINE内ブラウザで操作できることは実装時の受入確認に残す。

### 用語と既存決定

- [CONTEXT.md](../../../CONTEXT.md)の文章判定ラボと、本spec要件の比較元・書き換え後を使用する。
- `docs/adr/`の既存4件は外部リッチメニューの所有・削除・置換に関する決定であり、本件の画面内比較への変更はない。
- 新規の共有ドメイン概念、API変更、複数機能に及ぶ長期的な制約を導入しないため、CONTEXT・ADR・steeringは変更しない。比較snapshotは本画面内の表示状態として定義する。

## Architecture Pattern Evaluation

| 方針 | 利点 | 制約・判定 |
|---|---|---|
| 既存hookと純粋遷移、union履歴 | 通常・比較の共通失敗処理と結果型を維持できる | 採用。Stateとhookの責務を分離する |
| 比較専用hookと通常履歴の併設 | 通常送信への変更量を減らせる | 不採用。同時送信guardと認証寿命が二重になる |
| 比較API・履歴DB | サーバーで比較関係を管理できる | 対象外。新規契約・保存目的・移行が必要になる |
| 汎用比較ビュー・状態管理ライブラリ | 多数のモデルや比較軸へ拡張できる | 不採用。今回の二文章比較には過剰 |

## Design Decisions

### 成功snapshotとsubmissionの分離

選択対象の共通単位を「文章と成功応答」とし、通常turnと比較両側を同じResultRefで選択する。比較の連鎖でも通常結果扱いの複製を作らず、元と書き換え後のsnapshotだけを持つ。一般化は選択と表示の契約に限り、任意件数・任意判定軸へは広げない。

送信ごとに元・draft・trim済みtextを固定する。比較元を現在の選択から完了時に読み直す案は取り違えを起こすため採用しない。snapshotコピーのメモリ消費はあるが、画面寿命限定の既存履歴と同じ範囲に収まる。履歴上限による暗黙の消去は追加しない。

### 既存部品とブラウザ機能を採用する

Jev通信・DTO・認証context・結果描画を再利用する。状態遷移だけはこの機能の受入条件に直接対応する小さな純粋関数とする。汎用ライブラリ導入による設定・version互換性・新規runtimeは不要。

置換確認は既存例とブラウザ機能を採用する。ブラウザにより確認が抑止された場合は置換せず、利用者が入力を空にしてから比較開始できる。confirmを使った実機操作が成立しない場合は、UI境界内の確認方式を再検討し、状態契約は維持する。

### 認証方式を維持したまま画面状態を保持する

認証失効・利用資格の確認不可は入力データの消去理由にしない。既存AuthGateの許可後deniedだけをmount維持へ合わせる。Backendに新しい認証例外は設けず、既存ゲートの非許可案内と全操作停止を使う。再認証が文書遷移する場合までの保存は行わず、要件5.7の画面離脱として扱う。

## Risks & Mitigations

- 二重送信・二重履歴: 単一job guardとunion履歴、比較成功時の一件追加を統合テストする。
- 再送後に前の応答が混入: 無効化後のID照合と、abortを無視する遅延応答を使って検証する。
- UIから見えない状態消去: 実AuthGateを含むテストで許可後denied・確認中・失効を検証する。
- 元データを表示丸めで変更: 深いreadonly snapshotと異なる値のfixtureで元のJSONを検証する。
- 狭い列の横はみ出し・focus喪失: 320px・375px、切替境界、長いmodelとJSON、キーボード操作を実ブラウザで確認する。
- 既存通常送信の回帰: 共通hook変更後に通常成功・失敗・Enter・制限・認証の既存テストを継続する。

## Spec Size Assessment

- **Verdict: PASS (single-spec)**
- 独立した責任境界候補: B1 状態・送信制御、B2 表示・入力、B3 既存認証ゲートの画面寿命接続。B3は新しい認証ワークフローではなく、一つのmount条件の整合である。
- 独立して提供する成果: 文章比較の1件。B1〜B3はこの成果としてまとめて提供し、別rollout／rollbackは行わない。
- 外部ワークフロー: 既存Jev判定の1系統。新規外部サービス、補償フロー、永続状態機械は0件。
- 実行可能タスク見積り: **7〜9件、各1〜3時間**。briefの4〜6件から、認証ゲートの寿命接続と純粋遷移・競合テストを明示して再見積りした。

| 作業単位 | 件数 | 所有範囲・検証 |
|---|---|---|
| snapshot・union・純粋遷移 | 1〜2 | B1 Stateと単体テスト。選択・置換・完了の不変条件 |
| hookの送信・失敗・後着制御 | 1〜2 | B1 hookとUI統合テスト。実行guardと15秒・失効 |
| 認証ゲートの保持接続 | 1 | B3 Gateと認証テスト。初回拒否との区別 |
| 共通結果表示と比較操作 | 1 | B2 二つの表示ファイルとUIテスト |
| レスポンシブ・focus | 1 | B2 CSSと実ブラウザの主要経路 |
| Page・Gate・hookの統合 | 1 | B1・B3専用統合テスト。復帰・unmount・再入場 |
| 全体回帰・build・受入確認 | 1 | Frontend全体検査、通常送信・実画面確認 |

契約レビューはState型→hookの実行・認証接続→表示→統合の順とする。Stateとhookを別々の担当が同時変更しない。B2のファイルはB1と重ならず、契約確定後は独立に作業できる。境界横断の統合はPage・Gate・hookの一箇所で、反復する統合や独立基盤の同居はない。40件基準、複合境界リスク、広すぎるtaskのいずれにも該当しない。

## Design Review Gate

保存前の一時draftに対して以下を確認し、**PASS**とした。レビュー1回、修正0回で収束した。

- 機械検査: 要件見出しと受入条件番号から37件の数値IDを抽出し、Requirements Traceability内の対応漏れ0件を確認した。
- 境界4節、具体的な変更・作成パス10件、変更なしの依存・検証パス6件、5 componentのファイル対応、文書内の相対リンク、サイズ根拠を確認した。placeholderや孤立componentはない。
- 判断レビュー: 成功snapshot、編集draft、送信textの所有が明確。比較の失敗から手動再送まで元を保持し、通常結果と二重追加しない。job無効化・期限検査・ID照合で後着を破棄する。
- 認証境界: 初回拒否と許可後の読取専用保持を区別し、本人認証と許可規則は既存契約のまま。再認証による文書遷移は離脱時消去へ収束する。
- 実行可能性: 型・遷移、通信寿命、表示、認証接続の所有ファイルと統合順を確認した。新しいruntime、migration、外部契約の調査待ちはない。
- サイズ: 7〜9件、成果1件、既存外部ワークフロー1系統で`PASS (single-spec)`。要件差し戻しや分割は不要。
- 未実施: 実装コードの変更・テスト実行・実ブラウザ確認・実Jev通信。これらは設計内の実装時検証計画であり、本gateの実行済み証拠とは区別する。

## References

- [要件](requirements.md)、[brief](brief.md) — 承認済みの範囲と受入条件。
- [Frontend規則](../../../frontend/AGENTS.md) — flat配置、純粋State、検証規約。
- [サイズ方針](../../steering/spec-sizing.md) — Design時の再判定。
- [LINE連携](../../steering/line-integration.md) — 既存信頼境界。本ラボは既存Bearer契約を維持する。
- [React: Preserving and Resetting State](https://react.dev/learn/preserving-and-resetting-state) — mountと状態保持。
- [MDN: Window.confirm](https://developer.mozilla.org/en-US/docs/Web/API/Window/confirm) — 置換確認の返却値と抑止時の動作。

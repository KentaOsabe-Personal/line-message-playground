---
name: kiro-impl
description: Implement approved Kiro tasks with task-level workers and independent review-unit acceptance. No task selector runs pending tasks autonomously; a selector runs only those tasks manually. Preserve TDD, bounded remediation, and the mode-specific commit policy.
---

# Kiro実装

承認済みspecの実行taskを実装し、独立reviewerの承認とfresh evidenceを得てから完了にします。

## 前提と実行単位

- `.kiro/specs/<feature>/` の `spec.json`、`requirements.md`、`design.md`、`tasks.md` が必要。tasksの承認がなければ編集前に停止する。
- core steering、対象サービスAGENTS、関連custom steering、Implementation Notesを読む。未関係のSkill一式は読み込まない。
- project manifest／task runner／CI・既存integration設定／READMEからcanonicalな `TEST_COMMANDS`、`BUILD_COMMANDS`、`SMOKE_COMMANDS`、`STATIC_CHECK_COMMANDS` を確定する。runtime smokeは実成果物が最初の使用可能状態へ達する最小の信頼できる方法。workerへは該当部分を渡す。
- 開始時に既存差分を記録し、無関係な変更を保護する。
- **実行task**は子を持たないmajor task `X.`、または子task `X.Y`。子を持つmajorはreview単位のheader。単独majorは自身が1つのreview単位になる。
- 引数なしはpending taskを依存順に自律実装。major番号指定はそのpendingな子へ展開し、単独majorならそれ自体を選ぶ。子番号指定は同じ親ごとにreview単位へまとめる。
- `_Blocked:_` は実行しない。`_Depends:_` は選択範囲外なら `[x]` が必要。同一review単位の先行taskは `READY_FOR_REVIEW` を依存充足として使えるが、checkboxはreviewまで未完了のままにする。
- 手動選択で未完了依存が範囲外なら停止してexactな前提taskを示し、選択範囲を勝手に広げない。`(P)` は独立性の情報であり、この実装loopは競合防止のため逐次実行する。

## 共通の実装・レビューloop

1. 各実行taskについて受入条件、完成時の成果物、design制約、検証方法をTask Briefへまとめる。元のspec番号と `_Boundary:_` を維持する。
2. taskごとにTDDと下記Feature Flag Protocolを実行する。[READMEのローカル品質チェック](../../../README.md#ローカル品質チェック)から変更範囲に対応する `STATIC_CHECK_COMMANDS` を選び、対象サービス全体を検査する。失敗・未実施のまま `READY_FOR_REVIEW` にしない。文書のみで対象外なら理由を記録する。RED出力、freshなtask-local検証、変更file一覧を `READY_FOR_REVIEW` recordへ保存する。
3. 同じreview単位の全選択taskがreadyになったら、独立したfresh reviewerへ [reviewer-prompt.md](templates/reviewer-prompt.md) と全taskの本文・境界・spec参照・report・検証commandを渡す。reviewerは `kiro-review` の正本を読み、実diffから相互作用も判定する。
4. `APPROVED` 後に [completion-gate.md](references/completion-gate.md) をreview単位へ適用する。`VERIFIED` のtaskだけ `[x]` にし、親は全子完了時だけ `[x]` にする。
5. `REJECTED` は指摘されたtask境界を修正し、単位全体を再reviewする。初回reviewに加えて修正・再reviewは最大2回。その後はdebugへ進む。

controllerは実装reportの `## Status Report`／`- STATUS:`、reviewの `## Review Verdict`／`- VERDICT:` をexactに読む。値が欠落・曖昧なら構造化blockのみを1回再要求し、解釈で補って先へ進まない。

## 自律モード

- [implementer-prompt.md](templates/implementer-prompt.md) でfresh workerを**1実行taskずつ**起動する。独立した所有範囲を渡す。workerはtasks.mdとcommitを操作しない。
- 各iterationでtasks.mdを読み直す。reviewまで全taskのreport・RED・検証・変更fileを保持し、単位承認後は短い要約へ縮約できる。
- `READY_FOR_REVIEW` は保留recordへ保存する。`NEEDS_CONTEXT` は1回追加contextで再試行し、未解消ならdebug。`BLOCKED` は直ちにskipせずdebugする。
- review・完了gate通過後、単位の変更fileとtasks.mdだけ明示pathでstageし、1回commitする。`git add -A`／`git add .` は使わない。
- commit形式は `feat(<feature-name>): complete task <review-unit-number> <description>`。横断的な学びはtasks.mdの `## Implementation Notes` に残す。

## 手動モード

選択taskをmain contextで実装し、同じready record・独立review・完了gateを適用します。自動stage・commitは行いません。別途依頼された場合だけ公開します。

## Feature Flag Protocol（既存規約を維持）

behaviorを追加・変更するtaskでは、適切なOFF既定flagのscaffoldingを作り、新behaviorのtestをflag OFFで実行して受入条件に対応する失敗を記録します。flag ONで実装して成功させ、flagを除去して再検証します。OFFでもtestが通る場合はtest対象を修正します。

refactor、設定、文書などbehaviorを変更しないtaskではこのflag protocolを省略します。behavioral TDDはRED → GREEN → REFACTOR → VERIFYを保持します。

## Debugと停止条件

[debugger-prompt.md](templates/debugger-prompt.md) でfresh investigatorへfailure、現在のdiff、task／spec参照、review findings、関連Notesを渡し、`kiro-debug` の正本を使います。失敗を繰り返す会話全体は渡しません。

- `RETRY_TASK`: 現在のworktreeを維持し、新しいimplementerへFIX_PLAN・NOTES・diffを渡して明示編集で修復する。ready後に単位全体を再reviewする。
- `BLOCK_TASK`: `_Blocked: <ROOT_CAUSE>_` を記録し、依存関係が許す次のtaskへ進む。
- `STOP_FOR_HUMAN`: blockを記録し、feature実行を停止する。順序・境界・分解が不正なら承認済みtask planの見直しへ返す。
- debugはtaskごとに最大2round。解決しなければblockedにし、学びをNotesへ記録する。全taskがblockedなら停止する。
- 破壊的reset／checkoutで復旧しない。上流specが原因なら所有するspecへ返し、下流の回避策で隠さない。上流修正後は依存specのvalidation／smokeを再確認する。
- 予期したRED失敗は証拠。GREEN以降・review・regressionの予期しない失敗時は後続へ進まず診断する。
- implementerを使えない場合はmain contextへfallback可能。独立reviewerが使えなければ `MANUAL_VERIFY_REQUIRED` としてcheckboxを変えず停止する。必要なfresh debuggerが使えない場合も自己承認しない。

## 全体検証と再開

自律モードは全task完了後 `$kiro-validate-impl <feature>` を実行します。GOはfeature claimの共有completion gateが通った場合だけ報告します。NO-GOへの修正は具体的な指摘に限定し最大3round、未解消や `MANUAL_VERIFY_REQUIRED` なら停止します。手動モードでは全体検証を案内し、自動実行しません。

中断後は未完了単位のdiffを保護し、task-local検証からready状態を再構成します。実装済みtaskを盲目的にやり直さず、全選択taskがreadyならreviewから再開します。

結果はspecの言語で、対象ID、実装status、review verdict、検証、残task、自律モードのcommitを報告します。

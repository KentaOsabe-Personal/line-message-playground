---
name: kiro-review
description: Review a bounded Kiro implementation unit against approved specs, task ownership, RED evidence, and actual changes. Use as the independent acceptance gate after implementation or remediation; feature-wide integration belongs to kiro-validate-impl.
---

# Kiro実装単位レビュー

独立reviewerが、1つの実行taskまたは同一親の選択された子task群を判定します。全taskが `READY_FOR_REVIEW` になった後、checkbox更新前に実行します。発見した不足を勝手に要件へ追加しません。

## 入力

review単位のheader、全taskのexact本文・元の要件／設計節番号・ `_Boundary:_`、spec path、各実装reportとRED出力、変更file一覧、controller指定の検証command、必要なsteering／Implementation Notesを受け取ります。

## 合否の正本

実diffとspecを直接読みます。`git diff` だけで見えないstaged／untracked変更もreview範囲に含め、既存の無関係な変更と区別します。

- canonical test suiteを実行し、失敗なら拒否する。[READMEのローカル品質チェック](../../../README.md#ローカル品質チェック)に従い、対象サービス全体のLint・整形チェック・Frontend型検査を行う。同じコード状態・設定・依存・対象範囲のfresh evidenceだけ再利用できる。必要なチェックの失敗・未実施は拒否し、command・exit code・対象範囲を記録する。文書のみで対象外なら理由を記録する。runtime import・module load・native dependency・boot設定のdiff確認も維持する。
- 新規のTBD／TODO／FIXME／HACK／XXXは明示的なtask上の根拠がなければ拒否する。
- concreteなhardcoded secret／credentialを拒否する。単なる変数名・placeholder・安全なfixtureへのkeyword一致だけで秘密と断定せず、実値を出力しない。
- taskごとのfile ownershipと `_Boundary:_`、designのBoundary Commitments／Out of Boundary／Allowed Dependenciesに適合する。説明なしの範囲外変更、隠れた依存、上流への下流固有処理混入を拒否する。
- behavioral taskは受入条件に対応する `RED_PHASE_OUTPUT` が必須。欠落・空・無関係な失敗を証拠としない。non-behavioralは `N/A` を許容する。
- 全taskの受入条件、元のrequirement番号、designの指定構造・契約・依存方向に照合する。実体のないstubやdeferred shell、暗黙の設計差し替え、子task間の契約不一致を拒否する。
- testが要求動作を観測し、実装を壊すと失敗すること、必要な失敗経路が処理されることを確認する。

重大なspec曖昧性、技術的な不可能、証拠不足、所有境界の不明はcontrollerへ返します。severityは `Critical`、`Important`、`Suggestion`、`FYI`。前二者は受入前に解消します。

最終応答は次のblockを1つ返します。field名・enumは変更せず、説明は `spec.json.language` に従います。修正が必要な場合はtask ID、file、spec節、具体的な修正を付けます。

```md
## Review Verdict
- VERDICT: APPROVED | REJECTED
- TASK: <review単位IDと対象task ID>
- MECHANICAL_RESULTS:
  - Tests: PASS | FAIL (commandとexit code)
  - TBD/TODO grep: CLEAN | <該当と判断>
  - Secrets grep: CLEAN | <秘密を表示しない指摘>
  - Static checks: PASS | FAIL | NOT_RUN | N/A (command、exit code、対象範囲または対象外の理由)
  - Boundary: WITHIN | <範囲外file>
  - Boundary audit: CLEAN | <漏れた責務・依存>
  - RED phase: VERIFIED | MISSING | N/A
- FINDINGS:
  1. <severity、task ID、具体的な指摘と根拠>
- REMEDIATION: <REJECTEDの場合は必須>
- SUMMARY: <要約>
```

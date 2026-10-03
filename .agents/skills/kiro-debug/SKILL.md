---
name: kiro-debug
description: Investigate a blocked Kiro implementation or repeated review/validation failure and return the controller’s structured root-cause decision. Use inside the approved task scope; plan or specification conflicts must be returned to their owner.
---

# Kiro原因調査

実装の `BLOCKED`、追加contextでも解消しない失敗、修正reviewの不収束、予期しない検証失敗を、実装workerとは独立したcontextで調査します。修正コードの代わりに原因・最小修正案・検証方法を返します。

## 入力と判定

exact error、失敗commandの出力、stack trace、現在のdiff、task本文と `_Boundary:_`、specの元の節番号、review findings、Implementation Notes、runtime制約を受け取ります。local runtime／config／dependencyを確認し、runtimeや外部契約が関係する場合は該当versionの公式資料を参照します。

| NEXT_ACTION | 条件 |
|---|---|
| RETRY_TASK | 承認済みtask範囲内のrepo変更で解決可能 |
| BLOCK_TASK | 当該taskは進められないが他taskは安全に継続可能 |
| STOP_FOR_HUMAN | 要件・設計の判断、順序・分解・境界の再承認、利用不能な外部環境などが必要 |

依存task欠落・task順序・責務分解の問題をコードの回避策で隠しません。候補を無差別に変更する案を避け、根拠とconfidenceを示します。出力値・field名はcontroller契約、説明は `spec.json.language` に従います。

```md
## Debug Report
- ROOT_CAUSE: <原因と根拠>
- CATEGORY: MISSING_DEPENDENCY | RUNTIME_MISMATCH | MODULE_FORMAT | NATIVE_ABI | CONFIG_GAP | LOGIC_ERROR | TASK_ORDERING_PROBLEM | TASK_DECOMPOSITION_PROBLEM | SPEC_CONFLICT | EXTERNAL_DEPENDENCY
- FIX_PLAN:
  1. <対象pathと最小修正>
- VERIFICATION: <原因解消を示すcommand／観測>
- NEXT_ACTION: RETRY_TASK | BLOCK_TASK | STOP_FOR_HUMAN
- CONFIDENCE: HIGH | MEDIUM | LOW
- NOTES: <次の実装担当への必要情報>
```

---
name: kiro-validate-impl
description: Validate integration across approved Kiro implementation units and return GO, NO-GO, or MANUAL_VERIFY_REQUIRED. Checks full-suite and runtime evidence, requirements coverage, design boundaries, blocked tasks, and upstream ownership.
---

# Kiro feature統合検証

task単位の受入は独立reviewerが完了している前提です。このSkillは承認された単位を横断する契約・data flow・feature全体の完成を検証し、個々のtask reviewを重複実行しません。

## 対象と入力

- feature／task引数があればその範囲。featureのみは完了task、引数なしは会話の実装対象またはspecsの `[x]` から候補を示す。対象がなければ検証対象なしと報告する。
- spec.json、requirements、design、tasksとImplementation Notes、core steering、対象サービスと関連customを読む。必須spec欠落は停止する。
- manifest、task runner、CI／integration設定、READMEからcanonicalなtest／build／smoke commandを確定する。実成果物が最初の使用可能状態へ達する最小のsmokeを選ぶ。
- test実行、要件網羅、design整合、横断統合は必要に応じて独立workerへ委譲する。最終判断は全結果を統合して行う。

## 必須確認

1. **Full suite**：canonical full-test commandの実結果とexit code。失敗はNO-GO、command不明はMANUAL_VERIFY_REQUIRED。
2. **Runtime smoke**：build成果物の起動・最初の使用可能状態。runtime crash、module／ABI／必須設定の失敗はNO-GO。環境・信頼できるcommandがなければMANUAL_VERIFY_REQUIRED。
3. **残存markerと秘密**：featureが追加したplaceholderをwarningとして評価し、実際のhardcoded secretはCritical。keyword一致だけで実秘密と断定せず、値を出力しない。
4. **統合**：task間のinterface、data shape、共有状態、API、依存順、責務境界が合成可能か確認する。
5. **要件網羅**：元の要件番号を実装・完了taskへ対応付け、部分的または横断的な漏れを示す。架空のaliasを作らない。
6. **Design全体**：component graph、File Structure Plan、依存方向、Boundary Commitments／Out of Boundary／Allowed Dependencies／Revalidation Triggersに照合する。発火したtriggerに対する隣接・下流の再検証を確認する。
7. **残作業**：未完了・ `_Blocked:_` とNotesがfeature完成に与える影響を評価する。選択範囲だけの検証をfeature全体のGOへ拡大しない。

## 完了gateとownership

GOの前に [completion-gate.md](../kiro-impl/references/completion-gate.md) の `FEATURE_GO` 契約を適用します。scopeに対応したfresh evidenceが全条件を満たす場合だけGOです。具体的失敗はNO-GO、必須検証を実行できない場合はMANUAL_VERIFY_REQUIRED。

findingのownershipを `LOCAL`／`UPSTREAM`／`UNCLEAR` に分類します。上流原因を下流の局所修正へ丸めず、owner specと修正後に再検証すべき依存先を示します。

```md
## Validation Report
- DECISION: GO | NO-GO | MANUAL_VERIFY_REQUIRED
- MECHANICAL_RESULTS:
  - Tests: PASS | FAIL (commandとexit code)
  - TBD/TODO grep: CLEAN | <該当と判断>
  - Secrets grep: CLEAN | <秘密を表示しない指摘>
  - Smoke boot: PASS | FAIL | MANUAL_REQUIRED
- INTEGRATION:
  - Cross-task contracts: <状態>
  - Shared state consistency: <状態>
  - Boundary audit: <状態>
- COVERAGE:
  - Requirements mapped: <件数／総数>
  - Coverage gaps: <未対応箇所>
- DESIGN:
  - Architecture drift: <指摘>
  - Dependency direction: <違反>
  - File Structure Plan vs actual: <対応>
- OWNERSHIP: LOCAL | UPSTREAM | UNCLEAR
- UPSTREAM_SPEC: <featureまたはN/A>
- BLOCKED_TASKS: <残作業と影響>
- REMEDIATION: <NO-GOでは具体的修正が必須>
```

説明はspecの言語で返します。検証は実装やcheckboxを変更しません。NO-GOは対象taskの修正と再検証、MANUAL_VERIFY_REQUIREDは不足する環境・検証を示し、完了と扱いません。

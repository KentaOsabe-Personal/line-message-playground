---
name: kiro-spec-tasks
description: Generate an executable Kiro task graph from approved requirements and design. Preserves numerical traceability, leaf-task granularity, ownership, dependencies, optional-test semantics, sizing gates, and task approval.
metadata:
  shared-rules: "tasks-generation.md, tasks-parallel-analysis.md"
---

# Kiroタスク生成

spec.json、requirements、design、既存tasksと必要なsteering・対象サービス規則を読みます。requirementsとdesignの承認が必要です。明示的な `-y` は両者と生成tasksを承認し、`--sequential` は `(P)` markerを省きます。

## 生成契約

[tasks-generation.md](rules/tasks-generation.md)、必要時だけ [tasks-parallel-analysis.md](rules/tasks-parallel-analysis.md)、 `.kiro/settings/templates/specs/tasks.md` を使います。単なるfile読み込みのためにworkerを増やす必要はありません。

- 元の数値requirement IDだけを列挙し、説明やaliasを混ぜない。全要件、design component、contract、統合点、runtime前提、移行・検証を覆う。
- 実行taskは1〜3時間の検証可能な成果物を持つleaf。単独major `X.` と子task `X.Y` の両形式を使える。子が1件だけならmajorへ昇格し、container-only majorへ詳細を重複させない。
- 通常taskは一つの責任境界に収め、境界横断は明示的なintegration taskにする。非自明な前提は `_Depends:_`、境界は `_Boundary:_` で表す。runtime／SDK／configの必要setupを暗黙にしない。
- 実行subtaskに完成を観測できるdetailを含める。単独majorの成果物も本文で明確にする。
- `(P)` は共有file・依存・承認待ちのないtaskだけ。optionalな `- [ ]*` は既に満たした受入条件に対する延期可能な補助testだけで、実装や統合必須検証には付けない。
- 既存tasksがあれば完了・blocked・Notesを尊重してmergeする。本文はspecの言語に従う。

## 保存前の2段階review

draftを保存せず、まずTask Plan Review Gateでcoverage、実行可能性、依存・境界・前提を確認します。container-only majorを除く実行task数を数え、 `.kiro/steering/spec-sizing.md` を適用します。local修正は最大2回。要件・設計不足は元のphaseへ返し、`SPLIT_REQUIRED` なら書かずdiscoveryへ戻します。

次にfresh workerが使えれば1回の独立task-graph sanity review、使えなければmain contextで確認します。ただしサイズ方針が独立reviewを必須とする30〜39件ではfallbackで確定せず、独立reviewが完了するまで保存しません。渡すのはdraft、spec path、merge context。reviewerがrequirements／design／生成規則を直接読み、隠れた前提、順序、境界重複、広すぎるtask、矛盾を確認します。

- `PASS`: 保存可能。
- `NEEDS_FIXES`: 1回修正して1回再review。
- `RETURN_TO_DESIGN`: exactなgapを示し、tasksを保存しない。

## 保存と承認

通過後にtasks.mdを保存し、`phase: tasks-generated`、tasks.generatedをtrue、tasks.approvedをfalse、requirements／design.approvedをtrue、updated_atを更新します。`-y` ならtasks.approvedもtrue、それ以外は生成内容の承認後にtrueへ変更します。拒否・修正希望ではfalseを維持します。

必須spec欠落・未承認・requirementの非数値IDは停止します。template欠落時の基本形式fallbackは明示します。結果はmajor／実行task数、網羅性、サイズ判定、review結果、承認状態を示し、承認後に `$kiro-impl <feature> [tasks]` へ案内します。

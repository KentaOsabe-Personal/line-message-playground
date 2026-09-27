---
name: kiro-discovery
description: Choose the Kiro specification path when scope is unclear or a new feature needs a brief or roadmap decomposition. Distinguishes existing-spec updates, direct implementation, new specs, and mixed work; does not turn routine fixes into mandatory interviews.
---

# Kiro作業の分解

既存specの更新、直接実装、新規spec、roadmap分割を選び、合意した範囲をbriefへ保存します。工程全体の案内は [workflow.md](references/workflow.md)。

## 経路選択

まず `.kiro/specs/*/spec.json` のfeature・phase・approvals、steeringの存在、root構造を調べます。既存roadmapと [サイズ方針](../../../.kiro/steering/spec-sizing.md) は読み、関係のないspec本文を一括で読まないようにします。

| Path | 選択条件 | 次の行動 |
|---|---|---|
| A | 意味のある要求が1つの既存spec内に収まる | 対象specの更新を提案して停止 |
| B | 新規・既存specの更新を必要としないbug fix、設定、小さな変更 | 直接実装を提案して停止 |
| C | 新規の単一specで `PASS (single-spec)` | briefを作成 |
| D | サイズ方針で `SPLIT_REQUIRED` | roadmapと各新規specのbriefを作成 |
| E | 既存spec更新・新規spec・直接実装の混在 | 新規specがある場合だけ混在roadmapを作成 |

C／D／Eを選ぶ前に、テスト・移行・統合を含む1〜3時間単位の実行task数、責任境界、判定理由を示して方向を確認します。独立した境界が複数あるだけで分割せず、サイズ方針の複合リスク・review不収束条件を適用します。

## 範囲と方針の合意

C／D／Eではcore steering、対象サービスAGENTS、隣接specの要件を読む。大きな調査は独立したcodebase／domain調査へ委譲でき、既存・新規境界、依存、patternを短く返してもらいます。小さく明確な作業では委譲しません。

不明な項目だけを一問ずつ確認します：誰の問題か、完成時の結果、Boundary Candidates、Out of Boundary、既存specとの接点、Upstream／Downstream、制約。factは環境から調べます。

2〜3の具体的な方針を利点・欠点・範囲とともに比較し、推奨を示します。選択後はfreshな調査workerで保守状況、ライセンス、互換性、致命的制約を確認し、問題があれば選択へ戻ります。合意した方針でサイズ判定をやり直し、最終範囲を確認します。

## 保存契約

次のcommandを案内する前に、合意した結果を対象specの言語で保存し、読み返して確認します。過去の完了項目・既存の合意は保護します。

単一specの `.kiro/specs/<feature>/brief.md` は次を含みます。

```md
# Brief: <feature>
## Problem
<対象利用者と問題>
## Current State
<現状と不足>
## Desired Outcome
<完成時の結果>
## Approach
<選択した方針と理由>
## Scope
- In: <含む範囲>
- Out: <含まない範囲>
## Boundary Candidates
<責任境界候補>
## Out of Boundary
<このspecが所有しない責務>
## Upstream / Downstream
<依存元と利用先>
## Existing Spec Touchpoints
- Extends: <既存spec>
- Adjacent: <隣接spec>
## Spec Size Assessment
- Verdict: PASS (single-spec)
- Projected executable tasks: <範囲>
- Independent responsibility seams: <数と名称>
- Rationale: <単一specとしてreview可能な根拠>
## Constraints
<制約>
```

D／Eでは `.kiro/steering/roadmap.md` に Overview、Approach Decision（選択・理由・不採用案）、Scope、Constraints、Boundary Strategy、分割前のSpec Size Assessmentを記録します。機械的な読み取りに使う次の見出しを保持します。

```md
## Specs (dependency order)
- [ ] feature-a -- <説明>. Dependencies: none
- [ ] feature-b -- <説明>. Dependencies: feature-a
```

この節は**新規specだけ**です。列挙した全新規specにPath Cと同形式のbriefと個別のサイズ判定を作ります。Eの既存更新は `## Existing Spec Updates`、直接実装は `## Direct Implementation Candidates` に分け、batch実行対象へ混入させません。新規specがない場合はEへ進みません。

既存roadmapへの再入では必要な次specのbriefと変更された順序・範囲を更新し、既存の完了・過去phaseを上書きしません。

## 次の入口

Aは `$kiro-spec-requirements <feature>`、Bは直接実装、Cは `$kiro-spec-init <feature>`。明示的な連続生成希望なら `$kiro-spec-quick`。D／Eの新規specが複数なら `$kiro-spec-batch`、1件ならinitへ案内します。既存更新・直接実装の残作業も示し、下流のspec生成は自動実行しません。

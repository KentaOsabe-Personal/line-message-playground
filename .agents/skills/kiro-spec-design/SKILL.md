---
name: kiro-spec-design
description: Create or revise a Kiro technical design after requirements approval. Produces traceable contracts, ownership boundaries, a concrete file plan, and research evidence; selects discovery depth to match the feature.
metadata:
  shared-rules: "design-principles.md, design-discovery-full.md, design-discovery-light.md, design-synthesis.md, design-review-gate.md"
---

# Kiro設計生成

対象specのmetadata、requirements、既存design／research、core steering、対象サービスAGENTSと関連custom steeringを読みます。requirementsの承認が必要です。明示的な `-y` はrequirementsを承認します。

## 調査と設計

- 新規・複雑な統合は [design-discovery-full.md](rules/design-discovery-full.md)、既存拡張は [design-discovery-light.md](rules/design-discovery-light.md)。単純な追加は既存patternの確認に留める。
- 独立したcodebase／外部契約調査は委譲可能。外部API、version、互換性など判断に必要な一次資料を確認し、境界・依存・制約・根拠をresearchへ残す。
- 合成は [design-synthesis.md](rules/design-synthesis.md) に従い、全要件を見て共通化、採用／自作、不要な抽象化の除去を判断する。統合判断は分断して委譲しない。
- document構成は `.kiro/settings/templates/specs/design.md`、researchは `.kiro/settings/templates/specs/research.md`。詳細形式は [design-principles.md](rules/design-principles.md)。既存designは合意を保つmerge対象にする。

## 必須契約

- 元の数値requirement IDでtraceabilityを示す。Boundary Commitments、Out of Boundary、Allowed Dependencies、Revalidation Triggersを具体化する。
- File Structure Planには作成／変更する具体的pathと単一の責務を記す。taskの `_Boundary:_` と実装briefが参照できる粒度にする。
- interface、data、状態遷移、統合点、移行、runtime前提、失敗経路、検証方法を必要な範囲で明示する。
- test項目は受入条件と設計上のcomponent／動作へ結び付け、E2E経路は重要なuser flowへ対応させる。
- stackに応じた型安全性を保つ。TypeScriptの `any` やunsafe castを避け、Python等は可能な型注釈と境界validationを使う。複雑な構造は図で示す。
- 本文・researchは `spec.json.language` に従う。発見・採用理由・出典・riskはresearchへ記録する。

## 保存gateとmetadata

[design-review-gate.md](rules/design-review-gate.md) でcoverage、境界、実行可能性、 `.kiro/steering/spec-sizing.md` の再判定を確認します。サイズ根拠はresearchへ保存します。draft修正は最大2回。要件の実質的な不足はrequirementsへ返し、`SPLIT_REQUIRED` はdesignを確定せずdiscoveryへ戻します。

通過後にdesign／researchを保存し、`phase: design-generated`、`approvals.design.generated: true`、`approvals.design.approved: false`、`approvals.requirements.approved: true`、`updated_at` を更新します。

requirements欠落・未承認・非数値IDは停止します。template欠落やsteering欠落は明示し、既存の基本形式fallbackを使う場合は未確認範囲を示します。結果は調査の深さ、主要判断、gate、file、次の承認を示し、任意の `$kiro-validate-design` またはdesign承認後の `$kiro-spec-tasks` へ案内します。

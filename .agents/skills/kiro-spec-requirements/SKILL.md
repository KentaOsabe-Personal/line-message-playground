---
name: kiro-spec-requirements
description: Generate or revise Kiro requirements from an initialized specification and agreed scope. Produces numbered EARS acceptance criteria, boundary context, review-gate evidence, and generation metadata; leaves implementation choices to design.
metadata:
  shared-rules: "ears-format.md, requirements-review-gate.md"
---

# Kiro要件生成

`.kiro/specs/<feature>/spec.json` と初期requirements、存在するbrief、core steering、関連するサービスAGENTS／custom steeringを読みます。既存機能の調査や必要なdomain調査は独立して委譲できますが、要件の合成と曖昧な業務判断はmain contextで扱います。

## 正本と成果物

- EARSは [ears-format.md](rules/ears-format.md)、保存前のgateは [requirements-review-gate.md](rules/requirements-review-gate.md)。構成は `.kiro/settings/templates/specs/requirements.md` を使う。
- 受入条件は `spec.json.language` に従い、EARSの固定句を保つ。具体的なsystemを主語とし、観測可能でtest可能な動作を書く。
- 要件見出しに数値IDを付ける。既存のalphabetic IDを正規化する場合は対応を一貫させ、架空の `REQ-*` aliasを作らない。
- discoveryのBoundary Candidatesから必要なinclusion／exclusion・隣接への期待を明示する。内部のowner・API・data model・実現技術はdesignへ残す。
- scope、ユーザーの観測動作、business rule、境界条件に解釈の分岐が残る場合は確認する。既存contextが答える項目を再質問しない。

## 保存前gate

draftはcoverage、EARS、曖昧性、境界と `.kiro/steering/spec-sizing.md` の判定を通すまで保存しません。localな不足は最大2回修正します。scopeの矛盾は確認へ、`SPLIT_REQUIRED` はrequirementsを確定せずdiscoveryへ戻します。

通過後にrequirements.mdを保存し、`phase: requirements-generated`、`approvals.requirements.generated: true`、`updated_at` を更新します。人間の承認を生成完了から推測しません。

template欠落は具体的なpathを示し、既存のfallback方針どおり基本形式で進める場合は明記します。language未指定は `en`。project description欠落やscope矛盾は生成前に確認します。

結果は要件の概要、更新file、gate結果、承認・変更の次の行動を示します。既存システムでは任意の `$kiro-validate-gap`、要件承認後は `$kiro-spec-design` へ進みます。

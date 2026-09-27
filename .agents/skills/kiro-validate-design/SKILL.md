---
name: kiro-validate-design
description: Review an existing Kiro technical design when design validation is requested. Returns an interactive GO or NO-GO assessment with requirement and design evidence; it does not generate the design or replace the generation-time gate.
metadata:
  shared-rules: "design-review.md"
---

# 生成済み設計の検証

対象specのmetadata、requirements、design、core steering、対象サービスと関連customの規則を読みます。designがなければ `$kiro-spec-design` へ返します。生成metadataが未設定なら警告して実文書をreviewできます。

[design-review.md](rules/design-review.md) を正本として、既存architectureとの適合、設計整合、型・interface、保守性をreviewします。必要なcode pattern調査は独立して委譲できます。

- 重要な懸念は最大3件、保持すべき良い点は1〜2件を示す。
- 各指摘にrequirement ID、design節、影響、修正案を付ける。曖昧な設計意図は対話で確認し、技術選択や実装設計をこのreviewで勝手に確定しない。
- `GO`／`NO-GO` と根拠を示す。NO-GOはdesign修正と再検証へ、GOはtasks生成前の人間の確認へ案内する。
- 説明はspecの言語。未指定は `en`。steering不足は未確認範囲として示す。

この検証は任意の再レビューです。design生成時の必須gateを置き換えません。

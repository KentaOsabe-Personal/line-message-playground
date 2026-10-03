---
name: kiro-validate-gap
description: Analyze gaps between Kiro requirements and an existing codebase before design. Records reusable assets, missing or uncertain capabilities, implementation options, and research needs in research.md without deciding the final architecture.
metadata:
  shared-rules: "gap-analysis.md"
---

# 既存実装との差分調査

対象specのmetadataとrequirements、core steering、対象サービス規則、関係するcustom steeringを読みます。requirements欠落は停止して生成へ返し、未承認ならその状態を明示したうえで改訂に役立つ調査を行えます。

[gap-analysis.md](rules/gap-analysis.md) に従い、既存資産・interface・patternと要件を対応付けます。必要ならcode調査と外部dependency確認を独立して委譲します。

- requirement-to-asset mapに `Missing`／`Unknown`／`Constraint` を区別する。
- 既存拡張、新規component、hybridのうち実際に成立する選択肢とtrade-offを示す。
- effortとriskに根拠を付け、designへ渡す判断・調査事項を明記する。最終architectureや技術をこのSkillで確定しない。
- 結果を `.kiro/specs/<feature>/research.md` に保存する。既存researchは上書きせず、区切りを付けて追記し、読み返して確認する。

本文はspecの言語（未指定は `en`）。概要、保存先、次のdesign工程を報告します。複雑な未知事項は調査課題としてdesignへ渡し、推測で埋めません。

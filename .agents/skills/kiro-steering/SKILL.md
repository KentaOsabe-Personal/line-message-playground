---
name: kiro-steering
description: Create or synchronize Kiro project memory, or create a topic-specific custom steering document with the custom mode. Use when maintaining project conventions; ordinary implementation reads relevant steering without invoking this maintenance workflow.
metadata:
  shared-rules: "steering-principles.md"
---

# Kiroプロジェクト知識の保守

`.kiro/steering/` のcore（product、tech、structure）とcustomを保守します。既存ユーザーの意図・安全制約を保護し、fileやdependencyの網羅表ではなく判断に使うpatternを記録します。

## モード

- **bootstrap**: coreが欠けている場合。 `.kiro/settings/templates/steering/` と [steering-principles.md](rules/steering-principles.md) を使い、product・tech・structureの実際のpatternを生成する。
- **sync**: coreが揃っている場合。既存steeringを確認し、codeとの差分、陳腐化、customとの重複を調べ、根拠のある更新を行う。
- **custom `<topic>`**: 新しい専門topicの規則。 [custom-steering.md](references/custom-steering.md) を使う。

独立したproduct／tech／structureの調査は委譲できますが、単なるfile読み込みはbatch化で十分です。編集所有範囲を分け、他者の変更を戻しません。

全steeringを読む必要があるのはsteering全体の同期・監査時です。通常実装ではroot AGENTSに従い、coreと関連custom・局所AGENTSだけを読みます。

## 更新条件

- 新しいcodeが既存patternに従うだけなら追記しない。
- userのsection・exampleを保護し、不明なpolicyは上書きせず差分を提示する。既存のadditive優先を、矛盾した二重規約を増やす理由にしない。
- 秘密値、credential、database URL等を記録しない。agent toolingや `.kiro/settings/` の内部構成をproduct knowledgeへ混ぜない。
- 作成・更新理由と日付を記録し、変更file、drift、未解決判断を日本語で報告する。

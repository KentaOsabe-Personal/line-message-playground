# custom steeringの作成

`$kiro-steering custom <topic>` または専門topicのsteering作成依頼時だけ参照します。

1. topicと必要な制約が未提示なら確認し、既に与えられた内容を再質問しない。
2. `.kiro/settings/templates/steering-custom/` の該当templateを使う。候補はapi-standards、testing、security、database、error-handling、authentication、deployment。なければ実際のproject patternから構成する。
3. [steering-principles.md](../rules/steering-principles.md) と既存core／customを照合し、重複や矛盾を避ける。関連するcode／domain調査は必要な範囲だけ行う。
4. `.kiro/steering/<topic>.md` に単一topicのpattern、制約、具体例を日本語で保存する。100〜200行は目安であり、内容が短ければ引き延ばさない。
5. 適用される作業を文書に示し、作成file、根拠、確認が必要な判断を報告する。通常作業で全customを読み込む前提にしない。

秘密情報を含めず、既存ユーザーのsectionと具体例を保護します。templateは形式の出発点であり、未採用の技術や運用をprojectの既定にしません。

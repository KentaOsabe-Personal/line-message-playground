# Steeringの原則

steeringは判断に必要なproject memoryです。組織化、命名、import、architecture判断、技術標準を記録し、全file・component・dependency・実装詳細の一覧にしません。既存patternに従うcodeが増えただけなら更新不要です。

- 1文書1topicとし、具体例と理由を必要な範囲で添える。100〜200行は目安で、行数のために情報を増やさない。
- API key、password、credential、秘密のdatabase URL、内部IP等を含めない。
- 更新時はuser sectionとcustom例を保護し、不確かなpolicy変更を勝手に行わない。変更理由と日付を記す。
- productは目的・価値・business context、techはstack・判断・標準、structureは配置・命名・依存pattern、customは該当topicに限定する。
- agent-specific toolingや `.kiro/settings/` 内部をsteeringへ展開しない。specとsteeringへの軽い参照は許容する。
- 全文書を読むのはsteering全体の同期時。通常作業はcoreと関連custom・局所AGENTSに限定する。customの重要度は適用範囲で判断し、常時読み込みと混同しない。

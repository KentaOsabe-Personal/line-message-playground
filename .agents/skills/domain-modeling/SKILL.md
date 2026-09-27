---
name: domain-modeling
description: Define or revise domain terminology and record consequential architectural decisions in CONTEXT.md and ADRs. Use when the domain model changes or an explicitly selected workflow needs documentation; ordinary glossary reading does not require this skill.
---

# ドメインモデルの記録

用語・関係・境界を明確にし、合意した語彙と判断を記録します。既存語彙を読むだけの作業にはこのSkillを必要としません。

- rootの `CONTEXT-MAP.md` があれば対応するcontextへ、なければ `CONTEXT.md` と `docs/adr/` を使う。ファイルは記録すべき内容ができた時に作成する。
- 既存定義と異なる用語、曖昧な同義語、コードと説明の食い違いは具体例とともに確認する。通常のfactは環境から調べ、業務上の選択を勝手に決めない。
- 解決した用語は [CONTEXT_FORMAT.md](CONTEXT_FORMAT.md) に従いその場で更新する。CONTEXTは語彙に限定し、spec・scratchpad・実装詳細を置かない。
- ADRは「変更を戻すコストが高い」「背景なしでは意外」「実際のトレードオフがある」の3条件が揃う場合だけ提案する。[ADR_FORMAT.md](ADR_FORMAT.md) の短い形式と連番を使う。
- 複数contextでは語彙・context固有ADRを各所有範囲へ置き、全体の決定をrootのADRへ残す。

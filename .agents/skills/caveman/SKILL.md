---
name: caveman
description: Use only when the user explicitly requests caveman or wenyan style, or invokes caveman. Provides six reversible terse-writing modes; a general request for brevity alone does not select this style.
---

# Caveman

明示されたcaveman／wenyan文体で応答を圧縮します。単なる「短く」「トークンを減らして」は発火条件にしません。

## モード

`/caveman lite|full|ultra|wenyan-lite|wenyan-full|wenyan-ultra|off`。既定は `full`。選択は同じセッション内で継続し、`stop caveman`／`normal mode`／`off` で解除します。

| モード | 文体 |
|---|---|
| lite | 文を保ち、冗長な前置き・丁寧表現を削る |
| full | 短い表現と断片を使う。冠詞は意味を損なわない言語でのみ省く |
| ultra | 因果・順序が明確な範囲で接続表現も圧縮する |
| wenyan-lite | 軽い文言文調 |
| wenyan-full | 文言文調で強く圧縮する |
| wenyan-ultra | 文言文調を最も短くする |

## 保持する情報

- ユーザーの言語、技術内容、否定・条件・例外、数値・単位、code、API名、command、正確なerror文字列を保持する。日本語の助詞を機械的に削らない。
- 標準的なDB／API等の略語以外を作らない。矢印・不自然な文法・文言文字への置換を短縮のために足さない。wenyan文字はその指定時だけ使う。
- 不要な前置き、装飾、長い生ログを避ける。上位の進捗報告・説明義務は守る。
- security、不可逆操作の確認、順序が重要な手順、圧縮すると曖昧な説明、再質問への回答は通常の明瞭な文体へ戻す。その部分の後で指定モードを再開する。
- code、comment、commit、doc、issue／PR、memory、外部宛て文章には通常の文章を使い、成果物の言語規約に従う。明示的な `/caveman-compress` の対象は例外。

# Jev入門：何ができて、どう使うのか

調査日：2026年9月19日。公式資料と、アプリ内ブラウザで開かれていたTypeSafe AIコンソールを確認。

## 1. Jevとは

**Jevは、文章を読んで「分類・採点・条件判定」を行い、その結果をプログラムに返すAIモデルです。** TypeSafe AIが提供する「System One Model」の最初のモデルで、アプリの中で素早い判断を繰り返す用途を想定しています。

たとえば問い合わせ文を渡し、「担当は技術窓口」「緊急の可能性が高い」と判定させ、アプリ側で振り分けます。返信文・コード・判断理由の文章を生成する機能はありません。現在の入力はテキストと、テキストを含むJSONなどで、画像・音声・動画には直接対応していません。［[公式の概要](https://docs.typesafe.ai/concepts/system-one)］

基本形は **「判断材料（State）＋質問（Questions）→ 型の決まった結果 → アプリ側の処理」** です。複雑な判断は小さな質問に分け、結果をコードで組み合わせます。［[Introduction](https://docs.typesafe.ai/introduction)］

## 2. できることは、まず3種類

| 種類 | 使う場面 | 返るもの |
| --- | --- | --- |
| **Choice** | 問い合わせを「不具合・使い方・その他」に分類する | 選んだ項目、各候補の確率、confidence |
| **Score** | 不具合の深刻さを、説明付きの段階で採点する | 点数、各段階の確率、confidence |
| **Noul** | 「この文章は返金を求めているか」を判定する | 「はい」である確率を表す0〜1の値 |

Scoreは段階を0から数えるため、3段階なら0〜2で、小数にもなります。Noulの0.5は「程度が中くらい」ではなく、「はい・いいえの確率が同程度」という意味です。［[Choice](https://docs.typesafe.ai/primitives/choice)／[Score](https://docs.typesafe.ai/primitives/score)／[Noul](https://docs.typesafe.ai/primitives/noul)］

主な活用例は、問い合わせの振り分け、文書や商品の分類、検索候補の関連度評価、LLMの回答チェック、処理を人や別のAIへ回す判断です。判定後の実行や通知は、組み込む側のアプリが担当します。［[公式ユースケース](https://docs.typesafe.ai/concepts/use-case-map)］

## 3. 今開いているコンソールでの始め方

確認した[Playground](https://console.typesafe.ai/playground)には、**State・Questions・モデル選択（jev-latest）・Run**がありました。右側には3種類の入門例に加え、サポート応対の監査やLLMガードレールなどの実例があります。左側にはUsage、API Keys、Documentationへのメニューがあります。

今回は画面の確認までで、推論は実行していません。まずは次の架空の問い合わせで試すと、役割がつかみやすいです。

1. **State**に、以下の文章を入力します。

   > LINEの通知が昨日から届きません。今日の予約確認に必要なので、すぐに直してほしいです。

2. **Questions**の内容を、以下のJSONに置き換えます。

```json
{
  "category": {
    "type": "choice",
    "instructions": "この問い合わせを最も適切な窓口に分類してください。",
    "criteria": {
      "technical": "機能が動かない、エラーなどの不具合相談",
      "how_to": "操作方法や設定手順についての質問",
      "other": "上記以外、または内容が不明な問い合わせ"
    }
  },
  "urgent": {
    "type": "noul",
    "instructions": "この文章には、今日中または直ちに対応してほしいという要望がありますか？"
  }
}
```

3. モデルを**jev-latest**にして**Run**を押し、分類結果と`urgent`の値を見ます。これは結果未確認の練習例ですが、意図した分類は`technical`です。
4. Stateを「通知の設定方法を教えてください。急ぎではありません」に変え、分類と緊急性がどう変わるか比較します。

Stateは「読む材料」、Questionsは「その材料について何を判定するか」です。複数の質問は同じStateに対して独立に評価されるので、ある質問の答えを別の質問が読む前提にはしません。［[Quick start](https://docs.typesafe.ai/introduction/quickstart)／[Introduction](https://docs.typesafe.ai/introduction)］

## 4. このプロジェクトなら、何に使えそうか

**最初の実験には、架空のLINE問い合わせを分類し、画面に結果だけ表示する機能が向いています。** これは本プロジェクトへの提案で、現時点の実装機能ではありません。

- **問い合わせの分類**：不具合・操作質問・その他をChoiceで分ける。
- **確認の優先度付け**：期限の有無をNoul、不具合の深刻さをScoreで評価する。
- **返信候補の選択**：事前に用意した定型文から候補を選び、人が確認して送る。

組み込む場合は、Djangoバックエンドから`POST https://api.typesafe.ai/v1/systemone`へ`state`・`model`・`questions`を送ります。APIキーはバックエンドの環境変数で管理します。既存の本人確認・署名検証・許可リスト・送信確認は引き続きアプリ側で担当し、AIの分類を認可の根拠にはしません。［[API仕様](https://docs.typesafe.ai/api)］

## 5. 期待できる点と、読み違えやすい点

- **速さ・安さが特徴**：公式発表では応答70〜500ms。公式料金は入力100万トークンあたり**0.042米ドル**、出力は無料です。速度や比較倍率は提供元の測定値で、日本からの実測や、あらゆる用途で同じ性能を保証する値ではありません。［[発表記事](https://typesafe.ai/blog/introducing-system-one-models-and-jev)／[Models](https://docs.typesafe.ai/models)］
- **「型が正しい」と「判断が正しい」は別**：「ハルシネーションゼロ」という宣伝は、定義した出力形式・候補から外れないという保証として読む必要があります。候補の選び間違いまでゼロにはなりません。［[発表記事](https://typesafe.ai/blog/introducing-system-one-models-and-jev)］
- **confidenceは正答率そのものではない**：ChoiceとScoreの確率分布から計算した、判断の確かさの指標です。`0.9`をそのまま「90%正解」とは読めません。Noulには別のconfidence欄はありません。自動処理へ進める境界値は、自分の例で確認して決めます。［[Confidence](https://docs.typesafe.ai/confidence)］
- **日本語は実例で確認する**：公式には英語が主要な学習言語で、現在最も精度が高いとされています。日本語も扱えますが、明確な文・曖昧な文・否定を含む文で期待どおりか比較してから用途を広げるのがよいでしょう。［[Models](https://docs.typesafe.ai/models)］

まずは上の2つの文章をPlaygroundで比較すれば、「自然文をアプリが使える判断に変える」というJevの使い方を体験できます。

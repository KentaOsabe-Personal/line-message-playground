# 完了証拠の共有契約

Kiro実装の受入とfeatureのGO判定に適用します。独立reviewerの合否判定は `kiro-review`、feature統合検証は `kiro-validate-impl` が所有します。

claim、claim種別、現在のコード状態に対応するcommand出力とexit code、task／requirement／design参照を受け取ります。同じ変更状態・同じ対象範囲を証明する既存のfresh evidenceは再利用できます。変更後や証拠の対象外を完了と扱いません。

| CLAIM_TYPE | 必須証拠 |
|---|---|
| TASK | task-local検証、境界との対応、未解決のblocking review findingがないこと |
| FIX | 元の症状の解消、関連範囲の回帰検証 |
| TEST_OR_BUILD | 実際のcommand出力とexit code。別種類の検証から推測しない |
| FEATURE_GO | full suite、build成果物が最初の使用可能状態へ到達するruntime smoke、要件網羅、task横断統合、design全体整合、blocked taskの評価 |

[READMEのローカル品質チェック](../../../../README.md#ローカル品質チェック)が必要な変更では、TASK・FIX・FEATURE_GOの証拠に対象サービス全体の静的チェック結果を含めます。失敗は `NOT_VERIFIED`、環境不足による未実施は `MANUAL_VERIFY_REQUIRED` とし、文書のみで対象外なら理由を残します。

failed／skipped／未検証範囲も確認します。test成功だけでは `FEATURE_GO` を返せません。

- `VERIFIED`: claimの全範囲を証拠が満たす。
- `NOT_VERIFIED`: command失敗、stale／部分証拠、claimが証拠より広い、未解消blockerまたは要件漏れ。
- `MANUAL_VERIFY_REQUIRED`: canonical command不明、必要な環境が利用不能、必須のmanual検証を実行できない。

```md
## Verification Result
- STATUS: VERIFIED | NOT_VERIFIED | MANUAL_VERIFY_REQUIRED
- CLAIM_TYPE: TASK | FIX | TEST_OR_BUILD | FEATURE_GO
- CLAIM: <確認対象>
- EVIDENCE: <command／観測、結果、exit code、コード状態>
- GAPS: <未検証範囲>
- NOTES: <必要な次の行動>
```

説明は対象specの言語で返します。未検証をGO、完了、修正済みとは報告しません。

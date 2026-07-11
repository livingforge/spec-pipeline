# 脅威モデル — context-sync

context-sync は実行体を持たない**手順スキル**であり、それ自体に攻撃面（ネットワーク・
依存・秘密情報）はない。ここでは、この手順が正本を安全に更新するために**前提とする
安全性の担保**（実処理を委譲する contextdb 側の防御）を明示する。関連:
[GOVERNANCE.md](GOVERNANCE.md) / [dependencies.md](dependencies.md) /
`package-meta/contextdb/threat-model.md`。

## スコープと前提

- context-sync は「実装差分 → 正本（`.contextdb`）への同期」という**運用規律**であり、
  自前のコード・依存・秘密情報を持たない（読み取りは Git、書き込みは contextdb `mutate`）。
- 主眼は「**正本を誤って壊さないこと**」「**未レビュー変更を既成事実化しないこと**」
  「**実装だけ進んで正本が陳腐化するのを防ぐこと**」。

## リスク → 手順上の防御（委譲先）

| ID | リスク | 手順上の防御 | 委譲先の担保 |
|---|---|---|---|
| R1 | 正本 YAML を手編集して不整合を混入する | 変更は **`mutate` 経由**に限定し YAML を手で編集しない。ID・連番は自動採番 | contextdb `mutate`（自動採番・スキーマ検証） |
| R2 | 未レビューの仕様変更が `approved` として既成事実化する | 新規・変更は必ず `status: review`。`approved` へ上げるのは人のレビュー後、`approve` 操作でのみ（自分で approved にしない） | contextdb `mutate`（review 強制・approve 分離） |
| R3 | 適用で新たな不整合が生じたまま正本が壊れる | `mutate apply` は適用後に全体を再検証し、**新たな error を生む plan は自動巻き戻し**（トランザクショナル） | contextdb `mutate`（fail-closed な適用） |
| R4 | 実装だけ進み正本が陳腐化する（ドリフト） | 実装の締めくくりに `sync-check` で乖離を列挙し、影響を判定してから同期。`--strict` で CI ゲート化できる | contextdb `sync-check --strict`（error 級で exit 1） |
| R5 | 仕様に影響しない変更まで正本へ書き、履歴を汚す | タイポ・リファクタ・テストのみ等は `.contextdb` を触らず「仕様影響なし」と根拠（sync-check 結果）を添えて報告する運用 | 手順そのもの（判定は人） |

context-sync 固有の攻撃面は無いため、上記は「攻撃者対策」ではなく**誤操作・陳腐化の
抑止**を担保する設計上の約束である。

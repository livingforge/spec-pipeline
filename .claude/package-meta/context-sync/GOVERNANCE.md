# Governance — context-sync

この手順スキルのバージョニング・互換性方針。変更履歴は [CHANGELOG.md](CHANGELOG.md)、
依存は [dependencies.md](dependencies.md)、前提とする安全性は
[threat-model.md](threat-model.md) を参照。

## バージョニング（SemVer）

context-sync は実行体を持たない**手順スキル**のため、互換性の対象は「手順そのもの」と
「前提とする contextdb のインターフェース」。

- **MAJOR** — 手順の破壊的変更（前提とする contextdb サブコマンド／出力の非互換な変更に
  追随した手順の作り直し）。
- **MINOR** — 後方互換な手順追加（新しい検出種別への対応、判定表への項目追加）。
- **PATCH** — 文言修正・説明の明確化。

## 前提とするインターフェース

- contextdb の `sync-check` / `mutate` / `engine` / `visualize` / `generate` / `history`
  の各サブコマンドと、`sync-check` の検出種別（`stale` / `unregistered` / `vanished` /
  `dead-path` / `dead-doc` / `stale-evidence`）。
- 乖離検出・棚卸しの規則は消費側プロジェクトの `.contextdb/sync.yaml` に宣言される。

これらが非互換に変わった場合は手順（SKILL.md）を追随させる。手順は特定の内部実装では
なく、上記の**公開サブコマンドと検出種別**にのみ依存させる。

## 位置づけ

context-sync は実装作業の締めくくりに実行し、実装だけ進んで正本（`.contextdb`）が
陳腐化するのを防ぐ運用手順。判断が要るのは「この実装変更は仕様の変化か」の判定と
出典の文章化だけで、候補列挙・適用・検証は contextdb ツールが機械化する。

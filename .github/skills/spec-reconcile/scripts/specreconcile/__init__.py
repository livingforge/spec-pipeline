"""specreconcile — 抽出ファクト (facts.json) の意味的な名寄せ・矛盾検出。

docextract → docagent で溜まった出典付きファクトは **文書ごとに独立** に積まれ
(``docagent.facts``)、同じ概念 (同じデータ項目・業務ルール等) が複数文書に出れば
重複ファクトがそのまま蓄積される。specdb エンジンは参照整合性・宣言済み制約
という **構造的一貫性** は保証するが、「別 ID が同一概念を指す」「相反する値を
主張する」といった **意味的一貫性** は検出しない。

このパッケージは facts.json → specdb の間に **提案生成ステップ** を挟む:

1. ブロッキング (``blocking``)   — LLM を使わず決定的に「同一かもしれない」候補を束ねる
2. LLM 裁定 (``adjudicate``)     — 候補クラスタを「同一概念」と「矛盾」に判定する
3. reconcile.json               — concept / contradiction / term_map の提案 (人がレビュー)
4. mutate plan (``plan``)        — 承認済み concept を specdb の add-item op へ決定的に落とす

①④が決定的 (文書の提示順に依存しない)、②のみ非決定的だが必ずレビューを通る提案、
という構造で「文書を順に渡すと畳み込みが揺れる」問題を 1 箇所に封じ込める。
出力はすべて specdb の ``status: review`` ゲートを通り、承認は人が行う。
"""

from __future__ import annotations

__version__ = "1.0.0"

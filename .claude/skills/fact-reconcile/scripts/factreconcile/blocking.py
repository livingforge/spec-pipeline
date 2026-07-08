"""① 候補クラスタ生成 — LLM を使わず決定的に「同一かもしれない」ファクトを束ねる。

名寄せ (エンティティ解決) を全ファクト総当たりで LLM に投げるのは非現実的
(O(n^2) の呼び出し)。ここでは **安価で決定的な** ブロッキングで「同じ概念を
指すかもしれない」集合に絞り、LLM の裁定 (``adjudicate``) は候補クラスタだけに
使う。ここは再現性が命なので乱数・時刻・並び順に依存せず、同じ facts なら
何度回しても同じクラスタを返す (文書の提示順にも依存しない)。

判定は同一 ``type`` 内でのみ行い (種別をまたぐ統合はしない)、次のいずれかで
2 ファクトを候補として連結する:

- キーワードを 1 つ以上共有する
- 一方の statement (畳んだ形) が他方を包含する (短い別名 ↔ 長い説明)
- statement の文字 2-gram Jaccard 類似度が閾値以上

連結を推移的にたどり (union-find)、2 件以上の塊だけをクラスタとして返す
(単独ファクトは重複の余地がないので LLM に送らない)。全角/半角・大小文字・
空白の揺れは :func:`docagent.store._fold_text` (NFKC + 小文字化 + 空白除去) で吸収する。
"""

from __future__ import annotations

from typing import Any

from docagent.store import _fold_text

# 文字 2-gram Jaccard 類似度がこの値以上なら候補として連結する。
# 候補生成は再現率重視 (取りこぼしを減らす)。精度は LLM 裁定側で担保する。
DEFAULT_THRESHOLD = 0.5
# これ未満のサイズの塊は「重複の余地なし」として LLM に送らない。
_MIN_CLUSTER = 2


def _fold(s: str) -> str:
    return _fold_text(s or "")[0]


def _bigrams(folded: str) -> set[str]:
    """畳んだ文字列の隣接 2 文字集合。1 文字以下ならその文字自体を 1 要素に。"""
    if len(folded) < 2:
        return {folded} if folded else set()
    return {folded[i:i + 2] for i in range(len(folded) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


class _UnionFind:
    """経路圧縮つき union-find。連結成分でクラスタを取り出すためだけに使う。"""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 常に小さい添字を根にして決定的にする。
            self.parent[max(ra, rb)] = min(ra, rb)


def _linked(fa: dict[str, Any], fb: dict[str, Any], threshold: float) -> bool:
    """2 ファクトを候補として連結するか (対称・決定的)。"""
    ka = {_fold(k) for k in (fa.get("keywords") or []) if str(k).strip()}
    kb = {_fold(k) for k in (fb.get("keywords") or []) if str(k).strip()}
    if ka & kb:
        return True
    sa, sb = fa["_folded"], fb["_folded"]
    if sa and sb and (sa in sb or sb in sa):
        return True
    return _jaccard(fa["_bigrams"], fb["_bigrams"]) >= threshold


def _prepare(fact: dict[str, Any]) -> dict[str, Any]:
    folded = _fold(fact.get("statement", ""))
    return {**fact, "_folded": folded, "_bigrams": _bigrams(folded)}


def _strip(fact: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fact.items() if not k.startswith("_")}


def candidate_clusters(
    facts: list[dict[str, Any]], threshold: float = DEFAULT_THRESHOLD
) -> list[list[dict[str, Any]]]:
    """同一かもしれないファクトの候補クラスタ (各 2 件以上) を決定的に返す。

    入力の並び順に依存しない: 種別ごとに ID 昇順で処理し、クラスタも
    メンバーも ID 昇順、クラスタ間は先頭メンバーの ID 昇順で整列する。
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_type.setdefault(fact.get("type", ""), []).append(fact)

    clusters: list[list[dict[str, Any]]] = []
    for _type, group in sorted(by_type.items()):
        prepared = [_prepare(f) for f in sorted(group, key=lambda f: f.get("id", ""))]
        uf = _UnionFind(len(prepared))
        for i in range(len(prepared)):
            for j in range(i + 1, len(prepared)):
                if _linked(prepared[i], prepared[j], threshold):
                    uf.union(i, j)
        buckets: dict[int, list[dict[str, Any]]] = {}
        for idx, fact in enumerate(prepared):
            buckets.setdefault(uf.find(idx), []).append(fact)
        for members in buckets.values():
            if len(members) >= _MIN_CLUSTER:
                cleaned = sorted((_strip(m) for m in members),
                                 key=lambda f: f.get("id", ""))
                clusters.append(cleaned)

    clusters.sort(key=lambda c: c[0].get("id", ""))
    return clusters

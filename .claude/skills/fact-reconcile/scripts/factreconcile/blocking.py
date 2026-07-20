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

これとは別に、**粒度差 (概要 vs 実装詳細) 候補**を拾う第二のパスがある
(:func:`refine_candidate_clusters`)。統合候補とは測り方も束ね方も違うので分けて
ある — 詳細はその docstring を参照。両方をまとめて 1 本の裁定入力にするのが
:func:`combined_clusters`。
"""

from __future__ import annotations

import re
from typing import Any

from docagent.store import _fold_text

# 文字 2-gram Jaccard 類似度がこの値以上なら候補として連結する。
# 候補生成は再現率重視 (取りこぼしを減らす)。精度は LLM 裁定側で担保する。
DEFAULT_THRESHOLD = 0.5
# これ未満のサイズの塊は「重複の余地なし」として LLM に送らない。
_MIN_CLUSTER = 2

# ── refine (粒度差) 候補の既定値 ──────────────────────────────
# 粒度差ペア (概要 vs 実装詳細) は文長が大きく違うため Jaccard (和集合で割る) では
# 拾えない。包含率 (小さい方で割る) を使い、閾値も低く取る。
#
# 0.10 という低さは意図的。無関係なペアは内容語ベースだと 0.000 にきれいに落ちる
# 一方、拾いたい粒度差ペアが 0.14 程度まで沈むため (概要と実装詳細は実質 1 語しか
# 共有しないことがある)、閾値を上げると取りこぼす。誤検出のコストは REFINE_TOP_K
# (1 アンカーあたりの上限) が抑え、最終的な精度は LLM 裁定が担保する
# — 「候補生成は recall、絞り込みは LLM」という分業。
REFINE_THRESHOLD = 0.10
# 1 アンカーにつき LLM に見せる近傍の数。閾値を低く取るぶん、ここが実質的な
# コスト上限になる (クラスタ数はアンカー数以下、1 クラスタは最大 K+1 件)。
REFINE_TOP_K = 4
# 粒度差が起こりうる「意図の層」だけを対象にする。骨格 (メソッド/データ項目など) は
# codescan が決定論で出しており、階層は has-method/has-column が既に表現している。
REFINE_TYPES = ("機能要件", "非機能要件", "業務ルール", "制約・前提", "外部インターフェース")


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
    return {**fact, "_folded": folded, "_bigrams": _bigrams(folded),
            "_terms": _terms(folded)}


def _strip(fact: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fact.items() if not k.startswith("_")}


def combined_clusters(
    facts: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
    *,
    refine: bool = True,
    refine_threshold: float = REFINE_THRESHOLD,
    refine_top_k: int = REFINE_TOP_K,
) -> tuple[list[list[dict[str, Any]]], list[str]]:
    """統合候補 + 粒度差候補をこの順で連結し ``(clusters, kinds)`` を返す。

    統合候補を **先** に置くのは cluster_id (``cl001`` …) を安定させるため。refine
    パスを足しても既存クラスタの番号は動かないので、過去の verdicts が読み替え
    なしで使える。``kinds`` は各クラスタの狙い (``"merge"`` / ``"refine"``) で、
    裁定プロンプトと emit-clusters の表示に使う。

    メンバー集合が統合候補と完全に一致する refine クラスタは落とす (同じ材料を
    二度裁定させない)。
    """
    merge = candidate_clusters(facts, threshold=threshold)
    clusters = list(merge)
    kinds = ["merge"] * len(merge)
    if not refine:
        return clusters, kinds

    seen = {frozenset(f.get("id", "") for f in c) for c in merge}
    for cluster in refine_candidate_clusters(
            facts, threshold=refine_threshold, top_k=refine_top_k):
        key = frozenset(f.get("id", "") for f in cluster)
        if key in seen:
            continue
        seen.add(key)
        clusters.append(cluster)
        kinds.append("refine")
    return clusters, kinds


def _containment(a: set[str], b: set[str]) -> float:
    """小さい方の集合で割る重なり率。粒度差 (短い概要 ⊂ 長い詳細) に効く。

    Jaccard は和集合で割るため、文長が大きく違うペアでは分母が膨らんで必ず低く出る
    (概要「名寄せ・矛盾検出ができる」と詳細「候補クラスタを LLM で裁定し…」は
    Jaccard では 0.08)。包含率なら「概要側の語がどれだけ詳細側に現れるか」を
    測れるので、統合ではなく **階層** の候補を拾える。
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / min(len(a), len(b))


# 内容語らしい連なり (漢字/カタカナ/英数) を拾う。助詞・記号は落とす。
_RUN = re.compile(r"[一-龥々]+|[ァ-ヴー]+|[a-z0-9_]+")


def _terms(folded: str) -> set[str]:
    """内容語とその 2-gram の集合。

    素の文字 2-gram を全文から採ると助詞・語尾 (「できる」「して」) が大量に混ざり、
    無関係な文どうしでも重なってしまう (ノイズで薄まる)。内容語の連なりの中だけで
    2-gram を採ると、無関係なペアは 0.0 に落ちて分離が効く。連なりそのもの
    (「矛盾検出」) も入れるので、部分一致 (「矛盾検出」↔「矛盾」) も拾える。
    """
    out: set[str] = set()
    for run in _RUN.findall(folded):
        if len(run) >= 2:
            out.add(run)
            out |= {run[i:i + 2] for i in range(len(run) - 1)}
        elif run:
            out.add(run)
    return out


def _refine_score(fa: dict[str, Any], fb: dict[str, Any]) -> float:
    """粒度差候補としての近さ (対称・決定的)。2 経路の最大値。

    - キーワード包含: 抽出時に人/LLM が選んだ語なので精度が高い
    - 内容語包含: 語彙が重なるペアを拾う

    IDF 重み付けも試したが、共有語の重みより分母 (自分の全語の重み) の伸びが
    勝って素の包含率を下回り続けたため採らない (無関係ペアは素の包含率で既に
    0.0 に落ちており、希少語で押し上げる必要が無い)。
    """
    ka = {_fold(k) for k in (fa.get("keywords") or []) if str(k).strip()}
    kb = {_fold(k) for k in (fb.get("keywords") or []) if str(k).strip()}
    kw = _containment(ka, kb) if (ka and kb) else 0.0
    return max(kw, _containment(fa["_terms"], fb["_terms"]))


def refine_candidate_clusters(
    facts: list[dict[str, Any]],
    threshold: float = REFINE_THRESHOLD,
    top_k: int = REFINE_TOP_K,
    types: tuple[str, ...] = REFINE_TYPES,
) -> list[list[dict[str, Any]]]:
    """粒度差 (上位/下位) 候補のクラスタを再現率重視で返す。

    :func:`candidate_clusters` (統合候補) とは狙いが違うので別パスにする:

    - **連結成分にしない**。緩い閾値で union-find を回すと全部が 1 個の巨大クラスタに
      潰れて裁定不能になる。代わりに各ファクトを *アンカー* とし、その上位 ``top_k``
      近傍だけを 1 クラスタにする (アンカー + 近傍 = 小さく濃い候補)。
    - **包含率**で測る (:func:`_containment`)。粒度差は文長差が大きい。
    - 対象は意図の層 (``types``) のみ。同一 ``type`` 内でのみ候補にする。

    メンバー集合が同じクラスタは 1 つに畳む。並びは決定的 (ID 昇順)。
    """
    wanted = set(types)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        ftype = fact.get("type", "")
        if ftype in wanted:
            by_type.setdefault(ftype, []).append(fact)

    clusters: list[list[dict[str, Any]]] = []
    seen: set[frozenset[str]] = set()
    for _type, group in sorted(by_type.items()):
        prepared = [_prepare(f) for f in sorted(group, key=lambda f: f.get("id", ""))]
        for i, anchor in enumerate(prepared):
            scored = []
            for j, other in enumerate(prepared):
                if i == j:
                    continue
                score = _refine_score(anchor, other)
                if score >= threshold:
                    # 同点は ID 昇順で決定的に並べる (スコア降順 → ID 昇順)。
                    scored.append((-score, other.get("id", ""), j))
            if not scored:
                continue
            scored.sort()
            members = [anchor] + [prepared[j] for _s, _id, j in scored[:top_k]]
            key = frozenset(m.get("id", "") for m in members)
            if len(key) < _MIN_CLUSTER or key in seen:
                continue
            seen.add(key)
            clusters.append(sorted((_strip(m) for m in members),
                                   key=lambda f: f.get("id", "")))

    clusters.sort(key=lambda c: [f.get("id", "") for f in c])
    return clusters


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

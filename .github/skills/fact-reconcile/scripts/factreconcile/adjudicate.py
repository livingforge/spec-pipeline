"""② LLM 裁定 — 候補クラスタを「同一概念のグループ」と「矛盾」に判定する。

ブロッキング (``blocking``) が束ねた候補クラスタは「同じかもしれない」だけの
集合なので、ここで LLM が **意味の同一判定** を行う。判定は接地必須で、
ファクトの本文・原文にある情報だけを根拠にし、根拠のない統合はしない。値が
食い違うメンバーは勝手にどちらか選ばず **矛盾** として別に報告する
(contextdb / doc-author の「矛盾なら停止」方針に合わせる)。

LLM 呼び出しは docsummary の :mod:`docsummary.providers` /
:mod:`docsummary.settings` をそのまま再利用する (プロバイダ切替・``.env`` 共有)。
出力は reconcile.json (concept / contradiction / term_map)。同じ facts なら
再実行で同じ結果になるよう、``generated_from`` に facts の内容ハッシュと
プロンプト版を刻み、``cli`` 側でキャッシュ判定に使う。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from docsummary import providers
from docsummary.settings import LLMConfig

# プロンプトを変えたら必ず上げる。reconcile.json のキャッシュ鮮度に効く。
PROMPT_VERSION = "fact-reconcile/2"

# LLM 応答は JSON のみを要求する。フォーマットは指定するが「何を同一とみなすか」は
# 与えない (ファクトの記述だけで判断させる)。
SYSTEM_PROMPT = (
    "あなたは仕様・要件の名寄せ (エンティティ解決) を行う専門家である。"
    "与えられた同種のファクト群について次を判定する:\n"
    "1. 同一の概念 (同じデータ項目・業務ルール・画面など) を指すファクトを"
    "グループ (concept) にまとめる。別概念なら別グループにする。\n"
    "2. 同一 concept 内で値が食い違う場合は、勝手にどちらかを選ばず"
    " contradiction として別に報告する。\n"
    "3. 一方が他方の **上位概念 (包含関係)** である場合は統合せず refinement として"
    "報告する。判定基準: 記述内容が矛盾せず、一方が「何ができるか」を広く述べ"
    "(概要)、他方がその実現手段・条件・内訳を具体的に述べている (実装詳細) なら"
    "refinement である。parent には概要側、child には詳細側を置く。\n"
    "同一 (言い換えにすぎない) なら concept に、包含なら refinement に入れる。"
    "両方には入れない。判断が付かない場合はどちらにも入れない。\n"
    "根拠のない統合・階層化はしない。ファクトの statement・evidence にある情報だけで"
    "判断し、推測で補わない。\n"
    "出力は JSON のみ。前置き・後書き・コードフェンス (```) を付けない。形式:\n"
    '{"concepts":[{"member_fact_ids":["f0003","f0041"],'
    '"canonical_term":"正準的な名称・短い見出し",'
    '"canonical_statement":"メンバーの記述を統合した中立な1文",'
    '"variants":["表記ゆれ1","表記ゆれ2"]}],'
    '"contradictions":[{"fact_ids":["f0007","f0055"],'
    '"issue":"何がどう食い違うか",'
    '"claims":[{"fact_id":"f0007","position":"そのファクトの主張"}]}],'
    '"refinements":[{"parent_fact_id":"f0012","child_fact_id":"f0034",'
    '"rationale":"親の概要を子が具体化している根拠"}]}\n'
    "member_fact_ids・parent_fact_id・child_fact_id には入力に無い ID を書かない。"
    "単独の概念 (他と統合しないファクト) は member 1 件の concept として返してよい。"
)


def facts_hash(facts: list[dict[str, Any]]) -> str:
    """名寄せに効くフィールドだけを正準化した内容ハッシュ (並び順非依存)。"""
    rows = sorted(
        (
            {
                "id": f.get("id"),
                "type": f.get("type"),
                "statement": f.get("statement"),
                "evidence": f.get("evidence"),
                "keywords": sorted(f.get("keywords") or []),
            }
            for f in facts
        ),
        key=lambda r: r["id"] or "",
    )
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_KIND_HINT = {
    "merge": "このクラスタは語彙の重なりで束ねた **統合候補** である。",
    "refine": "このクラスタは包含率で束ねた **粒度差候補** である。"
              "同一物でなくても、一方が他方の概要になっていないかを特に確認する。",
}


def _cluster_prompt(cluster: list[dict[str, Any]], kind: str = "merge") -> str:
    lines = ["同一種別のファクト群 (同じ概念が複数含まれ得る):"]
    hint = _KIND_HINT.get(kind)
    if hint:
        lines.append(hint)
    lines.append("")
    for f in cluster:
        loc = json.dumps(f.get("location") or {}, ensure_ascii=False)
        kws = "、".join(f.get("keywords") or [])
        lines.append(f"- id: {f.get('id')}")
        lines.append(f"  type: {f.get('type')}")
        lines.append(f"  statement: {f.get('statement', '')}")
        if f.get("evidence"):
            lines.append(f"  evidence: {f['evidence']}")
        lines.append(f"  location: {loc}")
        if kws:
            lines.append(f"  keywords: {kws}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 応答から JSON オブジェクトを取り出す (コードフェンス等に耐性)。"""
    s = text.strip()
    if s.startswith("```"):
        # ```json ... ``` を剥がす
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


def _source_of(fact: dict[str, Any]) -> dict[str, Any]:
    """ファクトの出典を contextdb の source 形式へ写経する (言い換えない)。"""
    src: dict[str, Any] = {"doc": fact.get("doc_id")}
    if fact.get("location"):
        src["location"] = fact["location"]
    if fact.get("evidence"):
        src["evidence"] = fact["evidence"]
    return src


def cluster_id(index: int) -> str:
    """クラスタの安定 ID。emit-clusters と verdicts の突き合わせ鍵。

    ブロッキング (``blocking.candidate_clusters``) は決定的な順序でクラスタを返す
    ため、同じ facts なら emit-clusters と verdicts 経路で同じ番号が割り当たる。
    """
    return f"cl{index + 1:03d}"


def _ground_verdict(
    data: dict[str, Any], cluster: list[dict[str, Any]]
) -> dict[str, Any]:
    """裁定結果 (LLM でも外部でも同形) を接地する共通処理。

    入力クラスタに無い ID は捨てる (接地の安全弁)。LLM 経路も外部 verdicts 経路も
    この 1 箇所を通すことで、採番・出典写経の前段で同じ検証を受ける。
    """
    valid_ids = {f.get("id") for f in cluster}
    concepts = []
    for c in data.get("concepts") or []:
        members = [i for i in (c.get("member_fact_ids") or []) if i in valid_ids]
        if not members:
            continue
        concepts.append({
            "member_fact_ids": sorted(members),
            "canonical_term": (c.get("canonical_term") or "").strip(),
            "canonical_statement": (c.get("canonical_statement") or "").strip(),
            "variants": [v for v in (c.get("variants") or []) if str(v).strip()],
        })

    contradictions = []
    for c in data.get("contradictions") or []:
        ids = [i for i in (c.get("fact_ids") or []) if i in valid_ids]
        if len(ids) < 2:
            continue
        claims = [
            {"fact_id": cl.get("fact_id"), "position": (cl.get("position") or "").strip()}
            for cl in (c.get("claims") or [])
            if cl.get("fact_id") in valid_ids
        ]
        contradictions.append({
            "fact_ids": sorted(ids),
            "issue": (c.get("issue") or "").strip(),
            "claims": claims,
        })

    refinements = []
    seen_pairs: set[tuple[str, str]] = set()
    for r in data.get("refinements") or []:
        parent, child = r.get("parent_fact_id"), r.get("child_fact_id")
        if parent not in valid_ids or child not in valid_ids or parent == child:
            continue
        # 相互 refine (a→b と b→a) は階層として成立しないので先勝ちで 1 本に畳む。
        if (parent, child) in seen_pairs or (child, parent) in seen_pairs:
            continue
        seen_pairs.add((parent, child))
        refinements.append({
            "parent_fact_id": parent,
            "child_fact_id": child,
            "rationale": (r.get("rationale") or "").strip(),
        })

    return {
        "concepts": concepts,
        "contradictions": contradictions,
        "refinements": refinements,
    }


def adjudicate_cluster(
    cfg: LLMConfig,
    cluster: list[dict[str, Any]],
    *,
    kind: str = "merge",
    complete: Callable[..., str] = providers.complete,
    max_output_tokens: int = 4096,
    timeout: float = providers.DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """1 クラスタを LLM で裁定し concepts / contradictions / refinements を返す。

    JSON が壊れていたらプロンプトを補強して 1 回だけ再試行する。接地 (入力に無い
    ID を捨てる) は :func:`_ground_verdict` が行う。``kind`` はブロッキングがその
    クラスタを束ねた狙い (``"merge"`` / ``"refine"``) で、プロンプトの着眼点に使う。
    """
    user = _cluster_prompt(cluster, kind)
    text = complete(cfg, SYSTEM_PROMPT, user,
                    max_output_tokens=max_output_tokens, timeout=timeout)
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, IndexError):
        text = complete(cfg, SYSTEM_PROMPT,
                        user + "\n\n応答は JSON オブジェクトのみにすること。",
                        max_output_tokens=max_output_tokens, timeout=timeout)
        data = _extract_json(text)
    return _ground_verdict(data, cluster)


def emit_clusters(
    facts: list[dict[str, Any]],
    clusters: list[list[dict[str, Any]]],
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    """候補クラスタを本文付きで書き出す (LLM 不要)。

    呼び出し元エージェント (Claude) が裁定材料にする。各クラスタに安定 ID
    (:func:`cluster_id`) を振り、``verdicts`` 経路で突き合わせられるようにする。
    ``generated_from`` は reconcile.json と同じ鮮度鍵。
    """
    out_clusters = []
    for idx, cluster in enumerate(clusters):
        members = [
            {
                "id": f.get("id"),
                "type": f.get("type"),
                "statement": f.get("statement", ""),
                "evidence": f.get("evidence"),
                "location": f.get("location") or {},
                "keywords": f.get("keywords") or [],
            }
            for f in cluster
        ]
        out_clusters.append({
            "cluster_id": cluster_id(idx),
            "fact_type": cluster[0].get("type", "") if cluster else "",
            # 束ねた狙い。裁定する側が「統合を疑う」か「粒度差を疑う」かの手がかり。
            "kind": (kinds[idx] if kinds and idx < len(kinds) else "merge"),
            "members": members,
        })
    return {
        "version": 1,
        "generated_from": {
            "facts_hash": facts_hash(facts),
            "prompt_version": PROMPT_VERSION,
        },
        "clusters": out_clusters,
    }


def build_reconcile(
    cfg: LLMConfig | None,
    facts: list[dict[str, Any]],
    clusters: list[list[dict[str, Any]]],
    *,
    kinds: list[str] | None = None,
    complete: Callable[..., str] = providers.complete,
    verdicts: dict[str, dict[str, Any]] | None = None,
    max_output_tokens: int = 4096,
    timeout: float = providers.DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """全クラスタを裁定して reconcile.json (提案アーティファクト) を組み立てる。

    ``concepts`` には **2 件以上を統合した提案だけ** を載せる (単独に割れた
    ファクトは重複ではないので載せない — 通常どおり doc-author が扱う)。
    各 concept には出典 (member 全員の doc/location/evidence) を写経する。

    ``verdicts`` を渡すと LLM を呼ばず、外部裁定 (cluster_id → 裁定) を採用する
    (呼び出し元エージェントが裁定する Claude 経路)。裁定の接地・採番・出典写経は
    LLM 経路と同一のコードを通る。裁定の無いクラスタは統合なしとして扱う。
    """
    by_id = {f.get("id"): f for f in facts}
    concepts: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    raw_refinements: list[dict[str, Any]] = []
    counter = 0

    for idx, cluster in enumerate(clusters):
        if verdicts is not None:
            raw = verdicts.get(cluster_id(idx)) or {}
            verdict = _ground_verdict(raw, cluster)
        else:
            verdict = adjudicate_cluster(
                cfg, cluster,
                kind=(kinds[idx] if kinds and idx < len(kinds) else "merge"),
                complete=complete,
                max_output_tokens=max_output_tokens, timeout=timeout)
        # クラスタ内は全メンバー同一種別なので type は先頭から採る。
        fact_type = cluster[0].get("type", "")
        for c in verdict["concepts"]:
            members = c["member_fact_ids"]
            if len(members) < 2:
                continue  # 単独は重複ではない → 提案しない
            counter += 1
            sources = [_source_of(by_id[i]) for i in members if i in by_id]
            concepts.append({
                "concept_id": f"c{counter:03d}",
                "fact_type": fact_type,
                "canonical_term": c["canonical_term"],
                "canonical_statement": c["canonical_statement"],
                "member_fact_ids": members,
                "variants": c["variants"],
                "sources": sources,
            })
        for con in verdict["contradictions"]:
            contradictions.append({**con, "fact_type": fact_type})
        raw_refinements.extend(verdict.get("refinements") or [])

    refinements = _collect_refinements(raw_refinements, by_id, concepts)

    term_map = []
    for c in concepts:
        variants = sorted({v for v in c["variants"] if v and v != c["canonical_term"]})
        if variants:
            term_map.append({"variants": variants, "canonical": c["canonical_term"]})

    return {
        "version": 1,
        "generated_from": {
            "facts_hash": facts_hash(facts),
            "prompt_version": PROMPT_VERSION,
        },
        "concepts": concepts,
        "contradictions": contradictions,
        "refinements": refinements,
        "term_map": term_map,
    }


def _collect_refinements(
    raw: list[dict[str, Any]],
    by_id: dict[Any, dict[str, Any]],
    concepts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """クラスタ横断で refinement を畳み、出典と種別を付けて確定させる。

    recall 重視のブロッキングは同じペアを複数のアンカーから提案しうるので重複を
    畳む。**統合済み concept の内部ペアは落とす** — 同一物と判定されたものを
    親子にすると、統合と階層が二重に張られて矛盾するため (統合が優先)。
    """
    merged: set[frozenset[Any]] = set()
    for c in concepts:
        members = c.get("member_fact_ids") or []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                merged.add(frozenset((a, b)))

    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for r in raw:
        parent, child = r.get("parent_fact_id"), r.get("child_fact_id")
        if parent not in by_id or child not in by_id:
            continue
        if (parent, child) in seen or (child, parent) in seen:
            continue
        if frozenset((parent, child)) in merged:
            continue
        seen.add((parent, child))
        out.append({
            "parent_fact_id": parent,
            "child_fact_id": child,
            "parent_type": by_id[parent].get("type", ""),
            "child_type": by_id[child].get("type", ""),
            "rationale": r.get("rationale", ""),
            "sources": [_source_of(by_id[parent]), _source_of(by_id[child])],
        })
    out.sort(key=lambda r: (str(r["parent_fact_id"]), str(r["child_fact_id"])))
    return out


def is_fresh(reconcile: dict[str, Any], facts: list[dict[str, Any]]) -> bool:
    """既存 reconcile.json が現在の facts + プロンプト版と一致しているか。"""
    gen = reconcile.get("generated_from") or {}
    return (
        gen.get("facts_hash") == facts_hash(facts)
        and gen.get("prompt_version") == PROMPT_VERSION
    )

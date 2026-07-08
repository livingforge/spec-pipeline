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
PROMPT_VERSION = "fact-reconcile/1"

# LLM 応答は JSON のみを要求する。フォーマットは指定するが「何を同一とみなすか」は
# 与えない (ファクトの記述だけで判断させる)。
SYSTEM_PROMPT = (
    "あなたは仕様・要件の名寄せ (エンティティ解決) を行う専門家である。"
    "与えられた同種のファクト群について次を判定する:\n"
    "1. 同一の概念 (同じデータ項目・業務ルール・画面など) を指すファクトを"
    "グループ (concept) にまとめる。別概念なら別グループにする。\n"
    "2. 同一 concept 内で値が食い違う場合は、勝手にどちらかを選ばず"
    " contradiction として別に報告する。\n"
    "根拠のない統合はしない。ファクトの statement・evidence にある情報だけで"
    "判断し、推測で補わない。\n"
    "出力は JSON のみ。前置き・後書き・コードフェンス (```) を付けない。形式:\n"
    '{"concepts":[{"member_fact_ids":["f0003","f0041"],'
    '"canonical_term":"正準的な名称・短い見出し",'
    '"canonical_statement":"メンバーの記述を統合した中立な1文",'
    '"variants":["表記ゆれ1","表記ゆれ2"]}],'
    '"contradictions":[{"fact_ids":["f0007","f0055"],'
    '"issue":"何がどう食い違うか",'
    '"claims":[{"fact_id":"f0007","position":"そのファクトの主張"}]}]}\n'
    "member_fact_ids には入力に無い ID を書かない。単独の概念 (他と統合しない"
    "ファクト) は member 1 件の concept として返してよい。"
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


def _cluster_prompt(cluster: list[dict[str, Any]]) -> str:
    lines = ["同一種別のファクト群 (同じ概念が複数含まれ得る):", ""]
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


def adjudicate_cluster(
    cfg: LLMConfig,
    cluster: list[dict[str, Any]],
    *,
    complete: Callable[..., str] = providers.complete,
    max_output_tokens: int = 4096,
    timeout: float = providers.DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """1 クラスタを裁定し ``{"concepts":[...], "contradictions":[...]}`` を返す。

    LLM が入力に無い ID を返しても捨てる (接地の安全弁)。JSON が壊れていたら
    プロンプトを補強して 1 回だけ再試行する。
    """
    valid_ids = {f.get("id") for f in cluster}
    user = _cluster_prompt(cluster)
    text = complete(cfg, SYSTEM_PROMPT, user,
                    max_output_tokens=max_output_tokens, timeout=timeout)
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, IndexError):
        text = complete(cfg, SYSTEM_PROMPT,
                        user + "\n\n応答は JSON オブジェクトのみにすること。",
                        max_output_tokens=max_output_tokens, timeout=timeout)
        data = _extract_json(text)

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
    return {"concepts": concepts, "contradictions": contradictions}


def build_reconcile(
    cfg: LLMConfig,
    facts: list[dict[str, Any]],
    clusters: list[list[dict[str, Any]]],
    *,
    complete: Callable[..., str] = providers.complete,
    max_output_tokens: int = 4096,
    timeout: float = providers.DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """全クラスタを裁定して reconcile.json (提案アーティファクト) を組み立てる。

    ``concepts`` には **2 件以上を統合した提案だけ** を載せる (単独に割れた
    ファクトは重複ではないので載せない — 通常どおり doc-author が扱う)。
    各 concept には出典 (member 全員の doc/location/evidence) を写経する。
    """
    by_id = {f.get("id"): f for f in facts}
    concepts: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    counter = 0

    for cluster in clusters:
        verdict = adjudicate_cluster(
            cfg, cluster, complete=complete,
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
        "term_map": term_map,
    }


def is_fresh(reconcile: dict[str, Any], facts: list[dict[str, Any]]) -> bool:
    """既存 reconcile.json が現在の facts + プロンプト版と一致しているか。"""
    gen = reconcile.get("generated_from") or {}
    return (
        gen.get("facts_hash") == facts_hash(facts)
        and gen.get("prompt_version") == PROMPT_VERSION
    )

"""④ contextdb mutate plan 生成 — 承認済み concept を add-item op へ決定的に落とす。

reconcile.json の concept (統合された正準概念) を、ターゲット contextdb の
メタモデルに合わせた ``{"ops":[{"op":"add-item", ...}]}`` へ変換する。実適用は
行わず、既存の ``contextdb mutate apply`` (トランザクション + ``status: review``
ゲート + 再検証) に委ねる。ここは決定的で、同じ reconcile.json + 同じ
メタモデルなら同じ plan を返す。

ファクトの種別 (機能要件 / データ項目 / 業務ルール …) と contextdb のアイテム種別は
別語彙なので、メタモデル各種別の ``label`` と突き合わせて対応づける
(:func:`docagent.store._resolve_term` で表記揺れを吸収)。ファクトは contextdb が
要求する構造化属性 (enum の type、一意な physical_name 等) を持たないため、
**埋められない必須属性が残る concept は plan に載せず skipped として報告する**
(engine error 0 を保つ)。それらは人が doc-author で補完する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docagent.store import DocAgentError, _resolve_term


def load_item_types(metamodel_path: str | Path) -> dict[str, dict[str, Any]]:
    """メタモデル YAML から item_types 宣言を読む。"""
    import yaml  # 遅延 import: plan 以外 (analyze/review) は PyYAML 不要にする

    path = Path(metamodel_path)
    if not path.is_file():
        raise DocAgentError(
            f"メタモデルが見つかりません: {path}。"
            " --metamodel でパスを指定するか --root で .contextdb を指定してください"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("item_types") or {}


def map_fact_type(fact_type: str,
                  item_types: dict[str, dict[str, Any]]) -> str | None:
    """ファクト種別を contextdb のアイテム種別キーへ対応づける (label 突合)。

    対応が付かなければ None (plan では skip する)。
    """
    labels = {t.get("label", ""): key for key, t in item_types.items() if t.get("label")}
    if not labels:
        return None
    try:
        label = _resolve_term(fact_type, list(labels), label="アイテム種別")
    except DocAgentError:
        return None
    return labels.get(label)


def _fill_attrs(concept: dict[str, Any],
                tdef: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """concept から埋められる属性を作り、埋められない必須属性名を返す。

    label_field には正準的な見出しを入れる (statement 系フィールドなら
    canonical_statement、それ以外は canonical_term)。宣言に description が
    あれば canonical_statement を入れる。それ以外の必須属性は埋められない。
    """
    declared: dict[str, Any] = tdef.get("attributes") or {}
    label_field = tdef.get("label_field")
    term = concept.get("canonical_term") or ""
    statement = concept.get("canonical_statement") or ""

    attrs: dict[str, Any] = {}
    if label_field and label_field in declared:
        attrs[label_field] = statement if label_field == "statement" else term
    if "description" in declared and "description" not in attrs:
        if statement:
            attrs["description"] = statement

    required = [a for a, spec in declared.items()
                if isinstance(spec, dict) and spec.get("required")]
    missing = [a for a in required if a not in attrs or attrs[a] == ""]
    return attrs, missing


def build_plan(reconcile: dict[str, Any],
               metamodel_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """reconcile.json → (mutate plan, skipped 一覧)。

    plan には全必須属性を埋められた concept の add-item op だけを載せる
    (ID は種別接頭辞 + concept_id で決定的: 例 br-c001)。skipped には理由を付ける。
    """
    item_types = load_item_types(metamodel_path)
    ops: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for concept in reconcile.get("concepts") or []:
        cid = concept.get("concept_id", "")
        target = map_fact_type(concept.get("fact_type", ""), item_types)
        if target is None:
            skipped.append({
                "concept_id": cid,
                "reason": f"種別 '{concept.get('fact_type')}' に対応する"
                          " contextdb アイテム種別がメタモデルにありません",
            })
            continue
        attrs, missing = _fill_attrs(concept, item_types[target])
        if missing:
            skipped.append({
                "concept_id": cid,
                "type": target,
                "reason": "ファクトから埋められない必須属性があります: "
                          + "、".join(missing) + " (doc-author で補完してください)",
            })
            continue
        ops.append({
            "op": "add-item",
            "type": target,
            "slug": cid,
            "attrs": attrs,
            "source": concept.get("sources") or [],
            "status": "review",
        })

    return {"ops": ops}, skipped

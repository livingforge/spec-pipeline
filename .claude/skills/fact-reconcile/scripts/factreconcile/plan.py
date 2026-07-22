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

import sys
from pathlib import Path
from typing import Any

from docagent.store import DocAgentError, _resolve_term


_PACK_SPEC = "@"


def _find_pack_metamodel(name: str, root: Path) -> Path | None:
    """標準パックの metamodel を探す (contextdb の探索順を踏襲)。

    fact-reconcile スキルは contextdb を同梱しないので ``standard.resolve_chain`` を
    import できない。ここは **item_types を読むだけ** の軽量な解決に留める
    (版の厳密照合・循環検出は contextdb 側の責務。plan が誤っても
    ``contextdb mutate apply`` が再検証して弾く)。
    """
    import os

    candidates = [root / "packs" / name]
    for p in os.environ.get("CONTEXTDB_PACK_PATH", "").split(os.pathsep):
        if p:
            candidates.append(Path(p) / name)
    # 開発リポ (contextdb/packs/) と展開済みスキル (…/contextdb/scripts/packs/) の
    # 両レイアウトを、このファイルの位置から上へ辿って探す。
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "contextdb" / "packs" / name)
        candidates.append(parent / "contextdb" / "scripts" / "packs" / name)
    for d in candidates:
        mm = d / "metamodel" / "core.yaml"
        if (d / "pack.yaml").is_file() and mm.is_file():
            return mm
    return None


def _merge_item_types(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """パックの宣言にプロジェクト側の追加・厳格化を重ねる (属性単位)。"""
    merged = {k: dict(v) for k, v in base.items()}
    for key, tdef in (over or {}).items():
        if key not in merged:
            merged[key] = dict(tdef)
            continue
        cur = merged[key]
        attrs = {**(cur.get("attributes") or {}), **((tdef or {}).get("attributes") or {})}
        cur.update({k: v for k, v in (tdef or {}).items() if k != "attributes"})
        if attrs:
            cur["attributes"] = attrs
    return merged


def load_item_types(metamodel_path: str | Path) -> dict[str, dict[str, Any]]:
    """メタモデル YAML から item_types 宣言を読む (``extends`` を解決する)。

    消費側プロジェクトの metamodel.yaml は標準パックを ``extends`` し、種別の本体は
    パック側にある。ここで解決しないと item_types がほぼ空になり、どのファクト種別も
    対応付かず plan が常に空になる。
    """
    import yaml  # 遅延 import: plan 以外 (analyze/review) は PyYAML 不要にする

    path = Path(metamodel_path)
    if not path.is_file():
        raise DocAgentError(
            f"メタモデルが見つかりません: {path}。"
            " --metamodel でパスを指定するか --root で .contextdb を指定してください"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    item_types = data.get("item_types") or {}

    spec = data.get("extends")
    root = path.parent
    seen: set[str] = set()
    while spec:
        name = str(spec).split(_PACK_SPEC)[0].strip()
        if not name or name in seen:
            break
        seen.add(name)
        pack_mm = _find_pack_metamodel(name, root)
        if pack_mm is None:
            raise DocAgentError(
                f"継承元パック '{spec}' を解決できません。"
                " contextdb スキルを同じプロジェクトに展開するか、"
                " --metamodel で実効メタモデルを直接指定してください")
        pdata = yaml.safe_load(pack_mm.read_text(encoding="utf-8")) or {}
        item_types = _merge_item_types(pdata.get("item_types") or {}, item_types)
        spec = pdata.get("extends")
    return item_types


def map_fact_type(fact_type: str,
                  item_types: dict[str, dict[str, Any]]) -> str | None:
    """ファクト種別を contextdb のアイテム種別キーへ対応づける (label 突合)。

    対応が付かなければ None (plan では skip する)。区分つきの種別
    (「機能要件」= 区分「機能」+ label「要件」) は :func:`resolve_fact_type` が扱う。
    """
    labels = {t.get("label", ""): key for key, t in item_types.items() if t.get("label")}
    if not labels:
        return None
    try:
        label = _resolve_term(fact_type, list(labels), label="アイテム種別")
    except DocAgentError:
        return None
    return labels.get(label)


def _match_qualified(fact_type: str,
                     item_types: dict[str, dict[str, Any]]
                     ) -> tuple[str, dict[str, Any]] | None:
    """「区分 + label」形のファクト種別を (種別キー, 区分属性) へ分解する。

    ファクト側の語彙は contextdb の種別より細かいことがある。「機能要件」「非機能要件」は
    どちらも contextdb では ``requirement`` (label「要件」) で、違いは ``kind`` 属性の
    enum 値 (機能 / 非機能) に入る。label 突合だけでは「機能要件」と「要件」が
    結び付かない (前方一致せず difflib も 0.8 に届かない) ため、**enum 値 + label で
    綴られた種別名** を宣言から復元する。

    ハードコードした対応表ではなくメタモデルの宣言 (label と enum values) から導くので、
    パックを差し替えても同じ規則で効く。
    """
    for key, tdef in item_types.items():
        label = tdef.get("label")
        if not label or not fact_type.endswith(label):
            continue
        qualifier = fact_type[: -len(label)]
        if not qualifier:
            continue  # label そのもの (= 区分なし)。label 突合の担当。
        for attr, spec in (tdef.get("attributes") or {}).items():
            if not isinstance(spec, dict) or spec.get("kind") != "enum":
                continue
            for value in spec.get("values") or []:
                if qualifier == str(value):
                    return key, {attr: value}
    return None


def resolve_fact_type(fact_type: str,
                      item_types: dict[str, dict[str, Any]]
                      ) -> tuple[str | None, dict[str, Any]]:
    """ファクト種別 → (アイテム種別キー, 種別名から導ける属性)。

    label 突合を先に試し、付かなければ「区分 + label」形として解釈する。
    """
    target = map_fact_type(fact_type, item_types)
    if target is not None:
        return target, {}
    qualified = _match_qualified(fact_type, item_types)
    if qualified is not None:
        return qualified
    return None, {}


def _fill_attrs(concept: dict[str, Any],
                tdef: dict[str, Any],
                extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    """concept から埋められる属性を作り、埋められない必須属性名を返す。

    埋める順に:

    - ``label_field`` に正準的な見出し (statement 系フィールドなら canonical_statement、
      それ以外は canonical_term)
    - ``extra`` — 種別名から導けた属性 (「機能要件」→ ``kind: 機能``)
    - ``statement`` が宣言にあれば canonical_statement。**required なのに埋めずに
      保留していた**ので必ず入れる (label_field が name の種別で落ちていた)
    - ``description`` は statement 属性を持たない種別のときだけ canonical_statement
      (両方に同じ本文を入れて重複させない)

    ``sequence`` で自動採番される属性は「埋められない必須」に数えない。採番は
    ``contextdb mutate`` が適用時に行うので、ここで埋める必要も手段も無い
    (数えてしまうと全種別が保留になり plan が常に空になる)。
    """
    declared: dict[str, Any] = tdef.get("attributes") or {}
    label_field = tdef.get("label_field")
    term = concept.get("canonical_term") or ""
    statement = concept.get("canonical_statement") or ""

    attrs: dict[str, Any] = {}
    if label_field and label_field in declared:
        attrs[label_field] = statement if label_field == "statement" else term
    for key, value in (extra or {}).items():
        if key in declared and key not in attrs:
            attrs[key] = value
    if "statement" in declared and "statement" not in attrs and statement:
        attrs["statement"] = statement
    if ("description" in declared and "description" not in attrs
            and "statement" not in declared and statement):
        attrs["description"] = statement

    auto = (tdef.get("sequence") or {}).get("attribute")
    required = [a for a, spec in declared.items()
                if isinstance(spec, dict) and spec.get("required") and a != auto]
    missing = [a for a in required if a not in attrs or attrs[a] == ""]
    return attrs, missing


def _refine_ops(reconcile: dict[str, Any],
                fact_map: dict[str, str] | None,
                ops: list[dict[str, Any]],
                skipped: list[dict[str, Any]]) -> None:
    """refinement を ``child refines parent`` の add-relation op に落とす。

    エッジの両端は **contextdb のアイテム ID** でなければならないが、refinement の
    両端は個々のファクト (統合されなかったので concept にもならない) なので、
    fact_id → item_id の対応は doc-author が採番するまで決まらない。よって
    ``fact_map`` が与えられ、両端とも解決できるものだけを op にし、残りは
    「doc-author で張る」として skipped に積む (統合と違い両アイテムは残るので、
    ここで落としても情報は失われない)。
    """
    fmap = fact_map or {}
    for r in reconcile.get("refinements") or []:
        parent, child = r.get("parent_fact_id", ""), r.get("child_fact_id", "")
        src, dst = fmap.get(child), fmap.get(parent)
        if not src or not dst:
            unresolved = [f for f, i in ((child, src), (parent, dst)) if not i]
            skipped.append({
                "concept_id": f"refine:{child}->{parent}",
                "code": "refine-no-fact-map",
                "reason": "ファクトに対応する contextdb アイテム ID が未確定です: "
                          + "、".join(unresolved)
                          + " (--fact-map で与えるか doc-author で張ってください)",
            })
            continue
        ops.append({
            "op": "add-relation",
            "type": "refines",
            "from": src,
            "to": dst,
            "source": r.get("sources") or [],
            "status": "review",
        })


def build_plan(reconcile: dict[str, Any],
               metamodel_path: str | Path,
               fact_map: dict[str, str] | None = None,
               ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """reconcile.json → (mutate plan, skipped 一覧)。

    plan には全必須属性を埋められた concept の add-item op と、両端の解決できた
    refinement の add-relation op を載せる (ID は種別接頭辞 + concept_id で決定的:
    例 br-c001)。skipped には理由を付ける。
    """
    item_types = load_item_types(metamodel_path)
    ops: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for concept in reconcile.get("concepts") or []:
        cid = concept.get("concept_id", "")
        target, extra = resolve_fact_type(concept.get("fact_type", ""), item_types)
        if target is None:
            skipped.append({
                "concept_id": cid,
                "code": "type-unmapped",
                "reason": f"種別 '{concept.get('fact_type')}' に対応する"
                          " contextdb アイテム種別がメタモデルにありません",
            })
            continue
        attrs, missing = _fill_attrs(concept, item_types[target], extra)
        if missing:
            skipped.append({
                "concept_id": cid,
                "type": target,
                "code": "missing-attrs",
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

    _refine_ops(reconcile, fact_map, ops, skipped)
    if not ops:
        # plan が空。4 分類のどれで落ちたかを skipped と item_types から診断する。
        # （呼び出し元エージェントが「plan が空」だけ見て途方に暮れないように）
        for line in diagnose_empty_plan(reconcile, item_types, skipped):
            print(line, file=sys.stderr)
    return {"ops": ops}, skipped


# plan が空になる 4 分類（依頼書 P3-4）と、それぞれの一次対処。
_EMPTY_PLAN_HINTS = {
    "metamodel-empty":
        "① メタモデルの item_types が空（extends 解決失敗の可能性）。"
        " contextdb スキルを同じプロジェクトに展開するか --metamodel で実効"
        "メタモデルを直接指定する。",
    "type-unmapped":
        "② ファクト種別が contextdb 種別に対応付かない（label 突合失敗/"
        "item_types が空）。メタモデルの label 宣言を確認する。",
    "missing-attrs":
        "③ 必須属性がファクトから埋まらない。doc-author の authoring で補完する。",
    "refine-no-fact-map":
        "④ refinement の両端 ID が未確定。--fact-map を与えるか doc-author で張る。",
}


def diagnose_empty_plan(reconcile: dict[str, Any],
                        item_types: dict[str, dict[str, Any]],
                        skipped: list[dict[str, Any]]) -> list[str]:
    """plan が空のとき、原因が 4 分類のどれかを特定できる診断行を返す。"""
    n_concepts = len(reconcile.get("concepts") or [])
    n_refines = len(reconcile.get("refinements") or [])
    lines = [f"診断: plan が空です（concepts {n_concepts} 件 / "
             f"refinements {n_refines} 件はすべて保留）。"]

    labels = [t.get("label") for t in item_types.values() if t.get("label")]
    if not item_types or not labels:
        # 個々の skip 理由（type-unmapped が並ぶ）より上位の根本原因。
        lines.append("  " + _EMPTY_PLAN_HINTS["metamodel-empty"])
        return lines

    counts: dict[str, int] = {}
    for s in skipped:
        counts[s.get("code", "other")] = counts.get(s.get("code", "other"), 0) + 1
    if not counts:
        lines.append("  reconcile.json に統合・粒度差の裁定がありません"
                     "（矛盾のみ、または全クラスタが空裁定）。")
        return lines
    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        hint = _EMPTY_PLAN_HINTS.get(code, f"分類不明の保留 ({code})")
        lines.append(f"  保留 {n} 件: {hint}")
    return lines

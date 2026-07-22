"""fact-reconcile コマンドラインインターフェース。

    python -m factreconcile <サブコマンド> [オプション]
    fact-reconcile <サブコマンド> [オプション]        # venv コマンド (launcher 経由)

サブコマンド:
    analyze    facts を名寄せして reconcile.json (提案) を生成する (①ブロッキング + ②LLM 裁定)
    review     reconcile.json を人間可読で一覧する (concept / refinement / contradiction / term_map)
    plan       reconcile.json から contextdb mutate plan.json を生成する (④。適用は contextdb mutate apply)
    name       仕様アイテムの name を LLM で整え names.json (提案) を生成する (⑤命名パス)
    name-plan  names.json から contextdb mutate plan.json を生成する (name のみの set-attr)
    config     LLM 接続設定の確認 (--check) と .env 雛形の作成 (--init)。docsummary と共有

秘密情報 (API キー) は `.env` か環境変数で渡す。このツールは値を表示・保存しない。
出力はすべて提案であり、contextdb への適用は人の承認 (contextdb mutate apply → approve) を通す。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from docagent.facts import FactStore
from docagent.store import DocAgentError, Library
from docextract import paths as _paths
from docsummary import providers, settings as settings_mod
from docsummary.settings import Settings, SettingsError

from . import adjudicate, blocking, classify as classify_mod, naming
from . import plan as plan_mod

DEFAULT_RECONCILE = "reconcile.json"
DEFAULT_PLAN = "plan.json"
DEFAULT_NAMES = "names.json"
DEFAULT_CLASSIFY = "classify.json"


def _emit(obj: Any, as_json: bool, human) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        human(obj)


# ── analyze ──────────────────────────────────────────────────
def _load_facts(args: argparse.Namespace) -> list[dict[str, Any]]:
    facts_path = args.facts or _paths.facts_path()
    store = FactStore.load(facts_path)
    facts = store.items
    if args.doc:
        wanted = set(args.doc)
        facts = [f for f in facts if f.get("doc_id") in wanted]
    if args.dir:
        lib = Library.load(args.store or _paths.store_path())
        prefix = str(Path(args.dir))
        keep = {
            d["id"] for d in lib.documents
            if str(Path(d.get("source", ""))).startswith(prefix)
        }
        facts = [f for f in facts if f.get("doc_id") in keep]
    return facts


def cmd_analyze(args: argparse.Namespace) -> int:
    facts = _load_facts(args)
    clusters, kinds = blocking.combined_clusters(
        facts,
        threshold=args.block_threshold,
        refine=not args.no_refine,
        refine_threshold=args.refine_threshold,
        refine_top_k=args.refine_top_k,
    )

    if args.dry_run:
        payload = {
            "facts": len(facts),
            "clusters": [
                {"size": len(c), "kind": k, "member_fact_ids": [f.get("id") for f in c]}
                for c, k in zip(clusters, kinds)
            ],
        }
        _emit(payload, args.json, lambda o: (
            print(f"ファクト {o['facts']} 件 / 候補クラスタ {len(o['clusters'])} 個"
                  f" (統合 {sum(1 for c in o['clusters'] if c['kind'] == 'merge')} /"
                  f" 粒度差 {sum(1 for c in o['clusters'] if c['kind'] == 'refine')})"
                  " (LLM 未呼び出し)"),
            [print(f"  [{c['size']}件 {c['kind']}] {', '.join(c['member_fact_ids'])}")
             for c in o["clusters"]],
        ))
        return 0

    # 本文付きクラスタを書き出す (API キー不要)。呼び出し元エージェント (Claude) が
    # 裁定材料にし、裁定結果を --verdicts で正規 build 経路に戻す。
    if args.emit_clusters:
        payload = adjudicate.emit_clusters(facts, clusters, kinds)
        cp = Path(args.emit_clusters)
        cp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(f"生成: {cp} — 候補クラスタ {len(payload['clusters'])} 個"
              f" / ファクト {len(facts)} 件 (LLM 未呼び出し)")
        print("次の一手: クラスタを裁定して --verdicts <裁定> で reconcile.json を組む")
        return 0

    # クラスタをバッチに割って書き出す (API キー不要)。大量クラスタを分担裁定し、
    # 各バッチの verdicts を連結して --verdicts で正規 build 経路に戻す。
    if args.emit_batches:
        payload = adjudicate.emit_cluster_batches(
            facts, clusters, kinds, batch_size=args.batch_size)
        bp = Path(args.emit_batches)
        bp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        n_merge = sum(1 for b in payload["batches"] if b["kind"] == "merge")
        n_refine = sum(1 for b in payload["batches"] if b["kind"] == "refine")
        print(f"生成: {bp} — バッチ {len(payload['batches'])} 個"
              f" (統合 {n_merge} / 粒度差 {n_refine})"
              f" / クラスタ {len(clusters)} 個 / ファクト {len(facts)} 件 (LLM 未呼び出し)")
        print("次の一手: バッチごとに裁定 → 各 verdicts を連結して"
              " --verdicts <連結> で reconcile.json を組む")
        return 0

    # 外部裁定 (Claude 経路) を正規 build 経路で reconcile.json に組み立てる。
    if args.verdicts:
        try:
            vraw = json.loads(Path(args.verdicts).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"エラー: 裁定ファイルが読めません: {args.verdicts} ({e})",
                  file=sys.stderr)
            return 1
        vlist = vraw.get("verdicts") if isinstance(vraw, dict) else vraw
        verdicts = {
            v["cluster_id"]: v
            for v in (vlist or [])
            if isinstance(v, dict) and v.get("cluster_id")
        }
        reconcile = adjudicate.build_reconcile(
            None, facts, clusters, kinds=kinds, verdicts=verdicts)
        out = Path(args.out or DEFAULT_RECONCILE)
        out.write_text(json.dumps(reconcile, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"生成: {out} — 統合提案 {len(reconcile['concepts'])} 件 /"
              f" 粒度差 {len(reconcile['refinements'])} 件 /"
              f" 矛盾 {len(reconcile['contradictions'])} 件 /"
              f" 用語 {len(reconcile['term_map'])} 件 (外部裁定)")
        print("次の一手: fact-reconcile review でレビュー → plan で mutate plan を作る")
        return 0

    out = Path(args.out or DEFAULT_RECONCILE)
    if out.is_file() and not args.force:
        try:
            existing = json.loads(out.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing and adjudicate.is_fresh(existing, facts):
            print(f"変更なし (facts が前回と同一): {out}。再生成は --force")
            return 0

    if not clusters:
        reconcile = {
            "version": 1,
            "generated_from": {
                "facts_hash": adjudicate.facts_hash(facts),
                "prompt_version": adjudicate.PROMPT_VERSION,
            },
            "concepts": [], "contradictions": [], "refinements": [], "term_map": [],
        }
    else:
        try:
            cfg = settings_mod.resolve_config(
                Settings.load(args.env_file), args.provider, args.model)
        except SettingsError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1
        try:
            reconcile = adjudicate.build_reconcile(
                cfg, facts, clusters, kinds=kinds,
                max_output_tokens=args.max_output_tokens, timeout=args.timeout)
        except providers.ProviderError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1

    out.write_text(json.dumps(reconcile, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"生成: {out} — 統合提案 {len(reconcile['concepts'])} 件 /"
          f" 粒度差 {len(reconcile.get('refinements') or [])} 件 /"
          f" 矛盾 {len(reconcile['contradictions'])} 件 /"
          f" 用語 {len(reconcile['term_map'])} 件")
    print("次の一手: fact-reconcile review でレビュー → plan で mutate plan を作る")
    return 0


# ── review ───────────────────────────────────────────────────
def _read_reconcile(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise DocAgentError(
            f"reconcile.json が見つかりません: {p}。"
            " 先に fact-reconcile analyze で生成してください")
    return json.loads(p.read_text(encoding="utf-8-sig"))


def cmd_review(args: argparse.Namespace) -> int:
    try:
        rec = _read_reconcile(args.reconcile or DEFAULT_RECONCILE)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

    concepts = rec.get("concepts") or []
    contradictions = rec.get("contradictions") or []
    term_map = rec.get("term_map") or []
    print(f"■ 統合提案 (concept) {len(concepts)} 件 — 同一概念とみなしたファクト群")
    for c in concepts:
        print(f"  {c['concept_id']} [{c.get('fact_type')}] {c.get('canonical_term')}")
        print(f"      {c.get('canonical_statement')}")
        print(f"      members: {', '.join(c.get('member_fact_ids') or [])}")
    refinements = rec.get("refinements") or []
    print(f"\n■ 粒度差 (refinement) {len(refinements)} 件 — 統合せず child refines parent を張る")
    for r in refinements:
        print(f"  {r.get('child_fact_id')} refines {r.get('parent_fact_id')}"
              f" [{r.get('child_type')}]")
        if r.get("rationale"):
            print(f"      {r['rationale']}")

    print(f"\n■ 矛盾 (contradiction) {len(contradictions)} 件 — 値が食い違う (人の判断が必要)")
    for con in contradictions:
        print(f"  {', '.join(con.get('fact_ids') or [])}: {con.get('issue')}")
        for cl in con.get("claims") or []:
            print(f"      - {cl.get('fact_id')}: {cl.get('position')}")
    print(f"\n■ 用語 (term_map) {len(term_map)} 件 — 表記ゆれ → 正準用語")
    for t in term_map:
        print(f"  {'、'.join(t.get('variants') or [])} → {t.get('canonical')}")
    return 0


# ── plan ─────────────────────────────────────────────────────
def _metamodel_path(args: argparse.Namespace) -> Path:
    if args.metamodel:
        return Path(args.metamodel)
    root = Path(args.root) if args.root else Path.cwd() / ".contextdb"
    return root / "metamodel.yaml"


def cmd_plan(args: argparse.Namespace) -> int:
    fact_map: dict[str, str] = {}
    if args.fact_map:
        try:
            raw = json.loads(Path(args.fact_map).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"エラー: fact-map が読めません: {args.fact_map} ({e})", file=sys.stderr)
            return 1
        mapping = raw.get("fact_map") if isinstance(raw, dict) else None
        fact_map = {str(k): str(v) for k, v in (mapping or raw).items()}

    try:
        rec = _read_reconcile(args.infile or DEFAULT_RECONCILE)
        the_plan, skipped = plan_mod.build_plan(rec, _metamodel_path(args), fact_map)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    out = Path(args.out or DEFAULT_PLAN)
    out.write_text(json.dumps(the_plan, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"plan": the_plan, "skipped": skipped},
                         ensure_ascii=False, indent=2))
        return 0
    n_item = sum(1 for o in the_plan["ops"] if o["op"] == "add-item")
    n_rel = sum(1 for o in the_plan["ops"] if o["op"] == "add-relation")
    print(f"生成: {out} — add-item {n_item} 件 / add-relation {n_rel} 件")
    if skipped:
        print(f"保留 {len(skipped)} 件 (doc-author で補完):")
        for s in skipped:
            print(f"  {s['concept_id']}: {s['reason']}")
    print("次の一手: contextdb mutate apply "
          f"{out} --dry-run で検証 → 外して適用 → contextdb approve")
    return 0


# ── name ─────────────────────────────────────────────────────
def _naming_root(args: argparse.Namespace) -> Path:
    return Path(args.root) if args.root else Path.cwd() / ".contextdb"


def cmd_name(args: argparse.Namespace) -> int:
    types = tuple(args.type) if args.type else naming.DEFAULT_TYPES
    items = naming.load_items(_naming_root(args), types)
    if not items:
        print(f"命名対象のアイテムがありません (種別: {'、'.join(types)})",
              file=sys.stderr)
        return 1

    seeds: dict[str, str] = {}
    if args.reconcile:
        try:
            seeds = naming.seed_names(
                json.loads(Path(args.reconcile).read_text(encoding="utf-8-sig")))
        except (OSError, json.JSONDecodeError) as e:
            print(f"エラー: reconcile.json が読めません: {args.reconcile} ({e})",
                  file=sys.stderr)
            return 1

    batches = naming.emit_batches(items, args.batch_size, seeds)

    # バッチを書き出すだけ (API キー不要)。呼び出し元エージェントが命名し
    # --verdicts で正規 build 経路に戻す。
    if args.emit_batches:
        bp = Path(args.emit_batches)
        bp.write_text(json.dumps(batches, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(f"生成: {bp} — バッチ {len(batches['batches'])} 個"
              f" / アイテム {len(items)} 件 (LLM 未呼び出し)")
        print("次の一手: 各バッチを命名して --verdicts <命名> で names.json を組む")
        return 0

    out = Path(args.out or DEFAULT_NAMES)
    verdicts = None
    if args.verdicts:
        try:
            vraw = json.loads(Path(args.verdicts).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"エラー: 命名ファイルが読めません: {args.verdicts} ({e})",
                  file=sys.stderr)
            return 1
        vlist = vraw.get("verdicts") if isinstance(vraw, dict) else vraw
        verdicts = {
            v["batch_id"]: v
            for v in (vlist or [])
            if isinstance(v, dict) and v.get("batch_id")
        }
    elif out.is_file() and not args.force:
        try:
            existing = json.loads(out.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing and naming.is_fresh(existing, items):
            print(f"変更なし (items が前回と同一): {out}。再生成は --force")
            return 0

    cfg = None
    if verdicts is None:
        try:
            cfg = settings_mod.resolve_config(
                Settings.load(args.env_file), args.provider, args.model)
        except SettingsError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1
    try:
        names = naming.build_names(
            cfg, items, batches, verdicts=verdicts,
            max_output_tokens=args.max_output_tokens, timeout=args.timeout)
    except providers.ProviderError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    out.write_text(json.dumps(names, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    conflicts = sum(1 for n in names["names"] if n.get("conflict"))
    print(f"生成: {out} — 改名提案 {len(names['names']) - conflicts} 件"
          f" / 重複により保留 {conflicts} 件")
    print("次の一手: fact-reconcile name-plan で mutate plan を作る")
    return 0


# ── classify（ルール層の rule_kind 付与） ─────────────────────
def cmd_classify(args: argparse.Namespace) -> int:
    types = tuple(args.type) if args.type else classify_mod.DEFAULT_TYPES
    try:
        items = classify_mod.load_rule_items(_naming_root(args), types)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    if not items:
        print(f"分類対象のアイテムがありません (種別: {'、'.join(types)})",
              file=sys.stderr)
        return 1

    batches = classify_mod.emit_batches(items, args.batch_size)
    if args.emit_batches:
        bp = Path(args.emit_batches)
        bp.write_text(json.dumps(batches, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(f"生成: {bp} — バッチ {len(batches['batches'])} 個"
              f" / アイテム {len(items)} 件 (LLM 未呼び出し)")
        print("次の一手: 各バッチを裁定して --verdicts <裁定> で classify.json を組む")
        return 0

    out = Path(args.out or DEFAULT_CLASSIFY)
    verdicts = None
    if args.verdicts:
        try:
            vraw = json.loads(Path(args.verdicts).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"エラー: 裁定ファイルが読めません: {args.verdicts} ({e})",
                  file=sys.stderr)
            return 1
        vlist = vraw.get("verdicts") if isinstance(vraw, dict) else vraw
        verdicts = {v["batch_id"]: v for v in (vlist or [])
                    if isinstance(v, dict) and v.get("batch_id")}

    # 既定は決定論の仕分けだけで classify.json を組む（API キー不要）。
    # --verdicts は外部裁定を採り LLM を呼ばない。--llm 指定時のみ自動裁定。
    cfg = None
    if verdicts is None and args.llm:
        try:
            cfg = settings_mod.resolve_config(
                Settings.load(args.env_file), args.provider, args.model)
        except SettingsError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1
    if verdicts is None and not args.llm:
        classified = classify_mod.classify_items(items)
        result = {"version": 1,
                  "generated_from": {
                      "items_hash": classify_mod.items_hash(items),
                      "prompt_version": classify_mod.PROMPT_VERSION},
                  "classifications": classified}
    else:
        try:
            result = classify_mod.build_classify(
                cfg, items, batches, verdicts=verdicts,
                max_output_tokens=args.max_output_tokens, timeout=args.timeout)
        except providers.ProviderError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1

    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    counts: dict[str, int] = {}
    for c in result["classifications"]:
        counts[c["rule_kind"]] = counts.get(c["rule_kind"], 0) + 1
    summary = " / ".join(f"{k} {counts[k]}" for k in sorted(counts))
    print(f"生成: {out} — 分類 {len(result['classifications'])} 件 ({summary})")
    print("次の一手: fact-reconcile classify-plan で mutate plan を作る")
    return 0


def cmd_classify_plan(args: argparse.Namespace) -> int:
    path = Path(args.infile or DEFAULT_CLASSIFY)
    if not path.is_file():
        print(f"エラー: classify.json が見つかりません: {path}。"
              " 先に fact-reconcile classify で生成してください", file=sys.stderr)
        return 1
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    the_plan, skipped = classify_mod.build_classify_plan(result)

    out = Path(args.out or DEFAULT_PLAN)
    out.write_text(json.dumps(the_plan, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"plan": the_plan, "skipped": skipped},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"生成: {out} — set-attr {len(the_plan['ops'])} 件 (rule_kind のみ)")
    if skipped:
        print(f"保留 {len(skipped)} 件:")
        for s in skipped:
            print(f"  {s['id']}: {s['reason']}")
    print("次の一手: contextdb mutate apply "
          f"{out} --dry-run で検証 → 外して適用 → contextdb approve")
    return 0


def cmd_name_plan(args: argparse.Namespace) -> int:
    path = Path(args.infile or DEFAULT_NAMES)
    if not path.is_file():
        print(f"エラー: names.json が見つかりません: {path}。"
              " 先に fact-reconcile name で生成してください", file=sys.stderr)
        return 1
    names = json.loads(path.read_text(encoding="utf-8-sig"))
    the_plan, skipped = naming.build_name_plan(names)

    out = Path(args.out or DEFAULT_PLAN)
    out.write_text(json.dumps(the_plan, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if args.json:
        print(json.dumps({"plan": the_plan, "skipped": skipped},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"生成: {out} — set-attr {len(the_plan['ops'])} 件 (name のみ)")
    if skipped:
        print(f"保留 {len(skipped)} 件:")
        for s in skipped:
            print(f"  {s['id']}: {s['reason']}")
    print("次の一手: contextdb mutate apply "
          f"{out} --dry-run で検証 → 外して適用 → contextdb approve")
    return 0


# ── config ───────────────────────────────────────────────────
def cmd_config(args: argparse.Namespace) -> int:
    if args.init:
        target = Path(args.path) if args.path else Path.cwd() / ".env"
        example = target.with_name(target.name + ".example")
        example.write_text(settings_mod.ENV_TEMPLATE, encoding="utf-8")
        created = [str(example)]
        if not target.exists():
            target.write_text(settings_mod.ENV_TEMPLATE, encoding="utf-8")
            created.append(str(target))
        for p in created:
            print(f"作成しました: {p}")
        print("次の一手: 使うプロバイダの API キーを .env に記入する (値は読まない)")
        return 0
    env = Settings.load(args.env_file)
    payload = settings_mod.check_payload(env, args.provider)

    def human(o):
        print(f".env: {o['env_file'] or '(見つからない)'}")
        if o["selected_provider"]:
            print(f"使用プロバイダ: {o['selected_provider']}")
        else:
            print(f"使用プロバイダ: 未決定 — {o['selection_error']}")
        for name, p in o["providers"].items():
            print(f"[{'OK' if p['configured'] else '--'}] {name}")

    _emit(payload, args.json, human)
    return 0 if payload["selected_provider"] else 1


# ── パーサ ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="機械可読な JSON で出力")

    p = argparse.ArgumentParser(
        prog="fact-reconcile",
        description="抽出ファクトを意味的に名寄せし、contextdb への提案を作る",
        parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("analyze", help="facts を名寄せして reconcile.json を生成",
                        parents=[common])
    sp.add_argument("--facts", help="facts.json のパス (既定 <home>/store/facts.json)")
    sp.add_argument("--store", help="docagent 集約 JSON (--dir 解決に使う)")
    sp.add_argument("--doc", action="append", help="対象文書 ID (複数可)")
    sp.add_argument("--dir", help="このフォルダ配下の元ファイルを持つ文書のファクトに絞る")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_RECONCILE})")
    sp.add_argument("--env-file", help=".env のパス (既定は cwd から上方探索)")
    sp.add_argument("--provider",
                    help="LLM プロバイダ (openai / azure / gemini / anthropic)")
    sp.add_argument("--model", help="モデル名/デプロイ名の上書き")
    sp.add_argument("--block-threshold", type=float, default=blocking.DEFAULT_THRESHOLD,
                    help=f"ブロッキングの類似閾値 (既定 {blocking.DEFAULT_THRESHOLD})")
    sp.add_argument("--no-refine", action="store_true",
                    help="粒度差 (refine) 候補パスを回さず統合候補だけ裁定する")
    sp.add_argument("--refine-threshold", type=float, default=blocking.REFINE_THRESHOLD,
                    help="粒度差候補の包含率閾値"
                         f" (既定 {blocking.REFINE_THRESHOLD}。下げると recall 増)")
    sp.add_argument("--refine-top-k", type=int, default=blocking.REFINE_TOP_K,
                    help=f"1 アンカーあたりの粒度差近傍数 (既定 {blocking.REFINE_TOP_K})")
    sp.add_argument("--max-output-tokens", type=int, default=4096,
                    help="生成の上限トークン数")
    sp.add_argument("--timeout", type=float, default=providers.DEFAULT_TIMEOUT,
                    help="API タイムアウト秒")
    sp.add_argument("--force", action="store_true", help="facts 不変でも再生成する")
    sp.add_argument("--dry-run", action="store_true",
                    help="候補クラスタだけ表示して LLM は呼ばない (API キー不要)")
    sp.add_argument("--emit-clusters", metavar="FILE",
                    help="候補クラスタを本文付きで書き出す (API キー不要)。"
                         "呼び出し元が裁定し --verdicts で戻す")
    sp.add_argument("--emit-batches", metavar="FILE",
                    help="候補クラスタを 1 ファイル内のバッチ (kind 別・batch_size 件ずつ)"
                         " に割って書き出す (API キー不要)。大量クラスタを分担裁定する用")
    sp.add_argument("--batch-size", type=int,
                    default=adjudicate.DEFAULT_CLUSTER_BATCH_SIZE,
                    help="--emit-batches の 1 バッチあたりクラスタ数"
                         f" (既定 {adjudicate.DEFAULT_CLUSTER_BATCH_SIZE})")
    sp.add_argument("--verdicts", metavar="FILE",
                    help="外部裁定 (cluster_id 付き) を正規 build 経路で reconcile.json へ"
                         " (API キー不要・LLM を呼ばない)。分担裁定を連結したものでよい")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("review", help="reconcile.json を人間可読で一覧",
                        parents=[common])
    sp.add_argument("reconcile", nargs="?", help=f"reconcile.json (既定 {DEFAULT_RECONCILE})")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("plan", help="reconcile.json から contextdb mutate plan を生成",
                        parents=[common])
    sp.add_argument("--in", dest="infile", help=f"reconcile.json (既定 {DEFAULT_RECONCILE})")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_PLAN})")
    sp.add_argument("--metamodel", help="ターゲット metamodel.yaml のパス")
    sp.add_argument("--root", help="contextdb データルート (既定 ./.contextdb)。metamodel.yaml を探す")
    sp.add_argument("--fact-map", metavar="FILE",
                    help="fact_id → contextdb アイテム ID の対応 JSON。"
                         "refinement を refines エッジの add-relation op にするのに使う")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("name", help="仕様アイテムの name を LLM で整え names.json を生成",
                        parents=[common])
    sp.add_argument("--root", help="contextdb データルート (既定 ./.contextdb)")
    sp.add_argument("--type", action="append",
                    help="命名対象の種別 (複数可。既定 "
                         f"{'、'.join(naming.DEFAULT_TYPES)})")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_NAMES})")
    sp.add_argument("--reconcile", metavar="FILE",
                    help="reconcile.json。統合済み concept の canonical_term を"
                         "命名の初期値として流用する")
    sp.add_argument("--batch-size", type=int, default=naming.DEFAULT_BATCH_SIZE,
                    help=f"1 バッチのアイテム数 (既定 {naming.DEFAULT_BATCH_SIZE})")
    sp.add_argument("--emit-batches", metavar="FILE",
                    help="命名バッチを本文付きで書き出す (API キー不要)。"
                         "呼び出し元が命名し --verdicts で戻す")
    sp.add_argument("--verdicts", metavar="FILE",
                    help="外部命名 (batch_id 付き) を正規 build 経路で names.json へ"
                         " (API キー不要・LLM を呼ばない)")
    sp.add_argument("--env-file", help=".env のパス (既定は cwd から上方探索)")
    sp.add_argument("--provider", help="LLM プロバイダ")
    sp.add_argument("--model", help="モデル名/デプロイ名の上書き")
    sp.add_argument("--max-output-tokens", type=int, default=4096,
                    help="生成の上限トークン数")
    sp.add_argument("--timeout", type=float, default=providers.DEFAULT_TIMEOUT,
                    help="API タイムアウト秒")
    sp.add_argument("--force", action="store_true", help="items 不変でも再生成する")
    sp.set_defaults(func=cmd_name)

    sp = sub.add_parser("name-plan", help="names.json から contextdb mutate plan を生成",
                        parents=[common])
    sp.add_argument("--in", dest="infile", help=f"names.json (既定 {DEFAULT_NAMES})")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_PLAN})")
    sp.set_defaults(func=cmd_name_plan)

    sp = sub.add_parser("classify",
                        help="ルール台帳に rule_kind を付ける (決定論 + 任意で裁定)",
                        parents=[common])
    sp.add_argument("--root", help="contextdb データルート (既定 ./.contextdb)")
    sp.add_argument("--type", action="append",
                    help="分類対象の種別 (複数可。既定 "
                         f"{'、'.join(classify_mod.DEFAULT_TYPES)})")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_CLASSIFY})")
    sp.add_argument("--batch-size", type=int, default=40,
                    help="1 バッチのアイテム数 (既定 40。--emit-batches 用)")
    sp.add_argument("--emit-batches", metavar="FILE",
                    help="分類バッチを suggested_kind 付きで書き出す (API キー不要)。"
                         "呼び出し元が裁定し --verdicts で戻す")
    sp.add_argument("--verdicts", metavar="FILE",
                    help="外部裁定 (batch_id 付き) を正規 build 経路で classify.json へ"
                         " (API キー不要・LLM を呼ばない)")
    sp.add_argument("--llm", action="store_true",
                    help="決定論でなく .env の LLM で裁定する (既定は決定論のみ)")
    sp.add_argument("--env-file", help=".env のパス (既定は cwd から上方探索)")
    sp.add_argument("--provider", help="LLM プロバイダ")
    sp.add_argument("--model", help="モデル名/デプロイ名の上書き")
    sp.add_argument("--max-output-tokens", type=int, default=4096,
                    help="生成の上限トークン数")
    sp.add_argument("--timeout", type=float, default=providers.DEFAULT_TIMEOUT,
                    help="API タイムアウト秒")
    sp.set_defaults(func=cmd_classify)

    sp = sub.add_parser("classify-plan",
                        help="classify.json から contextdb mutate plan を生成",
                        parents=[common])
    sp.add_argument("--in", dest="infile", help=f"classify.json (既定 {DEFAULT_CLASSIFY})")
    sp.add_argument("--out", help=f"出力先 (既定 {DEFAULT_PLAN})")
    sp.set_defaults(func=cmd_classify_plan)

    sp = sub.add_parser("config", help="接続設定の確認 (--check) / .env 雛形の作成 (--init)",
                        parents=[common])
    sp.add_argument("--check", action="store_true", help="設定状態を表示 (値は出さない)")
    sp.add_argument("--init", action="store_true", help=".env / .env.example の雛形を作成")
    sp.add_argument("--path", help="--init の書き出し先 .env パス")
    sp.add_argument("--env-file", help=".env のパス")
    sp.add_argument("--provider", help="LLM プロバイダ")
    sp.set_defaults(func=cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

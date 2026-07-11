# 依存ライブラリとライセンス — fact-reconcile

本スキルのライセンスは MIT ([LICENSE](LICENSE))。

## 実行時依存 (pip)

**自前の追加依存は持たない。** 名寄せの実体（factreconcile パッケージ）は
**Python 3.10+ の標準ライブラリのみ**で実装している。

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| （標準ライブラリのみ） | ブロッキング（決定的なクラスタリング）・JSON・プラン生成 | PSF |

## 二段構えと LLM の使いどころ

- **ブロッキング（`blocking`）は LLM を使わない決定的処理**。同一かもしれない候補を
  文字列・正規化で束ねるだけで、外部サービスもモデルも要らない（オフラインで動く）。
- **LLM 裁定（`adjudicate`）のみ外部 LLM を呼ぶ**。その接続設定（プロバイダ・`.env`）は
  **docsummary の設定を再利用**し、fact-reconcile 独自の接続実装・依存は持たない。

## 兄弟スキルへの実行時参照（docextract / docagent / docsummary）

入力の出典付きファクト（`facts.json`）は docextract → docagent が溜めたもので、
LLM 接続は docsummary のプロバイダ層を使う。fact-reconcile はこれらを**コピー同梱せず、
同一プロジェクトに展開された兄弟スキルの scripts を実行時参照で解決する**
（`run_fact_reconcile.py`）。共有 venv・依存インストールのマーカーは docextract と共用
（[GOVERNANCE.md](GOVERNANCE.md) / `dr-07`）。

## 外部サービス（LLM API）

LLM 裁定時のみ外部プロバイダ（OpenAI / Azure OpenAI / Gemini / Anthropic）を呼ぶ。
API キーは `.env`・環境変数で渡し保存しない。**ファクト本文が外部へ送信される**点は
docsummary と同じで、[threat-model.md](threat-model.md) の T1 / T2 を参照。

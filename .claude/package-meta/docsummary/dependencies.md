# 依存ライブラリとライセンス — docsummary

本スキルのライセンスは MIT ([LICENSE](LICENSE))。

## 実行時依存 (pip)

**自前の追加依存は持たない。** docsummary の要約実体は **Python 3.10+ の標準ライブラリ
のみ**で実装している（LLM プロバイダ呼び出しも `urllib` で行い、`openai` 等の SDK に
依存しない）。

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| （標準ライブラリのみ） | プロバイダ HTTP 呼び出し（`urllib`）・JSON・.env 解析 | PSF |

## 兄弟スキルへの実行時参照（docextract / docagent）

要約の入力は docextract が抽出し docagent が索引化した文書（`library.json`）であり、
docsummary はこれらのパッケージを**コピー同梱せず、同一プロジェクトに展開された
兄弟スキル docextract の scripts を実行時参照で解決する**（`run_docsummary.py`。
依存記述 `requirements` も docextract のものを参照する）。共有 venv・依存インストールの
マーカーは docextract と共用するので二重インストールは起きない
（[GOVERNANCE.md](GOVERNANCE.md) / `dr-07`：スキル単位の実行体同梱と兄弟スキル参照）。

## 外部サービス（LLM API）— 前提であり同梱物ではない

要約は**外部の LLM API を呼び出す**。API そのものは本スキルが用意・課金するもの
ではなく、利用者が契約・設定する前提の外部サービス。

| プロバイダ | 呼び出し方 | 認証 |
|-----------|-----------|------|
| OpenAI | REST（`urllib`） | API キー |
| Azure OpenAI | REST（`urllib`） | API キー + エンドポイント |
| Gemini | REST（`urllib`） | API キー |
| Anthropic | REST（`urllib`） | API キー |

- **API キー等の秘密情報は環境変数または `.env` で渡し、コード・ストア・ログには
  保存しない**（[threat-model.md](threat-model.md) の T1）。`.env` はコミットしない。
- **文書本文が外部プロバイダへ送信される**（データ egress）。送信可否・プロバイダ選定は
  運用側の責務であり、機微文書の取り扱いは threat-model の T2 を参照。

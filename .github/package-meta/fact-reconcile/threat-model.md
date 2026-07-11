# 脅威モデル — fact-reconcile

想定する信頼境界と、各層の防御、そして**それを検証するテスト**を対応づける文書。
関連: [GOVERNANCE.md](GOVERNANCE.md) / [dependencies.md](dependencies.md)。

## スコープと前提

- fact-reconcile は**抽出ファクト（facts.json）の意味的な名寄せ提案**を作るツール。
  LLM 裁定時に外部プロバイダへファクト本文を送信する（データ egress を伴う）。
- 想定利用者は、自組織のファクトを自分の LLM 契約で名寄せするエンジニア／エージェント。
- 主眼は「**秘密情報を漏らさないこと**」「**矛盾を勝手に消さないこと**」
  「**提案を正本へ黙って書き込まないこと**」。

## 信頼境界（trust boundaries）

| # | 境界 | 内→外 / 外→内 | 主なリスク |
|---|---|---|---|
| B1 | 設定・秘密情報（docsummary 設定を共用） | 外→内 | API キーの漏洩・コミット |
| B2 | adjudicate → LLM プロバイダ | 内→外（ファクト本文の送信） | 機微ファクトの意図しない外部送信 |
| B3 | reconcile 提案 → contextdb 正本 | 内→内 | 矛盾の自動解決・未レビュー提案の既成事実化 |

## 脅威 → 防御層 → 検証テスト

| ID | 脅威 | 信頼境界 | 防御層 | 検証テスト |
|---|---|---|---|---|
| T1 | API キーがコード・出力・ログに載って漏洩する／誤ってコミットされる | B1 | **D1**: 秘密は docsummary と共通の `.env`・環境変数経由でのみ受け取り、値は表示・保存しない | `tests/test_factreconcile.py`（キー値を出力・保存しない／設定共用） |
| T2 | 機微ファクトが利用者の承知なく外部 LLM へ送信される | B2 | **D2**: LLM を呼ぶのは裁定（adjudicate）段だけ。ブロッキングは**決定的・オフライン**で LLM を使わない。送信先は明示設定したプロバイダのみ | `tests/test_factreconcile.py`（ブロッキングが LLM 非依存／設定プロバイダのみ呼ぶ） |
| T3 | 相反する値を主張するファクトを「同一」として勝手に統合し、矛盾が消える | B3 | **D3**: 矛盾は `contradiction` として提示し**自動解決しない**。ブロッキングは順序非依存で、投入順に結果が揺れない | `tests/test_factreconcile.py`（矛盾の非自動解決／順序非依存） |
| T4 | 未レビューの名寄せ提案が正本へ黙って書き込まれる | B3 | **D4**: `reconcile.json` は**レビュー専用**。承認済み concept だけを決定的に contextdb `add-item`（`status: review`）へ落とし、`mutate apply --dry-run` で検証してから適用（人の approve が必須） | `tests/test_factreconcile.py`（plan 生成が review 状態・dry-run 検証） |

> 上表のテスト名は同梱テストの意図を示す（実ファイルは `tests/test_factreconcile.py`
> に対応）。防御を変えたら対応テストも併せて更新する。

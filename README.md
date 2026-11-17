<p align="center">
  <img src="docs/assets/banner.png" alt="Leansail: a Lean language server with a Rust host" width="880">
</p>

<p align="center"><strong>A Lean language server with a Rust host and Lean-verified lifecycle rules</strong></p>

<p align="center">
  <a href="https://github.com/conao3/rust-leansail/actions/workflows/ci.yml"><img src="https://github.com/conao3/rust-leansail/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/conao3/rust-leansail" alt="License: Apache-2.0"></a>
  <a href="Cargo.toml"><img src="https://img.shields.io/badge/Rust-edition%202024-orange" alt="Rust edition 2024"></a>
  <a href="lean-toolchain"><img src="https://img.shields.io/badge/Lean-v4.35.0-blue" alt="Lean v4.35.0"></a>
  <a href="https://github.com/conao3/rust-leansail/stargazers"><img src="https://img.shields.io/github/stars/conao3/rust-leansail?style=flat" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="#run">Run</a> ·
  <a href="#エディタの設定">エディタの設定</a> ·
  <a href="#what-runs-where">What runs where</a> ·
  <a href="#mathlib-の計測">Mathlib の計測</a> ·
  <a href="#verify">Verify</a> ·
  <a href="#lsp-conformance">LSP conformance</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="https://github.com/conao3/lean-leanfran">leanfran</a> ·
  <a href="https://github.com/conao3/rust-lsp-touchstone">lsp-touchstone</a>
</p>

# Leansail

Leansail is a Lean language server with a Rust host, a native Lean backend, and
protocol lifecycle rules written in Lean and compiled into Rust with
[leanfran](https://github.com/conao3/lean-leanfran).

Real development sessions are validated with
[lsp-touchstone](https://github.com/conao3/rust-lsp-touchstone) through its strict
client and checking proxy.

## Run

```bash
nix run . -- server run-server
```

Run from the root of a Lean 4.35.0 project. The Nix package includes the backend
and selects its matching toolchain. Project dependencies can be supplied with
`lake env` around the installed `leansail` command. Configure an editor to run
`leansail server run-server` on stdio.

For development:

```bash
nix develop
make build
target/debug/leansail server run-server \
  --backend "$PWD/native/.lake/build/bin/leansail-backend"
```

## エディタの設定

Emacs 31.1 / Eglot 1.24.31 と Neovim 0.12.6 の組み込み LSP で検証する。
`leansail` を PATH に置き、Lean 4.35.0 のプロジェクトから起動する。
依存ライブラリを使う場合は、以下のコマンドを
`lake env leansail server run-server` にする。

### Emacs / Eglot

```elisp
(require 'eglot)
(define-derived-mode leansail-lean-mode prog-mode "Lean")
(add-to-list 'auto-mode-alist '("\\.lean\\'" . leansail-lean-mode))
(add-to-list 'eglot-server-programs
             '(leansail-lean-mode . ("leansail" "server" "run-server")))
(add-hook 'leansail-lean-mode-hook #'eglot-ensure)
(add-hook 'kill-emacs-hook #'eglot-shutdown-all)
```

Emacs 31 の Flymake は、ユーザーが `M-x customize-option RET trusted-content`
で信頼したプロジェクトのファイルを診断する。検証スクリプトは自分で作った
fixture のファイルだけを信頼する。診断一覧は `M-x flymake-show-buffer-diagnostics`、
hover は ElDoc、定義ジャンプは `M-.`、補完は `C-M-i` を使う。

lean4-mode を導入している場合は、上の `leansail-lean-mode` に対する Eglot の
登録と hook を `lean4-mode` に設定する。検証スクリプトも `require` できれば
lean4-mode を選ぶ。固定した nixpkgs と検証環境は上記の派生モードを使うため、
lean4-mode 独自の infoview は別途確認する。

ゴールは次のコマンドで表示できる。

```elisp
(defun leansail-show-goal ()
  (interactive)
  (let ((goal (jsonrpc-request (eglot-current-server) :$/lean/plainGoal
                               (eglot--TextDocumentPositionParams))))
    (with-help-window "*Lean goal*"
      (princ (plist-get goal :rendered)))))
```

### Neovim / 組み込み LSP

```lua
vim.filetype.add({ extension = { lean = "lean" } })
vim.api.nvim_create_autocmd("FileType", {
  pattern = "lean",
  callback = function()
    vim.lsp.start({
      name = "leansail",
      cmd = { "leansail", "server", "run-server" },
      root_dir = vim.fs.root(0, { "lakefile.toml", "lakefile.lean", "lean-toolchain", ".git" }),
      exit_timeout = 10000,
    })
  end,
})
```

`exit_timeout` はエディタ終了時に `shutdown` の応答と `exit` を待つ時間 (ms)。
Neovim 0.12.6 の既定値 `false` は終了時の待機時間が 0 になる。
診断一覧は `vim.diagnostic.setloclist()`、hover は `K`、定義ジャンプは
`vim.lsp.buf.definition()`、補完は挿入モードの `C-x C-o` を使う。
ゴールは次のコマンドで表示できる。

```lua
vim.api.nvim_create_user_command("LeanGoal", function()
  local client = assert(vim.lsp.get_clients({ bufnr = 0, name = "leansail" })[1])
  local params = vim.lsp.util.make_position_params(0, client.offset_encoding)
  client:request("$/lean/plainGoal", params, function(err, result)
    assert(not err, vim.inspect(err))
    if result and result.rendered then
      vim.lsp.util.open_floating_preview(vim.split(result.rendered, "\n"), "markdown", {})
    end
  end, 0)
end, {})
```

## What runs where

- Rust handles LSP framing, process supervision and the client lifecycle gate.
- `src/lifecycle.rs`・`src/capabilities.rs`・`src/recovery.rs` は、lifecycle・capability・旧応答の抑止・worker 復旧の判定規則と定理を Lean で定義する。leanfran が生成した Rust 関数を実際の転送経路で呼ぶ。
- `native/Main.lean` embeds upstream `Lean.Server`, retaining its watchdog,
  per-document workers, request handlers, widgets and RPC. 解析済みの文書のハイライト索引と semantic tokens の応答を再利用する。索引の module 名は import の配列から直接取得し、上流との同値性を Lean で検査する。

ファイルごとの worker は native Watchdog が管理する。Mathlib 3 文書・6 コアの前景 p95 と PSS の比較に基づき、この境界を継続する。[設計](docs/architecture.md) が host の担当範囲と leanfran の判定規則を定める。

## Mathlib の計測

公式 Lean 4.35.0 の `lean --server` と現行の release host・native backend を、helios の同じ Mathlib v4.35.0 の Lake workspace で交互に実行する。3 文書を保持し、解析と索引準備後の要求を各構成 5 セッションで測る。Leansail は作成済みの永続索引を読み込み、cache hit を必須にする。

以下は IntegralEqImproper.lean の中央値 / p95（ms）と、本家 / Leansail の倍率。初回は各 5 標本、反復は各 50 標本である。

| 項目 | 本家 中央値 / p95 | Leansail 中央値 / p95 | 倍率 中央値 / p95 |
| --- | ---: | ---: | ---: |
| ハイライト初回 | 673.635 / 794.671 | 117.852 / 121.331 | 5.72 / 6.55 |
| ハイライト反復 | 672.346 / 694.872 | 0.924 / 1.094 | 727.37 / 634.90 |
| semantic tokens 反復 | 74.716 / 81.904 | 3.963 / 9.207 | 18.85 / 8.90 |
| 補完反復 | 322.236 / 360.081 | 70.428 / 97.846 | 4.58 / 3.68 |
| workspace/symbol 反復 | 850.690 / 871.502 | 87.456 / 89.395 | 9.73 / 9.75 |
| 参照反復 | 20.872 / 23.427 | 13.106 / 14.219 | 1.59 / 1.65 |
| 定義反復 | 21.493 / 22.622 | 13.333 / 14.388 | 1.61 / 1.57 |
| hover 反復 | 1.364 / 1.713 | 1.445 / 1.564 | 0.94 / 1.10 |
| plainGoal 反復 | 1.446 / 1.751 | 1.573 / 2.135 | 0.92 / 0.82 |

| 項目 | 本家 中央値 / p95 | Leansail 中央値 / p95 | 倍率 中央値 / p95 |
| --- | ---: | ---: | ---: |
| 起動から最初の非空診断 | 6884.395 / 10684.064 | 7015.356 / 7273.548 | 0.98 / 1.47 |
| 起動からプロジェクト索引準備 | 21418.880 / 21545.757 | 5519.296 / 5563.834 | 3.88 / 3.87 |
| 3 文書解析直後の全 PSS | 3596.155 / 3604.026 | 2526.203 / 2528.910 | 1.42 / 1.43 |
| 3 文書要求実行後の全 PSS | 3767.910 / 3781.936 | 3126.693 / 3139.848 | 1.21 / 1.20 |

起動は ms、3 文書の全 PSS は MiB。起動から索引準備までは文書を開かない独立セッションで測る。全 3 文書・全完走回の初回と反復の応答本文、補完の解決結果が本家と一致する。

[全 3 文書の表、採用・除外、JSON とグラフ、再現手順](docs/mathlib-benchmark.md#本家との比較) を参照する。

## Verify

```bash
nix develop --command make check
nix build
```

`make check` は native backend と Rust host のビルド、format、Clippy、leanfran の定理 21 件と native backend の同値性の定理 6 件、Rust の単体テスト 9 件、capability の通信テスト 5 ケース、既存の 11 種類の要求と編集・UTF-16・複数ファイルの回帰検査を実行する。semantic tokens は同一版の再要求、編集、再オープン、部分応答から完了後への移行を上流と比較する。`make check-acceptance` では、同じ Lean 4.35.0 の `lean --server` と Leansail を起動し、上流の全 59 protocol entry、キャンセル、編集後の旧要求、実際のプロセスを kill する障害検査を実行する。`make check-editors` の実エディタ検査も含め、CI の `make check` で実行する。

### 実エディタの比較検証

```bash
nix develop --command make check-editors
LEANSAIL_TEST_BINARY="$PWD/result/bin/leansail" nix develop --command python3 tests/editors.py
```

Emacs は `--batch -Q`、Neovim は `--headless -u NONE` で個人設定から独立して起動する。
開発環境に両エディタを固定し、各エディタで上流と Leansail の 2 セッションを実行する。
単独検査は `python3 tests/editors.py --editor emacs` または `--editor neovim` を使う。

| 操作               | Emacs / Eglot                                          | Neovim / 組み込み LSP                  |
| ------------------ | ------------------------------------------------------ | -------------------------------------- |
| 起動・初期化       | capability と応答本文が一致                            | capability と応答本文が一致            |
| 診断表示           | Flymake の一覧とエラー 2 件が一致                      | location list とエラー 2 件が一致      |
| hover              | ElDoc の `editorValue : Nat` が一致                    | フロートの `editorValue : Nat` が一致  |
| 定義ジャンプ       | xref が定義の行・列へ移動                              | 組み込みの定義ジャンプが同じ位置へ移動 |
| 補完               | CAPF の `succ` を含む候補が一致                        | `C-x C-o` の `succ` を含む候補が一致   |
| `$/lean/plainGoal` | `n : Nat ⊢ n = n` が一致                               | 同じゴールが一致                       |
| 連続入力後の診断   | `false` の型エラーと、修正後のエラー 0 件が一致        | 同じ更新結果が一致                     |
| close・reopen      | 同一サーバーで診断 2 件が復帰                          | 同一サーバーで診断 2 件が復帰          |
| shutdown・exit     | Leansail は終了コード 0。上流は Eglot による強制終了 9 | 両方とも終了コード 0                   |

上流 Lean 4.35.0 は `shutdown` 後にパラメータを省略した `exit` を受理する。
Eglot の `params: {}` 付き `exit` は待機を続け、Eglot が約 0.3 秒後に強制終了する。
Leansail は lifecycle が `exit` を受理した後、元の frame を転送して backend の
入力を閉じる。backend は EOF でも終了できるため、同じ Eglot の通知で正常終了する。
`params` の省略・`{}`・`null` の 3 ケースも自動検査する。

`target/editors/summary.json` が操作別の結果、各 `*.json` がエディタの表示内容、
`*.ndjson` が透過 proxy による送受信の全文、`*.editor.log` と `*.server.log` がログを持つ。
主要要求の応答本文と、初期・連続入力後・修正後・再オープン後の最新版の診断本文を
上流と照合する。編集中にキャンセルされる背景の semantic tokens・色・inlay hints は
`-32800` または `-32801` を許容し、通知の回数と途中の到着順は比較対象から除く。
エディタの検証対象は小さい Lean 文書と標準ライブラリを使う編集である。Mathlib の性能は `make bench-mathlib` で計測する。[計測手順と条件](docs/mathlib-benchmark.md) を参照する。GUI 固有の表示は個別の確認対象となる。

59 項目は要求 34 件、クライアント通知 10 件、server→client 要求 3 件、server 通知 2 件、Widget RPC 10 件。全項目の比較が成功する。RPC の不透明な ID を正規化し、通知は最終状態と要求の集合で比較する。解析途中の通知回数・到着順序・進捗範囲は実行時刻に依存するため比較対象から除く。比較方法と検査範囲は [受け入れ検証](docs/acceptance.md)、項目一覧は [tests/protocol.json](tests/protocol.json) が持つ。実行時にも固定 toolchain の `ProtocolOverview.lean` と一覧を照合する。

`target/conformance.json` は 59 項目ごとの結果と上流・Leansail のキャンセル結果、`target/recovery.json` は障害時の結果を持つ。送受信の全文は同じディレクトリの `*-protocol.json`・`*-cancellation.json`・`worker_*.json`・`watchdog_*.json` に保存する。

`$/cancelRequest` 後の要求は `-32800`、編集で無効になった要求は `-32801` を返し、最新の文書への要求が最新の型を返すことを検査する。FileWorker の異常終了は fatal な `$/lean/fileProgress` と要求中の `-32902` を転送し、次の文書要求で同じ版・内容から worker を復旧する。Watchdog の異常終了は保留中の要求に `-32603`、クライアントに `window/showMessage` を送り、host を終了コード 255 で終了する。待機中と要求中の両方を検査する。

Lean の定義を変更したときは `lean/` と `.leanfran/cache/` を同じ commit に含める。開発環境の固定された leanfran で生成し、キャッシュを使う Rust ビルドは生成済みコードを読む。

## LSP conformance

```bash
nix develop --command make check-protocol
```

lsp-touchstone の strict client で実際の Leansail を検査し、`test-out/strict.ndjson` に記録する。この検査は server・client ともにエラー 0 件、警告 0 件で成功する。検査対象は初期化、ファイルの開閉、document symbol、hover、終了の通信である。上流の全 59 protocol entry は `make check-acceptance` の比較検査が担う。

host は `initialize.params.capabilities` を記録し、semantic tokens・inlay hints・code lens・inline values・diagnostics・folding ranges の refresh を、それぞれの `refreshSupport` が `true` の場合に転送する。抑止した要求には元の ID と `result: null` を backend へ返す。`workspace/didChangeWatchedFiles` の動的登録は `dynamicRegistration: true` を要件とし、未対応の登録を含む要求全体を拒否して backend に `-32601` を返す。対応を宣言したクライアントへの要求と応答は元の frame を転送する。

判定規則は `src/capabilities.rs` の Lean 定義と 6 件の定理で検査する。Rust 側は JSON の読取と応答の配送を担う。`make check` は capability の欠落・`false`・不正な型・一部対応・全対応について、実際の host とテスト用 backend の通信と終了を検査する。

To validate the editing regression client through the proxy:

```bash
mkdir -p test-out
touchstone_run_dir="$(mktemp -d test-out/protocol.XXXXXX)"
python3 tests/baseline.py --lean "$PWD/tests/command" \
  --touchstone "$(command -v lsp-touchstone)" \
  --record-file "$touchstone_run_dir/session.ndjson" \
  --output "$touchstone_run_dir/client.json" \
  --samples 2 --declarations 30 --edits 2 --regression
lsp-touchstone check check-record \
  --record-file "$touchstone_run_dir/session.ndjson" --target both
```

proxy の計測には検査器の負荷が含まれる。性能は直接接続して計測する。実エディタの編集操作は `make check-editors`、実用サイズのプロジェクトでの性能は個別の計測で評価する。

編集回帰を proxy で検査した場合も server・client のエラーは 0 件となる。次の警告は検査器の標準 LSP モデルとの違いを表す。件数は解析中の通知のタイミングに依存する。

| 側     | 指摘                                                                    | 理由                                                                                                                                                                                  |
| ------ | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| server | `capability/client-did-not-declare` (`textDocument/publishDiagnostics`) | 検査クライアントは空の capability を送る。LSP の `publishDiagnostics` capability は診断の関連情報や版などの追加機能を表し、基本の push diagnostics を受け取るための opt-in ではない。 |
| server | `types/unknown-property` (`Diagnostic.fullRange`)                       | Lean の診断拡張。Lean のクライアントとの互換性のため転送する。                                                                                                                        |
| client | `types/unknown-method` (`textDocument/waitForDiagnostics`)              | 検証クライアントが解析の完了を待つために使う Lean 拡張。                                                                                                                              |

capability の意味と refresh 応答は [LSP semantic tokens](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#semanticTokens_refreshRequest)、[inlay hints](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_inlayHint_refresh)、[ファイル監視](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_didChangeWatchedFiles)、[診断通知](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_publishDiagnostics) に従う。

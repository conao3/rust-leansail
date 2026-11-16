# Leansail

Leansail is a Lean language server with a Rust host, a native Lean backend, and
protocol lifecycle rules written in Lean and compiled into Rust with
[leanfran](https://github.com/conao3/lean-leanfran).

We use [lsp-touchstone](https://github.com/conao3/rust-lsp-touchstone) to validate
real development sessions through its strict client and checking proxy.

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

## What runs where

- Rust handles LSP framing, process supervision and the client lifecycle gate.
- `src/lifecycle.rs`・`src/capabilities.rs`・`src/recovery.rs` は、lifecycle・capability・旧応答の抑止・worker 復旧の判定規則と定理を Lean で定義する。leanfran が生成した Rust 関数を実際の転送経路で呼ぶ。
- `native/Main.lean` embeds upstream `Lean.Server`, retaining its watchdog,
  per-document workers, request handlers, widgets and RPC. It caches completed
  document reference indexes for highlighting and invalidates them on edits.

Rust currently supervises one native watchdog; upstream Lean still manages
the per-document workers. Project-wide indexing, worker scheduling and resource
budgets are subsequent architecture work. [Architecture](docs/architecture.md)
describes these boundaries and the role of leanfran.

## Verify

```bash
nix develop --command make check
nix build
```

`make check` は native backend と Rust host のビルド、format、Clippy、Lean の定理 16 件、Rust の単体テスト 9 件、capability の通信テスト 5 ケース、既存の 11 種類の要求と編集・UTF-16・複数ファイルの回帰検査を実行する。`make check-acceptance` では、同じ Lean 4.35.0 の `lean --server` と Leansail を起動し、上流の全 59 protocol entry、キャンセル、編集後の旧要求、実際のプロセスを kill する障害検査を実行する。このターゲットは `make check` と CI に含まれる。

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

proxy の計測には検査器の負荷が含まれる。性能は直接接続して計測する。エディタとの手動接続と実用サイズのプロジェクトでの性能評価は、実通信の比較検査に加えて行う。

編集回帰を proxy で検査した場合も server・client のエラーは 0 件となる。次の警告は検査器の標準 LSP モデルとの違いを表す。件数は解析中の通知のタイミングに依存する。

| 側 | 指摘 | 理由 |
|---|---|---|
| server | `capability/client-did-not-declare` (`textDocument/publishDiagnostics`) | 検査クライアントは空の capability を送る。LSP の `publishDiagnostics` capability は診断の関連情報や版などの追加機能を表し、基本の push diagnostics を受け取るための opt-in ではない。 |
| server | `types/unknown-property` (`Diagnostic.fullRange`) | Lean の診断拡張。Lean のクライアントとの互換性のため転送する。 |
| client | `types/unknown-method` (`textDocument/waitForDiagnostics`) | 検証クライアントが解析の完了を待つために使う Lean 拡張。 |

capability の意味と refresh 応答は [LSP semantic tokens](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#semanticTokens_refreshRequest)、[inlay hints](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_inlayHint_refresh)、[ファイル監視](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_didChangeWatchedFiles)、[診断通知](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_publishDiagnostics) に従う。

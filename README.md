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
- `src/lifecycle.rs` と `src/capabilities.rs` は、lifecycle と capability の判定規則と定理を Lean で定義する。leanfran が生成した Rust 関数を実際の転送経路で呼ぶ。
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

The checks build the native backend and Rust host, check the embedded Lean
proofs when generating code, run Clippy and unit tests, and exercise 11 LSP
request methods plus lifecycle, editing, cursor movement, UTF-16, multi-file
and syntax highlight regressions.

Commit `lean/` and `.leanfran/cache/` with embedded Lean changes. Cached Rust
builds do not need the Lean compiler. Regeneration uses the pinned leanfran
compiler in the development shell.

## LSP conformance

```bash
nix develop --command make check-protocol
```

lsp-touchstone の strict client で実際の Leansail を検査し、`test-out/strict.ndjson` に記録する。この検査は server・client ともにエラー 0 件、警告 0 件で成功する。検査対象は初期化、ファイルの開閉、document symbol、hover、終了の通信である。上流の全 59 protocol entry の受け入れ検証は別途行う。

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

Proxy measurements include checker overhead; use direct sessions to measure
server performance. Manual editor integration, cancellation latency and worker
crash recovery require further acceptance testing.

編集回帰を proxy で検査した場合も server・client のエラーは 0 件となる。次の警告は検査器の標準 LSP モデルとの違いを表す。件数は解析中の通知のタイミングに依存する。

| 側 | 指摘 | 理由 |
|---|---|---|
| server | `capability/client-did-not-declare` (`textDocument/publishDiagnostics`) | 検査クライアントは空の capability を送る。LSP の `publishDiagnostics` capability は診断の関連情報や版などの追加機能を表し、基本の push diagnostics を受け取るための opt-in ではない。 |
| server | `types/unknown-property` (`Diagnostic.fullRange`) | Lean の診断拡張。Lean のクライアントとの互換性のため転送する。 |
| client | `types/unknown-method` (`textDocument/waitForDiagnostics`) | 検証クライアントが解析の完了を待つために使う Lean 拡張。 |

capability の意味と refresh 応答は [LSP semantic tokens](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#semanticTokens_refreshRequest)、[inlay hints](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_inlayHint_refresh)、[ファイル監視](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_didChangeWatchedFiles)、[診断通知](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_publishDiagnostics) に従う。

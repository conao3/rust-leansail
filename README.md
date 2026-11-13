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
- `src/lifecycle.rs` embeds the Lean state machine and its theorems. leanfran
  generates the Rust function called by the actual message forwarding path.
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

This runs the lsp-touchstone strict client against Leansail's real server
command. It writes `test-out/strict.ndjson`. The inherited backend currently
sends refresh requests without checking client refresh support, so this check
can report errors and exit 254. A successful smoke test is not a claim that all
59 upstream protocol entries have passed conformance testing.

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

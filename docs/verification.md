# Verification

Leansail checks its behavior against upstream `lean --server` from the same Lean 4.35.0 toolchain. This page lists what each check covers, where it writes results and which differences it ignores.

## Commands

| Command | What it checks |
| --- | --- |
| `nix develop --command make check` | Format, lint, Lean theorems, unit tests, regression checks, `make check-acceptance` and `make check-editors` |
| `nix develop --command make check-acceptance` | The upstream protocol comparison, cancellation and fault recovery |
| `nix develop --command make check-editors` | Real Emacs and Neovim sessions against upstream |
| `nix develop --command make check-protocol` | The lsp-touchstone strict client |
| `nix build` | The packaged binary with its bundled backend |

CI runs `make check`, `nix build` and `make check-protocol`. Since `make check` includes `make check-editors`, CI also runs the real editor checks.

## What `make check` runs

`make check` builds the native backend and the Rust host, then runs:

- format checks and Clippy
- 29 leanfran theorems, their audit with lean-assay (`lake -d audit build --wfail`), and 6 native backend equivalence theorems
- 9 Rust unit tests
- 5 capability communication test cases
- regression checks for the existing 11 request kinds, plus edits, UTF-16 and multiple files
- semantic tokens checks: repeated requests on the same version, edits, reopening, and the transition from partial responses to completion, all compared against upstream
- `make check-acceptance` and `make check-editors`

## Upstream protocol comparison

`make check-acceptance` starts `lean --server` and Leansail from the same Lean 4.35.0 and runs 59 upstream protocol entries against both:

| Kind | Entries |
| --- | ---: |
| Requests | 34 |
| Client notifications | 10 |
| Server→client requests | 3 |
| Server notifications | 2 |
| Widget RPC calls | 10 |

All 59 entries compare successfully. [tests/protocol.json](../tests/protocol.json) lists the entries. At run time, the test also checks that list against `ProtocolOverview.lean` from the pinned toolchain. [Acceptance testing](acceptance.md) describes the comparison method, the scope and the per-entry results.

The comparison normalizes a few things that depend on timing or process state:

- Opaque RPC IDs are normalized.
- Notifications are compared by final state, and server→client requests by the set of requests.
- Notification counts, arrival order and progress ranges during analysis depend on timing, so they're left out.

The check also covers cancellation, stale requests after edits, and fault checks that kill real processes.

### Result files

| File | Contents |
| --- | --- |
| `target/conformance.json` | The result for each of the 59 entries, plus the cancellation results of upstream and Leansail |
| `target/recovery.json` | The fault results |
| `target/*-protocol.json`, `target/*-cancellation.json` | Full traffic of the protocol and cancellation runs |
| `target/worker_*.json`, `target/watchdog_*.json` | Full traffic of the fault runs |

## Cancellation and fault recovery

| Situation | Leansail's behavior |
| --- | --- |
| Request after `$/cancelRequest` | Returns `-32800` |
| Request invalidated by an edit | Returns `-32801` |
| Request on the latest document | Returns the latest type |
| FileWorker crash | Forwards the fatal `$/lean/fileProgress` and `-32902` for in-flight requests. On the next request for that document, recovers the worker from the same version and content. |
| Watchdog crash | Sends `-32603` to pending requests and `window/showMessage` to the client, then exits with code 255 |

The fault checks cover both the idle and the in-request cases.

## Real editor comparison

```bash
nix develop --command make check-editors
LEANSAIL_TEST_BINARY="$PWD/result/bin/leansail" nix develop --command python3 tests/editors.py
```

The second command checks the binary that `nix build` produced. To check a single editor, use `python3 tests/editors.py --editor emacs` or `--editor neovim`.

The development environment pins both editors. Emacs starts with `--batch -Q` and Neovim with `--headless -u NONE`, so personal settings don't apply. Each editor runs 2 sessions, one with upstream and one with Leansail.

Flymake in Emacs 31 only diagnoses trusted files (`trusted-content`), and the script trusts only the fixture files it creates. The script uses lean4-mode when it can `require` it. The pinned nixpkgs and verification environment don't provide lean4-mode, so the check uses a mode derived from `prog-mode`, and lean4-mode's own infoview needs a separate check.

| Operation | Emacs / Eglot | Neovim / built-in LSP |
| --- | --- | --- |
| Startup and initialization | capability and response bodies match | capability and response bodies match |
| Diagnostics display | Flymake list and 2 errors match | location list and 2 errors match |
| hover | ElDoc's `editorValue : Nat` matches | floating window's `editorValue : Nat` matches |
| Go to definition | xref moves to the definition's line and column | built-in go to definition moves to the same position |
| Completion | CAPF candidates including `succ` match | `C-x C-o` candidates including `succ` match |
| `$/lean/plainGoal` | `n : Nat ⊢ n = n` matches | same goal matches |
| Diagnostics after continuous typing | `false` type error and 0 errors after the fix match | same updated results match |
| close and reopen | 2 diagnostics come back on the same server | 2 diagnostics come back on the same server |
| shutdown and exit | Leansail exits with code 0. Eglot force-kills upstream with code 9. | both exit with code 0 |

### What the editor check compares

The check compares these against upstream:

- response bodies of the main requests
- the latest diagnostics bodies after the initial open, continuous typing, the fix and reopening

It leaves out these:

- background semantic tokens, colors and inlay hints that edits cancel, which may return `-32800` or `-32801`
- notification counts and intermediate arrival order

The editor checks use small Lean documents and edits that depend only on the standard library. `make bench-mathlib` measures performance on Mathlib; see [measurement steps and conditions](mathlib-benchmark.md). GUI-specific display needs a separate check.

### Editor result files

| File | Contents |
| --- | --- |
| `target/editors/summary.json` | Per-operation results |
| `target/editors/*.json` | What the editor displayed |
| `target/editors/*.ndjson` | Full traffic through the transparent proxy |
| `target/editors/*.editor.log`, `target/editors/*.server.log` | Editor and server logs |

### Exit with Eglot

Upstream Lean 4.35.0 accepts `exit` without parameters after `shutdown`. Eglot sends `exit` with `params: {}`. Upstream keeps waiting on that, and Eglot force-kills it after about 0.3 seconds.

After the lifecycle accepts `exit`, Leansail forwards the original frame and closes the backend's input. The backend also exits on EOF, so Eglot's notification ends it normally. The tests check all 3 forms of `params`: omitted, `{}` and `null`.

## LSP conformance with lsp-touchstone

```bash
nix develop --command make check-protocol
```

The [lsp-touchstone](https://github.com/conao3/rust-lsp-touchstone) strict client drives the real Leansail and records the session to `test-out/strict.ndjson`. It covers initialization, opening and closing files, document symbol, hover and shutdown. This check passes with 0 errors and 0 warnings on both the server and the client side. The full upstream protocol coverage comes from the 59 entries in `make check-acceptance`.

### Editing regression through the proxy

To run the editing regression client through the lsp-touchstone checking proxy:

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

This run gives 0 server errors and 0 client errors. Timings through the proxy include the checker's overhead, so performance numbers come from a direct connection. `make check-editors` evaluates editing in real editors, and separate measurements cover performance on realistically sized projects.

The run reports the warnings below. They reflect differences from the checker's standard LSP model, and their counts depend on the timing of notifications during analysis.

| Side | Finding | Reason |
| --- | --- | --- |
| server | `capability/client-did-not-declare` (`textDocument/publishDiagnostics`) | The checking client sends empty capabilities. LSP's `publishDiagnostics` capability describes extras such as related information and versions. It isn't an opt-in for receiving basic push diagnostics. |
| server | `types/unknown-property` (`Diagnostic.fullRange`) | Lean diagnostics extension. Leansail forwards it for compatibility with Lean clients. |
| client | `types/unknown-method` (`textDocument/waitForDiagnostics`) | Lean extension the verification client uses to wait for analysis to finish. |

## Client capability forwarding

The host records `initialize.params.capabilities` and uses it to decide which server→client requests reach the client.

- Refresh requests for semantic tokens, inlay hints, code lens, inline values, diagnostics and folding ranges go to the client only when the matching `refreshSupport` is `true`. For a suppressed request, the host answers the backend with the original ID and `result: null`.
- Dynamic registration for `workspace/didChangeWatchedFiles` requires `dynamicRegistration: true`. If a request contains an unsupported registration, the host rejects the whole request and returns `-32601` to the backend.
- For clients that declare support, the host forwards requests and responses as the original frames.

The decision rules live in Lean in `src/capabilities.rs`, and 6 theorems check them. The Rust side reads JSON and delivers responses. `make check` runs the real host against a test backend and checks the communication and shutdown for missing, `false`, wrongly typed, partial and full capabilities.

Capability meanings and refresh responses follow the LSP 3.17 specification for [semantic tokens](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#semanticTokens_refreshRequest), [inlay hints](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_inlayHint_refresh), [file watching](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_didChangeWatchedFiles) and [diagnostics notifications](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_publishDiagnostics).

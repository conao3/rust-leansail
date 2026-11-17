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
  <a href="#editor-setup">Editor setup</a> ·
  <a href="#what-runs-where">What runs where</a> ·
  <a href="#mathlib-measurements">Mathlib measurements</a> ·
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

## Editor setup

Verified with Emacs 31.1 / Eglot 1.24.31 and the built-in LSP client of
Neovim 0.12.6. Put `leansail` on PATH and start it from a Lean 4.35.0 project.
When using dependency libraries, change the commands below to
`lake env leansail server run-server`.

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

Emacs 31's Flymake diagnoses files in projects the user has trusted with
`M-x customize-option RET trusted-content`. The verification script trusts only
the fixture files it creates itself. Use `M-x flymake-show-buffer-diagnostics`
for the diagnostics list, ElDoc for hover, `M-.` for go to definition, and
`C-M-i` for completion.

If you have lean4-mode installed, set the Eglot registration and hook above for
`leansail-lean-mode` on `lean4-mode` instead. The verification script also
picks lean4-mode when it can `require` it. The pinned nixpkgs and verification
environment use the derived mode above, so lean4-mode's own infoview is checked
separately.

The following command shows the goal:

```elisp
(defun leansail-show-goal ()
  (interactive)
  (let ((goal (jsonrpc-request (eglot-current-server) :$/lean/plainGoal
                               (eglot--TextDocumentPositionParams))))
    (with-help-window "*Lean goal*"
      (princ (plist-get goal :rendered)))))
```

### Neovim / built-in LSP

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

`exit_timeout` is how long (ms) the editor waits for the `shutdown` response
and `exit` when it quits. Neovim 0.12.6's default `false` makes that wait 0.
Use `vim.diagnostic.setloclist()` for the diagnostics list, `K` for hover,
`vim.lsp.buf.definition()` for go to definition, and `C-x C-o` in insert mode
for completion. The following command shows the goal:

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
- `src/lifecycle.rs`, `src/capabilities.rs` and `src/recovery.rs` define the decision rules and theorems for the lifecycle, capabilities, suppression of stale responses and worker recovery in Lean. The real forwarding path calls the Rust functions leanfran generates from them.
- `native/Main.lean` embeds upstream `Lean.Server`, retaining its watchdog,
  per-document workers, request handlers, widgets and RPC. It reuses the highlight index and semantic tokens responses of analyzed documents. Module names for the index come directly from the import array, and their equivalence with upstream is checked in Lean.

The native Watchdog manages per-file workers. This boundary stays, based on the comparison of foreground p95 and PSS on 3 Mathlib documents with 6 cores. The [design](docs/architecture.md) defines the host's responsibilities and leanfran's decision rules.

## Mathlib measurements

Official Lean 4.35.0 `lean --server` and the current release host with the native backend run alternately on the same Mathlib v4.35.0 Lake workspace on helios. Each keeps 3 documents open, and requests after analysis and index preparation are measured over 5 sessions per configuration. Leansail loads a prebuilt persistent index, and a cache hit is required.

Below are the median / p95 (ms) for IntegralEqImproper.lean and the upstream / Leansail ratio. First requests have 5 samples each, and repeated requests have 50 samples each.

| Item | Upstream median / p95 | Leansail median / p95 | Ratio median / p95 |
| --- | ---: | ---: | ---: |
| Highlight, first | 673.635 / 794.671 | 117.852 / 121.331 | 5.72 / 6.55 |
| Highlight, repeated | 672.346 / 694.872 | 0.924 / 1.094 | 727.37 / 634.90 |
| semantic tokens, repeated | 74.716 / 81.904 | 3.963 / 9.207 | 18.85 / 8.90 |
| Completion, repeated | 322.236 / 360.081 | 70.428 / 97.846 | 4.58 / 3.68 |
| workspace/symbol, repeated | 850.690 / 871.502 | 87.456 / 89.395 | 9.73 / 9.75 |
| References, repeated | 20.872 / 23.427 | 13.106 / 14.219 | 1.59 / 1.65 |
| Definition, repeated | 21.493 / 22.622 | 13.333 / 14.388 | 1.61 / 1.57 |
| hover, repeated | 1.364 / 1.713 | 1.445 / 1.564 | 0.94 / 1.10 |
| plainGoal, repeated | 1.446 / 1.751 | 1.573 / 2.135 | 0.92 / 0.82 |

| Item | Upstream median / p95 | Leansail median / p95 | Ratio median / p95 |
| --- | ---: | ---: | ---: |
| Startup to first non-empty diagnostics | 6884.395 / 10684.064 | 7015.356 / 7273.548 | 0.98 / 1.47 |
| Startup to project index ready | 21418.880 / 21545.757 | 5519.296 / 5563.834 | 3.88 / 3.87 |
| Total PSS right after analyzing 3 documents | 3596.155 / 3604.026 | 2526.203 / 2528.910 | 1.42 / 1.43 |
| Total PSS after requests on 3 documents | 3767.910 / 3781.936 | 3126.693 / 3139.848 | 1.21 / 1.20 |

Startup values are in ms, and total PSS for 3 documents is in MiB. Startup to index ready is measured in separate sessions that don't open documents. For all 3 documents and all completed runs, the first and repeated response bodies and the completion resolve results match upstream.

See [tables for all 3 documents, accepted and excluded runs, JSON and graphs, and reproduction steps](docs/mathlib-benchmark.md#%E6%9C%AC%E5%AE%B6%E3%81%A8%E3%81%AE%E6%AF%94%E8%BC%83).

## Verify

```bash
nix develop --command make check
nix build
```

`make check` builds the native backend and Rust host and runs format, Clippy, 21 leanfran theorems and 6 native backend equivalence theorems, 9 Rust unit tests, 5 capability communication test cases, and regression checks for the existing 11 request kinds plus edits, UTF-16 and multiple files. For semantic tokens, it compares repeated requests on the same version, edits, reopening, and the transition from partial responses to completion against upstream. `make check-acceptance` starts `lean --server` from the same Lean 4.35.0 and Leansail, and runs all 59 upstream protocol entries, cancellation, stale requests after edits, and fault checks that kill real processes. CI runs all of this through `make check`, including the real editor checks of `make check-editors`.

### Real editor comparison

```bash
nix develop --command make check-editors
LEANSAIL_TEST_BINARY="$PWD/result/bin/leansail" nix develop --command python3 tests/editors.py
```

Emacs starts with `--batch -Q` and Neovim with `--headless -u NONE`, independent
of personal settings. The development environment pins both editors, and each
editor runs 2 sessions, one with upstream and one with Leansail. To check a
single editor, use `python3 tests/editors.py --editor emacs` or `--editor neovim`.

| Operation                           | Emacs / Eglot                                                             | Neovim / built-in LSP                                |
| ----------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------- |
| Startup and initialization          | capability and response bodies match                                      | capability and response bodies match                 |
| Diagnostics display                 | Flymake list and 2 errors match                                           | location list and 2 errors match                     |
| hover                               | ElDoc's `editorValue : Nat` matches                                       | floating window's `editorValue : Nat` matches        |
| Go to definition                    | xref moves to the definition's line and column                            | built-in go to definition moves to the same position |
| Completion                          | CAPF candidates including `succ` match                                    | `C-x C-o` candidates including `succ` match          |
| `$/lean/plainGoal`                  | `n : Nat ⊢ n = n` matches                                                 | same goal matches                                    |
| Diagnostics after continuous typing | `false` type error and 0 errors after the fix match                       | same updated results match                           |
| close and reopen                    | 2 diagnostics come back on the same server                                | 2 diagnostics come back on the same server           |
| shutdown and exit                   | Leansail exits with code 0. Upstream is force-killed by Eglot with code 9 | both exit with code 0                                |

Upstream Lean 4.35.0 accepts `exit` without parameters after `shutdown`.
For Eglot's `exit` with `params: {}`, it keeps waiting, and Eglot force-kills it
after about 0.3 seconds. After the lifecycle accepts `exit`, Leansail forwards
the original frame and closes the backend's input. The backend can also exit on
EOF, so the same Eglot notification ends it normally. The 3 cases of omitted,
`{}` and `null` `params` are also checked automatically.

`target/editors/summary.json` holds the per-operation results, each `*.json`
holds what the editor displayed, `*.ndjson` holds the full traffic through the
transparent proxy, and `*.editor.log` and `*.server.log` hold the logs.
Response bodies of the main requests and the latest diagnostics bodies after the
initial open, continuous typing, the fix and reopening are checked against
upstream. Background semantic tokens, colors and inlay hints cancelled during
edits may return `-32800` or `-32801`, and notification counts and intermediate
arrival order are left out of the comparison.
The editor checks cover small Lean documents and edits that use the standard library. Mathlib performance is measured with `make bench-mathlib`. See [measurement steps and conditions](docs/mathlib-benchmark.md). GUI-specific display is checked separately.

The 59 entries are 34 requests, 10 client notifications, 3 server→client requests, 2 server notifications and 10 Widget RPC calls. All entries compare successfully. Opaque RPC IDs are normalized, and notifications are compared by final state and the set of requests. Notification counts, arrival order and progress ranges during analysis depend on timing, so they're left out of the comparison. [Acceptance testing](docs/acceptance.md) describes the comparison method and scope, and [tests/protocol.json](tests/protocol.json) lists the entries. At run time, the list is also checked against `ProtocolOverview.lean` from the pinned toolchain.

`target/conformance.json` holds the result for each of the 59 entries and the cancellation results of upstream and Leansail, and `target/recovery.json` holds the fault results. The full traffic is saved in the same directory as `*-protocol.json`, `*-cancellation.json`, `worker_*.json` and `watchdog_*.json`.

Requests after `$/cancelRequest` return `-32800`, requests invalidated by edits return `-32801`, and requests on the latest document are checked to return the latest type. When a FileWorker crashes, the host forwards the fatal `$/lean/fileProgress` and `-32902` for in-flight requests, and on the next document request recovers the worker from the same version and content. When the Watchdog crashes, the host sends `-32603` to pending requests and `window/showMessage` to the client, then exits with code 255. Both the idle and in-request cases are checked.

When you change Lean definitions, include `lean/` and `.leanfran/cache/` in the same commit. They're generated with the pinned leanfran of the development environment, and Rust builds that use the cache read the generated code.

## LSP conformance

```bash
nix develop --command make check-protocol
```

The lsp-touchstone strict client checks the real Leansail and records the session to `test-out/strict.ndjson`. This check passes with 0 errors and 0 warnings on both the server and client sides. It covers initialization, opening and closing files, document symbol, hover and shutdown. The comparison in `make check-acceptance` covers all 59 upstream protocol entries.

The host records `initialize.params.capabilities` and forwards refresh requests for semantic tokens, inlay hints, code lens, inline values, diagnostics and folding ranges only when the matching `refreshSupport` is `true`. For a suppressed request, it returns the original ID with `result: null` to the backend. Dynamic registration for `workspace/didChangeWatchedFiles` requires `dynamicRegistration: true`. The host rejects the whole request that contains an unsupported registration and returns `-32601` to the backend. For clients that declare support, requests and responses are forwarded as the original frames.

The decision rules are defined in Lean in `src/capabilities.rs` and checked by 6 theorems. The Rust side reads JSON and delivers responses. `make check` checks the communication and shutdown between the real host and a test backend for missing, `false`, wrongly typed, partial and full capabilities.

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

Proxy measurements include the checker's overhead. Performance is measured over a direct connection. Editing in real editors is evaluated with `make check-editors`, and performance on realistically sized projects with separate measurements.

Checking the editing regression through the proxy also gives 0 server and client errors. The following warnings reflect differences from the checker's standard LSP model. Their counts depend on the timing of notifications during analysis.

| Side   | Finding                                                                 | Reason                                                                                                                                                                                                                   |
| ------ | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| server | `capability/client-did-not-declare` (`textDocument/publishDiagnostics`) | The checking client sends empty capabilities. LSP's `publishDiagnostics` capability describes extras for diagnostics such as related information and versions, and isn't an opt-in for receiving basic push diagnostics. |
| server | `types/unknown-property` (`Diagnostic.fullRange`)                       | Lean diagnostics extension. It's forwarded for compatibility with Lean clients.                                                                                                                                          |
| client | `types/unknown-method` (`textDocument/waitForDiagnostics`)              | Lean extension the verification client uses to wait for analysis to finish.                                                                                                                                              |

Capability meanings and refresh responses follow [LSP semantic tokens](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#semanticTokens_refreshRequest), [inlay hints](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_inlayHint_refresh), [file watching](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#workspace_didChangeWatchedFiles) and [diagnostics notifications](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_publishDiagnostics).

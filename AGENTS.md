# Development

Leansail is a Rust language-server host with a native Lean backend. Pure protocol
rules are written in Lean and compiled into Rust with leanfran. Keep native Lean
objects and the leanfran runtime separate; communicate through LSP frames.

- Use English for code, tests, commit messages and documentation.
- Keep user-visible source strings in English; use translation resources for
  additional languages.
- Run `nix develop --command make check` and `nix build` before committing.
- Run `nix fmt` before reviewing the staged diff.
- Commit `lean/` and `.leanfran/cache/` whenever embedded Lean changes.
- Use lsp-touchstone as a development dependency to validate real sessions;
  record remaining conformance findings honestly.
- Keep worker isolation and the complete upstream Lean protocol as design goals.

## Lean and Rust

The `lean!` blocks in `src/lifecycle.rs`, `src/capabilities.rs`, `src/recovery.rs`
and `src/index_cache.rs` define the protocol decisions called by the Rust host.
Rust owns JSON, LSP frames, files, hashing and process management. The native
backend owns Lean objects and the upstream language-server implementation.

The lifecycle theorems describe legal protocol progress and rejection:

- `exit_is_always_accepted`: every phase accepts exit.
- `exited_is_terminal`: an exited session stays exited or rejects the event.
- `running_has_a_valid_predecessor`: running is reached exactly by completing
  initialization, serving a request while running, or failing shutdown.
- `requests_require_running`: ordinary requests are accepted exactly while running.
- `initializing_iff_initialize`: initialization starts exactly from a fresh session.
- `ready_iff_initialize_success`: readiness requires a successful pending initialization.
- `fresh_iff_initialize_failure`: a failed initialization permits a retry.
- `stopping_iff_shutdown`: shutdown starts exactly from a running session.
- `stopped_iff_shutdown_success`: a session stops exactly when pending shutdown succeeds.
- `exited_iff_exit`: only an exit event enters the exited phase.

The capability theorems protect the client's declared support:

- `refresh_requires_support`: each of the six supported refresh methods is forwarded
  exactly when the client declares its support.
- `unsupported_refresh_is_acknowledged`: each unsupported refresh is acknowledged locally.
- `watched_files_require_dynamic_registration`: file-watcher registration follows
  the client's dynamic-registration flag.
- `registration_requires_all_entries`: registration is forwarded exactly when all
  entries are allowed.
- `unsupported_registration_is_rejected`: a disallowed entry rejects registration.
- `unrelated_requests_are_forwarded`: unrelated requests pass through.
- `unrelated_registrations_are_allowed`: registration of another method passes through.
- `refresh_capabilities_are_independent`: the six refresh methods use distinct
  capability paths, so enabling one does not enable another.

The recovery theorems protect pending requests and worker restarts:

- `cancelled_response_is_rejected`: cancellation takes priority over document version.
- `stale_response_is_rejected`: an uncancelled stale response is reported as modified.
- `forwarded_response_is_current`: forwarded responses are current and uncancelled.
- `restart_requires_crash`: a healthy worker is kept.
- `restart_requires_open_request`: restarting requires an open document and a request.
- `crashed_open_request_restarts`: a request for an open document restarts its crashed worker.

The cache theorems protect the stored index identity:

- `valid_requires_current_files`: an accepted cache matches the current files.
- `valid_requires_current_version`: an accepted cache matches the format version.
- `valid_requires_current_toolchain`: an accepted cache matches the toolchain.
- `valid_requires_current_payload`: an accepted cache matches the payload hash.
- `matching_cache_is_valid`: a cache matching all identity fields is accepted.

## Generation and verification

`cargo build` in the dev shell generates `lean/LeanRustGen/Embed/leansail/` and
`.leanfran/cache/` from the embedded Lean blocks. Commit these generated files
with their source. The source-path hash identifies each generated module.

`audit/` is a separate Lake package using Lean 4.35.0 and lean-assay pinned in its
manifest. Each embedded block has its own audit module because the blocks share
`namespace model`. Its targets are `transition`, `refreshCapability`,
`registrationAllowed`, `decide`, `restartWorker`, `responseDecision` and `cacheValid`.
A generated function called by Rust gets a useful theorem and an `#assay` command
in the same change.
All seven generated functions called by Rust are included. The audit covers
420 mutants: 385 refuted, 0 holes, 0 untested, 0 equivalent, 0 timed out and
35 rejected during typechecking.

| Function | Mutants | Refuted | Equivalent | Rejected during typechecking |
| --- | ---: | ---: | ---: | ---: |
| `transition` | 338 | 303 | 0 | 35 |
| `refreshCapability` | 44 | 44 | 0 | 0 |
| `registrationAllowed` | 6 | 6 | 0 | 0 |
| `decide` | 9 | 9 | 0 | 0 |
| `restartWorker` | 8 | 8 | 0 | 0 |
| `responseDecision` | 5 | 5 | 0 | 0 |
| `cacheValid` | 10 | 10 | 0 | 0 |

`lake -d audit build --wfail` rejects holes, missing theorems, vacuous hypotheses
and functions whose theorems refute no mutant. An equivalent mutant means equal
outputs on the inputs tested, subject to the theorems' hypotheses; it is not a
proof of equivalence.

- `nix develop --command lake -d audit build --wfail`: audit the generated functions.
- `nix develop --command make check`: build both backends, format, lint, check Lean
  proofs and their audit, run Rust and protocol regressions, compare upstream
  acceptance and drive Emacs/Eglot and Neovim.
- `nix build`: build the packaged host and native backend.
- `nix develop --command make check-protocol`: check a real LSP session with lsp-touchstone.

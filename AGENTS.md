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

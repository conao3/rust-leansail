.PHONY: build
build:
	$(MAKE) -C native build
	cargo build -j 4

.PHONY: check
check: build
	nix fmt -- --ci
	ruff check tests
	ruff format --check tests
	cargo clippy --all-targets -j 4 -- -D warnings
	lake -d lean build
	cargo test -j 4
	python3 tests/index_cache.py
	timeout 30s python3 tests/capabilities.py
	python3 tests/baseline.py --lean "$$(pwd)/tests/command" --output target/session.json --samples 2 --declarations 30 --edits 2 --regression --lifecycle-check
	python3 tests/semantic_tokens.py
	$(MAKE) check-acceptance
	$(MAKE) check-editors

.PHONY: check-acceptance
check-acceptance: build
	python3 tests/conformance.py
	python3 tests/recovery.py

.PHONY: check-protocol
check-protocol: build
	mkdir -p test-out
	lsp-touchstone check check-server --record-file test-out/strict.ndjson --open-file tests/fixtures/Dependency.lean --language-id lean4 --server-command-args target/debug/leansail server run-server --backend "$$(pwd)/native/.lake/build/bin/leansail-backend"

.PHONY: check-editors
check-editors: build
	python3 tests/editors.py

.PHONY: bench-mathlib
bench-mathlib:
	cargo build --release -j 4
	python3 tests/mathlib.py $(BENCH_ARGS)

.PHONY: bench-mathlib-parallel
bench-mathlib-parallel:
	cargo build --release -j 4
	python3 tests/mathlib_parallel.py $(BENCH_ARGS)

.PHONY: bench-mathlib-startup
bench-mathlib-startup:
	cargo build --release -j 4
	python3 tests/mathlib_startup.py --startup-only $(BENCH_ARGS)

.PHONY: bench-mathlib-index
bench-mathlib-index:
	cargo build --release -j 4
	python3 tests/mathlib_startup.py $(BENCH_ARGS)

.PHONY: bench-mathlib-index-prime
bench-mathlib-index-prime:
	cargo build --release -j 4
	python3 tests/mathlib_index_prime.py $(BENCH_ARGS)

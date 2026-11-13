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
	python3 tests/baseline.py --lean "$$(pwd)/tests/command" --output target/session.json --samples 2 --declarations 30 --edits 2 --regression --lifecycle-check

.PHONY: check-protocol
check-protocol: build
	mkdir -p test-out
	lsp-touchstone check check-server --record-file test-out/strict.ndjson --open-file native/Main.lean --language-id lean4 --server-command-args target/debug/leansail server run-server --backend "$$(pwd)/native/.lake/build/bin/leansail-backend"

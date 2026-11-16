.PHONY: build
build:
	lake build
	mkdir -p .lake/build/src
	ln -sfn "$$(lean --print-prefix)/src/lean" .lake/build/src/lean

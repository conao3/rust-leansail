# Mathlib upstream comparison

Mathlib v4.35.0: `56a005855ea17e0b0958f73ec801ae140b0a1679`. Official Lean 4.35.0 (`eb6cafb7290e8bd5f2937522d8d804f58f02bafc`).
Leansail: `67d191376c7025d718c3c0f902d05056a184cc60` (host and backend SHA-256 and source hashes are in raw.json).
Host: Linux-6.8.0-1060-aws-x86_64-with-glibc2.44; 8 logical CPUs; Intel(R) Xeon(R) Processor; 32866576 KiB RAM.
Iterations: 5 sessions per server, 10 repeated requests per method/file/session. Server order alternates between sessions. Edits are not used by this scenario.

Both servers use the same cached Mathlib files and request fixtures. Requests follow completed diagnostics and index readiness; Leansail must hit its primed persistent index. These are warm-cache, not cold-start results. p95 uses the existing harness's nearest-rank estimator. Small samples and shared VM scheduling limit generalization. Ratios are upstream / Leansail (above 1 favors Leansail). All durations are milliseconds.

Reproduce: `nix develop --command python3 tests/bench_mathlib_upstream.py --output-dir results/new-run` (choose a fresh directory for each run).
Use `--samples` and `--repetitions` to change counts; `--output-dir` selects a fresh destination. Requires Nix, Git, and network access. Only the cache downloader and Leansail backend are built, not Mathlib.

External CPU limit: 0.75 cores. Preflight skips: 0; load-rejected sessions: 0; interruptions: 0.

## Mathlib/MeasureTheory/Integral/IntegralEqImproper.lean

| Request / phase | Upstream median / p95 | Leansail median / p95 | Ratio median / p95 |
| --- | ---: | ---: | ---: |
| textDocument/documentHighlight / first | 355.526 / 366.288 | 113.391 / 141.101 | 3.14 / 2.60 |
| textDocument/documentHighlight / repeated | 337.176 / 355.996 | 1.026 / 1.666 | 328.76 / 213.70 |
| textDocument/hover / first | 2.331 / 2.955 | 1.619 / 1.844 | 1.44 / 1.60 |
| textDocument/hover / repeated | 1.252 / 2.099 | 1.274 / 1.654 | 0.98 / 1.27 |
| textDocument/definition / first | 26.491 / 27.226 | 29.788 / 32.167 | 0.89 / 0.85 |
| textDocument/definition / repeated | 16.897 / 22.083 | 8.983 / 11.163 | 1.88 / 1.98 |
| textDocument/references / first | 16.622 / 17.417 | 9.274 / 14.099 | 1.79 / 1.24 |
| textDocument/references / repeated | 13.040 / 14.718 | 8.055 / 10.435 | 1.62 / 1.41 |
| textDocument/semanticTokens/full / first | 98.091 / 105.108 | 88.587 / 95.495 | 1.11 / 1.10 |
| textDocument/semanticTokens/full / repeated | 83.683 / 94.080 | 2.899 / 4.215 | 28.86 / 22.32 |
| textDocument/completion / first | 800.475 / 809.111 | 727.738 / 742.190 | 1.10 / 1.09 |
| textDocument/completion / repeated | 266.896 / 283.779 | 60.619 / 74.529 | 4.40 / 3.81 |
| $/lean/plainGoal / first | 2.247 / 2.649 | 2.665 / 2.744 | 0.84 / 0.97 |
| $/lean/plainGoal / repeated | 1.473 / 2.164 | 1.545 / 2.321 | 0.95 / 0.93 |
| workspace/symbol / first | 721.298 / 959.956 | 79.997 / 80.323 | 9.02 / 11.95 |
| workspace/symbol / repeated | 698.962 / 786.731 | 70.134 / 76.997 | 9.97 / 10.22 |

## Mathlib/Topology/Algebra/Module/FiniteDimension.lean

| Request / phase | Upstream median / p95 | Leansail median / p95 | Ratio median / p95 |
| --- | ---: | ---: | ---: |
| textDocument/documentHighlight / first | 150.275 / 160.572 | 65.551 / 73.596 | 2.29 / 2.18 |
| textDocument/documentHighlight / repeated | 138.891 / 218.846 | 0.695 / 1.711 | 199.78 / 127.91 |
| textDocument/hover / first | 2.737 / 3.060 | 1.725 / 1.764 | 1.59 / 1.73 |
| textDocument/hover / repeated | 1.390 / 1.970 | 1.310 / 1.671 | 1.06 / 1.18 |
| textDocument/definition / first | 23.732 / 24.712 | 12.800 / 13.860 | 1.85 / 1.78 |
| textDocument/definition / repeated | 19.189 / 23.423 | 9.323 / 11.240 | 2.06 / 2.08 |
| textDocument/references / first | 18.711 / 21.653 | 8.793 / 9.218 | 2.13 / 2.35 |
| textDocument/references / repeated | 14.046 / 20.082 | 7.434 / 8.258 | 1.89 / 2.43 |
| textDocument/semanticTokens/full / first | 47.956 / 56.216 | 42.606 / 54.418 | 1.13 / 1.03 |
| textDocument/semanticTokens/full / repeated | 40.476 / 49.390 | 1.423 / 2.117 | 28.45 / 23.33 |
| textDocument/completion / first | 726.390 / 823.900 | 628.509 / 637.111 | 1.16 / 1.29 |
| textDocument/completion / repeated | 239.805 / 326.293 | 49.036 / 68.063 | 4.89 / 4.79 |
| $/lean/plainGoal / first | 3.612 / 9.228 | 3.694 / 3.897 | 0.98 / 2.37 |
| $/lean/plainGoal / repeated | 2.602 / 5.269 | 2.901 / 3.466 | 0.90 / 1.52 |
| workspace/symbol / first | 695.947 / 840.545 | 77.184 / 78.630 | 9.02 / 10.69 |
| workspace/symbol / repeated | 693.146 / 873.490 | 70.676 / 78.318 | 9.81 / 11.15 |

## Mathlib/Tactic.lean

| Request / phase | Upstream median / p95 | Leansail median / p95 | Ratio median / p95 |
| --- | ---: | ---: | ---: |
| textDocument/documentHighlight / first | 4.088 / 4.584 | 3.214 / 3.935 | 1.27 / 1.17 |
| textDocument/documentHighlight / repeated | 1.627 / 2.686 | 0.438 / 1.331 | 3.71 / 2.02 |
| textDocument/hover / first | 1.507 / 1.559 | 1.310 / 1.456 | 1.15 / 1.07 |
| textDocument/hover / repeated | 1.045 / 1.702 | 1.112 / 1.567 | 0.94 / 1.09 |
| textDocument/definition / first | 21.647 / 22.750 | 11.995 / 13.253 | 1.80 / 1.72 |
| textDocument/definition / repeated | 18.852 / 20.762 | 9.236 / 11.552 | 2.04 / 1.80 |
| textDocument/references / first | 15.892 / 16.846 | 7.742 / 9.626 | 2.05 / 1.75 |
| textDocument/references / repeated | 13.209 / 15.580 | 6.829 / 7.827 | 1.93 / 1.99 |
| textDocument/semanticTokens/full / first | 4.432 / 5.561 | 4.409 / 4.629 | 1.01 / 1.20 |
| textDocument/semanticTokens/full / repeated | 2.970 / 3.756 | 0.801 / 1.386 | 3.71 / 2.71 |
| textDocument/completion / first | 801.599 / 804.236 | 738.185 / 753.657 | 1.09 / 1.07 |
| textDocument/completion / repeated | 215.430 / 329.058 | 60.014 / 78.277 | 3.59 / 4.20 |
| $/lean/plainGoal / first | 1.776 / 2.460 | 2.082 / 2.196 | 0.85 / 1.12 |
| $/lean/plainGoal / repeated | 0.980 / 1.691 | 1.146 / 1.663 | 0.86 / 1.02 |
| workspace/symbol / first | 693.438 / 826.758 | 76.253 / 79.257 | 9.09 / 10.43 |
| workspace/symbol / repeated | 689.863 / 951.033 | 71.313 / 80.313 | 9.67 / 11.84 |

Raw response digests are retained for inspection. The harness checks nonempty successful responses and repeat stability within each server; this report does not assert cross-server response equivalence.

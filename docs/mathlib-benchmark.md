# Mathlib の計測

`make bench-mathlib` は Mathlib の実ファイルを開き、LSP の要求、編集後の解析、プロセス別 RSS を計測する。通常の `make check` と CI は小さい文書による機能の比較を実行する。Mathlib の依存と計測結果は `target/` に生成する。

## 再現手順

Linux と公式 Lean 4.35.0 の toolchain を使う。Mathlib は v4.35.0 の commit `56a005855ea17e0b0958f73ec801ae140b0a1679` に固定する。比較する `lean --server` と native backend は同じ toolchain の compiler、runtime、標準ライブラリを使い、host は release ビルドを使う。

```bash
elan toolchain install leanprover/lean4:v4.35.0
toolchain_prefix="$(elan run leanprover/lean4:v4.35.0 lean --print-prefix)"
nix develop --command make bench-mathlib \
  BENCH_ARGS="--prepare --toolchain $toolchain_prefix --samples 10 --repetitions 5 --edits 2 --wait-idle --output target/mathlib-results.json"
```

`--prepare` は小さい Lake プロジェクトを作り、固定した Mathlib を依存に加える。[Mathlib の cache コマンド](https://github.com/leanprover-community/mathlib4/blob/v4.35.0/Cache/README.md) の `lake exe cache get` で Mathlib 全体とその依存を取得し、import の検査と対象ごとの `lake setup-file` を済ませる。`--prepare-only` はここまでを実行する。以降は `--prepare` を省略して同じ workspace を再利用する。計測環境は約 7.3 GiB、プロジェクトと依存の `.ilean` は 9,049 本である。

Nix の Lean 4.35.0 は git hash が空で、同梱する leantar は 0.1.19 である。公式 toolchain は git hash `eb6cafb7290e8bd5f2937522d8d804f58f02bafc` と leantar 0.1.20 を持ち、公式 Mathlib キャッシュを利用できる。スクリプトは compiler の git hash を検査する。`--toolchain` はキャッシュの取得、Lake の準備、backend のビルド、両サーバーの実行に適用する。通常の Nix 配布物は `nix build` で検証する。

native backend のソースは `native/` から `target/mathlib-backend/` にコピーしてビルドする。`--backend` で比較したい保存済みの backend を指定できる。改修前を保存する場合は、実行ファイルの相対位置 `../src/lean` に同じ toolchain のソースへの symlink も作る。

## シナリオ

| 対象                                                                                                                                                                             |                             規模 | 選択の理由                                                         |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------: | ------------------------------------------------------------------ |
| [IntegralEqImproper.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/MeasureTheory/Integral/IntegralEqImproper.lean) |   77,968 bytes、直接 import 4 本 | この版の Mathlib で最大のソース。積分の証明を再 elaboration する。 |
| [FiniteDimension.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/Topology/Algebra/Module/FiniteDimension.lean)      |  40,074 bytes、直接 import 14 本 | import が多く、実際の証明を持つ。                                  |
| [Tactic.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/Tactic.lean)                                                | 18,296 bytes、直接 import 397 本 | 多数の tactic を import する。                                     |

3 文書を順番に開き、最後まで開いた状態にする。ディスク上の Mathlib ソースをそのまま読み、末尾に小さい `twice` の定義、証明、`#check` を追加した未保存の文書を送る。hover、定義、参照、ハイライト、補完はこの共通の識別子、plainGoal はその証明の位置で測る。semantic tokens は Mathlib のソース全体が対象である。参照検索の件数は開いた文書と非同期に読み込む `.ilean` に依存する。

`didOpen` 後の空でない最初の `publishDiagnostics` と、`waitForDiagnostics` の応答までを別々に測る。起動からの時間は最初の文書の診断までとする。初期化応答の時間も保存する。要求ごとに初回と、続く `--samples` 回を記録する。

編集は UTF-16 の範囲を持つ増分通知で、1 文字の空白の挿入と削除を交互に送る。`tail` は末尾に追加した証明、`early` は実ソース内で最初に見つかる `by` の直後を編集する。`Tactic.lean` の `early` は実ソース直後である。各編集は `waitForDiagnostics` の完了まで測り、位置と標本を JSON に保存する。

## 集計と負荷

反復ごとに新しいサーバーを起動し、実行順を交互にする。OS のファイルキャッシュは維持する。初回要求と反復要求、最初の文書と追加の文書を分けて比較する。中央値と p95 は元の標本から計算し、p95 は nearest rank を使う。

Linux の `/proc` を 100 ms 間隔で読み、host、Watchdog、FileWorker、Lake の準備プロセスを区別する。プロセスごとの RSS 最大値と、同時点で合算した RSS の最大値を保存する。RSS には共有ページもプロセスごとに含まれるため、合算は物理メモリの専有量とは異なる。

開始前の 2 秒間とセッション中に他プロセスの CPU 使用量を検査する。既定の上限は 0.75 cores。上限を超えた回は `accepted: false` として保存し、`--resume` で成功済みの回を再利用しながら再計測できる。プロファイルの回は通常の性能表と分ける。

`--wait-idle` は負荷による見送りと除外の後に自動で再試行する。間隔は `--retry-delay` (既定 30 秒)、各条件・各反復の試行上限は `--max-attempts` (既定 200 回) で指定する。機能上の失敗はエラーとして停止する。開始前の見送りもサーバー別に JSON に保存する。再開時はホスト、シナリオ、実行ファイルの hash、標本数と負荷基準を照合し、集計時にも異なるホストの標本を拒否する。

改善前の保存済み backend は `--before-backend` で指定する。`--server upstream --server before --server leansail` を併用すると、3 条件を反復ごとに交互に実行する。改善前のソースは `git show 1fa2f5a:native/Main.lean` と `git show 1fa2f5a:native/lakefile.toml` から取得し、同じ公式 toolchain で `lake build` する。実行ファイルの `../src/lean` をその toolchain のソースへ向ける。保存した実行ファイルと比較対象の hash は結果 JSON に含む。

3 条件を揃える再現コマンドは次のとおり。`toolchain_prefix` は「再現手順」の値を使う。

```bash
nix develop --command bash -c '
  set -euo pipefail
  export PATH="$1/bin:$PATH" LEAN_SYSROOT="$1"
  mkdir -p target/mathlib-before-backend
  git show 1fa2f5a:native/Main.lean > target/mathlib-before-backend/Main.lean
  git show 1fa2f5a:native/lakefile.toml > target/mathlib-before-backend/lakefile.toml
  lake -d target/mathlib-before-backend build
  mkdir -p target/mathlib-before-backend/.lake/build/src
  ln -sfn "$1/src/lean" target/mathlib-before-backend/.lake/build/src/lean
' bash "$toolchain_prefix"
nix develop --command make bench-mathlib \
  BENCH_ARGS="--prepare --toolchain $toolchain_prefix --before-backend target/mathlib-before-backend/.lake/build/bin/leansail-backend --server upstream --server before --server leansail --samples 10 --repetitions 5 --edits 2 --wait-idle --resume --output target/mathlib-remeasure.json"
```

## 並列解析と PSS

```bash
nix develop --command make bench-mathlib-parallel \
  BENCH_ARGS="--toolchain $toolchain_prefix --samples 10 --repetitions 5 --edits 2 --wait-idle --resume --output target/mathlib-parallel.json"
nix develop --command python3 tests/mathlib_parallel_report.py \
  target/mathlib-parallel.json --output target/mathlib-parallel-summary.json
```

同じ 3 文書を待機を挟まずに開き、全体の解析完了時間を記録する。積分の文書を編集中の文書にし、全 3 文書を開いたまま `quiet` と `busy` の 2 条件を交互に測る。`quiet` は背景文書の解析完了後、`busy` は有限次元空間と Tactic の `early` への編集を通知した直後に開始する。背景の編集は空白の挿入と削除を交互に行う。

計測前に hover と補完を 1 回ずつ実行し、その初回の時間を別に保存する。各操作では積分の `tail` を編集し、hover、補完、最新版の `waitForDiagnostics` を同時に要求する。時間は編集通知から各応答の受信までで、前景文書の再解析の待ち時間も含む。最新版の診断通知の最終受信時刻を別に保存する。要求の応答本文、診断の版とエラーの有無を検査する。前景操作の完了時にも背景の解析完了応答を受信していなかった文書数を記録し、実際に並行していた標本を確認する。各セッションで各条件 10 回、各構成 5 セッションなら各条件 50 標本になる。

Linux の `smaps_rollup` の `Pss` を、サーバーから起動された全子プロセスについて採取する。採取の間には 100 ms の待機を入れ、各標本の経過時間・プロセス別 PSS・合計 PSS を保持する。共有ページは PSS の割合で数える。プロセス別の最大値の合算と、同じ標本内の合計の最大値を区別する。全操作の完了後の PSS も記録する。採取の例外は計測を失敗にする。通常の逐次シナリオは RSS のみを採取し、PSS の読み取り費用を含めない。

## 計測結果

計測ホストは helios (論理 CPU 6 個)。改善前は `1fa2f5a` 相当の backend、改善後は `00cbf90` のハイライト索引構築と semantic tokens の最適化を含む backend を使う。同じ公式 Lean 4.35.0 でビルドした両 backend と同じ release host を比較し、hash を JSON に保存する。

| 構成     | ホスト | 採用 | 負荷による完走の除外 | 開始前の見送り | 中断 |
| -------- | ------ | ---- | -------------------- | -------------- | ---- |
| 素のLean | helios | 5    | 0                    | 2              | 0    |
| 改善前   | helios | 5    | 1                    | 0              | 0    |
| 改善後   | helios | 5    | 6                    | 59             | 1    |

各構成 5 セッション。要求の反復は各 50 標本、初回と起動は各 5 標本、編集は種類ごとに各 10 標本を集計する。時間は **中央値 / p95、単位 ms**。p95 は nearest rank で計算する。5 標本の p95 は最大値になる。全 3 ファイル・7 種類の初回の応答本文は、採用した全構成・全セッションで一致する。

中断の 1 回は検証実行のために終了した計測で、`interrupted_sessions` に保存する。改善の対象は初回のハイライト索引と完了した semantic tokens の反復要求である。起動の中央値は改善前 6903.288 ms、改善後 7468.921 ms（+8.2%）、末尾編集から解析完了は両構成とも約 1.30 秒である。

| 対象               | 素のLean            | 改善前              | 改善後              |
| ------------------ | ------------------- | ------------------- | ------------------- |
| 起動から最初の診断 | 6731.764 / 9164.560 | 6903.288 / 7748.603 | 7468.921 / 7911.809 |

### IntegralEqImproper.lean

| 対象                             | 素のLean             | 改善前              | 改善後              |
| -------------------------------- | -------------------- | ------------------- | ------------------- |
| open から解析完了                | 7898.015 / 10367.803 | 8083.488 / 8843.967 | 8676.814 / 9060.701 |
| textDocument/hover               | 1.351 / 1.541        | 1.546 / 2.011       | 1.582 / 1.859       |
| textDocument/definition          | 7.655 / 8.587        | 7.314 / 8.143       | 7.925 / 9.877       |
| textDocument/references          | 7.789 / 9.199        | 7.383 / 7.965       | 7.637 / 10.328      |
| textDocument/documentHighlight   | 662.849 / 700.836    | 1.038 / 6.675       | 0.926 / 4.263       |
| textDocument/semanticTokens/full | 74.569 / 94.002      | 75.085 / 90.037     | 4.012 / 6.572       |
| textDocument/completion          | 341.086 / 446.507    | 334.147 / 409.798   | 338.129 / 430.978   |
| $/lean/plainGoal                 | 1.628 / 4.078        | 1.648 / 1.993       | 1.629 / 2.071       |
| ハイライト初回                   | 655.289 / 692.603    | 675.450 / 756.378   | 118.954 / 188.783   |
| 編集 tail                        | 1304.064 / 1341.211  | 1306.481 / 1340.508 | 1304.430 / 1338.907 |
| 編集 early                       | 5079.787 / 5446.765  | 5283.485 / 5903.414 | 5229.281 / 5690.638 |

### FiniteDimension.lean

| 対象                             | 素のLean              | 改善前                | 改善後                |
| -------------------------------- | --------------------- | --------------------- | --------------------- |
| open から解析完了                | 10989.616 / 11983.693 | 10925.175 / 11185.351 | 10605.040 / 11025.577 |
| textDocument/hover               | 1.421 / 1.542         | 1.552 / 1.826         | 1.584 / 2.012         |
| textDocument/definition          | 20.061 / 21.556       | 20.441 / 22.145       | 21.177 / 22.175       |
| textDocument/references          | 19.526 / 21.742       | 19.580 / 21.287       | 20.206 / 22.307       |
| textDocument/documentHighlight   | 288.280 / 296.746     | 0.701 / 0.878         | 0.640 / 0.854         |
| textDocument/semanticTokens/full | 38.475 / 50.286       | 39.441 / 50.418       | 1.659 / 1.732         |
| textDocument/completion          | 292.421 / 332.624     | 291.776 / 564.438     | 296.554 / 387.128     |
| $/lean/plainGoal                 | 3.597 / 3.952         | 3.927 / 4.124         | 3.832 / 4.370         |
| ハイライト初回                   | 291.771 / 305.889     | 283.501 / 285.377     | 67.660 / 70.069       |
| 編集 tail                        | 685.790 / 895.900     | 691.189 / 756.971     | 689.084 / 741.788     |
| 編集 early                       | 7244.404 / 7736.203   | 7242.633 / 7470.541   | 7250.354 / 7455.906   |

### Tactic.lean

| 対象                             | 素のLean            | 改善前              | 改善後              |
| -------------------------------- | ------------------- | ------------------- | ------------------- |
| open から解析完了                | 3998.374 / 5230.594 | 4022.346 / 4319.118 | 3807.901 / 4345.892 |
| textDocument/hover               | 1.091 / 1.365       | 1.164 / 1.277       | 1.241 / 1.463       |
| textDocument/definition          | 19.671 / 21.817     | 19.667 / 20.995     | 19.738 / 20.399     |
| textDocument/references          | 19.244 / 22.358     | 19.243 / 20.033     | 19.230 / 21.004     |
| textDocument/documentHighlight   | 1.646 / 1.978       | 0.406 / 0.583       | 0.362 / 0.554       |
| textDocument/semanticTokens/full | 3.044 / 3.501       | 3.262 / 4.010       | 0.835 / 0.994       |
| textDocument/completion          | 257.211 / 369.644   | 256.741 / 324.893   | 264.127 / 348.741   |
| $/lean/plainGoal                 | 1.088 / 1.283       | 1.207 / 1.471       | 1.243 / 1.358       |
| ハイライト初回                   | 2.119 / 2.144       | 2.345 / 4.822       | 1.383 / 1.496       |
| 編集 tail                        | 203.950 / 210.510   | 205.580 / 209.980   | 206.394 / 210.063   |
| 編集 early                       | 208.865 / 230.173   | 211.746 / 220.111   | 211.521 / 218.417   |

### メモリ

単位は MiB。RSS の採用セッションを通した最大値を示す。プロセス別の最大値と、同じ標本内で合算した RSS の最大値を分ける。共有ページを含むため、物理メモリは並列解析の PSS と合わせて判断する。

| プロセス                                              | 素のLean | 改善前 | 改善後 |
| ----------------------------------------------------- | -------- | ------ | ------ |
| host                                                  | 構成外   | 18.3   | 18.2   |
| setup                                                 | 864.7    | 862.1  | 865.1  |
| simultaneous_total                                    | 6346.6   | 6418.5 | 6402.6 |
| watchdog                                              | 1234.9   | 1237.1 | 1241.4 |
| worker:MeasureTheory/Integral/IntegralEqImproper.lean | 1943.1   | 1971.8 | 1954.0 |
| worker:Tactic.lean                                    | 1676.3   | 1690.6 | 1687.9 |
| worker:Topology/Algebra/Module/FiniteDimension.lean   | 1594.6   | 1608.1 | 1611.8 |

## 並列解析の結果

ホストは helios。同じ 3 文書を同時に開き、積分を編集中の文書にする。全 3 文書を保持したまま、背景が解析済みの条件と、有限次元空間と Tactic を再解析する条件を交互に各 10 回測る。hover と補完の初回要求は別に記録する。各構成 5 セッション、各条件 50 標本で、全ての hover と補完の応答本文が一致する。

| 構成      | 採用 | 負荷で除外した完走 | 開始前の見送り | 中断 |
| --------- | ---- | ------------------ | -------------- | ---- |
| 素の Lean | 5    | 3                  | 13             | 0    |
| Leansail  | 5    | 3                  | 15             | 0    |

時間は編集通知から応答・診断通知の受信までの p95 (ms)。PSS の採取を含む同じ条件で比較する。診断通知と `waitForDiagnostics` による解析完了を区別する。後者には診断通知後の snapshot stream の完了待ちも含む。

| 構成      | 条件           | hover  | 補完    | 診断通知 | 解析完了 |
| --------- | -------------- | ------ | ------- | -------- | -------- |
| 素の Lean | 背景は解析済み | 12.806 | 433.951 | 227.757  | 1357.217 |
| 素の Lean | 背景を再解析中 | 23.363 | 497.745 | 260.367  | 1479.751 |
| Leansail  | 背景は解析済み | 12.626 | 506.244 | 229.350  | 1413.924 |
| Leansail  | 背景を再解析中 | 23.706 | 562.340 | 262.257  | 1531.662 |

背景を再解析した条件では、両構成とも 50 / 50 回で、前景の全要求が終わった後も背景の解析が継続していた。最新版の診断の版とエラーの有無を検査する。

| 構成      | 3 文書の同時 open 完了 (中央値 / p95 ms) | 合計 PSS 最大 MiB | 解析後の合計 PSS 中央値 MiB |
| --------- | ---------------------------------------- | ----------------- | --------------------------- |
| 素の Lean | 12930.113 / 13747.983                    | 3805.3            | 3784.3                      |
| Leansail  | 12749.309 / 15727.553                    | 3850.9            | 3796.7                      |

プロセス別 PSS の採用セッションを通した最大値は次のとおり。各プロセスの最大値を合算せず、全プロセス合計には同じ標本内の合計の最大値を用いる。

| プロセス                   | 素の Lean (MiB) | Leansail (MiB) |
| -------------------------- | --------------- | -------------- |
| Rust host                  | 構成外          | 13.9           |
| Watchdog                   | 1223.7          | 1228.4         |
| FileWorker (積分)          | 978.8           | 984.1          |
| FileWorker (有限次元)      | 826.3           | 836.5          |
| FileWorker (Tactic)        | 855.2           | 861.8          |
| Lake などの準備 (単独最大) | 747.2           | 814.8          |

## worker 管理と索引の判断

この 3 文書・6 コアの条件では native Watchdog による worker 管理を継続する。Leansail の背景解析による p95 の上乗せは hover 11.1 ms、補完 56.1 ms、診断通知 32.9 ms である。静かな条件でも補完の p95 は 506.2 ms、解析完了は 1413.9 ms を要する。今回の数値では、Rust へ worker の配送・優先・キャンセル・復旧の境界を移す効果を十分に示せない。

解析後も合計 PSS 中央値 3796.7 MiB を保持する。各セッションの最大値と解析後の差は 2.6〜56.6 MiB。解析済み worker は文書が open の間、文書と import 環境を保持する。メモリ上限を設ける場合は、解析の同時実行数に加えて、保持する文書の退避・再構築と、その再解析による待ち時間を含む比較を必要条件にする。

この判断の対象は 3 文書を開く 6 コアのホストである。より多い文書、少ない CPU、メモリ制約のある条件は、それぞれの上限での前景 p95 と保持 PSS から判断する。新しい scheduling 規則を host に追加するときは、優先順位・版・上限の規則を Lean で定義し、定理を検査して leanfran で Rust に生成する。

プロジェクト索引は Watchdog 内で全 worker に共有される。Watchdog の最大 PSS は 1228.4 MiB。複数セッション間の共有と永続化の評価では、runtime と索引データの保持量を分け、重複する PSS、`.ilean` の再読込 CPU、準備完了までの時間、依存更新時の無効化を測る。cache key は Lean の toolchain、manifest、`.ilean` の hash を含め、未保存文書の版を別に管理する。

## プロファイル

```bash
nix shell nixpkgs#perf --command nix develop --command python3 tests/mathlib.py \
  --toolchain "$toolchain_prefix" --server leansail \
  --profile --perf --samples 5 --repetitions 1 --edits 2 \
  --output target/mathlib-profile/results.json
nix shell nixpkgs#perf --command perf report \
  -i target/mathlib-profile/leansail-0.perf.data
```

`--profile` は native backend の `LEAN_LS_PROFILE` を有効にし、stateless と stateful のハンドラーの経過時間をプロセス別 JSONL に保存する。snapshot の待機と応答の直列化を含む。Watchdog が担当する参照検索、transport、Rust host は perf のプロセスと stack から調べる。

`--perf` は `cpu-clock:u` を 99 Hz で採取し、DWARF の call graph を保存する。記録先は結果 JSON と同じディレクトリ。通常の性能計測は両オプションを省略する。

大きい積分ファイルの elaboration は、`lake setup-file` が返す JSON を `--setup` に渡し、`lake env lean -Dprofiler=true -Dprofiler.threshold=50` でも調べる。プロファイラーの累積時間は入れ子の処理と並列の実行を含むため、各項目を合算して wall time の割合にはしない。

## semantic tokens の再利用

native Lean の `textDocument/semanticTokens/full` は、完了した文書の応答を worker ごとに 1 件保持する。URI と版を照合し、直列化済みの本文を再利用する。編集時は世代を増やして破棄し、編集前に開始した処理が完了しても旧世代の応答を登録できないようにする。部分応答は上流の処理と refresh の仕組みを利用する。

保持するデータは直列化済みの応答である。解析済みの全 InfoTree と import 環境は引き続き Lean の worker が所有する。キャンセル検査は要求の受付と応答の返却時に実行する。判定とキャッシュは native Lean 内で完結し、Rust host は既存の leanfran で生成した lifecycle、capability、旧応答と障害復旧の規則を使う。

`make check` の `tests/semantic_tokens.py` は同じ本文を素の Lean と比較し、同一版の再要求、追加と取り消し、同じ URI と版での再オープン、解析途中から完了後への移行、Unicode を検査する。

## ハイライトの索引構築

ハイライトの索引は、参照が属する module の名前を import の配列から直接取得する。上流の `getModuleContainingDecl?` は参照ごとに `allImportedModuleNames` を呼び、この関数は全 import の配列を `map` する。直接の添字による取得は、この中間配列の確保と走査を省く。

`native/Main.lean` の 4 件の定理は、module 名の取得、各 Info の識別子、参照の収集、重複をまとめた索引が、それぞれ上流の処理と等しいことを検査する。既存の文書版ごとの索引再利用と組み合わせ、初回と編集後のハイライトを処理する。FileWorker の背景の索引通知は上流の実装を使う。

## 結果の集計

```bash
nix develop --command python3 tests/mathlib_report.py \
  --series 素のLean upstream target/mathlib-remeasure.json \
  --series 改善前 before target/mathlib-remeasure.json \
  --series 改善後 leansail target/mathlib-remeasure.json \
  --output target/mathlib-summary.json
```

`--allow-incomplete` は採用できた回だけを集計し、採用 0 件の構成を「未計測」と表示する。応答の一致の検査対象も、採用した構成の名前とともに JSON に保存する。標本と集計は [mathlib-results.json](mathlib-results.json) を参照する。

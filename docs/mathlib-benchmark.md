# Mathlib の計測

`make bench-mathlib` は Mathlib の実ファイルを開き、LSP の要求、編集後の解析、プロセス別 RSS を計測する。通常の `make check` と CI は小さい文書による機能の比較を実行する。Mathlib の依存と計測結果は `target/` に生成する。

## 再現手順

Linux と公式 Lean 4.35.0 の toolchain を使う。Mathlib は v4.35.0 の commit `56a005855ea17e0b0958f73ec801ae140b0a1679` に固定する。比較する `lean --server` と native backend は同じ toolchain の compiler、runtime、標準ライブラリを使い、host は release ビルドを使う。

```bash
elan toolchain install leanprover/lean4:v4.35.0
toolchain_prefix="$(elan run leanprover/lean4:v4.35.0 lean --print-prefix)"
nix develop --command make bench-mathlib \
  BENCH_ARGS="--prepare --toolchain $toolchain_prefix --output target/mathlib-results.json"
```

`--prepare` は小さい Lake プロジェクトを作り、固定した Mathlib を依存に加える。[Mathlib の cache コマンド](https://github.com/leanprover-community/mathlib4/blob/v4.35.0/Cache/README.md) の `lake exe cache get` で Mathlib 全体とその依存を取得し、import の検査と対象ごとの `lake setup-file` を済ませる。`--prepare-only` はここまでを実行する。以降は `--prepare` を省略して同じ workspace を再利用する。計測環境は約 7.3 GiB、プロジェクトと依存の `.ilean` は 9,049 本である。

Nix の Lean 4.35.0 は git hash が空で、同梱する leantar は 0.1.19 である。公式 toolchain は git hash `eb6cafb7290e8bd5f2937522d8d804f58f02bafc` と leantar 0.1.20 を持ち、公式 Mathlib キャッシュを利用できる。スクリプトは compiler の git hash を検査する。`--toolchain` はキャッシュの取得、Lake の準備、backend のビルド、両サーバーの実行に適用する。通常の Nix 配布物は `nix build` で検証する。

native backend のソースは `native/` から `target/mathlib-backend/` にコピーしてビルドする。`--backend` で比較したい保存済みの backend を指定できる。改修前を保存する場合は、実行ファイルの相対位置 `../src/lean` に同じ toolchain のソースへの symlink も作る。

## シナリオ

| 対象 | 規模 | 選択の理由 |
|---|---:|---|
| [IntegralEqImproper.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/MeasureTheory/Integral/IntegralEqImproper.lean) | 77,968 bytes、直接 import 4 本 | この版の Mathlib で最大のソース。積分の証明を再 elaboration する。 |
| [FiniteDimension.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/Topology/Algebra/Module/FiniteDimension.lean) | 40,074 bytes、直接 import 14 本 | import が多く、実際の証明を持つ。 |
| [Tactic.lean](https://github.com/leanprover-community/mathlib4/blob/56a005855ea17e0b0958f73ec801ae140b0a1679/Mathlib/Tactic.lean) | 18,296 bytes、直接 import 397 本 | 多数の tactic を import する。 |

3 文書を順番に開き、最後まで開いた状態にする。ディスク上の Mathlib ソースをそのまま読み、末尾に小さい `twice` の定義、証明、`#check` を追加した未保存の文書を送る。hover、定義、参照、ハイライト、補完はこの共通の識別子、plainGoal はその証明の位置で測る。semantic tokens は Mathlib のソース全体が対象である。参照検索の件数は開いた文書と非同期に読み込む `.ilean` に依存する。

`didOpen` 後の空でない最初の `publishDiagnostics` と、`waitForDiagnostics` の応答までを別々に測る。起動からの時間は最初の文書の診断までとする。初期化応答の時間も保存する。要求ごとに初回と、続く `--samples` 回を記録する。

編集は UTF-16 の範囲を持つ増分通知で、1 文字の空白の挿入と削除を交互に送る。`tail` は末尾に追加した証明、`early` は実ソース内で最初に見つかる `by` の直後を編集する。`Tactic.lean` の `early` は実ソース直後である。各編集は `waitForDiagnostics` の完了まで測り、位置と標本を JSON に保存する。

## 集計と負荷

反復ごとに新しいサーバーを起動し、実行順を交互にする。OS のファイルキャッシュは維持する。初回要求と反復要求、最初の文書と追加の文書を分けて比較する。中央値と p95 は元の標本から計算し、p95 は nearest rank を使う。

Linux の `/proc` を 100 ms 間隔で読み、host、Watchdog、FileWorker、Lake の準備プロセスを区別する。プロセスごとの RSS 最大値と、同時点で合算した RSS の最大値を保存する。RSS には共有ページもプロセスごとに含まれるため、合算は物理メモリの専有量とは異なる。

開始前の 2 秒間とセッション中に他プロセスの CPU 使用量を検査する。既定の上限は 0.75 cores。上限を超えた回は `accepted: false` として保存し、`--resume` で成功済みの回を再利用しながら再計測できる。プロファイルの回は通常の性能表と分ける。

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

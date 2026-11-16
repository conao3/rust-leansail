# 受け入れ検証

`nix develop --command make check` は同じ Lean 4.35.0 の `lean --server` と Leansail に同じ文書・capability・要求を送り、下表の 59 項目を比較する。`make check-acceptance` で比較と障害検査だけを実行できる。CI は `make check`、`nix build`、`make check-protocol` を実行する。

## 比較の範囲

34 件の通常要求、10 件のクライアント通知、3 件の server→client 要求、2 件の server 通知、10 件の Widget RPC がすべて一致する。項目単位の除外は 0 件。固定 toolchain の `ProtocolOverview.lean` と `tests/protocol.json` の method 一覧も実行時に照合する。

通常の応答本文と配列の順序を比較する。Widget RPC の不透明な参照は、同一の参照を同じ連番に置き換える。参照の取得後に popup・移動先・メッセージ変換・遅延 trace を取得し、参照の解放後のエラーまで検査する。sessionId は値がプロセスごとに変わるため、接続の成功と同じ session での呼び出しを比較する。`tests/fixtures/Protocol.lean` は `MessageData` と遅延 trace の参照、widget source、lazy code action を提供する。

server→client 要求は ID と登録 ID を除いた本文の集合を比較し、各要求へ元の ID で応答する。クライアントは監視の動的登録、semantic tokens と inlay hints の refresh を宣言する。省略・false・不正な型・一部対応は `tests/capabilities.py` の 5 ケースが検査する。

診断は最終版、file progress は文書・版ごとの完了通知を比較する。解析途中の通知回数・到着順序・進捗範囲は実行時刻に依存するため、比較対象から除く。保存とファイル監視は別文書の実際の import を使い、import closure の非同期登録を待ちながら通知し、依存文書に届く診断を比較する。

`target/conformance.json` が項目別の結果、`target/recovery.json` が障害時の結果を持つ。同じディレクトリに送受信の全文を保存する。比較の対象はこの fixture と操作であり、個々の method の全パラメータや実エディタの全操作は別途評価する。

## 項目別の結果

| method | 結果 | 比較対象 |
|---|---|---|
| `$/cancelRequest` | 一致 | 明示キャンセル後の `-32800` と最新文書の hover |
| `initialize` | 一致 | 応答本文 |
| `initialized` | 一致 | 初期化後の要求の成功 |
| `shutdown` | 一致 | 応答本文 |
| `exit` | 一致 | shutdown 後の終了コード 0 |
| `client/registerCapability` | 一致 | 登録の method・registerOptions と元の ID での応答 |
| `textDocument/didOpen` | 一致 | 開いた文書の goal と診断 |
| `textDocument/didClose` | 一致 | close 後の `-32801` |
| `textDocument/didChange` | 一致 | 版 2 の診断と再構築したハイライト |
| `textDocument/didSave` | 一致 | 依存文書の古い import を知らせる診断 |
| `workspace/didChangeWatchedFiles` | 一致 | 依存文書の古い import を知らせる診断 |
| `textDocument/publishDiagnostics` | 一致 | 最終版の診断本文・range・severity・version |
| `$/lean/fileProgress` | 一致 | 文書・版ごとの完了通知。crash 時の fatal 通知は障害テストでも検査 |
| `textDocument/completion` | 一致 | 応答本文 |
| `completionItem/resolve` | 一致 | 応答本文 |
| `textDocument/codeAction` | 一致 | 応答本文 |
| `codeAction/resolve` | 一致 | 応答本文 |
| `textDocument/signatureHelp` | 一致 | 応答本文 |
| `textDocument/hover` | 一致 | 応答本文 |
| `textDocument/documentHighlight` | 一致 | 応答本文 |
| `textDocument/documentSymbol` | 一致 | 応答本文 |
| `textDocument/foldingRange` | 一致 | 応答本文 |
| `textDocument/documentColor` | 一致 | 応答本文 |
| `textDocument/semanticTokens/range` | 一致 | 応答本文 |
| `textDocument/semanticTokens/full` | 一致 | 応答本文 |
| `workspace/semanticTokens/refresh` | 一致 | 要求の method・params の集合と応答 |
| `textDocument/inlayHint` | 一致 | 応答本文 |
| `workspace/inlayHint/refresh` | 一致 | 要求の method・params の集合と応答 |
| `textDocument/definition` | 一致 | 応答本文 |
| `textDocument/declaration` | 一致 | 応答本文 |
| `textDocument/typeDefinition` | 一致 | 応答本文 |
| `textDocument/references` | 一致 | 応答本文 |
| `textDocument/prepareCallHierarchy` | 一致 | 応答本文 |
| `callHierarchy/incomingCalls` | 一致 | 応答本文 |
| `callHierarchy/outgoingCalls` | 一致 | 応答本文 |
| `$/lean/prepareModuleHierarchy` | 一致 | 応答本文 |
| `$/lean/moduleHierarchy/imports` | 一致 | 応答本文 |
| `$/lean/moduleHierarchy/importedBy` | 一致 | 応答本文 |
| `textDocument/prepareRename` | 一致 | 応答本文 |
| `textDocument/rename` | 一致 | 応答本文 |
| `workspace/symbol` | 一致 | 応答本文 |
| `$/lean/plainGoal` | 一致 | 応答本文 |
| `$/lean/plainTermGoal` | 一致 | 応答本文 |
| `textDocument/waitForDiagnostics` | 一致 | 応答本文 |
| `$/lean/waitForILeans` | 一致 | 応答本文 |
| `$/lean/rpc/connect` | 一致 | sessionId の取得と同じ session での RPC 成功 |
| `$/lean/rpc/release` | 一致 | 解放した参照を使う RPC のエラー本文 |
| `$/lean/rpc/keepAlive` | 一致 | 通知後も同じ参照を使った RPC が成功 |
| `$/lean/rpc/call` | 一致 | 10 種類の組み込み Widget RPC と参照の往復 |
| `Lean.Widget.getInteractiveDiagnostics` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.getInteractiveGoals` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.getInteractiveTermGoal` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.getWidgets` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.InteractiveDiagnostics.infoToInteractive` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.getGoToLocation` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.lazyTraceChildrenToInteractive` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.highlightMatches` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.getWidgetSource` | 一致 | 応答本文（参照 ID を正規化） |
| `Lean.Widget.InteractiveDiagnostics.msgToInteractive` | 一致 | 応答本文（参照 ID を正規化） |

## キャンセルと障害

`tests/conformance.py` は解析待ちの hover に `$/cancelRequest` を送り `-32800` を検査する。別の解析を待つ要求を編集で無効にし、Leansail が `-32801` を返すこと、最新の hover が `current : Bool` を返すこと、取り消した要求に成功応答が重複して届かないことも検査する。

上流 Lean 4.35.0 は編集で無効になった hover に `null` を返す場合がある。Leansail は受付時の文書の識別子と現在の識別子を比較し、無効な要求の応答を `-32801` にする。明示キャンセルと最新版の hover は上流と比較し、編集で無効になった要求には Leansail 固有のエラー条件を適用する。

`tests/recovery.py` は実際の FileWorker を SIGKILL し、fatal な file progress、処理中の要求の `-32902`、次の要求での別 PID の worker 起動、全体編集と差分編集を適用した版・内容の保持を検査する。待機中と要求中の両方を実行する。上流だけのセッションでも、通常要求が保留され、`didChange` 後に応答することを検査する。

同じ検査で Watchdog も SIGKILL する。待機中・要求中ともクライアントへエラーの `window/showMessage` を送り、保留中の要求には `-32603` を返し、host を終了コード 255 で終了する。クライアントによる言語サーバー全体の再起動でセッションを再構築する。

上流の挙動だけを再現するコマンド:

```bash
nix develop --command python3 tests/conformance.py --upstream-only --output target/upstream-stale.json
nix develop --command python3 tests/recovery.py --upstream-only --output target/upstream-worker.json
```

Lean の 6 件の定理は、キャンセルされた応答の拒否、版が古い応答の拒否、転送する応答の条件、worker の再起動に crash と open と request が必要なことを検査する。Rust の単体テストは数値と文字列の ID、server→client 要求の ID との区別、他文書との隔離、close・reopen、古い版の fatal progress、空の変更通知を検査する。

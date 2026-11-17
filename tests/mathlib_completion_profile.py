import argparse
import hashlib
import json
import os
from pathlib import Path

from mathlib import ROOT, run
from mathlib_trace import replace


def build(prefix, output, revision=None, profile=False):
    output.mkdir(parents=True, exist_ok=True)
    names = [
        "Main.lean",
        "LeansailIndex.lean",
        "LeansailSymbols.lean",
        "LeansailTrace.lean",
        "lakefile.toml",
    ]
    has_completion = (
        bool(
            run(
                [
                    "git",
                    "ls-tree",
                    "--name-only",
                    revision,
                    "native/LeansailCompletion.lean",
                ],
                ROOT,
                capture_output=True,
            ).stdout
        )
        if revision
        else (ROOT / "native/LeansailCompletion.lean").exists()
    )
    if has_completion:
        names.append("LeansailCompletion.lean")
    for name in names:
        source = (
            run(
                ["git", "show", f"{revision}:native/{name}"], ROOT, capture_output=True
            ).stdout
            if revision
            else (ROOT / "native" / name).read_text()
        )
        (output / name).write_text(source)
    if profile and not has_completion:
        hashes = {}
        for name, target in [
            ("Completion/CompletionCollectors", "BenchCollectors"),
            ("Completion", "BenchCompletion"),
        ]:
            source = (prefix / f"src/lean/Lean/Server/{name}.lean").read_text()
            hashes[name] = hashlib.sha256(source.encode()).hexdigest()
            text = source.replace(
                "namespace Lean.Server.Completion",
                "namespace LeansailProfileCompletion",
            ).replace("end Lean.Server.Completion", "end LeansailProfileCompletion")
            text = text.replace(
                "public section", "import LeansailTrace\n\npublic section", 1
            )
            if target == "BenchCollectors":
                text = text.replace(
                    "open Meta\n", "open Meta Lean.Server.Completion Lean.Server\n", 1
                )
                text = replace(
                    text,
                    "  let env ← getEnv\n  forEligibleDeclsWithCancellationM",
                    '  let env ← getEnv\n  let _ ← getEligibleHeaderDecls env\n  leansailTrace "completion_header_ready"\n  forEligibleDeclsWithCancellationM',
                )
                before = """  forEligibleDeclsWithCancellationM fun declName decl => do
    let bestMatch? ← bestLabelForDecl? ctx declName id danglingDot
    if let some bestLabel := bestMatch? then
      addUnresolvedCompletionItem bestLabel (.const declName) (← decl.kind) (← decl.tags)
"""
                after = """  let mut eligible := (← getEligibleHeaderDecls env).toArray
  for (name, c) in env.constants.map₂ do
    if Lean.Meta.allowCompletion env name then
      eligible := eligible.push (name, { info := c, kind := getCompletionKindForDecl c, tags := getCompletionTagsForDecl name })
  leansailTrace "completion_enumeration_done" (toString eligible.size)
  let mut selected : Array (Name × Name × EligibleDecl) := #[]
  for (declName, decl) in eligible do
    if let some label ← bestLabelForDecl? ctx declName id danglingDot then
      selected := selected.push (declName, label, decl)
  leansailTrace "completion_match_done" (toString selected.size)
  for (declName, label, decl) in selected do
    addUnresolvedCompletionItem label (.const declName) (← decl.kind) (← decl.tags)
"""
                text = replace(text, before, after)
                text = replace(
                    text,
                    "  RequestCancellation.check\n  let matchAlias",
                    '  leansailTrace "completion_scan_done"\n  RequestCancellation.check\n  let matchAlias',
                )
                text = replace(
                    text,
                    "  -- Search keywords",
                    '  leansailTrace "completion_aliases_done"\n  -- Search keywords',
                )
                text = replace(
                    text,
                    "  completeNamespaces ctx id danglingDot",
                    '  leansailTrace "completion_keywords_done"\n  completeNamespaces ctx id danglingDot\n  leansailTrace "completion_namespaces_done"',
                )
            else:
                text = text.replace(
                    "public import Lean.Server.Completion.CompletionCollectors",
                    "public import BenchCollectors",
                )
                text = text.replace(
                    "open Lsp\nopen Elab\n",
                    "open Lean Lean.Lsp Lean.Elab Lean.Server Lean.Server.Completion\n",
                    1,
                )
                text = replace(
                    text,
                    "  let mut allCompletions := #[]",
                    '  leansailTrace "completion_selection_done"\n  let mut allCompletions := #[]',
                )
                text = replace(
                    text,
                    "  let finalCompletions := allCompletions",
                    '  leansailTrace "completion_candidates_done" (toString allCompletions.size)\n  let finalCompletions := allCompletions',
                )
                text = replace(
                    text,
                    "  return { items := finalCompletions",
                    '  leansailTrace "completion_dedup_done" (toString finalCompletions.size)\n  return { items := finalCompletions',
                )
            (output / f"{target}.lean").write_text(text)
        main = (output / "Main.lean").read_text()
        main = "import BenchCompletion\n" + main
        handler = """def profiledCompletion (params : Json) : RequestM (RequestTask SerializedLspResponse) := do
  leansailTrace "completion_start"
  let p ← parseRequestParams Lsp.CompletionParams params
  let doc ← readDoc
  let pos := doc.meta.text.lspPosToUtf8Pos p.position
  let caps := (← read).initParams.capabilities
  mapTaskCostly (Lean.Server.FileWorker.findCompletionCmdDataAtPos doc pos) fun data? => do
    leansailTrace "completion_snapshot_ready"
    let result ← match data? with
      | none => pure { items := #[], isIncomplete := true }
      | some (stx, tree) => LeansailProfileCompletion.find? doc.meta.uri p.position doc.meta.text pos stx tree caps
    let serialized := result.compressFast
    leansailTrace "completion_serialize_done"
    return { response? := none, serialized, isComplete := true }

"""
        main = replace(
            main,
            "def installHandlers (useCache : Bool)",
            handler + "def installHandlers (useCache : Bool)",
        )
        main = replace(
            main,
            "  if let some profilePath ← IO.getEnv",
            """  if let some original ← lookupLspRequestHandler "textDocument/completion" then
    requestHandlers.modify (·.insert "textDocument/completion" { original with handle := profiledCompletion })
  if let some profilePath ← IO.getEnv""",
        )
        (output / "Main.lean").write_text(main)
        with (output / "lakefile.toml").open("a") as stream:
            stream.write(
                '\n[[lean_lib]]\nname = "BenchCollectors"\n\n[[lean_lib]]\nname = "BenchCompletion"\n'
            )
        (output / "upstream-hashes.json").write_text(
            json.dumps(hashes, indent=2) + "\n"
        )
    if profile:
        resolution = (
            prefix / "src/lean/Lean/Server/Completion/CompletionResolution.lean"
        ).read_text()
        text = resolution.replace(
            "namespace Lean.Lsp", "namespace LeansailProfileResolve\nopen Lean Lean.Lsp"
        ).replace("end Lean.Lsp", "end LeansailProfileResolve")
        text = text.replace(
            "namespace Lean.Server.Completion",
            "namespace LeansailProfileResolveCompletion\nopen Lean Lean.Server.Completion",
        ).replace("end Lean.Server.Completion", "end LeansailProfileResolveCompletion")
        text = replace(text, "public section", "import LeansailTrace\n\npublic section")
        text = replace(
            text,
            "  if item.detail?.isNone then",
            '  leansailTrace "resolve_type_start"\n  if item.detail?.isNone then',
        )
        text = replace(
            text,
            "  if item.documentation?.isNone then",
            '  leansailTrace "resolve_type_done"\n  if item.documentation?.isNone then',
        )
        text = replace(
            text,
            "\n  return item",
            '\n  leansailTrace "resolve_docs_done"\n  return item',
        )
        text = replace(
            text,
            "  i.ctx.runMetaM i.info.lctx (item.resolve id)",
            '  leansailTrace "resolve_info_done"\n  i.ctx.runMetaM i.info.lctx (LeansailProfileResolve.CompletionItem.resolve item id)',
        )
        (output / "BenchResolution.lean").write_text(text)
        with (output / "lakefile.toml").open("a") as stream:
            stream.write('\n[[lean_lib]]\nname = "BenchResolution"\n')
        main = "import BenchResolution\n" + (output / "Main.lean").read_text()
        handler = """def serializeResolvedCompletion (item : Lsp.CompletionItem) : SerializedLspResponse :=
  let json := toJson item
  { response? := some json, serialized := json.compress, isComplete := true }

def profiledResolve (params : Json) : RequestM (RequestTask SerializedLspResponse) := do
  leansailTrace "resolve_start"
  let item ← parseRequestParams Lsp.CompletionItem params
  let doc ← readDoc
  let some (data : Lsp.ResolvableCompletionItemData) := item.data?.bind fun data => (fromJson? data).toOption
    | return .pure (serializeResolvedCompletion item)
  let some id := data.id?
    | return .pure (serializeResolvedCompletion item)
  let some cPos := data.cPos?
    | return .pure (serializeResolvedCompletion item)
  let pos := doc.meta.text.lspPosToUtf8Pos data.pos
  mapTaskCostly (Lean.Server.FileWorker.findCompletionCmdDataAtPos doc pos) fun data? => do
    leansailTrace "resolve_snapshot_ready"
    let result ← match data? with
      | none => pure item
      | some (stx, tree) => LeansailProfileResolveCompletion.resolveCompletionItem? doc.meta.text pos stx tree item id cPos
    let response := serializeResolvedCompletion result
    leansailTrace "resolve_serialize_done"
    return response

"""
        main = replace(
            main,
            "def installHandlers (useCache : Bool)",
            handler + "def installHandlers (useCache : Bool)",
        )
        main = replace(
            main,
            "  if let some profilePath ← IO.getEnv",
            """  if let some original ← lookupLspRequestHandler "completionItem/resolve" then
    requestHandlers.modify (·.insert "completionItem/resolve" { original with handle := profiledResolve })
  if let some profilePath ← IO.getEnv""",
        )
        (output / "Main.lean").write_text(main)
        hashes_path = output / "upstream-hashes.json"
        hashes = json.loads(hashes_path.read_text()) if hashes_path.exists() else {}
        hashes["Completion/CompletionResolution"] = hashlib.sha256(
            resolution.encode()
        ).hexdigest()
        hashes_path.write_text(json.dumps(hashes, indent=2) + "\n")
    run(["taskset", "-c", "0,1", "lake", "build"], output)
    link = output / ".lake/build/src/lean"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to(prefix / "src/lean", target_is_directory=True)
    return output / ".lake/build/bin/leansail-backend"


def main():
    parser = argparse.ArgumentParser(description="補完の段階別計測用 backend を作る")
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    prefix = args.toolchain.resolve()
    os.environ["PATH"] = str(prefix / "bin") + os.pathsep + os.environ["PATH"]
    os.environ["LEAN_SYSROOT"] = str(prefix)
    print(build(prefix, args.output.resolve(), args.revision, args.profile))


if __name__ == "__main__":
    main()

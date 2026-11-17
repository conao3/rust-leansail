import argparse
import os
from pathlib import Path

from mathlib import ROOT, run
from mathlib_trace import build_backend, replace

BASELINE_HANDLER = """def handleWorkspaceSymbol (p : WorkspaceSymbolParams) : ReaderT ReferenceRequestContext IO (Array SymbolInformation) := do
  if p.query.isEmpty then
    return #[]
  leansailTrace "symbol_start" p.query
  let references := (← read).references
  let all := references.allRefs
  leansailTrace "symbol_modules"
  let mut raw : Array (Name × DocumentUri × String × Range) := #[]
  let mut visited := 0
  for (module, uri, refs, _) in all do
    for (ident, info) in refs do
      visited := visited + 1
      if let (RefIdent.const _ name, some loc) := (ident, info.definition?) then
        raw := raw.push (module, uri, name, loc.range)
  leansailTrace "symbol_scan" s!"{visited}:{raw.size}"
  let normalized := raw.map fun (module, uri, name, range) =>
    let ident := name.toName
    (module, uri, privateToUserName? ident |>.getD ident |>.toString, range)
  leansailTrace "symbol_names"
  let symbols := normalized.filterMap fun (module, uri, name, range) => do
    let score ← FuzzyMatching.fuzzyMatchScoreWithThreshold? p.query name
    return ({ mod := module, modUri := uri, ident := (name, score), range } : References.MatchedDefinition (String × Float))
  leansailTrace "symbol_match" (toString symbols.size)
  let ltSymbol symbol1 symbol2 :=
    let (ident1, score1) := symbol1.ident
    let (ident2, score2) := symbol2.ident
    if score1 == score2 then ident1.length < ident2.length else score1 > score2
  let sorted := symbols.toList.mergeSort ltSymbol |>.extract 0 1000
  leansailTrace "symbol_sort"
  let result := sorted.map fun m => ({
    name := m.ident.1
    kind := SymbolKind.constant
    location := { uri := m.modUri, range := m.range }
  } : SymbolInformation)
  let result := result.toArray
  leansailTrace "symbol_format"
  return result
"""

REFERENCE_RESPONSE = """        let message : JsonRpc.Message := { id, result : Response β }
        let message := toJson message
        leansailTrace "reference_json"
        let serialized := message.compress
        leansailTrace "reference_compress"
        writeSerializedMessage serialized
        leansailTrace "reference_sent"
"""


def profile_watchdog(text, baseline):
    text = text.replace("import LeansailTrace", "public import LeansailTrace")
    if baseline:
        start = text.index("def handleWorkspaceSymbol (")
        end = text.index("\ndef handlePrepareRename", start)
        text = text[:start] + BASELINE_HANDLER + text[end:]
    return replace(
        text,
        "        writeMessage { id, result : Response β }",
        REFERENCE_RESPONSE.rstrip(),
    )


def main():
    parser = argparse.ArgumentParser(
        description="workspace/symbol の段階計測用 backend を作る"
    )
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    prefix = args.toolchain.resolve()
    os.environ["PATH"] = str(prefix / "bin") + os.pathsep + os.environ["PATH"]
    os.environ["LEAN_SYSROOT"] = str(prefix)
    source_dir = ROOT / "native"
    if args.revision:
        source_dir = args.output.resolve() / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "Main.lean",
            "LeansailIndex.lean",
            "LeansailTrace.lean",
            "lakefile.toml",
        ]:
            source = run(
                ["git", "show", f"{args.revision}:native/{name}"],
                ROOT,
                capture_output=True,
            ).stdout
            (source_dir / name).write_text(source)
    transform = (
        (lambda text: profile_watchdog(text, bool(args.revision)))
        if args.profile
        else None
    )
    print(build_backend(source_dir, args.output.resolve(), transform))


if __name__ == "__main__":
    main()

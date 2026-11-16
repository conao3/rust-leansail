import Lean

def twice (n : Nat) : Nat := n + n
example : twice 2 = 4 := by
  rfl
#check twice

open Lean Lean.Server Lean.Widget in
@[server_rpc_method]
def conformanceRefs (_ : Unit) : RequestM
    (RequestTask (WithRpcRef MessageData × WithRpcRef LazyTraceChildren)) :=
  RequestM.pureTask do
    return (← WithRpcRef.mk m!"検証メッセージ", ← WithRpcRef.mk {
      indent := 0, children := #[← WithRpcRef.mk m!"子のメッセージ"] })

@[widget_module]
def conformanceWidget : Lean.Widget.Module := {
  javascript := "export default function () { return '検証'; }"
}

open Lean.Server in
@[server_rpc_method]
def conformanceWidgetHash (_ : Unit) : RequestM (RequestTask UInt64) :=
  RequestM.pureTask do return conformanceWidget.javascriptHash

open Lean.Server in
@[server_rpc_method]
def conformanceSlow (_ : Unit) : RequestM (RequestTask Unit) :=
  RequestM.asTask do IO.sleep 10000

open Lean.Server in
@[code_action_provider]
meta def conformanceAction : CodeActionProvider := fun _ _ =>
  pure #[{ eager := { title := "検証" }, lazy? := some (pure { title := "解決済み" }) }]

#check conformanceWidget
#eval IO.sleep 1000

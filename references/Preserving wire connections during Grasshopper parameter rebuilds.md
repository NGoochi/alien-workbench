# Preserving wire connections during Grasshopper parameter rebuilds

**The Grasshopper SDK provides no automatic wire-preservation mechanism when you unregister and re-register parameters — but three proven patterns exist to solve this.** The most robust is the Hops component's approach: cache all `Sources` and `Recipients` into name-keyed dictionaries before teardown, rebuild parameters, then restore connections via `AddSource()` in a deferred callback. A simpler alternative exists when parameter count is stable: modify `Name`, `NickName`, `Access`, and `Description` directly on existing parameter objects, which preserves all wires because Grasshopper tracks connections by parameter object identity (`InstanceGuid`), not by name. The SDK also exposes a little-known `EmitSyncObject()`/`Sync()` mechanism on `GH_ComponentParamServer` for managing parameter lifecycle during sweeping changes, though even McNeel's own Hops component bypasses it in favor of manual wire caching.

---

## The fundamental constraint: no topology changes during solutions

Every approach must reckon with Grasshopper's core architectural rule, stated explicitly by David Rutten (Grasshopper's creator): **"Changes to the topology of components and changes to wires can only be made when a solution is not running."** Calling `AddSource()`, `RemoveSource()`, `RegisterInputParam()`, or `UnregisterInputParameter()` inside `SolveInstance` or `ComputeData` will throw an "object expired during a solution" exception or produce undefined behavior.

This means all parameter rebuilding must be deferred to run *between* solutions. Two timing mechanisms are used in practice:

**`GH_Document.ScheduleSolution(int delay, GH_ScheduleDelegate callback)`** is the officially recommended approach. The callback fires *before* the next solution starts, when topology changes are safe. David Rutten recommends a delay of **1–10 ms** (never 0 ms, which causes recursive stack overflow). Inside the callback, you modify parameters, restore wires, call `Params.OnParametersChanged()`, and then `ExpireSolution(false)` — the `false` is important because a new solution will start automatically after the callback returns.

```csharp
// Detected during SolveInstance that params need rebuilding:
if (needsRebuild && DA.Iteration == 0)
{
    OnPingDocument().ScheduleSolution(5, doc =>
    {
        RebuildParametersPreservingWires();
    });
    return; // Skip computation this iteration
}
```

**`Rhino.RhinoApp.Idle`** is the alternative used by McNeel's own Hops component. It subscribes to the Idle event, waits one cycle (to ensure the current solution has fully completed), then performs the rebuild and unsubscribes. This is slightly more conservative but equally valid.

---

## Pattern 1: the Hops dictionary cache (full teardown with wire restoration)

The **Hops component** (`mcneel/compute.rhino3d`, `HopsComponent.cs`) is the canonical reference implementation for dynamic parameter management with wire preservation. Its `DefineInputsAndOutputs()` method implements a four-step pattern that handles arbitrary parameter count changes, type changes, and reordering — all while preserving connections where parameter names match.

**Step 1 — Cache all connections keyed by parameter name:**

```csharp
// Cache input wires (upstream → this component)
Dictionary<string, List<IGH_Param>> inputSources = new Dictionary<string, List<IGH_Param>>();
foreach (var param in Params.Input)
    inputSources[param.Name] = new List<IGH_Param>(param.Sources);

// Cache output wires (this component → downstream)
Dictionary<string, List<IGH_Param>> outputRecipients = new Dictionary<string, List<IGH_Param>>();
foreach (var param in Params.Output)
    outputRecipients[param.Name] = new List<IGH_Param>(param.Recipients);
```

**Copying `param.Sources` into a `new List<IGH_Param>()`** is critical — you must snapshot the collection before modifying it, since the original list becomes invalid once the parameter is unregistered.

**Step 2 — Tear down and rebuild parameters.** Hops uses reflection to instantiate the normally-private `GH_InputParamManager` and `GH_OutputParamManager` constructors, then registers entirely new parameters matching the remote definition's schema. This is the most aggressive approach — a complete wipe-and-rebuild.

**Step 3 — Restore wires by name matching:**

```csharp
// Reconnect input wires
if (paramIndex >= 0 && inputSources.TryGetValue(name, out List<IGH_Param> rehookInputs))
{
    foreach (var source in rehookInputs)
        Params.Input[paramIndex].AddSource(source);
}

// Reconnect output wires (note: reversed direction!)
if (paramIndex >= 0 && outputRecipients.TryGetValue(name, out List<IGH_Param> rehookOutputs))
{
    foreach (var recipient in rehookOutputs)
        recipient.AddSource(Params.Output[paramIndex]);
}
```

The asymmetry here is important. For inputs, you call `newInputParam.AddSource(upstreamParam)`. For outputs, you call `downstreamParam.AddSource(newOutputParam)` — because the `Sources` relationship lives on the *receiving* parameter. The `Recipients` list is maintained automatically by Grasshopper; you never write to it directly.

**Step 4 — Finalize:** Call `Params.OnParametersChanged()` (mandatory after any parameter list modification — it invalidates caches and triggers layout recalculation), then expire the solution.

This pattern handles parameter reordering, additions, removals, and type changes gracefully. If a parameter's name no longer exists in the new layout, its wires are silently dropped. If a name persists, wires reconnect regardless of index position.

---

## Pattern 2: in-place modification preserves wires automatically

When you only need to change parameter metadata — not the count or fundamental type — **modifying properties directly on existing `IGH_Param` objects preserves all wire connections automatically**. This works because Grasshopper tracks connections by the parameter's object identity (its `InstanceGuid`), not by `Name` or `NickName`.

```csharp
// All of these preserve existing wires:
Params.Input[i].Name = "NewName";
Params.Input[i].NickName = "NN";
Params.Input[i].Description = "Updated description";
Params.Input[i].Access = GH_ParamAccess.list;   // was .item
Params.Input[i].MutableNickName = false;
Params.Input[i].Optional = true;
```

For `Param_ScriptVariable`, changing the `TypeHint` also preserves wires (though it may cause downstream type-conversion errors). This approach was confirmed by djordje on the McNeel Discourse forum and is used by Ondrej Vesely's component-refresh pattern, which iterates `zip(oldParams, newParams)` and copies names across without touching the parameter collection.

The **optimal hybrid strategy** combines both patterns: when the parameter count changes, only add new parameters at the end or remove surplus ones from the end, while modifying existing parameters in-place for any that survive. This minimizes wire disruption:

```csharp
void SmartRebuild(List<ParamDefinition> newDefs)
{
    // Phase 1: Modify existing params in-place (wires preserved)
    int overlapCount = Math.Min(Params.Input.Count, newDefs.Count);
    for (int i = 0; i < overlapCount; i++)
    {
        Params.Input[i].Name = newDefs[i].Name;
        Params.Input[i].NickName = newDefs[i].NickName;
        Params.Input[i].Access = newDefs[i].Access;
    }

    // Phase 2: Add new params if needed
    for (int i = Params.Input.Count; i < newDefs.Count; i++)
    {
        var p = new Param_GenericObject { Name = newDefs[i].Name, NickName = newDefs[i].NickName };
        Params.RegisterInputParam(p);
    }

    // Phase 3: Remove surplus params from the end
    while (Params.Input.Count > newDefs.Count)
        Params.UnregisterInputParameter(Params.Input[Params.Input.Count - 1]);

    Params.OnParametersChanged();
    VariableParameterMaintenance();
}
```

This is the approach used by the jSwan Deserialize component's `CreateOutputParams` method — it grows or shrinks the parameter list from the end, leaving existing parameters (and their wires) untouched.

---

## EmitSyncObject and Sync: the built-in snapshot mechanism

`GH_ComponentParamServer` exposes a lesser-known snapshot/restore API that most plugin developers overlook. **`EmitSyncObject()`** creates a shallow representation of the current parameter server state. After making sweeping changes, calling **`Sync(syncObject)`** properly cleans up any parameters that were removed — disconnecting their wires and disposing them correctly.

```csharp
var syncObj = Params.EmitSyncObject();

// Make sweeping changes: clear, re-register, modify params
// ...

Params.Sync(syncObj);  // Cleans up removed params properly
```

The documentation states: *"All param references in the sync object which are no longer present in the current server will be properly deleted. The sync object will be reset in the process, so you can only call this function with a specific Sync object once."*

However, **`Sync()` handles cleanup of removed parameters but does not automatically reconnect wires to new parameters.** You still need the manual `AddSource()` reconnection step from Pattern 1. The benefit of `EmitSyncObject`/`Sync` is that it ensures proper disposal — without it, orphaned parameters may linger in memory with stale references. Even McNeel's Hops component does not use this mechanism, opting instead for fully manual management. Still, combining `EmitSyncObject`/`Sync` with the dictionary-based wire cache gives you the most defensive implementation.

---

## How existing plugins handle dynamic parameters

The landscape of approaches across major plugins reveals distinct strategies depending on the use case:

**Hops** (McNeel built-in) performs a complete parameter wipe-and-rebuild via reflection-instantiated `GH_InputParamManager`/`GH_OutputParamManager`, caching and restoring wires by name using dictionaries, triggered by `RhinoApp.Idle`. This is the most comprehensive approach and handles arbitrary schema changes from remote Grasshopper definitions.

**jSwan** (Andrew Heumann) uses a grow/shrink-from-end strategy with `ScheduleSolution`. Parameters are only added or removed at the end of the list, so existing parameters' wires are naturally preserved. `VariableParameterMaintenance()` then updates names, nicknames, and access types on the surviving parameters in-place. A "Lock Structure" right-click option prevents unintended shape changes.

**HotLoader** (Cam Newnham) takes a fundamentally different approach — it never rebuilds parameters at all. Instead, it keeps the GH_Component shell intact on the canvas (preserving all wires and layout) and hot-swaps only the compiled assembly behind it. This sidesteps the wire-preservation problem entirely.

**MetaHopper** (Andrew Heumann) manipulates component parameters from outside — renaming, reconnecting, and modifying properties on existing components. It demonstrates that `param.Sources` and `param.Recipients` collections are fully accessible and manipulable from external code.

**compas_timber** (Gramazio Kohler Research) provides Python helper functions that use `param.IsolateObject()` before `UnregisterInputParameter()` to cleanly disconnect wires. Their code includes the comment *"we could consider renaming params if we don't want to disconnect GH component inputs"* — explicitly acknowledging in-place modification as the preferred alternative when possible.

---

## The complete recommended implementation

Combining all patterns into a production-ready solution requires careful attention to ordering, timing, and edge cases. Here is the synthesized approach:

```csharp
public class DynamicComponent : GH_Component, IGH_VariableParameterComponent
{
    private bool _needsRebuild = false;
    private List<ParamDef> _pendingDefs = null;

    protected override void SolveInstance(IGH_DataAccess DA)
    {
        if (_needsRebuild) return; // Skip while rebuild is pending

        // Detect that parameters need changing...
        var newDefs = ComputeNewParameterLayout(DA);
        if (!LayoutMatchesCurrent(newDefs) && DA.Iteration == 0)
        {
            _pendingDefs = newDefs;
            _needsRebuild = true;
            OnPingDocument()?.ScheduleSolution(5, RebuildCallback);
            return;
        }
        // Normal computation...
    }

    private void RebuildCallback(GH_Document doc)
    {
        if (_pendingDefs == null) return;

        // 1. Cache connections by name
        var inSources = new Dictionary<string, List<IGH_Param>>();
        foreach (var p in Params.Input)
            inSources[p.Name] = new List<IGH_Param>(p.Sources);

        var outRecipients = new Dictionary<string, List<IGH_Param>>();
        foreach (var p in Params.Output)
            outRecipients[p.Name] = new List<IGH_Param>(p.Recipients);

        // 2. Rebuild parameters (in-place where possible)
        int overlap = Math.Min(Params.Input.Count, _pendingDefs.Count);
        for (int i = 0; i < overlap; i++)
        {
            Params.Input[i].Name = _pendingDefs[i].Name;
            Params.Input[i].NickName = _pendingDefs[i].NickName;
            Params.Input[i].Access = _pendingDefs[i].Access;
            // Wires survive on these params automatically
        }
        while (Params.Input.Count < _pendingDefs.Count)
            Params.RegisterInputParam(CreateParameter(GH_ParameterSide.Input,
                Params.Input.Count));
        while (Params.Input.Count > _pendingDefs.Count)
            Params.UnregisterInputParameter(
                Params.Input[Params.Input.Count - 1]);

        // 3. Restore wires on newly registered params (beyond overlap)
        for (int i = overlap; i < Params.Input.Count; i++)
        {
            if (inSources.TryGetValue(Params.Input[i].Name, out var sources))
                foreach (var s in sources)
                    Params.Input[i].AddSource(s);
        }

        // 4. Finalize
        Params.OnParametersChanged();
        VariableParameterMaintenance();
        _needsRebuild = false;
        _pendingDefs = null;
        ExpireSolution(false);
    }
    // ... IGH_VariableParameterComponent implementation
}
```

Key details in this implementation: the `_needsRebuild` flag prevents infinite rebuild loops (a common pitfall noted by community member magicteddy). Parameters within the overlap range are modified in-place, preserving their wires without any caching. Only newly registered parameters need explicit `AddSource()` reconnection. The name-based matching means parameters that were renamed or reordered still reconnect correctly, while parameters that disappeared from the new layout lose their wires gracefully.

---

## Conclusion

The wire-preservation problem in Grasshopper's dynamic parameter system has no single built-in solution, but the ecosystem has converged on well-tested patterns. **In-place modification is always preferable when the parameter count is stable** — it's zero-risk for wire preservation. When parameters must be added or removed, the **Hops dictionary-cache pattern** (cache `Sources`/`Recipients` by name, rebuild, restore via `AddSource()`) is the proven production approach used by McNeel themselves. The hybrid strategy — modifying surviving parameters in-place while only using cache/restore for genuinely new parameters — minimizes both complexity and risk. All topology changes must occur outside a running solution, either in a `ScheduleSolution` callback or on `RhinoApp.Idle`. Finally, always implement `IGH_VariableParameterComponent` even if all its methods return false — without it, Grasshopper cannot serialize your dynamic parameter layout correctly during file save, undo, and paste operations.
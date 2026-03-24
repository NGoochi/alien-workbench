# GOTCHAS.md
### Known Issues, Platform Differences, and Hard-Won Lessons

---

## Critical: The Shebang Line

```python
#! python 3
```

This MUST be the first line of every script. Without it, Rhino 8 may default to IronPython 2 execution, which has a completely different standard library, different syntax (`print` is a statement not a function), and different RhinoCommon bindings. If your script works in a standalone Python editor but fails in Alien with mysterious import errors, check the shebang first.

---

## None Handling

Every input parameter may be `None` when:
- Nothing is wired to it
- The upstream component hasn't computed yet
- The upstream component errored

**Always guard every input:**
```python
# Single items
if origin is None:
    origin = rg.Point3d(0, 0, 0)

# Numbers with validation
if count is None or count < 1:
    count = 5

# List inputs (with list[] in header)
# These default to empty list [], not None
if not curves:
    curves = []
```

Failure to handle `None` is the #1 cause of `AttributeError: 'NoneType' object has no attribute...` errors.

---

## Platform Differences (Windows vs macOS)

### FileSystemWatcher
- **macOS:** Known to miss rapid successive saves, fire duplicate events, or lag. Alien has a fallback (timestamp check on each solve), but if you save rapidly multiple times, the node may not pick up intermediate states.
- **Windows:** Generally reliable. Occasional duplicate events are caught by the debounce logic (150ms).
- **Workaround:** If the node seems stale after a save, right-click → "Reload Script" to force a re-read.

### File Paths
- Windows: `C:\Users\nick\projects\my_script.py`
- macOS: `/Users/nick/projects/my_script.py`
- In scripts, always use `os.path.join()`. Never hardcode `\\` or `/`.
- The `script_path` input in GH must use the OS-native format. The user types this into a Panel.

### Font Rendering
- Component text may render slightly differently on macOS. No functional impact.

---

## Output Type Gotchas

### Polyline is not a Curve
```python
# WRONG — downstream Curve input won't accept this
polyline = rg.Polyline(points)
result = polyline

# RIGHT — convert to a Curve subclass
polyline = rg.Polyline(points)
result = polyline.ToNurbsCurve()
```

### Line is not a Curve
```python
# WRONG
result = rg.Line(pt_a, pt_b)

# RIGHT
result = rg.LineCurve(rg.Line(pt_a, pt_b))
```

### Boolean operations return arrays
```python
# WRONG — result is an array, not a single Brep
result = rg.Brep.CreateBooleanUnion(breps, 0.01)

# RIGHT — handle the array
unions = rg.Brep.CreateBooleanUnion(breps, 0.01)
result = list(unions) if unions else []
```

### CreateFromBox returns a Brep, not a Box
```python
box = rg.Box(plane, interval_x, interval_y, interval_z)
# box is a Box struct — not geometry you can output
brep = box.ToBrep()
# brep is a Brep — this is what GH can consume
```

---

## Import Gotchas

### `rhinoscriptsyntax` vs `Rhino.Geometry`
- `rhinoscriptsyntax` (aliased as `rs`) is a high-level convenience library. It works with GUIDs and the active document. Many functions add geometry to the document automatically.
- `Rhino.Geometry` (aliased as `rg`) is the low-level RhinoCommon API. It works with geometry objects in memory. Nothing gets added to the document unless you explicitly do so.
- **For Alien scripts, prefer `Rhino.Geometry`.** Your scripts should produce geometry objects that flow through GH wires, not add geometry directly to the Rhino document. Using `rs.AddPoint()` inside a Alien creates a point in the Rhino doc AND tries to return a GUID — not what you want.
- **Exception:** `rs` is fine for querying document state (e.g., `rs.ObjectsByLayer()`, `rs.LayerNames()`).

### System.Drawing.Color
```python
# If you use the color type, you need this import
import System.Drawing

# NOT import System.Drawing.Color — that's the class, not the namespace
# Create colors like:
col = System.Drawing.Color.FromArgb(255, 128, 0)
```

### math vs RhinoCommon math
```python
import math
# math.pi, math.sin(), math.cos() — standard Python

# RhinoCommon also has:
# rg.RhinoMath.ToRadians(degrees)
# rg.RhinoMath.ToDegrees(radians)
# Use whichever is more readable for your context
```

---

## Grasshopper-Specific Gotchas

### print() goes to Rhino, not GH
`print()` statements in your script output to Rhino's command history window, not to any GH parameter. If you want visible output in GH, use a string output:
```python
# NODE_OUTPUTS: result, log
log = f"Processed {count} items"
```
The agent can read `print()` output via `get_rhino_command_history` MCP tool.

### Tolerance
Rhino has a document tolerance (usually 0.01 or 0.001). Many geometry operations need a tolerance parameter:
```python
tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
# Use this instead of hardcoding 0.01
```

### Large data kills performance
Grasshopper recomputes the entire graph when any input changes. If your script generates 100,000 points, every downstream component processes all of them on every change. For expensive operations:
- Keep counts low during development (use a slider with small max)
- Consider outputting lightweight previews (bounding boxes, sample points) alongside full geometry
- Use GH's built-in "Disable" right-click option on expensive downstream components while iterating

### Component execution order
GH solves components in dependency order (upstream first). If Script A feeds Script B, A always runs before B. But if two scripts have no dependency relationship, their execution order is undefined. Don't rely on side effects between unconnected scripts.

---

## Plugin Deployment Gotcha

**If you rebuild the plugin from source, you MUST close Rhino first.** GH locks the `.gha` file at startup. The copy to the Grasshopper Libraries folder will fail silently if Rhino is running. You will then be running an OLD version of the plugin with zero indication. This applies to plugin developers only — users who installed a pre-built `.gha` don't need to worry about this.

---

## Common Error Messages and What They Mean

| Error | Cause | Fix |
|---|---|---|
| `name 'rg' is not defined` | Missing `import Rhino.Geometry as rg` | Add the import |
| `'NoneType' object has no attribute...` | Unconnected input used without None check | Add defensive default |
| `expected Point3d, got str` | Panel connected to Point3d input | Use a Point component, not a Panel |
| `Unable to convert...` | Type mismatch on wire | Check TYPE_LEXICON.md for compatible types |
| `Index out of range` | Empty list accessed by index | Check `if points:` before `points[0]` |
| `Script file not found` | Wrong path in script_path Panel | Verify the absolute path, check slashes |
| No error but no output | Output variable name doesn't match header | Check spelling: `# NODE_OUTPUTS: result` needs `result = ...` in script |
| Node is orange with no message | Header parse warning | Check header syntax — see HEADER_PROTOCOL.md |

## DataNode-Specific Gotchas

### Editor freezes on open (with many items)
The DataNode editor creates Eto.Forms controls for each item. With 14+ items × 3+ fields, that's 200+ controls — which can freeze the UI for up to a minute. The current implementation uses a `GridView` (single control, virtualized rows) to avoid this. If you ever revert to per-item sliders, you MUST wrap `RebuildContent()` in an `_isBuilding` flag to suppress `RequestRecompute()` during construction.

### Never call `ExpireSolution(true)` from the editor
`ExpireSolution(true)` forces an immediate synchronous recompute on the current thread. If called from an Eto event handler, it deadlocks the UI. Always use `ScheduleSolution(10, ...)` instead — it defers the recompute to the next GH solver tick.

### DataNode outputs raw values, not GH_Goo
DataNode outputs plain `double`/`string` values. Grasshopper wraps these as `GH_Number`/`GH_String` on the wire. Alien's list input handler unwraps `GH_Goo` objects automatically (added in this session), so downstream scripts receive plain Python types.

### The `.gha` deployment gotcha applies to DataNode too
See the "Plugin Deployment Gotcha" section above. If Rhino is running, the build copy may fail silently, and you'll test an old DataNode with old bugs.

---

*End of GOTCHAS.md.*


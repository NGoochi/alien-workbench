# HEADER_PROTOCOL.md
### How to Declare Inputs and Outputs

---

## The Header Block

Every Alien Python file must start with a header block near the top of the file. The parser scans the first **200 lines** for `NODE_INPUTS` / `NODE_OUTPUTS` (legacy docs often said 10 lines — the implementation allows a longer preamble).

```python
#! python 3
# NODE_INPUTS: origin:Point3d, x_count:int, y_count:int, cell_size:float
# NODE_OUTPUTS: voxels, centers, count
```

### Required elements

**`#! python 3`** — Shebang line. Must be present. Tells Rhino this is CPython 3, not IronPython. Without it, execution may fail silently or use the wrong runtime.

**`# NODE_INPUTS:`** — Declares input parameters. Each input is a `name:Type` pair, comma-separated. Whitespace around commas and colons is tolerated.

**`# NODE_OUTPUTS:`** — Declares output parameters. Each output is just a name, comma-separated. Outputs are always generic (untyped) — the script determines what goes in them.

### Rules

- Both lines are optional. A script with no `NODE_INPUTS` line gets no dynamic inputs (only the permanent `script_path` input). A script with no `NODE_OUTPUTS` line gets no outputs.
- If the header is malformed, the component shows an orange warning but does not crash. It will have no dynamic params.
- Input names must be valid Python identifiers (`snake_case` recommended). They become variable names in your script.
- Output names must also be valid Python identifiers. They are collected from the script's namespace after execution.
- Duplicate names within inputs or within outputs are not allowed — the parser will use the last occurrence.
- Names are used for wire preservation. If you rename an input, any wire connected to the old name will disconnect. If you keep the name the same, the wire survives edits.

---

## Input Type Hints

The type hint after the colon determines what kind of Grasshopper parameter is created.

### Item access (default)

```
name:Type
```

Creates a single-item input. The variable in your script receives one value (or `None` if unconnected).

### List access

```
name:list[Type]
```

Creates a list-access input. The variable in your script receives a Python list of values (or an empty list if unconnected).

### Supported types

| Type hint | GH Param type | Python receives | Notes |
|-----------|--------------|-----------------|-------|
| `Point3d` | `Param_Point` | `Rhino.Geometry.Point3d` | |
| `Vector3d` | `Param_Vector` | `Rhino.Geometry.Vector3d` | |
| `Plane` | `Param_Plane` | `Rhino.Geometry.Plane` | |
| `Line` | `Param_Line` | `Rhino.Geometry.Line` | |
| `Curve` | `Param_Curve` | `Rhino.Geometry.Curve` subclass | |
| `Surface` | `Param_Surface` | `Rhino.Geometry.Surface` subclass | |
| `Brep` | `Param_Brep` | `Rhino.Geometry.Brep` | |
| `Mesh` | `Param_Mesh` | `Rhino.Geometry.Mesh` | |
| `int` | `Param_Integer` | `int` | |
| `float` | `Param_Number` | `float` | |
| `str` | `Param_String` | `str` | |
| `bool` | `Param_Boolean` | `bool` | |
| `color` | `Param_Colour` | `System.Drawing.Color` | |
| `geometry` | `Param_GenericObject` | any geometry type | Use when input could be mixed types |

See `TYPE_LEXICON.md` for the full mapping including RhinoCommon namespaces and common mistakes.

---

## Parameter metadata (`|` suffix) — Alien browser UI

Optional **per-parameter** descriptions and slider hints for the Alien browser editor. Append `|` after the type, then a quoted description string. Structured keys `min=`, `max=`, `step=` may follow the closing quote (hybrid parsing also tries to read `Range: a – b` from the description text).

```python
# NODE_INPUTS: width:float | "Panel width (model units)" min=0.5 max=12.0 step=0.1
# NODE_OUTPUTS: mesh:geometry | "Resulting mesh for fabrication"
```

- Metadata is **optional** — scripts without `|` behave as before.
- **Do not** add a separate metadata block; everything stays on the same line as the parameter.
- Commas inside double-quoted descriptions are supported; use commas **between** parameters only outside quotes.

---

## Output Names

Outputs are usually plain names. You may add `:type` and the same `|` metadata pattern for documentation and typed GH outputs:

```python
# NODE_OUTPUTS: result:geometry, log:str
```

```
# NODE_OUTPUTS: result, log, count
```

After your script executes, Alien collects variables matching these names from the script's namespace. Whatever Python object is in that variable gets pushed to the corresponding output parameter.

### What you can output

- Single geometry objects (`Point3d`, `Curve`, `Brep`, etc.)
- Lists of geometry objects (automatically become GH lists)
- Primitive values (`int`, `float`, `str`, `bool`)
- Lists of primitives
- `None` (the output will be empty)
- Nested lists become GH DataTrees (one level of nesting = one tree branch)

### Output variable rules

- The variable must exist in the script's global namespace after execution.
- If a declared output variable is not set, the output will be empty (no error).
- If you set an output to a type that downstream components can't consume, the wire will show orange (type mismatch warning).

---

## Examples

### Minimal script (no inputs)
```python
#! python 3
# NODE_OUTPUTS: greeting
greeting = "Hello from Alien"
```

### Numeric processing
```python
#! python 3
# NODE_INPUTS: a:float, b:float
# NODE_OUTPUTS: sum, product
if a is None: a = 0.0
if b is None: b = 0.0
sum = a + b
product = a * b
```

### Geometry with list input
```python
#! python 3
# NODE_INPUTS: curves:list[Curve], offset_dist:float
# NODE_OUTPUTS: offset_curves, count
import Rhino.Geometry as rg

if offset_dist is None: offset_dist = 1.0
offset_curves = []
if curves:
    for c in curves:
        result = c.Offset(rg.Plane.WorldXY, offset_dist, 0.01, rg.CurveOffsetCornerStyle.Sharp)
        if result:
            offset_curves.extend(result)
count = len(offset_curves)
```

### All supported types in one header
```python
#! python 3
# NODE_INPUTS: pt:Point3d, vec:Vector3d, pln:Plane, ln:Line, crv:Curve, srf:Surface, brp:Brep, msh:Mesh, i:int, f:float, s:str, b:bool, col:color, geo:geometry
# NODE_OUTPUTS: report
report = f"Received {sum(1 for x in [pt,vec,pln,ln,crv,srf,brp,msh,i,f,s,b,col,geo] if x is not None)} inputs"
```

---

## Header Stability and Wire Preservation

**Wire connections in Grasshopper are preserved by parameter name.** When you edit a script and save, Alien compares the new header against the current parameters. Parameters whose names haven't changed keep their wires. Parameters that are new get added. Parameters that were removed get deleted (and their wires drop).

This means:
- **Don't rename inputs unnecessarily** — it breaks wires.
- **Adding new inputs at the end** is safe — existing wires are unaffected.
- **Reordering inputs** is safe — wires follow names, not positions.
- **Changing a type hint** (e.g., `pt:Point3d` → `pt:Curve`) rebuilds the parameter but reconnects the wire if the name matches. The wire may show a type mismatch warning if the upstream component outputs the wrong type.

---

*End of HEADER_PROTOCOL.md.*

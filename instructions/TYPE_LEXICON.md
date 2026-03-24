# TYPE_LEXICON.md
### Complete Type Reference for Alien

This document maps every supported type between the header hint, the Grasshopper parameter it creates, the RhinoCommon class your script receives, and how to construct/return that type in Python.

---

## Geometry Types

### Point3d
| | |
|---|---|
| **Header hint** | `Point3d` |
| **GH Param** | `Param_Point` |
| **Python receives** | `Rhino.Geometry.Point3d` |
| **Construct in script** | `rg.Point3d(x, y, z)` |
| **Common source nodes** | Point, Construct Point, Divide Curve |
| **Common mistake** | Writing `(x, y, z)` tuple instead of `rg.Point3d(x, y, z)`. Grasshopper cannot consume Python tuples as points. |

### Vector3d
| | |
|---|---|
| **Header hint** | `Vector3d` |
| **GH Param** | `Param_Vector` |
| **Python receives** | `Rhino.Geometry.Vector3d` |
| **Construct in script** | `rg.Vector3d(x, y, z)` |
| **Common source nodes** | Unit X, Unit Y, Vector 2Pt |
| **Common mistake** | Confusing `Point3d` and `Vector3d`. They are distinct types. A `Point3d` is a location; a `Vector3d` is a direction + magnitude. You can convert: `rg.Vector3d(point)` or `rg.Point3d(vector)`. |

### Plane
| | |
|---|---|
| **Header hint** | `Plane` |
| **GH Param** | `Param_Plane` |
| **Python receives** | `Rhino.Geometry.Plane` |
| **Construct in script** | `rg.Plane(origin_pt, normal_vec)` or `rg.Plane.WorldXY` |
| **Common source nodes** | XY Plane, Construct Plane, Plane Normal |
| **Common mistake** | Forgetting that `Plane.WorldXY` is a static property, not a constructor call. |

### Line
| | |
|---|---|
| **Header hint** | `Line` |
| **GH Param** | `Param_Line` |
| **Python receives** | `Rhino.Geometry.Line` |
| **Construct in script** | `rg.Line(pt_a, pt_b)` or `rg.Line(pt, vec, length)` |
| **Common source nodes** | Line, Line SDL |
| **Common mistake** | `Line` is a struct, not a curve. To use curve methods on a line, convert: `rg.LineCurve(line)`. |

### Curve
| | |
|---|---|
| **Header hint** | `Curve` |
| **GH Param** | `Param_Curve` |
| **Python receives** | `Rhino.Geometry.Curve` (usually a subclass: `NurbsCurve`, `LineCurve`, `ArcCurve`, `PolylineCurve`) |
| **Construct in script** | `rg.NurbsCurve.CreateFromLine(line)`, `rg.ArcCurve(arc)`, `rg.Polyline(points).ToNurbsCurve()` |
| **Common source nodes** | Curve, Interpolate, Polyline |
| **Common mistake** | Using `rg.Polyline(points)` directly — `Polyline` is not a `Curve`. Call `.ToNurbsCurve()` or `.ToPolylineCurve()` to get a Curve-compatible output. |

### Surface
| | |
|---|---|
| **Header hint** | `Surface` |
| **GH Param** | `Param_Surface` |
| **Python receives** | `Rhino.Geometry.Surface` (usually `NurbsSurface` or `BrepFace`) |
| **Construct in script** | `rg.NurbsSurface.CreateFromCorners(pt1, pt2, pt3, pt4)` |
| **Common source nodes** | Surface, Loft, Patch |
| **Common mistake** | Many GH operations return `Brep` not `Surface`. A single-face Brep can be accessed via `brep.Faces[0]` to get a Surface. |

### Brep
| | |
|---|---|
| **Header hint** | `Brep` |
| **GH Param** | `Param_Brep` |
| **Python receives** | `Rhino.Geometry.Brep` |
| **Construct in script** | `rg.Brep.CreateFromBox(box)`, `rg.Brep.CreateFromSphere(sphere)`, `rg.Brep.CreateBooleanUnion(breps, tol)` |
| **Common source nodes** | Box, Sphere, Brep, Boolean Union |
| **Common mistake** | `Brep.CreateBooleanUnion()` returns an array, not a single Brep. Iterate or index the result. |

### Mesh
| | |
|---|---|
| **Header hint** | `Mesh` |
| **GH Param** | `Param_Mesh` |
| **Python receives** | `Rhino.Geometry.Mesh` |
| **Construct in script** | `mesh = rg.Mesh()` then `mesh.Vertices.Add(pt)`, `mesh.Faces.AddFace(i,j,k)` |
| **Common source nodes** | Mesh, Mesh Box, Mesh Brep |
| **Common mistake** | Forgetting `mesh.Normals.ComputeNormals()` after building a mesh manually. Without normals, the mesh renders black in the viewport. |

---

## Primitive Types

### int
| | |
|---|---|
| **Header hint** | `int` |
| **GH Param** | `Param_Integer` |
| **Python receives** | `int` |
| **Common source nodes** | Number Slider (set to integer), Panel |
| **Common mistake** | GH Number Sliders default to float. The user must right-click the slider and set it to Integer for clean int input. Your script should handle receiving a float and cast: `int(round(x))`. |

### float
| | |
|---|---|
| **Header hint** | `float` |
| **GH Param** | `Param_Number` |
| **Python receives** | `float` |
| **Common source nodes** | Number Slider, Panel, Gene Pool |
| **Common mistake** | None — this is the most forgiving type. GH auto-casts ints to floats. |

### str
| | |
|---|---|
| **Header hint** | `str` |
| **GH Param** | `Param_String` |
| **Python receives** | `str` |
| **Common source nodes** | Panel, Text |
| **Common mistake** | Panel nodes in GH add trailing whitespace or newlines. Use `.strip()` on string inputs. |

### bool
| | |
|---|---|
| **Header hint** | `bool` |
| **GH Param** | `Param_Boolean` |
| **Python receives** | `bool` |
| **Common source nodes** | Boolean Toggle, Panel ("True"/"False") |
| **Common mistake** | Panel text "True" is auto-cast to bool by GH but "true" (lowercase) may not be. Prefer Boolean Toggle components. |

### color
| | |
|---|---|
| **Header hint** | `color` |
| **GH Param** | `Param_Colour` |
| **Python receives** | `System.Drawing.Color` |
| **Construct in script** | `System.Drawing.Color.FromArgb(r, g, b)` or `System.Drawing.Color.FromArgb(a, r, g, b)` |
| **Import needed** | `import System.Drawing` |
| **Common source nodes** | Colour Swatch, Construct Colour |
| **Common mistake** | RhinoCommon has its own `Rhino.Display.Color4f` — don't confuse it with `System.Drawing.Color`. GH uses `System.Drawing.Color`. |

---

## Generic Type

### geometry
| | |
|---|---|
| **Header hint** | `geometry` |
| **GH Param** | `Param_GenericObject` |
| **Python receives** | whatever was connected (could be any type) |
| **When to use** | When the input could be mixed geometry types, or when you want to accept anything. |
| **Common mistake** | Not checking the type at runtime. Always use `isinstance()` checks: `if isinstance(geo, rg.Curve):` |

---

## List Access

Wrap any type in `list[...]` to request list access:

```
# NODE_INPUTS: points:list[Point3d], values:list[float], meshes:list[Mesh]
```

With list access, your variable receives a Python `list` containing zero or more items of the specified type. If nothing is connected, you get an empty list `[]`, not `None`.

With item access (no `list[]` wrapper), you get a single item or `None`.

---

## Outputting Data

Outputs are untyped. You can put anything in them. But downstream components expect specific types, so match accordingly:

| If downstream expects | Your output should be |
|---|---|
| Points | `rg.Point3d` or `list` of `rg.Point3d` |
| Curves | `rg.Curve` subclass or `list` of them |
| Numbers | `int` or `float` or `list` of them |
| Text | `str` or `list` of `str` |
| Breps | `rg.Brep` or `list` of `rg.Brep` |
| Generic geometry | Any `GeometryBase` subclass |

### Outputting lists

If you set an output variable to a Python list, it becomes a GH list (single branch, multiple items). This is the most common pattern:

```python
result = [rg.Point3d(i, 0, 0) for i in range(10)]
```

### Outputting trees (nested lists)

A list of lists becomes a GH DataTree. Each inner list is a branch:

```python
# Creates a tree with 5 branches, 10 items each
result = [[rg.Point3d(i, j, 0) for i in range(10)] for j in range(5)]
```

---

## Common Import Block

Most scripts will use some combination of:

```python
import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import System.Drawing
import math
import os
```

Only import what you need. `Rhino.Geometry as rg` is almost always required.

---

*End of TYPE_LEXICON.md.*

# CHAINING.md
### Writing Scripts That Connect in Grasshopper

---

## Core Principle

In Grasshopper, components connect via wires. A wire carries data from one component's output to another's input. For two Alien components to chain together, the upstream script's output must produce a type that the downstream script's input can consume.

Alien outputs are untyped (generic). Alien inputs are typed (via the header). Grasshopper will attempt to auto-cast the generic output to the expected input type. This works reliably when the actual Python object matches the declared type. It fails when there's a genuine type mismatch.

---

## The Compatibility Table

| Upstream outputs | Downstream input type | Works? |
|---|---|---|
| `rg.Point3d` | `Point3d` | Yes |
| `list` of `rg.Point3d` | `list[Point3d]` | Yes |
| `list` of `rg.Point3d` | `Point3d` (item access) | Yes — GH iterates, calling the script once per point |
| `rg.Point3d` | `list[Point3d]` | Yes — GH wraps single item into list |
| `rg.LineCurve` | `Curve` | Yes — LineCurve is a subclass of Curve |
| `rg.NurbsCurve` | `Curve` | Yes — same reason |
| `rg.Brep` | `Mesh` | No — type mismatch, orange wire |
| `str` | `float` | Sometimes — GH tries to parse "3.14" to 3.14, but fragile |
| `int` | `float` | Yes — auto-cast |
| `float` | `int` | Yes — truncates to integer |
| `rg.Point3d` | `geometry` | Yes — geometry accepts anything |
| anything | `geometry` | Yes |

---

## Design Patterns for Chaining

### Pattern 1: Generator → Transformer
One script creates geometry, the next modifies it.

**Script A (generator):**
```python
#! python 3
# NODE_INPUTS: origin:Point3d, count:int
# NODE_OUTPUTS: curves

import Rhino.Geometry as rg
# ... generates a list of curves
curves = [rg.Circle(rg.Plane(rg.Point3d(i*5,0,0), rg.Vector3d.ZAxis), 2.0).ToNurbsCurve() for i in range(count or 5)]
```

**Script B (transformer):**
```python
#! python 3
# NODE_INPUTS: curves:list[Curve], offset:float
# NODE_OUTPUTS: offset_curves

import Rhino.Geometry as rg
if offset is None: offset = 1.0
offset_curves = []
for c in (curves or []):
    result = c.Offset(rg.Plane.WorldXY, offset, 0.01, rg.CurveOffsetCornerStyle.Sharp)
    if result:
        offset_curves.extend(result)
```

**Wire:** Script A `curves` output → Script B `curves` input. Works because both sides deal in `Curve` objects.

### Pattern 2: Analyser → Filter
One script computes values, the next uses those values to filter or select.

**Script A (analyser):**
```python
#! python 3
# NODE_INPUTS: points:list[Point3d], attractor:Point3d
# NODE_OUTPUTS: distances

import Rhino.Geometry as rg
if attractor is None: attractor = rg.Point3d(0,0,0)
distances = [pt.DistanceTo(attractor) for pt in (points or [])]
```

**Script B (filter):**
```python
#! python 3
# NODE_INPUTS: points:list[Point3d], distances:list[float], threshold:float
# NODE_OUTPUTS: filtered_points

if threshold is None: threshold = 10.0
filtered_points = [pt for pt, d in zip(points or [], distances or []) if d < threshold]
```

**Wires:** Something → Script A `points` and `attractor`. Script A `distances` → Script B `distances`. Same "something" → Script B `points`. Script B gets both the original points and the computed distances from Script A.

### Pattern 3: Multi-output hub
One script does heavy computation and exports multiple outputs that feed different downstream scripts.

```python
#! python 3
# NODE_INPUTS: brep:Brep
# NODE_OUTPUTS: edges, faces, vertices, volume

import Rhino.Geometry as rg
edges = []
faces = []
vertices = []
volume = 0.0

if brep:
    edges = [e.EdgeCurve for e in brep.Edges]
    faces = [f.DuplicateFace(False) for f in brep.Faces]
    vertices = [rg.Point3d(v) for v in brep.Vertices]
    vmp = rg.VolumeMassProperties.Compute(brep)
    if vmp:
        volume = vmp.Volume
```

Each output can feed a different downstream Alien or native GH component.

### Pattern 4: Mixing Aliens with native GH
Alien outputs are generic but Grasshopper handles the casting. You can wire a Alien output directly into a native GH component's input:

- Alien outputting `list[Point3d]` → native `Voronoi` component's `Points` input: works
- Alien outputting `list[Curve]` → native `Loft` component's `Curves` input: works
- Native `Divide Curve` component's `Points` output → Alien input `points:list[Point3d]`: works

This is a major advantage of the Alien approach — you can mix algorithmic Python scripts with the full Ladybug/Kangaroo/native GH ecosystem.

---

## Naming Conventions for Interoperable Scripts

When writing scripts that are designed to chain together, use consistent parameter names across the library:

| Concept | Suggested name | Type |
|---|---|---|
| A single point | `point` or `origin` | `Point3d` |
| Multiple points | `points` | `list[Point3d]` |
| A single curve | `curve` | `Curve` |
| Multiple curves | `curves` | `list[Curve]` |
| A single brep/solid | `brep` | `Brep` |
| Multiple breps | `breps` | `list[Brep]` |
| A mesh | `mesh` | `Mesh` |
| A bounding volume | `bounds` or `domain` | `Brep` |
| Scalar field values | `values` or `field` | `list[float]` |
| Index list | `indices` | `list[int]` |
| Toggle/switch | `enabled` or `active` | `bool` |
| Density/resolution | `resolution` or `count` | `int` |
| Scale factor | `scale` or `factor` | `float` |
| Log/debug text | `log` | (output only) |

These are suggestions, not requirements. Consistency within your project matters more than matching this table exactly.

---

## Data Tree Awareness

Grasshopper's data tree system is the most confusing part of chaining. Key things to know:

**When a Alien outputs a flat list** (e.g., `points = [pt1, pt2, pt3]`), it creates a single-branch tree `{0}` with 3 items.

**When a Alien outputs a nested list** (e.g., `points = [[pt1, pt2], [pt3, pt4]]`), it creates a two-branch tree `{0}` and `{1}`, with 2 items each.

**When a downstream Alien has `list[Point3d]` input**, it receives one branch at a time. If the upstream tree has multiple branches, the downstream script runs once per branch.

**When a downstream Alien has `Point3d` (item access) input**, it receives one item at a time. GH iterates through every item in every branch.

**Practical advice:** If you're not sure about tree structure, use `get_component_outputs` to inspect what's actually flowing through the wire. The `dataCount` field tells you total items; the `values` array shows the actual data.

---

*End of CHAINING.md.*

# Spatial Workflow — Agent Brief

## Context

You are building a 4-script workflow for a mixed-use academic tower on the RMIT City Campus, Carlton, Melbourne. The building is 10–14 floors of curvilinear, organic timber architecture — no right angles, no conventional structural grid. Think dense aggregation of timber members mapped onto flowing, dome-like interior surfaces.

The workflow generates the building's spatial envelope from the ground up, then maps discrete elements onto the resulting surfaces. Each script feeds the next. All scripts run in Grasshopper via ScriptNode (a custom C# plugin that loads external Python 3 scripts and dynamically rebuilds I/O from header comments). Communication between scripts happens via chained Grasshopper wires and a shared `site_def` JSON schema.

**Environment:** Rhino 8 / Grasshopper, CPython 3 only. Windows 11 and macOS — use `os.path.join()` for all paths. No IronPython. No hardcoded values — everything parameterised.

**Material language references:** Kengo Kuma's stacked timber staircase (dense, pixelated timber planks aggregated along a curving form), and AI-generated renders showing flowing lamella timber envelopes with organic voids and oculi. The interior spaces read as carved-out domes within a continuous timber mass.

---

## The `site_def` JSON Schema - see how it works by referencing base_grid.py and subject_site.py

Every script reads a base JSON object that defines the site. For now this is minimal — it will eventually carry data from solar analysis, wind, programme allocation, etc. Design every script to accept and pass through this object, even if it only reads `bounds` today.


---

## Script 1: `levels`

**Purpose:** Generate a stack of level planes across the site.

**Inputs:**
- `site_def` (JSON)
- `num_levels` (int) — how many floors
- `default_floor_height` (float) — baseline floor-to-floor height
- `level_heights` (list of floats, optional) — per-level height overrides. If provided, length must match `num_levels`. If not provided, all levels use `default_floor_height`.

**Logic:**
1. Read `site_def.bounds` to get the site origin and dimensions.
2. Generate `num_levels` horizontal planes, stacked vertically from the origin.
3. Each level is a plane at its computed Z elevation. The plane's XY extent matches the site bounds (or can be left as an infinite plane — downstream scripts clip to the envelope).
4. If `level_heights` is provided, use per-level values. Otherwise, uniform spacing.

**Outputs:**
- `level_planes` — list of planes (one per level), ordered bottom to top
- `level_elevations` — list of Z values
- `site_def` — passed through unchanged

This is the simplest script. It is essentially a reference implementation showing the pattern: read `site_def`, do work, output geometry + pass `site_def` downstream.

---

## Script 2: `envelope`

**Purpose:** Define a curvilinear building footprint on each level, with vertical holes punched through, and loft everything into a continuous 3D envelope.

**Inputs:**
- `site_def` (JSON)
- `level_planes` from Script 1
- `level_elevations` from Script 1
- `num_holes` (int) — number of vertical penetrations through the building. This count is constant across all floors (for now).
- `envelope_inset` (float) — how far the envelope boundary is offset inward from the site boundary
- `floor_area_factor` (float, 0.0–1.0) — controls how much of the available site area the envelope fills on each floor. Acts as a simple scalar on footprint size. Eventually this will be driven by more dynamic data, but for now it is a single slider.
- `noise_seed` (int) — seed for shape variation
- `noise_amplitude` (float) — how far the envelope boundary deviates from a clean offset. Higher = more organic / blobby.
- `hole_positions` — list of XY seed positions for the holes (one per hole). These define where holes start on Level 1.
- `hole_drift_per_level` (float) — maximum XY drift of each hole centre per level. Holes shift position floor-to-floor, creating angled/organic void shafts when lofted.
- `hole_radius` (float) — base radius of each hole

**Logic:**
1. For each level:
   a. Start with the site boundary rectangle, inset by `envelope_inset`.
   b. Scale the inset shape by `floor_area_factor`.
   c. Convert the rectangle to a closed interpolated curve (NURBS) with control points perturbed by Perlin noise (seeded by `noise_seed` + level index). The result should be smooth and curvilinear — no sharp corners.
   d. For each hole: compute its centre position on this level (base position + cumulative drift). Create a smooth closed curve (circle or noise-perturbed circle) at `hole_radius`. Boolean-subtract the hole curves from the envelope curve to produce the floor plate boundary.
2. Output per-level data: the envelope boundary curve, the hole curves, and the trimmed floor plate surface.
3. Loft the envelope boundary curves across levels to produce the outer envelope surface. This is the building skin.
4. Loft each hole's curves across levels to produce void tubes. These are classified separately from the envelope — they are vertical shafts / atriums / light wells.
5. The envelope surface and void surfaces should curve into the ground at Level 1 (the base of the envelope tapers to meet the ground plane, not a hard clip). Similarly, the top level's envelope should curve inward to form a roof gesture. Both of these are controlled by tangent weighting on the loft — parameterise this.

**Outputs:**
- `envelope_surface` — the outer building skin (lofted surface or polysurface)
- `void_surfaces` — list of lofted void tubes (one per hole)
- `floor_plates` — list of trimmed planar surfaces (one per level), with holes cut
- `envelope_curves` — list of per-level envelope boundary curves (for downstream use)
- `hole_curves` — list of lists: per-level hole curves
- `level_elevations` — passed through
- `site_def` — passed through

**Key constraint:** All boundary curves must be smooth, closed, and interpolated (degree 3+ NURBS). No polylines, no sharp angles. The aesthetic is organic and flowing.

---

## Script 3: `spaces`

**Purpose:** Subdivide each floor plate into interior spaces, then extrude those spaces upward with dome-like ceilings that taper off before reaching the floor above. Account for holes — spaces near holes have their ceiling geometry curve away from the void.

**Inputs:**
- `site_def` (JSON)
- `floor_plates` from Script 2
- `envelope_curves` from Script 2
- `hole_curves` from Script 2
- `level_elevations` from Script 2
- `num_spaces_per_floor` (int) — how many spaces to generate on each floor
- `space_wall_offset` (float) — how far space boundaries are offset inward from the envelope curve. Space walls are interior versions of the envelope — they follow the same curvilinear language but are smaller/nested.
- `ceiling_min_offset` (float) — minimum gap between the top of a space dome and the floor plate above. The dome never touches the slab.
- `ceiling_max_offset` (float) — maximum gap (the dome can be lower than the min in some configurations — this sets the range).
- `hole_influence_radius` (float) — radius around each hole centre within which space ceilings are affected. Spaces within this radius have their dome taper downward / open up toward the hole, creating a smooth transition from enclosed room to open void.

**Logic:**
1. For each floor plate:
   a. Subdivide the plate into `num_spaces_per_floor` regions. Use a Voronoi-like partition or similar organic subdivision — the boundaries should be curvilinear, not straight. Seed points can be randomly distributed within the plate boundary (away from holes). Each region becomes a "space."
   b. For each space region:
      - Offset the region boundary inward by `space_wall_offset` to create the interior space boundary.
      - Generate a wall surface by lofting between the space boundary at floor level and a modified version of the same boundary at ceiling height.
      - The ceiling height varies: the space wall curves inward as it rises, forming a dome. The dome peaks at `(floor_above_elevation - ceiling_min_offset)` at the space's centre, and meets the wall at `(floor_above_elevation - ceiling_max_offset)` at the perimeter. The ceiling surface is a smooth cap (lofted or interpolated surface).
      - **Hole influence:** For spaces whose centroid is within `hole_influence_radius` of a hole centre, the dome ceiling tapers off / opens on the side facing the hole. The ceiling surface should smoothly blend from full dome height down toward the floor plate level near the hole edge, creating a gradual reveal of the void. Think of it as the dome being "eroded" by proximity to the hole.
2. Each space outputs: floor surface, wall surface(s), ceiling/dome surface.

**Outputs:**
- `space_surfaces` — list of lists: per-level, per-space surface collections (floor, walls, ceiling). Each surface should carry metadata identifying which level and which space index it belongs to.
- `space_boundaries` — per-level, per-space boundary curves (for downstream element mapping)
- `void_surfaces` — passed through from envelope
- `envelope_surface` — passed through
- `site_def` — passed through

**Key constraint:** The spaces should read architecturally as rooms carved out of a continuous mass. The dome ceilings are not literal hemispheres — they are the *absence* of material between the space wall and the slab above, shaped by the curvilinear wall geometry curving inward. The gap at the top (the offset) is where the timber element aggregation will later create a porous, latticed threshold between floors.

---

## Script 4: `element_mapper`

**Purpose:** Map discrete elements onto any of the surfaces produced by the upstream scripts. This is the tectonic layer — where curvilinear surfaces become articulated assemblies of individual components (ultimately timber members, but for now generic rectangular elements).

**Inputs:**
- `site_def` (JSON)
- `target_surfaces` — one or more surfaces to map elements onto. Could be a space dome, a wall surface, the envelope surface, or a void tube. The script should accept any surface.
- `element_base_size` (list: `[width, height, depth]`) — base dimensions of each element
- `mapping_density` (float) — controls how closely packed the elements are. Higher = denser field.
- `mapping_method` (enum: `"uv_grid"` | `"point_field"`) — how element positions are generated on the surface.
  - `uv_grid`: regular UV subdivision of the surface. Works well on relatively flat or single-curvature surfaces. May distort on highly curved domes.
  - `point_field`: scatter points on the surface using Poisson disk sampling or similar. Better for double-curved / non-Euclidean surfaces. More organic distribution.
- `attractor_points` (list of points, optional) — points in 3D space that influence element scale. Elements closer to an attractor are scaled up (or down, depending on `attractor_mode`).
- `attractor_mode` (enum: `"grow"` | `"shrink"`) — whether proximity to attractor increases or decreases element size.
- `attractor_falloff` (float) — radius of influence for each attractor.
- `scale_range` (list: `[min_scale, max_scale]`) — bounds on how much elements can be scaled by attractors. `1.0` = base size.
- `normal_offset` (float) — how far elements are offset from the surface along its normal. Useful for creating layered / lifted effects.

**Logic:**
1. For each target surface:
   a. Generate element positions using the chosen `mapping_method`.
      - `uv_grid`: divide the surface's UV domain into a grid at `mapping_density` resolution. Evaluate surface point and normal at each grid cell.
      - `point_field`: scatter points on the surface at a spacing derived from `mapping_density`. Evaluate surface point and normal at each sample.
   b. At each position:
      - Orient the element (a box or placeholder geometry) to align with the surface normal and local UV frame.
      - Apply `normal_offset` to push the element off the surface.
      - If `attractor_points` are provided: compute distance to nearest attractor, remap through `attractor_falloff`, and scale the element within `scale_range`.
   c. Collect all oriented, scaled element geometries.
2. Output the element field as a list of oriented geometry (boxes, or eventually timber member meshes).

**Outputs:**
- `elements` — list of element geometries (oriented bounding boxes or mesh instances), with metadata (position, scale, surface normal, parent surface ID)
- `element_count` (int) — total elements generated
- `site_def` — passed through

**Key constraint:** The mapping must handle double-curved surfaces gracefully. On a dome ceiling, a regular UV grid will bunch at the poles — `point_field` is the better default for these surfaces. The script should warn or auto-switch if UV distortion exceeds a threshold (provision for this, doesn't need to be implemented in v1).

---

## Data Flow Summary

```
site_def ──────────────────────────────────────────────────────►
              │
         levels ──► level_planes, level_elevations
              │              │
              │         envelope ──► envelope_surface, void_surfaces,
              │              │       floor_plates, envelope_curves,
              │              │       hole_curves
              │              │              │
              │              │         spaces ──► space_surfaces,
              │              │              │     space_boundaries
              │              │              │
              │              │              ├──► element_mapper (dome surfaces)
              │              │              │
              │              ├──────────────┼──► element_mapper (envelope surface)
              │              │              │
              │              └──────────────┴──► element_mapper (void surfaces)
```

`element_mapper` is called multiple times with different target surfaces. It is a general-purpose surface-to-element tool, not tied to any specific upstream geometry type.

---

## What To Build First

Build and test in this order:

1. **`levels`** — get the site_def pattern working, confirm planes output correctly.
2. **`envelope`** — this is the most complex script. Get the per-level curve generation working first (noise-perturbed offsets), then add holes, then lofting. Test with 3 levels before scaling to 10+.
3. **`spaces`** — start with the Voronoi subdivision and basic wall extrusion. Add the dome ceiling after the subdivision is stable. Add hole influence last.
4. **`element_mapper`** — start with `uv_grid` on a simple test surface (e.g., one dome). Add `point_field` and attractors after the basic mapping works.

---

## Future Extensions (DO NOT build now, but design with awareness)

These features are planned but not in scope for v1. The script architecture should not make any of these impossible to add later.

- **Envelope split/merge across levels:** The envelope could split into two or more separate masses at certain levels, then merge back together — creating dramatic forked forms, bridges, or cantilevers. This would mean `envelope_curves` at some levels contain multiple closed curves instead of one. Downstream scripts (spaces, element_mapper) would need to handle multi-body envelopes.

- **Variable hole count per level:** Currently `num_holes` is constant. Eventually, holes could appear or disappear at different levels — a hole might start at Level 3 and end at Level 8, or two holes might merge into one. The lofting logic would need to handle partial-height voids.

- **Openings and fenestration:** Mapping window, door, and opening geometries onto the envelope surface and onto space wall surfaces. This would likely be a post-process on `element_mapper` output — removing elements within an opening boundary and adding frame geometry.

- **Space classification and programme:** Tagging spaces with programme types (lab, studio, office, circulation) and having that classification influence downstream parameters (element density, ceiling height, etc.).

- **External data driving parameters:** Solar analysis, wind, pedestrian flow, etc. feeding into `site_def.metadata` and being interpreted by each script to modulate its behaviour (e.g., envelope inset varies by orientation for solar shading, element density increases on sun-exposed facades).

- **Inter-space connections:** Doors and circulation paths between adjacent spaces on the same level, and vertical connections (stairs, ramps) through holes between levels.

- **Element type variation:** Instead of uniform rectangular elements, a library of element types (planks, blocks, curved members) selected based on surface curvature, structural load, or aesthetic rules.

- **Joint system:** Algorithmically generated connection geometry where elements meet — lap joints, notches, interlocking profiles. This is the studio leader's highest-value outcome and should be kept in mind from the start, even though it won't be implemented in v1.

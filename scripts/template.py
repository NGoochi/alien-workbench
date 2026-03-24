#! python 3
# NODE_INPUTS: origin:Point3d, count:int, spacing:float
# NODE_OUTPUTS: points, log

# ─── IMPORTS ──────────────────────────────────────────────────────────
# Only import what you need. rg is almost always required.
import Rhino.Geometry as rg

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
# Every input may be None (unconnected) or an unexpected value.
# Always set safe defaults. This prevents crashes on first load.
if origin is None:
    origin = rg.Point3d(0, 0, 0)
if count is None or count < 1:
    count = 5
if spacing is None or spacing <= 0:
    spacing = 1.0

# ─── PROCESSING ───────────────────────────────────────────────────────
# Your main logic goes here.
# The input variables (origin, count, spacing) are already in scope,
# injected by Alien from the Grasshopper parameter values.

points = []
for i in range(count):
    pt = rg.Point3d(
        origin.X + i * spacing,
        origin.Y,
        origin.Z
    )
    points.append(pt)

# ─── OUTPUTS ──────────────────────────────────────────────────────────
# Set variables matching the names declared in NODE_OUTPUTS.
# Alien collects these from the script namespace after execution.
# 'points' is already set above — it will become a GH list of Point3d.

log = f"Generated {len(points)} points at spacing {spacing}"

# ─── NOTES ────────────────────────────────────────────────────────────
# - Do NOT wrap outputs in a function or class. They must be top-level variables.
# - Do NOT use print() for output — use a string output variable instead.
#   print() goes to Rhino's command history, not to GH.
#   (The agent can read command history via get_rhino_command_history MCP tool.)
# - Lists become GH lists. Nested lists become GH DataTrees.
# - If an output variable is never set, the output will be empty (no error).

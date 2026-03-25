#! python 3
# NODE_INPUTS: site_def:str, num_levels:int, num_basement_levels:int, default_floor_height:float, level_heights:str, include_buffer:bool
# NODE_OUTPUTS: level_planes, level_elevations, levels_brep, site_def, log
#
# levels_v2 — generate stacked level planes from site_def origin.

import json
import Rhino.Geometry as rg


def _as_float(value, fallback):
    try:
        return float(value)
    except Exception:
        return float(fallback)


def _parse_level_heights(raw):
    if raw is None:
        return None
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        data = json.loads(txt)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    vals = []
    for item in data:
        try:
            vals.append(float(item))
        except Exception:
            return None
    return vals


def _as_bool(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    txt = str(value).strip().lower()
    if txt in ("true", "1", "yes", "y", "on"):
        return True
    if txt in ("false", "0", "no", "n", "off"):
        return False
    return fallback


level_planes = []
level_elevations = []
levels_brep = []
log = ""

if site_def is None or not str(site_def).strip():
    site_def = "{}"
    log = "Error: site_def is empty. Wire subject_site.site_def."
else:
    try:
        sd = json.loads(str(site_def))
    except Exception:
        sd = None
        log = "Error: site_def is not valid JSON."

    if sd is not None:
        gd = sd.get("grid_def", {})
        origin = gd.get("origin", [0.0, 0.0, 0.0])
        x_axis = gd.get("x_axis", [1.0, 0.0, 0.0])
        y_axis = gd.get("y_axis", [0.0, 1.0, 0.0])
        site_extents = sd.get("site_extents", [20000.0, 20000.0, 20000.0, 20000.0, 0.0, 40000.0])
        buffer_dist = _as_float(sd.get("buffer_dist", 0.0), 0.0)

        ox = _as_float(origin[0] if len(origin) > 0 else 0.0, 0.0)
        oy = _as_float(origin[1] if len(origin) > 1 else 0.0, 0.0)
        oz = _as_float(origin[2] if len(origin) > 2 else 0.0, 0.0)
        xax = rg.Vector3d(
            _as_float(x_axis[0] if len(x_axis) > 0 else 1.0, 1.0),
            _as_float(x_axis[1] if len(x_axis) > 1 else 0.0, 0.0),
            0.0,
        )
        yax = rg.Vector3d(
            _as_float(y_axis[0] if len(y_axis) > 0 else 0.0, 0.0),
            _as_float(y_axis[1] if len(y_axis) > 1 else 1.0, 1.0),
            0.0,
        )
        if xax.Length < 1e-9:
            xax = rg.Vector3d(1.0, 0.0, 0.0)
        if yax.Length < 1e-9:
            yax = rg.Vector3d(0.0, 1.0, 0.0)
        xax.Unitize()
        yax.Unitize()

        neg_x = _as_float(site_extents[0] if len(site_extents) > 0 else 20000.0, 20000.0)
        pos_x = _as_float(site_extents[1] if len(site_extents) > 1 else 20000.0, 20000.0)
        neg_y = _as_float(site_extents[2] if len(site_extents) > 2 else 20000.0, 20000.0)
        pos_y = _as_float(site_extents[3] if len(site_extents) > 3 else 20000.0, 20000.0)
        include_buf = _as_bool(include_buffer, False)
        if include_buf and buffer_dist > 0.0:
            neg_x += buffer_dist
            pos_x += buffer_dist
            neg_y += buffer_dist
            pos_y += buffer_dist

        nl = int(round(_as_float(num_levels, 12)))
        if nl < 1:
            nl = 1
        nb = int(round(_as_float(num_basement_levels, 2)))
        if nb < 0:
            nb = 0

        dfh = _as_float(default_floor_height, 4000.0)
        if dfh <= 0:
            dfh = 4000.0

        total_levels = nl + nb
        custom_heights = _parse_level_heights(level_heights)
        using_overrides = (
            custom_heights is not None and len(custom_heights) == total_levels
        )

        heights = []
        if using_overrides:
            for h in custom_heights:
                heights.append(h if h > 0 else dfh)
        else:
            heights = [dfh] * total_levels

        # Ground level is index == nb. Basements are below ground.
        if nb > total_levels - 1:
            nb = total_levels - 1
        level_elevations = [oz] * total_levels
        for i in range(nb + 1, total_levels):
            level_elevations[i] = level_elevations[i - 1] + heights[i - 1]
        for i in range(nb - 1, -1, -1):
            level_elevations[i] = level_elevations[i + 1] - heights[i]

        for i in range(total_levels):
            z = level_elevations[i]
            pln = rg.Plane.WorldXY
            pln.Origin = rg.Point3d(ox, oy, z)
            level_planes.append(pln)

            corners = []
            for lx, ly in [(-neg_x, -neg_y), (pos_x, -neg_y), (pos_x, pos_y), (-neg_x, pos_y)]:
                corners.append(
                    rg.Point3d(
                        ox + xax.X * lx + yax.X * ly,
                        oy + xax.Y * lx + yax.Y * ly,
                        z,
                    )
                )
            corners.append(corners[0])
            poly = rg.PolylineCurve(corners)
            breps = rg.Brep.CreatePlanarBreps(poly, 0.01)
            levels_brep.append(breps[0] if breps and len(breps) > 0 else None)

        if custom_heights is not None and not using_overrides:
            mode = "invalid list length; fallback default_floor_height"
        else:
            mode = "per-level overrides" if using_overrides else "uniform defaults"

        bottom_elev = level_elevations[0] if level_elevations else oz
        top_elev = level_elevations[-1] if level_elevations else oz
        log_lines = [
            "levels_v2",
            "above-ground: {} | basement: {} | total: {} | mode: {}".format(nl, nb, total_levels, mode),
            "include_buffer: {} | levels_brep: {}".format(include_buf, len(levels_brep)),
            "origin: ({:.2f}, {:.2f}, {:.2f})".format(ox, oy, oz),
            "default_floor_height: {:.2f}".format(dfh),
            "lowest elevation: {:.2f} | ground: {:.2f} | top: {:.2f}".format(
                bottom_elev, oz, top_elev
            ),
        ]
        log = "\n".join(log_lines)

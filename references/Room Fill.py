#! python3
"""
Grow Fill  --  Multiple Room Types + Non-Orthogonal Faces
==========================================================

Fills one or more user-selected closed volumes by BFS-chaining room
block instances.  Supports multiple room block types and rooms with
non-orthogonal (angled) faces.

KEY BEHAVIOURS
--------------
Port extraction
    Ports are read from the actual planar Brep faces of each room block.
    Each planar face contributes one port at its face centroid, with the
    face's true outward normal and a consistent up vector derived from the
    face frame.  Non-brep geometry falls back to six axis-aligned bbox ports.

Perpendicular connections
    Two ports connect only when their normals are antiparallel
    (dot product <= -0.98, i.e. within ~11 degrees of exactly opposite).
    This guarantees rooms always meet face-to-face and connectors are
    always perpendicular to the joining faces.

Multiple room types
    Any number of room blocks can be added to the list.  At each open
    port the BFS tries every room type and every matching local port,
    accepting the first valid (inside-target, non-overlapping) placement.

Per-instance connector mapping
    No data is baked into block definitions.  After the BFS, one connector
    block instance is spawned at every world-space port of every placed room,
    oriented so:
        local block +X  ->  world outward face normal
        local block +Y  ->  world face up vector
"""

import Rhino
import scriptcontext as sc
import System
import Eto.Forms  as forms
import Eto.Drawing as drawing
from Rhino.UI import RhinoEtoApp, EtoExtensions


# ---------------------------------------------------------------------------
# PORT
# ---------------------------------------------------------------------------

class Port(object):
    """A connector port in local block space or world space."""
    __slots__ = ("point", "normal", "up")

    def __init__(self, point, normal, up):
        self.point  = point
        self.normal = normal
        self.up     = up

    def transformed(self, xform):
        pt = Rhino.Geometry.Point3d(self.point)
        pt.Transform(xform)
        nv = Rhino.Geometry.Vector3d(self.normal)
        nv.Transform(xform)
        nv.Unitize()
        uv = Rhino.Geometry.Vector3d(self.up)
        uv.Transform(xform)
        uv.Unitize()
        return Port(pt, nv, uv)

    def world_key(self, bucket):
        p = self.point
        b = max(bucket, 1e-9)
        return (int(round(p.X / b)),
                int(round(p.Y / b)),
                int(round(p.Z / b)))


# ---------------------------------------------------------------------------
# BLOCK HELPERS
# ---------------------------------------------------------------------------

def _get_idef(name):
    for idef in sc.doc.InstanceDefinitions:
        if idef and not idef.IsDeleted and idef.Name == name:
            return idef
    return None


def list_all_blocks():
    names = [idef.Name for idef in sc.doc.InstanceDefinitions
             if idef and not idef.IsDeleted]
    names.sort()
    return names


def add_block_instance(block_name, xform):
    idef = _get_idef(block_name)
    if idef is None:
        return System.Guid.Empty
    return sc.doc.Objects.AddInstanceObject(idef.Index, xform)


# ---------------------------------------------------------------------------
# PORT EXTRACTION
# ---------------------------------------------------------------------------

def _consistent_up(normal):
    """In-plane up vector consistent for any face normal."""
    world_z = Rhino.Geometry.Vector3d(0.0, 0.0, 1.0)
    world_x = Rhino.Geometry.Vector3d(1.0, 0.0, 0.0)
    ref = world_z if abs(normal * world_z) <= 0.9 else world_x
    dot = ref * normal
    up  = ref - normal * dot
    if up.Unitize():
        return up
    return world_x


def _brep_face_ports(brep):
    """One Port per planar Brep face, at face centroid with true outward normal."""
    ports = []
    for fi in range(brep.Faces.Count):
        face = brep.Faces[fi]
        if not face.IsPlanar(1e-3):
            continue
        amp = Rhino.Geometry.AreaMassProperties.Compute(face)
        if amp is None:
            continue
        centroid = amp.Centroid
        # ClosestPoint(Point3d) -> (bool, u, v)  — no out-param version in Python
        ok, u0, v0 = face.ClosestPoint(centroid)
        if not ok:
            u0 = (face.Domain(0).Min + face.Domain(0).Max) / 2.0
            v0 = (face.Domain(1).Min + face.Domain(1).Max) / 2.0
        # FrameAt(u, v) -> (bool, Plane): ZAxis = surface normal, avoids NormalAt overload
        ok2, frame = face.FrameAt(u0, v0)
        if not ok2:
            continue
        normal = Rhino.Geometry.Vector3d(frame.ZAxis)
        if face.OrientationIsReversed:
            normal = normal * -1.0
        if not normal.Unitize():
            continue
        ports.append(Port(centroid, normal, _consistent_up(normal)))
    return ports


def extract_ports(block_name):
    """
    Extract local-space ports from a block definition.
    Reads planar Brep faces first; falls back to six bbox face centres.
    """
    idef = _get_idef(block_name)
    if idef is None:
        return []

    ports     = []
    body_pts  = []

    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue
        geo = obj.Geometry
        if isinstance(geo, (Rhino.Geometry.Point,
                             Rhino.Geometry.InstanceReferenceGeometry)):
            continue
        gb = geo.GetBoundingBox(True)
        if gb.IsValid:
            body_pts.extend(gb.GetCorners())
        if isinstance(geo, Rhino.Geometry.Brep):
            ports.extend(_brep_face_ports(geo))

    if ports:
        return ports

    # Fallback: axis-aligned bbox face centres
    if not body_pts:
        return []
    bbox = Rhino.Geometry.BoundingBox(body_pts)
    if not bbox.IsValid:
        return []
    cx = (bbox.Min.X + bbox.Max.X) / 2.0
    cy = (bbox.Min.Y + bbox.Max.Y) / 2.0
    cz = (bbox.Min.Z + bbox.Max.Z) / 2.0
    P, V = Rhino.Geometry.Point3d, Rhino.Geometry.Vector3d
    return [
        Port(P(bbox.Max.X, cy, cz), V( 1,0,0), V(0,0,1)),
        Port(P(bbox.Min.X, cy, cz), V(-1,0,0), V(0,0,1)),
        Port(P(cx, bbox.Max.Y, cz), V( 0,1,0), V(0,0,1)),
        Port(P(cx, bbox.Min.Y, cz), V( 0,-1,0), V(0,0,1)),
        Port(P(cx, cy, bbox.Max.Z), V( 0,0,1), V(1,0,0)),
        Port(P(cx, cy, bbox.Min.Z), V( 0,0,-1), V(1,0,0)),
    ]


def body_bbox(block_name):
    idef = _get_idef(block_name)
    if idef is None:
        return None
    pts = []
    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue
        if isinstance(obj.Geometry, (Rhino.Geometry.Point,
                                     Rhino.Geometry.InstanceReferenceGeometry)):
            continue
        gb = obj.Geometry.GetBoundingBox(True)
        if gb.IsValid:
            pts.extend(gb.GetCorners())
    if not pts:
        return None
    bb = Rhino.Geometry.BoundingBox(pts)
    return bb if bb.IsValid else None


# ---------------------------------------------------------------------------
# ROOM CONFIG
# ---------------------------------------------------------------------------

class RoomConfig(object):
    __slots__ = ("name", "bbox", "local_ports", "bucket")

    def __init__(self, name):
        self.name        = name
        self.bbox        = body_bbox(name)
        self.local_ports = extract_ports(name)
        diag = self.bbox.Diagonal.Length if self.bbox else 1.0
        tol  = sc.doc.ModelAbsoluteTolerance
        self.bucket = max(diag * 0.001, tol * 10.0, 1e-3)

    def is_valid(self):
        return self.bbox is not None and len(self.local_ports) > 0


# ---------------------------------------------------------------------------
# PORT MATCHING  --  antiparallel = perpendicular connection
# ---------------------------------------------------------------------------

CONNECT_DOT_THRESHOLD = -0.98

def ports_can_connect(world_port, local_port):
    """True when normals are antiparallel (face-to-face perpendicular join)."""
    d = (world_port.normal.X * local_port.normal.X +
         world_port.normal.Y * local_port.normal.Y +
         world_port.normal.Z * local_port.normal.Z)
    return d <= CONNECT_DOT_THRESHOLD


# ---------------------------------------------------------------------------
# GEOMETRY HELPERS
# ---------------------------------------------------------------------------

def transform_bbox(bbox, xform):
    pts = []
    for c in bbox.GetCorners():
        p = Rhino.Geometry.Point3d(c)
        p.Transform(xform)
        pts.append(p)
    return Rhino.Geometry.BoundingBox(pts)


def bbox_centre_inside(bbox, targets, tol):
    c = bbox.Center
    for kind, geom in targets:
        if kind == "brep" and geom.IsPointInside(c, tol, True):
            return True
        if kind == "mesh" and geom.IsPointInside(c, tol, False):
            return True
    return False


def bboxes_overlap(a, b, tol):
    return (a.Min.X < b.Max.X - tol and a.Max.X > b.Min.X + tol and
            a.Min.Y < b.Max.Y - tol and a.Max.Y > b.Min.Y + tol and
            a.Min.Z < b.Max.Z - tol and a.Max.Z > b.Min.Z + tol)


def pick_target_geometries():
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt("Select closed polysurfaces or meshes to fill")
    go.GeometryFilter = (Rhino.DocObjects.ObjectType.PolysrfFilter |
                         Rhino.DocObjects.ObjectType.Mesh)
    go.SubObjectSelect = False
    go.EnablePreSelect(True, True)
    go.GetMultiple(1, 0)
    if go.CommandResult() != Rhino.Commands.Result.Success:
        return []
    targets = []
    for i in range(go.ObjectCount):
        objref = go.Object(i)
        brep = objref.Brep()
        if brep:
            targets.append(("brep", brep))
            continue
        mesh = objref.Mesh()
        if mesh:
            targets.append(("mesh", mesh))
    return targets


# ---------------------------------------------------------------------------
# ATTACH TRANSFORM
# ---------------------------------------------------------------------------

def attach_xform(open_world_port, local_port):
    sx = Rhino.Geometry.Vector3d(local_port.normal)
    sy = Rhino.Geometry.Vector3d(local_port.up)
    if not sx.Unitize() or not sy.Unitize():
        return None
    src = Rhino.Geometry.Plane(local_port.point, sx, sy)

    tx = Rhino.Geometry.Vector3d(open_world_port.normal) * -1.0
    ty = Rhino.Geometry.Vector3d(open_world_port.up)
    if not tx.Unitize() or not ty.Unitize():
        return None
    tgt = Rhino.Geometry.Plane(open_world_port.point, tx, ty)

    return Rhino.Geometry.Transform.PlaneToPlane(src, tgt)


# ---------------------------------------------------------------------------
# SEED PLACEMENT
# ---------------------------------------------------------------------------

def _find_seed_for_target(room_cfg, kind, geom, tol):
    bbox   = room_cfg.bbox
    single = [(kind, geom)]
    tgt    = geom.GetBoundingBox(True)
    if not tgt.IsValid:
        return None

    move = tgt.Center - bbox.Center
    xf   = Rhino.Geometry.Transform.Translation(move)
    if bbox_centre_inside(transform_bbox(bbox, xf), single, tol):
        return xf

    dx = bbox.Diagonal.X or 1.0
    dy = bbox.Diagonal.Y or 1.0
    dz = bbox.Diagonal.Z or 1.0
    x  = tgt.Min.X
    while x <= tgt.Max.X:
        y = tgt.Min.Y
        while y <= tgt.Max.Y:
            z = tgt.Min.Z
            while z <= tgt.Max.Z:
                move = Rhino.Geometry.Point3d(x, y, z) - bbox.Min
                xf   = Rhino.Geometry.Transform.Translation(move)
                if bbox_centre_inside(transform_bbox(bbox, xf), single, tol):
                    return xf
                z += dz
            y += dy
        x += dx
    return None


def find_all_seeds(room_cfgs, targets, tol):
    """One seed per target geometry, using the first room type that fits."""
    seeds = []
    for kind, geom in targets:
        for cfg in room_cfgs:
            xf = _find_seed_for_target(cfg, kind, geom, tol)
            if xf is not None:
                seeds.append((xf, cfg))
                break
    return seeds


# ---------------------------------------------------------------------------
# GROW FILL
# ---------------------------------------------------------------------------

def grow_fill(room_cfgs, cb_name, targets, max_rooms=200):
    """
    BFS fill with multiple room types.
    Ports match when normals are antiparallel (perpendicular connection rule).
    Returns (ok, message, results) where results = [(xform, RoomConfig, [Port])].
    """
    if not room_cfgs:
        return False, "No room types added.", []
    if not targets:
        return False, "No target geometry selected.", []

    valid_cfgs = [c for c in room_cfgs if c.is_valid()]
    if not valid_cfgs:
        return False, "No valid room blocks.", []

    tol = sc.doc.ModelAbsoluteTolerance
    min_diag = min(c.bbox.Diagonal.Length for c in valid_cfgs)
    bucket   = max(min_diag * 0.001, tol * 10.0, 1e-3)

    seeds = find_all_seeds(valid_cfgs, targets, tol)
    if not seeds:
        return False, "No seed position found inside any target.", []

    results    = []
    placed_bbs = []
    used_keys  = set()
    n_out = [0]
    n_ovl = [0]

    def _key(wp):
        return wp.world_key(bucket)

    def try_place(cfg, xform):
        world_bb = transform_bbox(cfg.bbox, xform)
        if not bbox_centre_inside(world_bb, targets, tol):
            n_out[0] += 1
            return None
        for pb in placed_bbs:
            if bboxes_overlap(world_bb, pb, tol):
                n_ovl[0] += 1
                return None
        placed_bbs.append(world_bb)
        world_ports = [lp.transformed(xform) for lp in cfg.local_ports]
        open_ports  = [wp for wp in world_ports if _key(wp) not in used_keys]
        results.append((xform, cfg, world_ports))
        return open_ports

    # Seeds
    queue = []
    for seed_xf, cfg in seeds:
        open_ports = try_place(cfg, seed_xf)
        if open_ports is not None:
            queue.extend(open_ports)

    if not results:
        return False, "No seed rooms fit inside the target volumes.", []

    max_iter = max_rooms * max(len(c.local_ports) for c in valid_cfgs) * 8

    # BFS
    for _ in range(max_iter):
        if not queue or len(results) >= max_rooms:
            break

        open_wp = queue.pop(0)
        k = _key(open_wp)
        if k in used_keys:
            continue
        used_keys.add(k)

        placed = False
        for cfg in valid_cfgs:
            if placed:
                break
            for lp in cfg.local_ports:
                if not ports_can_connect(open_wp, lp):
                    continue
                xf = attach_xform(open_wp, lp)
                if xf is None:
                    continue
                new_open = try_place(cfg, xf)
                if new_open is None:
                    continue
                queue.extend(new_open)
                placed = True
                break

    msg = "Placed {} room(s)  ({} outside, {} overlap skipped).".format(
        len(results), n_out[0], n_ovl[0])
    return True, msg, results


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------

def insert_results(cb_name, results):
    """Insert rooms + one connector block per port per room."""
    room_guids = []
    cb_guids   = []
    cb_idef    = _get_idef(cb_name)

    for xf, cfg, world_ports in results:
        gid = add_block_instance(cfg.name, xf)
        if gid != System.Guid.Empty:
            room_guids.append(gid)
        if cb_idef is None:
            continue
        for wp in world_ports:
            face_plane = Rhino.Geometry.Plane(wp.point, wp.normal, wp.up)
            cb_xf = Rhino.Geometry.Transform.PlaneToPlane(
                        Rhino.Geometry.Plane.WorldXY, face_plane)
            cid = add_block_instance(cb_name, cb_xf)
            if cid != System.Guid.Empty:
                cb_guids.append(cid)

    sc.doc.Views.Redraw()
    return room_guids, cb_guids


# ---------------------------------------------------------------------------
# UI HELPERS
# ---------------------------------------------------------------------------

def _label(text):
    w = forms.Label()
    w.Text = text
    return w

def _button(text):
    w = forms.Button()
    w.Text = text
    return w

def _numeric(value, decimals, increment):
    w = forms.NumericStepper()
    w.DecimalPlaces = decimals
    w.Increment     = increment
    w.Value         = value
    return w

def _sep():
    w = forms.Label()
    w.Text = ""
    return w


# ---------------------------------------------------------------------------
# DIALOG
# ---------------------------------------------------------------------------

class GrowFillDialog(forms.Dialog):

    def __init__(self):
        super(GrowFillDialog, self).__init__()

        self.Title     = "Grow Fill"
        self.Padding   = drawing.Padding(0)
        self.Resizable = True
        self.Width     = 400
        self.Height    = 440

        EtoExtensions.UseRhinoStyle(self)

        blocks = list_all_blocks()

        # Connector block
        self.dd_cb = forms.DropDown()
        self.dd_cb.DataStore = blocks

        # Room type selector + list
        self.dd_add_room = forms.DropDown()
        self.dd_add_room.DataStore = blocks

        self.btn_add_room = _button("Add Room Type")
        self.btn_add_room.Click += self.on_add_room

        self.lst_rooms = forms.ListBox()
        self.lst_rooms.Height = 110

        self.btn_remove_room = _button("Remove Selected")
        self.btn_remove_room.Click += self.on_remove_room

        # Fill controls
        self.num_max  = _numeric(value=200, decimals=0, increment=50)
        self.btn_pick = _button("Pick Fill Geometry")
        self.btn_run  = _button("Grow Fill")
        self.btn_ref  = _button("Refresh Blocks")
        self.btn_clr  = _button("Clear Last")
        self.lbl_tgt  = _label("No target selected")
        self.lbl_stat = _label("Ready")

        self.btn_pick.Click += self.on_pick
        self.btn_run.Click  += self.on_run
        self.btn_ref.Click  += self.on_refresh
        self.btn_clr.Click  += self.on_clear

        # State
        self.targets     = []
        self._last_rooms = []
        self._last_cbs   = []

        if blocks:
            self.dd_cb.SelectedIndex       = 0
            self.dd_add_room.SelectedIndex = 0

        # Layout
        layout = forms.DynamicLayout()
        layout.Padding = drawing.Padding(12)
        layout.Spacing = drawing.Size(6, 8)

        layout.AddRow(_label("Connector block:"), self.dd_cb)
        layout.AddRow(_sep())
        layout.AddRow(_label("Room types:"))

        add_row = forms.DynamicLayout()
        add_row.Spacing = drawing.Size(6, 0)
        add_row.AddRow(self.dd_add_room, self.btn_add_room)
        layout.AddRow(add_row)

        layout.AddRow(self.lst_rooms)
        layout.AddRow(self.btn_remove_room)
        layout.AddRow(_sep())
        layout.AddRow(_label("Max rooms:"), self.num_max)
        layout.AddRow(self.btn_pick,         self.lbl_tgt)
        layout.AddRow(self.btn_ref,          self.btn_clr, self.btn_run)
        layout.AddRow(_label("Status:"),     self.lbl_stat)

        scroll = forms.Scrollable()
        scroll.Content             = layout
        scroll.ExpandContentWidth  = True
        scroll.ExpandContentHeight = False
        self.Content = scroll

    def _room_names(self):
        items = self.lst_rooms.DataStore
        return list(items) if items else []

    def _set_room_names(self, names):
        self.lst_rooms.DataStore = names

    def on_add_room(self, sender, e):
        name = self.dd_add_room.SelectedValue
        if not name:
            return
        names = self._room_names()
        if name in names:
            self.lbl_stat.Text = "'{}' already in list.".format(name)
            return
        names.append(name)
        self._set_room_names(names)
        cfg = RoomConfig(name)
        self.lbl_stat.Text = "Added '{}' -- {} port(s).".format(
            name, len(cfg.local_ports))

    def on_remove_room(self, sender, e):
        idx = self.lst_rooms.SelectedIndex
        if idx < 0:
            self.lbl_stat.Text = "Select a room type to remove."
            return
        names = self._room_names()
        if 0 <= idx < len(names):
            removed = names.pop(idx)
            self._set_room_names(names)
            self.lbl_stat.Text = "Removed '{}'.".format(removed)

    def on_refresh(self, sender, e):
        blocks = list_all_blocks()
        self.dd_cb.DataStore       = blocks
        self.dd_add_room.DataStore = blocks
        if blocks:
            self.dd_cb.SelectedIndex       = 0
            self.dd_add_room.SelectedIndex = 0
        self.lbl_stat.Text = "Found {} block(s).".format(len(blocks))

    def on_pick(self, sender, e):
        def _pick(s, evt):
            targets = pick_target_geometries()
            if not targets:
                self.lbl_stat.Text = "No valid geometry selected."
                return
            self.targets = targets
            n_b = sum(1 for k, _ in targets if k == "brep")
            n_m = sum(1 for k, _ in targets if k == "mesh")
            parts = []
            if n_b: parts.append("{} brep".format(n_b))
            if n_m: parts.append("{} mesh".format(n_m))
            self.lbl_tgt.Text  = ", ".join(parts)
            self.lbl_stat.Text = "{} target(s) selected.".format(len(targets))
        EtoExtensions.PushPickButton(self, _pick)

    def on_clear(self, sender, e):
        removed = 0
        for gid in self._last_rooms + self._last_cbs:
            if sc.doc.Objects.Delete(gid, True):
                removed += 1
        self._last_rooms = []
        self._last_cbs   = []
        sc.doc.Views.Redraw()
        self.lbl_stat.Text = "Cleared {} object(s).".format(removed)

    def on_run(self, sender, e):
        cb_name    = self.dd_cb.SelectedValue
        room_names = self._room_names()

        if not cb_name:
            self.lbl_stat.Text = "Choose a connector block."; return
        if not room_names:
            self.lbl_stat.Text = "Add at least one room type."; return
        if not self.targets:
            self.lbl_stat.Text = "Pick fill geometry first."; return

        room_cfgs = [RoomConfig(n) for n in room_names]
        bad = [c.name for c in room_cfgs if not c.is_valid()]
        if bad:
            self.lbl_stat.Text = "Cannot read geometry: {}".format(
                ", ".join(bad))
            return

        max_rooms = int(self.num_max.Value or 200)
        self.lbl_stat.Text = "Growing -- please wait..."

        ok, msg, res = grow_fill(room_cfgs, cb_name, self.targets, max_rooms)
        if not ok:
            self.lbl_stat.Text = msg
            return

        rooms, cbs = insert_results(cb_name, res)
        self._last_rooms = rooms
        self._last_cbs   = cbs
        self.lbl_stat.Text = msg


# ---------------------------------------------------------------------------
# ENTRY
# ---------------------------------------------------------------------------

def main():
    dlg    = GrowFillDialog()
    parent = RhinoEtoApp.MainWindowForDocument(sc.doc)
    EtoExtensions.ShowSemiModal(dlg, sc.doc, parent)

if __name__ == "__main__":
    main()
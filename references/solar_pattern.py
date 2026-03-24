"""
Solar-Driven Reaction-Diffusion Pattern Generator
===================================================
Gray-Scott RD model driven by astronomically accurate solar position
for Melbourne, Australia (-37.81, 144.96).

- Eto UI with live preview and Bake workflow
- Solar exposure mapped to feed/kill RD parameters
- Vertex coloring by solar exposure (heat-map)
- Face culling by chemical concentration B
"""

import rhinoscriptsyntax as rs
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import System
import System.Drawing as sd
import math
import random

# Eto imports
import Eto.Forms as forms
import Eto.Drawing as drawing

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
MELBOURNE_LAT = -37.81
MELBOURNE_LON = 144.96
PREVIEW_LAYER = "Solar_RD_Preview"
BAKE_LAYER = "Solar_RD_Pattern"

# Sticky key for tracking preview object GUID across runs
STICKY_KEY = "solar_rd_preview_guid"


# ===========================================================================
# 1. ASTRONOMICAL SOLAR ENGINE - Melbourne, Australia
# ===========================================================================

def is_melbourne_dst(month, day=15):
    """
    Check if Melbourne is in AEDT (daylight saving time).
    AEDT: First Sunday of October -> First Sunday of April.
    Approximate boundaries used for simplicity.
    """
    if month >= 10 or month <= 3:
        return True
    if month == 4 and day < 6:
        return True
    return False


def melbourne_local_to_utc(month, local_hour):
    """
    Convert Melbourne local time to UTC.
    AEST = UTC+10, AEDT = UTC+11.
    Returns (utc_hour, day_offset).
    """
    offset = 11.0 if is_melbourne_dst(month) else 10.0
    utc_hour = local_hour - offset
    day_offset = 0
    if utc_hour < 0:
        utc_hour += 24.0
        day_offset = -1
    elif utc_hour >= 24.0:
        utc_hour -= 24.0
        day_offset = 1
    return utc_hour, day_offset


def day_of_year(month, day=15):
    """Return approximate day-of-year for the given day of each month."""
    days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    doy = day
    for m in range(1, month):
        doy += days_in_month[m]
    return doy


def solar_declination(doy):
    """Solar declination angle in radians (Spencer, 1971 approximation)."""
    B = (2.0 * math.pi / 365.0) * (doy - 1)
    decl = (0.006918
            - 0.399912 * math.cos(B)
            + 0.070257 * math.sin(B)
            - 0.006758 * math.cos(2 * B)
            + 0.000907 * math.sin(2 * B)
            - 0.002697 * math.cos(3 * B)
            + 0.00148 * math.sin(3 * B))
    return decl


def equation_of_time(doy):
    """Equation of time in minutes (Spencer, 1971)."""
    B = (2.0 * math.pi / 365.0) * (doy - 1)
    eot = 229.18 * (0.000075
                     + 0.001868 * math.cos(B)
                     - 0.032077 * math.sin(B)
                     - 0.014615 * math.cos(2 * B)
                     - 0.04089 * math.sin(2 * B))
    return eot


def compute_solar_position(month, local_hour):
    """
    Compute solar altitude and azimuth for Melbourne.
    Returns (altitude_deg, azimuth_deg).
    """
    doy = day_of_year(month)

    # Solar declination
    decl = solar_declination(doy)

    # Equation of time
    eot = equation_of_time(doy)

    # Time correction factor (minutes)
    # LSTM (Local Standard Time Meridian) for AEST = 150 deg
    lstm = 150.0
    tc = 4.0 * (MELBOURNE_LON - lstm) + eot

    # Local solar time - use standard time (not DST) for solar calculation
    lst = local_hour + tc / 60.0
    if is_melbourne_dst(month):
        lst -= 1.0

    # Hour angle (degrees) - 15 deg per hour, solar noon = 0
    hour_angle = 15.0 * (lst - 12.0)
    ha_rad = math.radians(hour_angle)

    lat_rad = math.radians(MELBOURNE_LAT)

    # Solar altitude (elevation)
    sin_alt = (math.sin(lat_rad) * math.sin(decl) +
               math.cos(lat_rad) * math.cos(decl) * math.cos(ha_rad))
    sin_alt = max(-1.0, min(1.0, sin_alt))
    altitude_rad = math.asin(sin_alt)
    altitude_deg = math.degrees(altitude_rad)

    # Solar azimuth
    cos_alt = math.cos(altitude_rad)
    if abs(cos_alt) < 1e-10:
        azimuth_deg = 180.0
    else:
        cos_az = ((math.sin(decl) - math.sin(lat_rad) * sin_alt) /
                  (math.cos(lat_rad) * cos_alt))
        cos_az = max(-1.0, min(1.0, cos_az))
        azimuth_rad = math.acos(cos_az)
        azimuth_deg = math.degrees(azimuth_rad)
        if hour_angle > 0:
            azimuth_deg = 360.0 - azimuth_deg

    return altitude_deg, azimuth_deg


def sun_vector_from_angles(altitude_deg, azimuth_deg):
    """
    Convert solar altitude and azimuth to a 3D unit vector pointing TO the sun.
    Azimuth: 0=North(+Y), 90=East(+X), 180=South(-Y), 270=West(-X).
    """
    alt_rad = math.radians(altitude_deg)
    az_rad = math.radians(azimuth_deg)

    x = math.cos(alt_rad) * math.sin(az_rad)
    y = math.cos(alt_rad) * math.cos(az_rad)
    z = math.sin(alt_rad)

    return rg.Vector3d(x, y, z)


# ===========================================================================
# 2. MESH CREATION
# ===========================================================================

def create_mesh_plane(res_x, res_y, size_x, size_y):
    """
    Create a subdivided quad mesh plane on XY, centered at origin.
    Returns (mesh, vertex_grid) where vertex_grid[j][i] = vertex index.
    """
    mesh = rg.Mesh()
    nx = res_x + 1
    ny = res_y + 1

    dx = size_x / float(res_x)
    dy = size_y / float(res_y)

    x0 = -size_x / 2.0
    y0 = -size_y / 2.0

    vertex_grid = []
    for j in range(ny):
        row = []
        for i in range(nx):
            px = x0 + i * dx
            py = y0 + j * dy
            idx = mesh.Vertices.Add(px, py, 0.0)
            row.append(idx)
        vertex_grid.append(row)

    for j in range(res_y):
        for i in range(res_x):
            a = vertex_grid[j][i]
            b = vertex_grid[j][i + 1]
            c = vertex_grid[j + 1][i + 1]
            d = vertex_grid[j + 1][i]
            mesh.Faces.AddFace(a, b, c, d)

    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh, vertex_grid


# ===========================================================================
# 3. SOLAR EXPOSURE MAPPING
# ===========================================================================

def compute_exposure_map(mesh, sun_vec, res_x, res_y):
    """
    Compute per-vertex solar exposure (0.0 to 1.0).
    Dot product of vertex normal with sun direction, plus directional gradient.
    """
    n_verts = mesh.Vertices.Count
    exposure = [0.0] * n_verts

    alt_factor = max(0.0, sun_vec.Z)

    if alt_factor < 0.001:
        return exposure

    # Horizontal sun component for directional gradient
    sun_horiz = rg.Vector3d(sun_vec.X, sun_vec.Y, 0)
    horiz_len = sun_horiz.Length

    # Pre-compute bounding box diagonal once (not per-vertex)
    bbox = mesh.GetBoundingBox(True)
    diag = bbox.Diagonal.Length
    half_diag = diag * 0.5 if diag > 0 else 1.0

    for i in range(n_verts):
        pt = mesh.Vertices[i]
        normal = mesh.Normals[i]

        # Base exposure: dot product with sun vector
        dot = (normal.X * sun_vec.X + normal.Y * sun_vec.Y + normal.Z * sun_vec.Z)
        base_exposure = max(0.0, dot)

        # Add directional gradient for spatial variation
        if horiz_len > 0.01 and diag > 0:
            pos_dot = (pt.X * sun_horiz.X + pt.Y * sun_horiz.Y) / horiz_len
            gradient = 0.5 + 0.5 * (pos_dot / half_diag)
            gradient = max(0.0, min(1.0, gradient))
        else:
            gradient = 0.5

        exp = base_exposure * (0.7 + 0.3 * gradient)
        exposure[i] = max(0.0, min(1.0, exp))

    return exposure


# ===========================================================================
# 4. GRAY-SCOTT REACTION-DIFFUSION ENGINE
# ===========================================================================

def gray_scott_simulate(res_x, res_y, exposure, steps, f_min, f_max, k_min, k_max,
                        Du, Dv, dt, seed_count, progress_callback=None):
    """
    Gray-Scott RD on a grid. Exposure maps to spatially-varying f and k.
    Optimized inner loop using direct array indexing.
    """
    nx = res_x + 1
    ny = res_y + 1
    n = nx * ny

    # Initialize concentrations
    A = [1.0] * n
    B = [0.0] * n

    # Seed chemical B at random small locations
    random.seed(42)
    for _ in range(seed_count):
        ci = random.randint(2, nx - 3)
        cj = random.randint(2, ny - 3)
        for di in range(-1, 2):
            for dj in range(-1, 2):
                si = ci + di
                sj = cj + dj
                if 0 <= si < nx and 0 <= sj < ny:
                    idx = sj * nx + si
                    B[idx] = 1.0
                    A[idx] = 0.0

    # Per-vertex f and k based on exposure
    exp_len = len(exposure)
    f_range = f_max - f_min
    k_range = k_max - k_min
    f_arr = [0.0] * n
    k_arr = [0.0] * n
    for i in range(n):
        e = exposure[i] if i < exp_len else 0.5
        f_arr[i] = f_min + f_range * e
        k_arr[i] = k_min + k_range * e

    # Temp arrays
    newA = [0.0] * n
    newB = [0.0] * n

    report_interval = max(1, steps // 20)

    for step in range(steps):
        if progress_callback and step % report_interval == 0:
            progress_callback(step, steps)

        # Cache references for inner loop speed
        a_arr = A
        b_arr = B
        na = newA
        nb = newB

        for j in range(ny):
            # Pre-compute clamped row offsets
            j_nx = j * nx
            jp_nx = min(j + 1, ny - 1) * nx
            jm_nx = max(j - 1, 0) * nx

            for i in range(nx):
                idx = j_nx + i

                a = a_arr[idx]
                b = b_arr[idx]

                # Clamped neighbor indices
                ip = min(i + 1, nx - 1)
                im = max(i - 1, 0)

                lap_a = (a_arr[j_nx + ip] + a_arr[j_nx + im] +
                         a_arr[jp_nx + i] + a_arr[jm_nx + i] - 4.0 * a)
                lap_b = (b_arr[j_nx + ip] + b_arr[j_nx + im] +
                         b_arr[jp_nx + i] + b_arr[jm_nx + i] - 4.0 * b)

                abb = a * b * b
                f = f_arr[idx]
                k = k_arr[idx]

                new_a = a + dt * (Du * lap_a - abb + f * (1.0 - a))
                new_b = b + dt * (Dv * lap_b + abb - (k + f) * b)

                # Clamp to [0, 1]
                if new_a < 0.0:
                    new_a = 0.0
                elif new_a > 1.0:
                    new_a = 1.0
                if new_b < 0.0:
                    new_b = 0.0
                elif new_b > 1.0:
                    new_b = 1.0

                na[idx] = new_a
                nb[idx] = new_b

        # Swap arrays
        A, newA = newA, A
        B, newB = newB, B

    if progress_callback:
        progress_callback(steps, steps)

    return A, B


# ===========================================================================
# 5. MESH CULLING AND COLORING
# ===========================================================================

def exposure_to_color(exposure_val):
    """
    Map exposure 0.0-1.0 to a heat-map color.
    0.0 = Deep Blue -> 0.5 = Green -> 1.0 = Red
    """
    t = max(0.0, min(1.0, exposure_val))

    if t < 0.25:
        s = t / 0.25
        r = 0
        g = int(s * 200)
        b = int(255 - s * 55)
    elif t < 0.5:
        s = (t - 0.25) / 0.25
        r = 0
        g = int(200 + s * 55)
        b = int(200 - s * 200)
    elif t < 0.75:
        s = (t - 0.5) / 0.25
        r = int(s * 255)
        g = int(255 - s * 80)
        b = 0
    else:
        s = (t - 0.75) / 0.25
        r = 255
        g = int(175 - s * 175)
        b = 0

    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return sd.Color.FromArgb(255, r, g, b)


def build_culled_mesh(base_mesh, res_x, res_y, B_values, exposure, threshold):
    """
    Build a new mesh keeping only faces where average B > threshold.
    Vertices colored by solar exposure heat-map.
    """
    result = rg.Mesh()
    old_to_new = {}
    b_len = len(B_values)
    e_len = len(exposure)

    def get_or_add_vertex(old_idx):
        if old_idx in old_to_new:
            return old_to_new[old_idx]
        pt = base_mesh.Vertices[old_idx]
        ni = result.Vertices.Add(pt.X, pt.Y, pt.Z)
        e = exposure[old_idx] if old_idx < e_len else 0.5
        col = exposure_to_color(e)
        result.VertexColors.Add(col.R, col.G, col.B)
        old_to_new[old_idx] = ni
        return ni

    kept = 0
    total = base_mesh.Faces.Count

    for fi in range(total):
        face = base_mesh.Faces[fi]
        if face.IsQuad:
            vidxs = [face.A, face.B, face.C, face.D]
        else:
            vidxs = [face.A, face.B, face.C]

        # Average B concentration for this face
        avg_b = 0.0
        for vi in vidxs:
            if vi < b_len:
                avg_b += B_values[vi]
        avg_b /= len(vidxs)

        if avg_b > threshold:
            new_idxs = [get_or_add_vertex(vi) for vi in vidxs]
            if len(new_idxs) == 4:
                result.Faces.AddFace(new_idxs[0], new_idxs[1],
                                     new_idxs[2], new_idxs[3])
            else:
                result.Faces.AddFace(new_idxs[0], new_idxs[1], new_idxs[2])
            kept += 1

    result.Normals.ComputeNormals()
    result.Compact()
    return result, kept, total


# ===========================================================================
# 6. RHINO DOCUMENT HELPERS
# ===========================================================================

def ensure_layer(name, color=None):
    """Create layer if it doesn't exist, return the layer index."""
    if not rs.IsLayer(name):
        rs.AddLayer(name, color if color else [128, 128, 128])
    # Find layer index by iterating the layer table (works on all Rhino versions)
    layer_table = sc.doc.Layers
    layer_idx = -1
    for i in range(layer_table.Count):
        if layer_table[i].Name == name and not layer_table[i].IsDeleted:
            layer_idx = i
            break
    return layer_idx


def delete_preview():
    """Delete any existing preview object tracked in sc.sticky."""
    if STICKY_KEY in sc.sticky:
        guid = sc.sticky[STICKY_KEY]
        if guid:
            # Convert to System.Guid if stored as string
            if isinstance(guid, str):
                try:
                    guid = System.Guid(guid)
                except:
                    sc.sticky[STICKY_KEY] = None
                    return
            obj = sc.doc.Objects.FindId(guid)
            if obj is not None:
                sc.doc.Objects.Delete(guid, True)
        sc.sticky[STICKY_KEY] = None


def add_preview_mesh(mesh):
    """Add mesh to preview layer and track its GUID."""
    delete_preview()
    layer_idx = ensure_layer(PREVIEW_LAYER, [100, 100, 100])

    attr = Rhino.DocObjects.ObjectAttributes()
    attr.LayerIndex = layer_idx

    guid = sc.doc.Objects.AddMesh(mesh, attr)
    if guid == System.Guid.Empty:
        print("ERROR: Failed to add preview mesh to document.")
        return None

    sc.sticky[STICKY_KEY] = guid
    sc.doc.Views.Redraw()
    return guid


def bake_mesh(mesh):
    """Permanently add mesh to the bake layer."""
    layer_idx = ensure_layer(BAKE_LAYER, [220, 80, 40])

    attr = Rhino.DocObjects.ObjectAttributes()
    attr.LayerIndex = layer_idx

    guid = sc.doc.Objects.AddMesh(mesh, attr)
    if guid == System.Guid.Empty:
        print("ERROR: Failed to bake mesh to document.")
        return None

    sc.doc.Views.Redraw()
    return guid


# ===========================================================================
# 7. ETO UI DIALOG
# ===========================================================================

class SolarRDDialog(forms.Dialog[bool]):
    """Main dialog for Solar RD pattern generation."""

    def __init__(self):
        self.Title = "Solar Reaction-Diffusion Pattern Generator"
        self.Padding = drawing.Padding(12)
        self.Resizable = True
        self.MinimumSize = drawing.Size(520, 720)

        self.last_mesh = None
        self.last_info = ""
        self._is_running = False

        self._build_ui()

    def _build_ui(self):
        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(6, 6)
        layout.DefaultSpacing = drawing.Size(6, 4)

        # === Solar Parameters ===
        layout.BeginGroup("Solar Position - Melbourne, Australia")

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Month:"))
        self.month_slider = forms.Slider()
        self.month_slider.MinValue = 1
        self.month_slider.MaxValue = 12
        self.month_slider.Value = 1
        self.month_label = forms.Label(Text="January")
        self.month_slider.ValueChanged += self._on_month_changed
        layout.Add(self.month_slider, True)
        layout.Add(self.month_label)
        layout.EndHorizontal()

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Time of Day:"))
        self.hour_slider = forms.Slider()
        self.hour_slider.MinValue = 0
        self.hour_slider.MaxValue = 48
        self.hour_slider.Value = 24
        self.hour_label = forms.Label(Text="12:00 (AEDT)")
        self.hour_slider.ValueChanged += self._on_hour_changed
        layout.Add(self.hour_slider, True)
        layout.Add(self.hour_label)
        layout.EndHorizontal()

        self.solar_info = forms.Label(Text="Alt: ---  Az: ---")
        layout.Add(self.solar_info)

        layout.EndGroup()

        # === Mesh Parameters ===
        layout.BeginGroup("Mesh Canvas")

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Grid Resolution:"))
        self.res_slider = forms.Slider()
        self.res_slider.MinValue = 20
        self.res_slider.MaxValue = 120
        self.res_slider.Value = 60
        self.res_label = forms.Label(Text="60 x 60")
        self.res_slider.ValueChanged += self._on_res_changed
        layout.Add(self.res_slider, True)
        layout.Add(self.res_label)
        layout.EndHorizontal()

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Mesh Size (units):"))
        self.size_slider = forms.Slider()
        self.size_slider.MinValue = 20
        self.size_slider.MaxValue = 500
        self.size_slider.Value = 100
        self.size_label = forms.Label(Text="100 x 100")
        self.size_slider.ValueChanged += self._on_size_changed
        layout.Add(self.size_slider, True)
        layout.Add(self.size_label)
        layout.EndHorizontal()

        layout.EndGroup()

        # === RD Parameters ===
        layout.BeginGroup("Reaction-Diffusion Parameters")

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Simulation Steps:"))
        self.steps_slider = forms.Slider()
        self.steps_slider.MinValue = 200
        self.steps_slider.MaxValue = 5000
        self.steps_slider.Value = 1000
        self.steps_label = forms.Label(Text="1000")
        self.steps_slider.ValueChanged += self._on_steps_changed
        layout.Add(self.steps_slider, True)
        layout.Add(self.steps_label)
        layout.EndHorizontal()

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Cull Threshold:"))
        self.cull_slider = forms.Slider()
        self.cull_slider.MinValue = 0
        self.cull_slider.MaxValue = 100
        self.cull_slider.Value = 25
        self.cull_label = forms.Label(Text="0.25")
        self.cull_slider.ValueChanged += self._on_cull_changed
        layout.Add(self.cull_slider, True)
        layout.Add(self.cull_label)
        layout.EndHorizontal()

        # Feed range (slider 10-80 => 0.010 - 0.080)
        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Feed Min:"))
        self.fmin_slider = forms.Slider()
        self.fmin_slider.MinValue = 10
        self.fmin_slider.MaxValue = 60
        self.fmin_slider.Value = 20
        self.fmin_label = forms.Label(Text="0.020")
        self.fmin_slider.ValueChanged += self._on_fmin_changed
        layout.Add(self.fmin_slider, True)
        layout.Add(self.fmin_label)
        layout.EndHorizontal()

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Feed Max:"))
        self.fmax_slider = forms.Slider()
        self.fmax_slider.MinValue = 30
        self.fmax_slider.MaxValue = 80
        self.fmax_slider.Value = 55
        self.fmax_label = forms.Label(Text="0.055")
        self.fmax_slider.ValueChanged += self._on_fmax_changed
        layout.Add(self.fmax_slider, True)
        layout.Add(self.fmax_label)
        layout.EndHorizontal()

        # Kill range
        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Kill Min:"))
        self.kmin_slider = forms.Slider()
        self.kmin_slider.MinValue = 30
        self.kmin_slider.MaxValue = 70
        self.kmin_slider.Value = 45
        self.kmin_label = forms.Label(Text="0.045")
        self.kmin_slider.ValueChanged += self._on_kmin_changed
        layout.Add(self.kmin_slider, True)
        layout.Add(self.kmin_label)
        layout.EndHorizontal()

        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Kill Max:"))
        self.kmax_slider = forms.Slider()
        self.kmax_slider.MinValue = 50
        self.kmax_slider.MaxValue = 80
        self.kmax_slider.Value = 65
        self.kmax_label = forms.Label(Text="0.065")
        self.kmax_slider.ValueChanged += self._on_kmax_changed
        layout.Add(self.kmax_slider, True)
        layout.Add(self.kmax_label)
        layout.EndHorizontal()

        # Seed count
        layout.BeginHorizontal()
        layout.Add(forms.Label(Text="Seed Points:"))
        self.seed_slider = forms.Slider()
        self.seed_slider.MinValue = 5
        self.seed_slider.MaxValue = 80
        self.seed_slider.Value = 25
        self.seed_label = forms.Label(Text="25")
        self.seed_slider.ValueChanged += self._on_seed_changed
        layout.Add(self.seed_slider, True)
        layout.Add(self.seed_label)
        layout.EndHorizontal()

        layout.EndGroup()

        # === Status ===
        layout.BeginGroup("Status")
        self.status_label = forms.Label(Text="Ready. Click 'Generate Pattern' to begin.")
        self.status_label.Wrap = forms.WrapMode.Word
        layout.Add(self.status_label)
        layout.EndGroup()

        # === Buttons ===
        layout.BeginHorizontal()

        self.gen_button = forms.Button(Text="Generate Pattern")
        self.gen_button.Click += self._on_generate
        layout.Add(self.gen_button)

        self.bake_button = forms.Button(Text="Bake to Rhino")
        self.bake_button.Click += self._on_bake
        self.bake_button.Enabled = False
        layout.Add(self.bake_button)

        self.clear_button = forms.Button(Text="Clear Preview")
        self.clear_button.Click += self._on_clear
        layout.Add(self.clear_button)

        close_button = forms.Button(Text="Close")
        close_button.Click += self._on_close
        layout.Add(close_button)

        layout.EndHorizontal()

        self.Content = layout
        self._update_solar_info()

    # --- Slider callbacks ---

    def _month_name(self, m):
        names = ["", "January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
        return names[m] if 1 <= m <= 12 else "?"

    def _on_month_changed(self, sender, e):
        self.month_label.Text = self._month_name(self.month_slider.Value)
        self._update_solar_info()

    def _on_hour_changed(self, sender, e):
        hour = self.hour_slider.Value / 2.0
        h = int(hour)
        m = int((hour - h) * 60)
        tz = "AEDT" if is_melbourne_dst(self.month_slider.Value) else "AEST"
        self.hour_label.Text = "{:02d}:{:02d} ({})".format(h, m, tz)
        self._update_solar_info()

    def _on_res_changed(self, sender, e):
        v = self.res_slider.Value
        self.res_label.Text = "{} x {}".format(v, v)

    def _on_size_changed(self, sender, e):
        v = self.size_slider.Value
        self.size_label.Text = "{} x {}".format(v, v)

    def _on_steps_changed(self, sender, e):
        self.steps_label.Text = str(self.steps_slider.Value)

    def _on_cull_changed(self, sender, e):
        self.cull_label.Text = "{:.2f}".format(self.cull_slider.Value / 100.0)

    def _on_fmin_changed(self, sender, e):
        self.fmin_label.Text = "{:.3f}".format(self.fmin_slider.Value / 1000.0)

    def _on_fmax_changed(self, sender, e):
        self.fmax_label.Text = "{:.3f}".format(self.fmax_slider.Value / 1000.0)

    def _on_kmin_changed(self, sender, e):
        self.kmin_label.Text = "{:.3f}".format(self.kmin_slider.Value / 1000.0)

    def _on_kmax_changed(self, sender, e):
        self.kmax_label.Text = "{:.3f}".format(self.kmax_slider.Value / 1000.0)

    def _on_seed_changed(self, sender, e):
        self.seed_label.Text = str(self.seed_slider.Value)

    def _update_solar_info(self):
        month = self.month_slider.Value
        hour = self.hour_slider.Value / 2.0
        alt, az = compute_solar_position(month, hour)
        sun_status = "Above horizon" if alt > 0 else "BELOW horizon"
        self.solar_info.Text = "Alt: {:.1f} deg  |  Az: {:.1f} deg  |  {}".format(
            alt, az, sun_status)

    # --- Action buttons ---

    def _set_buttons_enabled(self, enabled):
        """Enable/disable buttons during long operations."""
        self.gen_button.Enabled = enabled
        self.bake_button.Enabled = enabled and self.last_mesh is not None
        self.clear_button.Enabled = enabled

    def _on_generate(self, sender, e):
        """Generate the RD pattern and show preview."""
        if self._is_running:
            return
        self._is_running = True
        self._set_buttons_enabled(False)

        try:
            self._run_generation()
        except Exception as ex:
            self.status_label.Text = "ERROR: {}".format(str(ex))
            print("Generation error: {}".format(str(ex)))
        finally:
            self._is_running = False
            self._set_buttons_enabled(True)

    def _run_generation(self):
        """Core generation logic, separated for clean error handling."""
        month = self.month_slider.Value
        hour = self.hour_slider.Value / 2.0
        res = self.res_slider.Value
        size = self.size_slider.Value
        steps = self.steps_slider.Value
        threshold = self.cull_slider.Value / 100.0
        f_min = self.fmin_slider.Value / 1000.0
        f_max = self.fmax_slider.Value / 1000.0
        k_min = self.kmin_slider.Value / 1000.0
        k_max = self.kmax_slider.Value / 1000.0
        seed_count = self.seed_slider.Value

        # Validate parameter ranges
        if f_min >= f_max:
            self.status_label.Text = "Error: Feed Min must be less than Feed Max."
            return
        if k_min >= k_max:
            self.status_label.Text = "Error: Kill Min must be less than Kill Max."
            return

        Du = 0.21
        Dv = 0.105
        dt = 1.0

        # --- Solar computation ---
        self.status_label.Text = "Computing solar position..."
        forms.Application.Instance.RunIteration()

        alt, az = compute_solar_position(month, hour)
        if alt <= 0:
            self.status_label.Text = (
                "Sun below horizon (Alt={:.1f} deg). "
                "Pattern will use zero exposure. Proceeding..."
            ).format(alt)
            forms.Application.Instance.RunIteration()

        sun_vec = sun_vector_from_angles(alt, az)

        # --- Mesh creation ---
        self.status_label.Text = "Creating {} x {} mesh...".format(res, res)
        forms.Application.Instance.RunIteration()

        base_mesh, vertex_grid = create_mesh_plane(res, res, size, size)

        # --- Exposure ---
        self.status_label.Text = "Computing solar exposure map..."
        forms.Application.Instance.RunIteration()

        exposure = compute_exposure_map(base_mesh, sun_vec, res, res)

        # --- RD Simulation ---
        def progress_cb(step, total):
            pct = 100.0 * step / total if total > 0 else 0
            self.status_label.Text = "RD Simulation: step {} / {} ({:.0f}%)".format(
                step, total, pct)
            forms.Application.Instance.RunIteration()

        self.status_label.Text = "Starting RD simulation ({} steps on {}x{} grid)...".format(
            steps, res, res)
        forms.Application.Instance.RunIteration()

        A, B_vals = gray_scott_simulate(
            res, res, exposure, steps,
            f_min, f_max, k_min, k_max,
            Du, Dv, dt, seed_count,
            progress_callback=progress_cb
        )

        # --- Cull and color ---
        self.status_label.Text = "Culling mesh faces (threshold={:.2f})...".format(
            threshold)
        forms.Application.Instance.RunIteration()

        culled_mesh, kept, total_faces = build_culled_mesh(
            base_mesh, res, res, B_vals, exposure, threshold)

        if kept == 0:
            self.status_label.Text = (
                "WARNING: All faces culled! Try lowering the threshold "
                "or adjusting solar/RD parameters."
            )
            self.last_mesh = None
            self.bake_button.Enabled = False
            return

        # --- Show preview ---
        guid = add_preview_mesh(culled_mesh)
        if guid is None:
            self.status_label.Text = "ERROR: Could not add preview mesh."
            return

        self.last_mesh = culled_mesh

        tz = "AEDT" if is_melbourne_dst(month) else "AEST"
        info = (
            "Done! Kept {}/{} faces ({:.1f}%).  "
            "Solar: Alt {:.1f} Az {:.1f}  |  "
            "{} {:02d}:{:02d} {}  |  "
            "Grid: {}x{}"
        ).format(
            kept, total_faces, 100.0 * kept / total_faces,
            alt, az,
            self._month_name(month),
            int(hour), int((hour % 1) * 60),
            tz, res, res
        )
        self.status_label.Text = info
        self.bake_button.Enabled = True

    def _on_bake(self, sender, e):
        """Bake the current preview to the permanent layer."""
        if self.last_mesh is None:
            self.status_label.Text = "Nothing to bake. Generate a pattern first."
            return

        delete_preview()

        guid = bake_mesh(self.last_mesh)
        if guid:
            self.status_label.Text = "Baked to layer '{}'. GUID: {}".format(
                BAKE_LAYER, guid)
        else:
            self.status_label.Text = "Error: Failed to bake mesh."

        self.bake_button.Enabled = False
        self.last_mesh = None

    def _on_clear(self, sender, e):
        """Clear preview from viewport."""
        delete_preview()
        self.status_label.Text = "Preview cleared."
        self.bake_button.Enabled = False
        self.last_mesh = None

    def _on_close(self, sender, e):
        """Close the dialog."""
        self.Close(False)


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    dialog = SolarRDDialog()
    dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)


if __name__ == "__main__":
    main()
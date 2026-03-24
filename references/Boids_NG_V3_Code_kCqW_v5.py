# -*- coding: utf-8 -*-
import rhinoscriptsyntax as rs
import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import math
import random
import System
import System.Drawing

# Eto for the settings dialog
import Eto.Forms as forms
import Eto.Drawing as drawing

# =============================================================================
# BOID CLASS
# =============================================================================
class Boid:
    """
    A single flocking agent with position, velocity, and acceleration.
    
    The boid follows Craig Reynolds' three rules:
      1. Separation - steer away from nearby neighbors to avoid crowding
      2. Alignment  - steer toward the average heading of nearby neighbors
      3. Cohesion   - steer toward the average position of nearby neighbors
    
    Additionally, a Containment rule steers the boid back when it
    approaches the boundary of a defined volume.
    
    """
    
    def __init__(self, position, velocity=None, max_speed=2.0, max_force=0.05):
        self.Position = rg.Point3d(position)
        
        # If no velocity given, assign a small random one
        if velocity is None:
            self.Velocity = rg.Vector3d(
                random.uniform(-1, 1),
                random.uniform(-1, 1),
                random.uniform(-1, 1)
            )
            self.Velocity.Unitize()
            self.Velocity *= random.uniform(0.5, max_speed)
        else:
            self.Velocity = rg.Vector3d(velocity)
        
        # Acceleration accumulates forces each frame, then resets
        self.Acceleration = rg.Vector3d(0, 0, 0)
        
        self.MaxSpeed = max_speed
        self.MaxForce = max_force
        
        # Trail history: list of Point3d
        self.Trail = [rg.Point3d(position)]
    
    def apply_force(self, force):
        """
        Newton's 2nd law: F = m*a. We assume mass = 1, so a += F.
        Forces accumulate each frame before being applied in update().
        """
        self.Acceleration += force
    
    def update(self, trail_length=50):
        """
        Euler integration:
          velocity += acceleration
          position += velocity
          acceleration = 0  (reset for next frame)
        Also maintains the trail history.
        """
        self.Velocity += self.Acceleration
        
        # Clamp velocity to MaxSpeed
        if self.Velocity.Length > self.MaxSpeed:
            self.Velocity.Unitize()
            self.Velocity *= self.MaxSpeed
        
        self.Position += self.Velocity
        
        # Append to trail and trim to max length
        self.Trail.append(rg.Point3d(self.Position))
        if len(self.Trail) > trail_length:
            self.Trail = self.Trail[-trail_length:]
        
        # Reset acceleration for next frame
        self.Acceleration = rg.Vector3d(0, 0, 0)
    
    def seek(self, target):
        """
        Calculate a steering vector toward a target point.
        """
        desired = rg.Vector3d(target - self.Position)
        desired.Unitize()
        desired *= self.MaxSpeed
        
        steer = desired - self.Velocity
        if steer.Length > self.MaxForce:
            steer.Unitize()
            steer *= self.MaxForce
        return steer
    
    # =========================================================================
    # THE THREE RULES + CONTAINMENT
    # =========================================================================
    
    def separation(self, neighbors, sep_radius):
        """
        SEPARATION: Steer away from neighbors that are too close.
        """
        steer = rg.Vector3d(0, 0, 0)
        count = 0
        
        for other in neighbors:
            dist = self.Position.DistanceTo(other.Position)
            if dist > 0 and dist < sep_radius:
                diff = rg.Vector3d(self.Position - other.Position)
                diff /= (dist * dist)
                steer += diff
                count += 1
        
        if count > 0:
            steer /= count
            
            if steer.Length > 0:
                steer.Unitize()
                steer *= self.MaxSpeed
                steer -= self.Velocity
                if steer.Length > self.MaxForce:
                    steer.Unitize()
                    steer *= self.MaxForce
        
        return steer
    
    def alignment(self, neighbors, align_radius):
        """
        ALIGNMENT: Steer toward the average velocity of nearby neighbors.
        """
        avg_vel = rg.Vector3d(0, 0, 0)
        count = 0
        
        for other in neighbors:
            dist = self.Position.DistanceTo(other.Position)
            if dist > 0 and dist < align_radius:
                avg_vel += other.Velocity
                count += 1
        
        if count > 0:
            avg_vel /= count
            
            if avg_vel.Length > 0:
                avg_vel.Unitize()
                avg_vel *= self.MaxSpeed
                steer = avg_vel - self.Velocity
                if steer.Length > self.MaxForce:
                    steer.Unitize()
                    steer *= self.MaxForce
                return steer
        
        return rg.Vector3d(0, 0, 0)
    
    def cohesion(self, neighbors, coh_radius):
        """
        COHESION: Steer toward the average position of nearby neighbors.
        """
        center = rg.Point3d(0, 0, 0)
        count = 0
        
        for other in neighbors:
            dist = self.Position.DistanceTo(other.Position)
            if dist > 0 and dist < coh_radius:
                center += rg.Point3d(other.Position)
                count += 1
        
        if count > 0:
            center /= count
            return self.seek(center)
        
        return rg.Vector3d(0, 0, 0)
    
    def containment_box(self, box, margin=5.0, strength=1.0):
        """
        CONTAINMENT (Box): Steer back when approaching box boundaries.
        """
        steer = rg.Vector3d(0, 0, 0)
        
        bbox = box.BoundingBox
        min_pt = bbox.Min
        max_pt = bbox.Max
        
        p = self.Position
        
        # --- X axis ---
        dist_to_min_x = p.X - min_pt.X
        dist_to_max_x = max_pt.X - p.X
        if dist_to_min_x < margin and dist_to_min_x > 0:
            urgency = 1.0 - (dist_to_min_x / margin)
            steer += rg.Vector3d(self.MaxSpeed * urgency, 0, 0)
        elif dist_to_min_x <= 0:
            steer += rg.Vector3d(self.MaxSpeed, 0, 0)
        
        if dist_to_max_x < margin and dist_to_max_x > 0:
            urgency = 1.0 - (dist_to_max_x / margin)
            steer += rg.Vector3d(-self.MaxSpeed * urgency, 0, 0)
        elif dist_to_max_x <= 0:
            steer += rg.Vector3d(-self.MaxSpeed, 0, 0)
        
        # --- Y axis ---
        dist_to_min_y = p.Y - min_pt.Y
        dist_to_max_y = max_pt.Y - p.Y
        if dist_to_min_y < margin and dist_to_min_y > 0:
            urgency = 1.0 - (dist_to_min_y / margin)
            steer += rg.Vector3d(0, self.MaxSpeed * urgency, 0)
        elif dist_to_min_y <= 0:
            steer += rg.Vector3d(0, self.MaxSpeed, 0)
        
        if dist_to_max_y < margin and dist_to_max_y > 0:
            urgency = 1.0 - (dist_to_max_y / margin)
            steer += rg.Vector3d(0, -self.MaxSpeed * urgency, 0)
        elif dist_to_max_y <= 0:
            steer += rg.Vector3d(0, -self.MaxSpeed, 0)
        
        # --- Z axis ---
        dist_to_min_z = p.Z - min_pt.Z
        dist_to_max_z = max_pt.Z - p.Z
        if dist_to_min_z < margin and dist_to_min_z > 0:
            urgency = 1.0 - (dist_to_min_z / margin)
            steer += rg.Vector3d(0, 0, self.MaxSpeed * urgency)
        elif dist_to_min_z <= 0:
            steer += rg.Vector3d(0, 0, self.MaxSpeed)
        
        if dist_to_max_z < margin and dist_to_max_z > 0:
            urgency = 1.0 - (dist_to_max_z / margin)
            steer += rg.Vector3d(0, 0, -self.MaxSpeed * urgency)
        elif dist_to_max_z <= 0:
            steer += rg.Vector3d(0, 0, -self.MaxSpeed)
        
        # Apply Reynolds steering with strength multiplier
        if steer.Length > 0:
            steer.Unitize()
            steer *= self.MaxSpeed * strength
            steer -= self.Velocity
            if steer.Length > self.MaxForce * 2:
                steer.Unitize()
                steer *= self.MaxForce * 2
        
        return steer
    
    def containment_sphere(self, center, radius, margin=5.0, strength=1.0):
        """
        CONTAINMENT (Sphere): Steer back when approaching sphere boundary.
        """
        dist_to_center = self.Position.DistanceTo(center)
        dist_to_wall = radius - dist_to_center
        
        if dist_to_wall < margin:
            if dist_to_wall > 0:
                urgency = 1.0 - (dist_to_wall / margin)
            else:
                urgency = 1.0
            
            toward_center = rg.Vector3d(center - self.Position)
            if toward_center.Length > 0:
                toward_center.Unitize()
                toward_center *= self.MaxSpeed * urgency * strength
                steer = toward_center - self.Velocity
                if steer.Length > self.MaxForce * 2:
                    steer.Unitize()
                    steer *= self.MaxForce * 2
                return steer
        
        return rg.Vector3d(0, 0, 0)
    
    def obstacle_avoidance(self, obstacle_meshes, detection_radius, w_obstacle):
        """
        OBSTACLE AVOIDANCE: For each nearby obstacle mesh, find the closest
        point on its surface. If within detection_radius, steer away from it.
        Uses a look-ahead ray for predictive avoidance plus proximity repulsion.
        """
        steer = rg.Vector3d(0, 0, 0)
        count = 0
        
        for mesh in obstacle_meshes:
            # --- Proximity repulsion ---
            closest_pt = mesh.ClosestPoint(self.Position)
            dist = self.Position.DistanceTo(closest_pt)
            
            if dist < detection_radius and dist > 1e-6:
                # Steer away from the closest surface point
                away = rg.Vector3d(self.Position - closest_pt)
                # Weight inversely by distance squared for stronger close repulsion
                urgency = 1.0 - (dist / detection_radius)
                away.Unitize()
                away *= self.MaxSpeed * urgency * urgency
                steer += away
                count += 1
            elif dist <= 1e-6:
                # Agent is ON the surface — push outward hard
                # Use mesh normal at closest point as escape direction
                mesh_pt = mesh.ClosestMeshPoint(self.Position, 0.0)
                if mesh_pt is not None:
                    normal = mesh.NormalAt(mesh_pt)
                    steer += rg.Vector3d(normal) * self.MaxSpeed * 2.0
                else:
                    # Fallback: reverse velocity
                    steer -= rg.Vector3d(self.Velocity) * 2.0
                count += 1
            
            # --- Predictive look-ahead (ray casting) ---
            vel_dir = rg.Vector3d(self.Velocity)
            if vel_dir.Length > 1e-6:
                vel_dir.Unitize()
                look_ahead = detection_radius * 1.5
                ray = rg.Ray3d(self.Position, vel_dir)
                hit_dist = rg.Intersect.Intersection.MeshRay(mesh, ray)
                
                if hit_dist >= 0 and hit_dist < look_ahead:
                    # A collision is predicted — find hit point and steer away
                    hit_pt = ray.PointAt(hit_dist)
                    mesh_pt = mesh.ClosestMeshPoint(hit_pt, 0.0)
                    if mesh_pt is not None:
                        normal = mesh.NormalAt(mesh_pt)
                        urgency = 1.0 - (hit_dist / look_ahead)
                        steer += rg.Vector3d(normal) * self.MaxSpeed * urgency
                        count += 1
        
        if count > 0:
            steer /= count
            if steer.Length > 0:
                steer.Unitize()
                steer *= self.MaxSpeed
                steer -= self.Velocity
                max_avoid_force = self.MaxForce * 3.0  # stronger than normal steering
                if steer.Length > max_avoid_force:
                    steer.Unitize()
                    steer *= max_avoid_force
        
        return steer * w_obstacle
    
    def run(self, flock, sep_radius, align_radius, coh_radius,
            w_sep, w_align, w_coh, w_contain,
            boundary_box=None, boundary_sphere_center=None,
            boundary_sphere_radius=None, contain_margin=5.0,
            trail_length=50, obstacle_meshes=None,
            obstacle_detection_radius=10.0, w_obstacle=2.0):
        """
        Execute one simulation step for this boid.
        """
        f_sep = self.separation(flock, sep_radius)
        f_ali = self.alignment(flock, align_radius)
        f_coh = self.cohesion(flock, coh_radius)
        
        f_sep *= w_sep
        f_ali *= w_align
        f_coh *= w_coh
        
        self.apply_force(f_sep)
        self.apply_force(f_ali)
        self.apply_force(f_coh)
        
        if boundary_box is not None:
            f_contain = self.containment_box(boundary_box, contain_margin, w_contain)
            self.apply_force(f_contain)
        
        if boundary_sphere_center is not None and boundary_sphere_radius is not None:
            f_contain = self.containment_sphere(
                boundary_sphere_center, boundary_sphere_radius,
                contain_margin, w_contain
            )
            self.apply_force(f_contain)
        
        # Obstacle avoidance
        if obstacle_meshes and len(obstacle_meshes) > 0:
            f_obstacle = self.obstacle_avoidance(
                obstacle_meshes, obstacle_detection_radius, w_obstacle
            )
            self.apply_force(f_obstacle)
        
        self.update(trail_length)


# =============================================================================
# FLOCK MANAGER - handles creation, stepping, and output
# =============================================================================
class FlockManager:
    """
    Manages a collection of Boid agents.
    """
    
    def __init__(self, start_points, max_speed=2.0, max_force=0.05):
        self.boids = []
        for pt in start_points:
            boid = Boid(pt, max_speed=max_speed, max_force=max_force)
            self.boids.append(boid)
        self.frame = 0
    
    def step(self, sep_radius, align_radius, coh_radius,
             w_sep, w_align, w_coh, w_contain,
             boundary_box=None, boundary_sphere_center=None,
             boundary_sphere_radius=None, contain_margin=5.0,
             trail_length=50, obstacle_meshes=None,
             obstacle_detection_radius=10.0, w_obstacle=2.0):
        """Advance the simulation by one frame."""
        for boid in self.boids:
            boid.run(
                self.boids,
                sep_radius, align_radius, coh_radius,
                w_sep, w_align, w_coh, w_contain,
                boundary_box, boundary_sphere_center,
                boundary_sphere_radius, contain_margin,
                trail_length, obstacle_meshes,
                obstacle_detection_radius, w_obstacle
            )
        self.frame += 1
    
    def get_positions(self):
        """Return list of Point3d for all boids."""
        return [rg.Point3d(b.Position) for b in self.boids]
    
    def get_orientations(self):
        """Return list of unit Vector3d for each boid's heading."""
        orientations = []
        for b in self.boids:
            v = rg.Vector3d(b.Velocity)
            if v.Length > 0:
                v.Unitize()
            orientations.append(v)
        return orientations


# =============================================================================
# ETO SETTINGS DIALOG
# =============================================================================
class BoidSettingsDialog(forms.Dialog[bool]):
    """
    A dialog box that gathers all simulation parameters before running.
    Includes: simulation params, behavior weights, boundary settings,
    output layer, trail length, and trail color.
    """
    
    def __init__(self):
        self.Title = "Boid Flocking Simulation Settings"
        self.Padding = drawing.Padding(10)
        self.Resizable = False
        self.MinimumSize = drawing.Size(480, 0)
        
        # --- Result storage ---
        self.result = None
        
        # =====================================================================
        # BUILD CONTROLS
        # =====================================================================
        
        # -- Simulation --
        self.num_boids_stepper = self._make_stepper(40, 3, 500, 0)
        self.num_steps_stepper = self._make_stepper(200, 1, 5000, 0)
        self.max_speed_stepper = self._make_stepper(2.0, 0.1, 20.0, 2)
        self.max_force_stepper = self._make_stepper(0.05, 0.001, 1.0, 3)
        
        # -- Behavior radii & weights --
        self.neighbor_radius_stepper = self._make_stepper(15.0, 1.0, 100.0, 1)
        self.w_sep_stepper = self._make_stepper(1.5, 0.0, 10.0, 2)
        self.w_align_stepper = self._make_stepper(1.0, 0.0, 10.0, 2)
        self.w_coh_stepper = self._make_stepper(1.0, 0.0, 10.0, 2)
        self.w_contain_stepper = self._make_stepper(2.0, 0.0, 10.0, 2)
        
        # -- Boundary --
        self.boundary_dropdown = forms.DropDown()
        self.boundary_dropdown.Items.Add(forms.ListItem(Text="Box", Key="Box"))
        self.boundary_dropdown.Items.Add(forms.ListItem(Text="Sphere", Key="Sphere"))
        self.boundary_dropdown.SelectedIndex = 0
        
        self.boundary_size_stepper = self._make_stepper(100.0, 10.0, 1000.0, 1)
        self.contain_margin_stepper = self._make_stepper(20.0, 5.0, 50.0, 1)
        
        # -- Output Layer --
        self.layer_name_text = forms.TextBox(Text="Boids")
        
        # -- Trail settings --
        self.trail_length_stepper = self._make_stepper(50, 2, 500, 0)
        
        # Trail color picker
        self.trail_color_button = forms.ColorPicker()
        self.trail_color_button.Value = drawing.Color.FromArgb(255, 80, 180, 255)
        
        # Agent color picker
        self.agent_color_button = forms.ColorPicker()
        self.agent_color_button.Value = drawing.Color.FromArgb(255, 255, 80, 80)
        
        # -- Obstacle settings --
        self.obstacle_ids = []  # will hold Rhino object GUIDs
        self.obstacle_label = forms.Label(Text="No obstacles selected")
        
        self.select_obstacles_button = forms.Button(Text="Select Obstacles...")
        self.select_obstacles_button.Click += self.on_select_obstacles
        
        self.clear_obstacles_button = forms.Button(Text="Clear")
        self.clear_obstacles_button.Click += self.on_clear_obstacles
        
        self.obstacle_radius_stepper = self._make_stepper(10.0, 1.0, 200.0, 1)
        self.w_obstacle_stepper = self._make_stepper(2.0, 0.0, 20.0, 2)
        self.obstacle_color_button = forms.ColorPicker()
        self.obstacle_color_button.Value = drawing.Color.FromArgb(255, 255, 165, 0)
        
        # =====================================================================
        # LAYOUT
        # =====================================================================
        layout = forms.DynamicLayout()
        layout.DefaultSpacing = drawing.Size(5, 5)
        layout.Padding = drawing.Padding(10)
        
        # -- Simulation group --
        layout.BeginGroup("Simulation")
        layout.AddRow(forms.Label(Text="Number of boids:"), self.num_boids_stepper)
        layout.AddRow(forms.Label(Text="Number of steps:"), self.num_steps_stepper)
        layout.AddRow(forms.Label(Text="Max boid speed:"), self.max_speed_stepper)
        layout.AddRow(forms.Label(Text="Max steering force:"), self.max_force_stepper)
        layout.EndGroup()
        
        # -- Behavior group --
        layout.BeginGroup("Behavior Weights & Radii")
        layout.AddRow(forms.Label(Text="Neighbor detection radius:"), self.neighbor_radius_stepper)
        layout.AddRow(forms.Label(Text="Separation weight:"), self.w_sep_stepper)
        layout.AddRow(forms.Label(Text="Alignment weight:"), self.w_align_stepper)
        layout.AddRow(forms.Label(Text="Cohesion weight:"), self.w_coh_stepper)
        layout.AddRow(forms.Label(Text="Containment weight:"), self.w_contain_stepper)
        layout.EndGroup()
        
        # -- Boundary group --
        layout.BeginGroup("Boundary")
        layout.AddRow(forms.Label(Text="Boundary type:"), self.boundary_dropdown)
        layout.AddRow(forms.Label(Text="Boundary size (full width / diameter):"), self.boundary_size_stepper)
        layout.AddRow(forms.Label(Text="Containment margin (%):"), self.contain_margin_stepper)
        layout.EndGroup()
        
        # -- Obstacles group --
        layout.BeginGroup("Obstacle Avoidance")
        
        obs_button_row = forms.DynamicLayout()
        obs_button_row.AddRow(self.select_obstacles_button, self.clear_obstacles_button, self.obstacle_label)
        layout.AddRow(obs_button_row)
        
        layout.AddRow(forms.Label(Text="Obstacle detection radius:"), self.obstacle_radius_stepper)
        layout.AddRow(forms.Label(Text="Obstacle avoidance weight:"), self.w_obstacle_stepper)
        layout.AddRow(forms.Label(Text="Obstacle display color:"), self.obstacle_color_button)
        layout.EndGroup()
        
        # -- Output group --
        layout.BeginGroup("Output & Appearance")
        layout.AddRow(forms.Label(Text="Output layer name:"), self.layer_name_text)
        layout.AddRow(forms.Label(Text="Agent trail color:"), self.trail_color_button)
        layout.AddRow(forms.Label(Text="Agent point color:"), self.agent_color_button)
        layout.AddRow(forms.Label(Text="Agent trail length (frames):"), self.trail_length_stepper)
        layout.EndGroup()
        
        # -- OK / Cancel --
        ok_button = forms.Button(Text="Run Simulation")
        ok_button.Click += self.on_ok
        cancel_button = forms.Button(Text="Cancel")
        cancel_button.Click += self.on_cancel
        
        layout.BeginVertical()
        layout.AddSpace()
        button_row = forms.DynamicLayout()
        button_row.AddRow(None, ok_button, cancel_button)
        layout.AddRow(button_row)
        layout.EndVertical()
        
        self.Content = layout
        self.DefaultButton = ok_button
        self.AbortButton = cancel_button
    
    def _make_stepper(self, value, min_val, max_val, decimals):
        """Helper to create a NumericStepper with sensible settings."""
        stepper = forms.NumericStepper()
        stepper.Value = value
        stepper.MinValue = min_val
        stepper.MaxValue = max_val
        stepper.DecimalPlaces = decimals
        stepper.Increment = 10 ** (-decimals) if decimals > 0 else 1
        stepper.Width = 120
        return stepper
    
    def on_select_obstacles(self, sender, e):
        """
        Temporarily hide the dialog, let the user pick geometry in Rhino,
        then return to the dialog with the selection stored.
        """
        self.Visible = False
        Rhino.RhinoApp.Wait()
        
        # Let user pick multiple objects (surfaces, polysurfaces, meshes, extrusions)
        filter_flags = (rs.filter.surface | rs.filter.polysurface |
                        rs.filter.mesh | rs.filter.extrusion)
        ids = rs.GetObjects(
            "Select obstacle geometry (surfaces, polysurfaces, meshes, extrusions)",
            filter=filter_flags,
            preselect=True
        )
        
        if ids:
            self.obstacle_ids = list(ids)
            count = len(self.obstacle_ids)
            self.obstacle_label.Text = "{} obstacle{} selected".format(
                count, "s" if count != 1 else "")
        else:
            # User cancelled selection — keep previous selection if any
            if not self.obstacle_ids:
                self.obstacle_label.Text = "No obstacles selected"
        
        self.Visible = True
        self.Focus()
    
    def on_clear_obstacles(self, sender, e):
        """Remove all selected obstacles."""
        self.obstacle_ids = []
        self.obstacle_label.Text = "No obstacles selected"
    
    def on_ok(self, sender, e):
        """Collect all values into a dictionary and close."""
        ec = self.trail_color_button.Value
        ac = self.agent_color_button.Value
        oc = self.obstacle_color_button.Value
        
        self.result = {
            "num_boids": int(self.num_boids_stepper.Value),
            "num_steps": int(self.num_steps_stepper.Value),
            "max_speed": self.max_speed_stepper.Value,
            "max_force": self.max_force_stepper.Value,
            "neighbor_radius": self.neighbor_radius_stepper.Value,
            "w_sep": self.w_sep_stepper.Value,
            "w_align": self.w_align_stepper.Value,
            "w_coh": self.w_coh_stepper.Value,
            "w_contain": self.w_contain_stepper.Value,
            "boundary_type": self.boundary_dropdown.SelectedKey,
            "boundary_size": self.boundary_size_stepper.Value,
            "contain_margin_pct": self.contain_margin_stepper.Value,
            "layer_name": self.layer_name_text.Text.strip() or "Boids",
            "trail_length": int(self.trail_length_stepper.Value),
            "trail_color": (int(ec.R * 255), int(ec.G * 255), int(ec.B * 255)),
            "agent_color": (int(ac.R * 255), int(ac.G * 255), int(ac.B * 255)),
            "obstacle_ids": list(self.obstacle_ids),
            "obstacle_detection_radius": self.obstacle_radius_stepper.Value,
            "w_obstacle": self.w_obstacle_stepper.Value,
            "obstacle_color": (int(oc.R * 255), int(oc.G * 255), int(oc.B * 255)),
        }
        self.Close(True)
    
    def on_cancel(self, sender, e):
        self.Close(False)


# =============================================================================
# OBSTACLE MESH CONVERSION
# =============================================================================
def convert_obstacles_to_meshes(obstacle_ids):
    """
    Convert selected Rhino objects (breps, extrusions, meshes) into
    a list of Rhino.Geometry.Mesh objects for obstacle collision testing.
    
    Returns a list of Mesh objects with normals computed.
    """
    meshes = []
    mesh_params = rg.MeshingParameters.Default
    
    for obj_id in obstacle_ids:
        rhino_obj = rs.coercerhinoobject(obj_id)
        if rhino_obj is None:
            continue
        
        geo = rhino_obj.Geometry
        
        if isinstance(geo, rg.Mesh):
            # Already a mesh — use directly
            mesh_copy = rg.Mesh()
            mesh_copy.CopyFrom(geo)
            mesh_copy.Normals.ComputeNormals()
            mesh_copy.Compact()
            meshes.append(mesh_copy)
        
        elif isinstance(geo, rg.Brep):
            # Convert brep to mesh
            mesh_array = rg.Mesh.CreateFromBrep(geo, mesh_params)
            if mesh_array:
                for m in mesh_array:
                    m.Normals.ComputeNormals()
                    m.Compact()
                    meshes.append(m)
        
        elif isinstance(geo, rg.Extrusion):
            # Convert extrusion to brep first, then mesh
            brep = geo.ToBrep()
            if brep:
                mesh_array = rg.Mesh.CreateFromBrep(brep, mesh_params)
                if mesh_array:
                    for m in mesh_array:
                        m.Normals.ComputeNormals()
                        m.Compact()
                        meshes.append(m)
        
        elif isinstance(geo, rg.Surface):
            # Convert surface to brep, then mesh
            brep = geo.ToBrep()
            if brep:
                mesh_array = rg.Mesh.CreateFromBrep(brep, mesh_params)
                if mesh_array:
                    for m in mesh_array:
                        m.Normals.ComputeNormals()
                        m.Compact()
                        meshes.append(m)
    
    # Optionally join into fewer meshes for efficiency
    if len(meshes) > 1:
        joined = rg.Mesh()
        for m in meshes:
            joined.Append(m)
        joined.Normals.ComputeNormals()
        joined.Compact()
        return [joined]
    
    return meshes


# =============================================================================
# REAL-TIME DRAWING HELPERS
# =============================================================================
def draw_conduit_frame(flock, params, boundary_ids, trail_ids, agent_ids):
    """
    Erase previous frame geometry and draw new positions + trails.
    Returns updated (trail_ids, agent_ids) lists.
    """
    layer_agents = params["layer_name"] + "_Agents"
    layer_trails = params["layer_name"] + "_Trails"
    
    # --- Delete previous frame's agents and trails ---
    for obj_id in agent_ids:
        rs.DeleteObject(obj_id)
    for obj_id in trail_ids:
        rs.DeleteObject(obj_id)
    
    new_agent_ids = []
    new_trail_ids = []
    
    positions = flock.get_positions()
    orientations = flock.get_orientations()
    arrow_scale = params["max_speed"] * 2.0
    
    # --- Draw agent points and orientation arrows ---
    for i in range(len(positions)):
        pos = positions[i]
        orient = orientations[i]
        
        pt_id = rs.AddPoint(pos)
        if pt_id:
            rs.ObjectLayer(pt_id, layer_agents)
            new_agent_ids.append(pt_id)
        
        end_pt = rg.Point3d(pos + orient * arrow_scale)
        line_id = rs.AddLine(pos, end_pt)
        if line_id:
            rs.ObjectLayer(line_id, layer_agents)
            new_agent_ids.append(line_id)
    
    # --- Draw trails as polylines ---
    for boid in flock.boids:
        if len(boid.Trail) > 1:
            trail_id = rs.AddPolyline(boid.Trail)
            if trail_id:
                rs.ObjectLayer(trail_id, layer_trails)
                new_trail_ids.append(trail_id)
    
    return new_trail_ids, new_agent_ids


# =============================================================================
# MAIN - Settings dialog, then real-time simulation
# =============================================================================
def main():
    # -----------------------------------------------------------------
    # 1. SHOW SETTINGS DIALOG
    # -----------------------------------------------------------------
    dialog = BoidSettingsDialog()
    rc = dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
    
    if not rc or dialog.result is None:
        print("Simulation cancelled.")
        return
    
    p = dialog.result  # shorthand
    
    # -----------------------------------------------------------------
    # 2. SET UP LAYERS
    # -----------------------------------------------------------------
    layer_agents = p["layer_name"] + "_Agents"
    layer_trails = p["layer_name"] + "_Trails"
    layer_boundary = p["layer_name"] + "_Boundary"
    layer_obstacles = p["layer_name"] + "_Obstacles"
    
    for layer_name in [layer_agents, layer_trails, layer_boundary, layer_obstacles]:
        if not rs.IsLayer(layer_name):
            rs.AddLayer(layer_name)
    
    rs.LayerColor(layer_agents, p["agent_color"])
    rs.LayerColor(layer_trails, p["trail_color"])
    rs.LayerColor(layer_boundary, [200, 200, 200])
    rs.LayerColor(layer_obstacles, p["obstacle_color"])
    
    # -----------------------------------------------------------------
    # 3. SET UP BOUNDARY
    # -----------------------------------------------------------------
    boundary_box = None
    boundary_sphere_center = None
    boundary_sphere_radius = None
    
    half = p["boundary_size"] / 2.0
    
    if p["boundary_type"] == "Box":
        min_pt = rg.Point3d(-half, -half, -half)
        max_pt = rg.Point3d(half, half, half)
        bbox = rg.BoundingBox(min_pt, max_pt)
        boundary_box = rg.Box(bbox)
        box_size = p["boundary_size"]
        contain_margin = box_size * p["contain_margin_pct"] / 100.0
    else:
        boundary_sphere_center = rg.Point3d(0, 0, 0)
        boundary_sphere_radius = half
        contain_margin = boundary_sphere_radius * p["contain_margin_pct"] / 100.0
    
    # Derived radii
    sep_radius = p["neighbor_radius"] * 0.5
    align_radius = p["neighbor_radius"]
    coh_radius = p["neighbor_radius"]
    
    # -----------------------------------------------------------------
    # 4. GENERATE STARTING POINTS
    # -----------------------------------------------------------------
    start_points = []
    
    if boundary_box is not None:
        bb = boundary_box.BoundingBox
        for _ in range(p["num_boids"]):
            x = random.uniform(bb.Min.X + contain_margin, bb.Max.X - contain_margin)
            y = random.uniform(bb.Min.Y + contain_margin, bb.Max.Y - contain_margin)
            z = random.uniform(bb.Min.Z + contain_margin, bb.Max.Z - contain_margin)
            start_points.append(rg.Point3d(x, y, z))
    else:
        for _ in range(p["num_boids"]):
            while True:
                x = random.uniform(-1, 1)
                y = random.uniform(-1, 1)
                z = random.uniform(-1, 1)
                if x*x + y*y + z*z <= 1.0:
                    r = boundary_sphere_radius - contain_margin
                    pt = rg.Point3d(
                        boundary_sphere_center.X + x * r,
                        boundary_sphere_center.Y + y * r,
                        boundary_sphere_center.Z + z * r
                    )
                    start_points.append(pt)
                    break
    
    # -----------------------------------------------------------------
    # 5. CREATE FLOCK
    # -----------------------------------------------------------------
    flock = FlockManager(start_points, max_speed=p["max_speed"], max_force=p["max_force"])
    sc.sticky["boid_flock"] = flock
    print("Flock initialized with {} boids. Running {} steps...".format(
        p["num_boids"], p["num_steps"]))
    
    # -----------------------------------------------------------------
    # 6. DRAW BOUNDARY (persistent, stays for entire sim)
    # -----------------------------------------------------------------
    rs.EnableRedraw(False)
    boundary_ids = []
    
    if boundary_box is not None:
        bb = boundary_box.BoundingBox
        corners = bb.GetCorners()
        edges = [
            (0,1),(1,2),(2,3),(3,0),
            (4,5),(5,6),(6,7),(7,4),
            (0,4),(1,5),(2,6),(3,7)
        ]
        for i, j in edges:
            line_id = rs.AddLine(corners[i], corners[j])
            if line_id:
                rs.ObjectLayer(line_id, layer_boundary)
                boundary_ids.append(line_id)
    else:
        sphere = rg.Sphere(boundary_sphere_center, boundary_sphere_radius)
        sphere_brep = sphere.ToBrep()
        sphere_id = sc.doc.Objects.AddBrep(sphere_brep)
        if sphere_id:
            rs.ObjectLayer(sphere_id, layer_boundary)
            boundary_ids.append(sphere_id)
    
    rs.EnableRedraw(True)
    
    # -----------------------------------------------------------------
    # 7. REAL-TIME SIMULATION LOOP
    # -----------------------------------------------------------------
    trail_ids = []
    agent_ids = []
    cancelled = False
    
    for step in range(p["num_steps"]):
        # Check for escape key
        if sc.escape_test(False):
            print("Simulation cancelled by user at step {}.".format(step))
            cancelled = True
            break
        
        # Advance the simulation one step
        flock.step(
            sep_radius, align_radius, coh_radius,
            p["w_sep"], p["w_align"], p["w_coh"], p["w_contain"],
            boundary_box=boundary_box,
            boundary_sphere_center=boundary_sphere_center,
            boundary_sphere_radius=boundary_sphere_radius,
            contain_margin=contain_margin,
            trail_length=p["trail_length"]
        )
        
        # Redraw every frame for real-time visualization
        rs.EnableRedraw(False)
        trail_ids, agent_ids = draw_conduit_frame(
            flock, p, boundary_ids, trail_ids, agent_ids
        )
        rs.EnableRedraw(True)
        
        # Force the viewport to refresh
        Rhino.RhinoApp.Wait()
        sc.doc.Views.Redraw()
    
    # -----------------------------------------------------------------
    # 8. FINAL OUTPUT - bake trails as permanent geometry
    # -----------------------------------------------------------------
    # The last frame's agents and trails are already in the document.
    # Print summary.
    print("=" * 50)
    print("BOID SIMULATION COMPLETE")
    print("=" * 50)
    print("  Boids:       {}".format(p["num_boids"]))
    print("  Steps run:   {}".format(flock.frame))
    print("  Separation:  radius={:.1f}, weight={:.2f}".format(sep_radius, p["w_sep"]))
    print("  Alignment:   radius={:.1f}, weight={:.2f}".format(align_radius, p["w_align"]))
    print("  Cohesion:    radius={:.1f}, weight={:.2f}".format(coh_radius, p["w_coh"]))
    print("  Containment: margin={:.1f}, weight={:.2f}".format(contain_margin, p["w_contain"]))
    print("  Max speed:   {:.2f}".format(p["max_speed"]))
    print("  Max force:   {:.4f}".format(p["max_force"]))
    print("  Trail length: {} frames".format(p["trail_length"]))
    print("")
    print("Output layers: {}, {}, {}".format(layer_agents, layer_trails, layer_boundary))
    
    # Store final state in sticky for potential re-use
    sc.sticky["boid_positions"] = flock.get_positions()
    sc.sticky["boid_orientations"] = flock.get_orientations()
    print("Positions and orientations stored in sc.sticky.")


if __name__ == "__main__":
    main()
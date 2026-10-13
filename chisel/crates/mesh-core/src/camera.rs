//! An orbit camera, with 3ds Max's habits.
//!
//! Pure maths, no window: this is the part of a viewport most likely to be
//! subtly wrong -- an inverted axis, a frame that cuts the model off, a zoom
//! that stops short or sails through the pivot -- and all of it can be checked
//! without anything being drawn.

use glam::{Mat4, Vec2, Vec3};

#[derive(Clone, Copy, Debug)]
pub struct Camera {
    /// What the camera orbits and looks at.
    pub target: Vec3,
    pub distance: f32,
    /// Around Y, radians. Positive drags right.
    pub yaw: f32,
    /// Up/down, radians, clamped short of the poles.
    pub pitch: f32,
    pub fov_y: f32,
    pub near: f32,
    pub far: f32,
}

/// Just short of straight up or down. At exactly the pole the view direction
/// is parallel to up and the matrix degenerates -- the picture snaps round on
/// the last pixel of travel.
const PITCH_LIMIT: f32 = std::f32::consts::FRAC_PI_2 - 0.001;

impl Default for Camera {
    fn default() -> Self {
        Self {
            target: Vec3::ZERO,
            distance: 5.0,
            yaw: std::f32::consts::FRAC_PI_4,
            pitch: 0.5,
            fov_y: 45f32.to_radians(),
            near: 0.01,
            far: 1000.0,
        }
    }
}

impl Camera {
    pub fn eye(&self) -> Vec3 {
        let (sy, cy) = self.yaw.sin_cos();
        let (sp, cp) = self.pitch.sin_cos();
        self.target + Vec3::new(cp * sy, sp, cp * cy) * self.distance
    }

    pub fn forward(&self) -> Vec3 {
        (self.target - self.eye()).normalize_or_zero()
    }

    pub fn right(&self) -> Vec3 {
        self.forward().cross(Vec3::Y).normalize_or_zero()
    }

    /// Up as the camera sees it, not world up.
    pub fn up(&self) -> Vec3 {
        self.right().cross(self.forward()).normalize_or_zero()
    }

    pub fn view(&self) -> Mat4 {
        Mat4::look_at_rh(self.eye(), self.target, Vec3::Y)
    }

    pub fn projection(&self, aspect: f32) -> Mat4 {
        Mat4::perspective_rh(self.fov_y, aspect.max(0.001), self.near, self.far)
    }

    pub fn view_projection(&self, aspect: f32) -> Mat4 {
        self.projection(aspect) * self.view()
    }

    /// Alt + middle-drag. Pixels in, radians out.
    pub fn orbit(&mut self, delta_px: Vec2, sensitivity: f32) {
        self.yaw -= delta_px.x * sensitivity;
        self.pitch = (self.pitch + delta_px.y * sensitivity).clamp(-PITCH_LIMIT, PITCH_LIMIT);
    }

    /// Middle-drag. Scaled by distance so a drag moves the model the same
    /// amount on screen whether you are close in or far out -- panning that
    /// crawls when zoomed out is the usual giveaway that this was left in
    /// world units.
    pub fn pan(&mut self, delta_px: Vec2, viewport_height: f32) {
        let world_per_px = 2.0 * self.distance * (self.fov_y / 2.0).tan()
            / viewport_height.max(1.0);
        self.target += (-self.right() * delta_px.x + self.up() * delta_px.y) * world_per_px;
    }

    /// Wheel. Multiplicative, so each notch covers the same proportion of what
    /// is left: linear zoom crawls from far away and jumps through the pivot
    /// when close.
    pub fn zoom(&mut self, scroll: f32) {
        self.distance = (self.distance * (1.0 - scroll * 0.1).clamp(0.2, 5.0))
            .clamp(self.near * 10.0, self.far * 0.5);
    }

    /// Z. Frame a bounding box so all of it is visible, with a little air.
    ///
    /// Fits by the vertical *and* horizontal field of view: fitting only the
    /// vertical leaves a wide model cut off at the sides on a wide window,
    /// which is exactly when you press this.
    pub fn frame(&mut self, bounds: (Vec3, Vec3), aspect: f32) {
        let (lo, hi) = bounds;
        self.target = (lo + hi) / 2.0;
        let radius = ((hi - lo).length() / 2.0).max(1e-4);
        let fov_x = 2.0 * ((self.fov_y / 2.0).tan() * aspect.max(0.001)).atan();
        let fit = (radius / (self.fov_y / 2.0).sin()).max(radius / (fov_x / 2.0).sin());
        self.distance = (fit * 1.1).clamp(self.near * 10.0, self.far * 0.5);
    }
}

//! The 3D view as an egui widget, with 3ds Max navigation.

use crate::renderer::Renderer;
use crate::App;
use eframe::egui;
use eframe::egui_wgpu;
use glam::{Mat4, Vec2, Vec3};

pub fn show(ui: &mut egui::Ui, app: &mut App, _frame: &mut eframe::Frame) {
    let (rect, response) =
        ui.allocate_exact_size(ui.available_size(), egui::Sense::click_and_drag());
    if rect.width() < 1.0 || rect.height() < 1.0 {
        return;
    }
    let aspect = rect.width() / rect.height();

    // --- navigation, Max bindings -----------------------------------------
    // Orbit is Alt + middle-drag; pan is middle-drag on its own. Reading the
    // modifier at drag time rather than latching it at drag start means
    // letting go of Alt mid-orbit turns it into a pan, which is what Max does.
    let drag = response.drag_delta();
    if drag != egui::Vec2::ZERO {
        let middle = ui.input(|i| i.pointer.middle_down());
        let alt = ui.input(|i| i.modifiers.alt);
        let d = Vec2::new(drag.x, drag.y);
        if middle && alt {
            app.camera.orbit(d, 0.01);
        } else if middle {
            app.camera.pan(d, rect.height());
        }
    }
    if response.hovered() {
        let scroll = ui.input(|i| i.smooth_scroll_delta.y);
        if scroll != 0.0 {
            app.camera.zoom(scroll / 50.0);
        }
    }

    // A left click selects. Only on release, and only if the pointer barely
    // moved: otherwise every orbit that happens to start on a vertex would
    // also select it.
    if response.clicked() {
        if let Some(pos) = response.interact_pointer_pos() {
            let ndc = Vec2::new(
                (pos.x - rect.left()) / rect.width() * 2.0 - 1.0,
                // egui's Y grows downward; NDC's grows up.
                1.0 - (pos.y - rect.top()) / rect.height() * 2.0,
            );
            let add = ui.input(|i| i.modifiers.ctrl || i.modifiers.shift);
            app.click(ndc, aspect, add);
        }
    }

    // --- draw --------------------------------------------------------------
    let view_proj = app.camera.view_projection(aspect);
    let eye = app.camera.eye();
    let mesh = if app.dirty {
        Some((app.mesh.clone(), app.selected_mask()))
    } else {
        None
    };
    app.dirty = false;

    ui.painter().add(egui_wgpu::Callback::new_paint_callback(
        rect,
        ViewportCallback {
            view_proj,
            eye,
            grid: app.grid,
            shaded: app.shaded,
            wireframe: app.wireframe || !app.shaded,
            upload: mesh,
        },
    ));
    ui.ctx().request_repaint();
}

struct ViewportCallback {
    view_proj: Mat4,
    eye: Vec3,
    grid: bool,
    shaded: bool,
    wireframe: bool,
    /// Some when the mesh or the selection changed this frame. Uploading every
    /// frame would rebuild the buffers for a camera move.
    upload: Option<(mesh_core::Mesh, Vec<bool>)>,
}

impl egui_wgpu::CallbackTrait for ViewportCallback {
    fn prepare(
        &self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        _screen: &egui_wgpu::ScreenDescriptor,
        _encoder: &mut wgpu::CommandEncoder,
        resources: &mut egui_wgpu::CallbackResources,
    ) -> Vec<wgpu::CommandBuffer> {
        if let Some(r) = resources.get_mut::<Renderer>() {
            if let Some((mesh, selected)) = &self.upload {
                r.set_mesh(device, mesh, selected);
            }
            r.prepare(queue, self.view_proj, self.eye, false);
        }
        Vec::new()
    }

    fn paint(
        &self,
        _info: egui::PaintCallbackInfo,
        pass: &mut wgpu::RenderPass<'static>,
        resources: &egui_wgpu::CallbackResources,
    ) {
        if let Some(r) = resources.get::<Renderer>() {
            r.paint(pass, self.grid, self.shaded, self.wireframe);
        }
    }
}

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

    // The gizmo gets first refusal on the pointer, because its handles sit on
    // top of the model and a click meant for a handle must not also select the
    // face behind it.
    let pointer_ndc = ui.input(|i| i.pointer.latest_pos()).and_then(|pos| {
        rect.contains(pos).then(|| {
            Vec2::new(
                (pos.x - rect.left()) / rect.width() * 2.0 - 1.0,
                1.0 - (pos.y - rect.top()) / rect.height() * 2.0,
            )
        })
    });
    let pressed = response.drag_started_by(egui::PointerButton::Primary);
    let released = response.drag_stopped_by(egui::PointerButton::Primary)
        || ui.input(|i| i.pointer.primary_released());
    let gizmo_busy = app.gizmo_input(aspect, pointer_ndc, pressed, released);

    // A left click selects. Only on release, and only if the pointer barely
    // moved: otherwise every orbit that happens to start on a vertex would
    // also select it.
    if response.clicked() && !gizmo_busy {
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
    // The display mesh: the base with the modifier stack run over it.
    let mesh = if app.dirty {
        Some((app.display.clone(), app.selected_mask()))
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
            overlay: app.gizmo_origin().map(|origin| {
                let scale = mesh_core::gizmo::screen_scale(&app.camera, origin, 0.11);
                crate::gizmo_draw::build(app.tool, origin, scale, app.hot_handle)
            }),
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
    /// The gizmo's lines, rebuilt every frame: it is a few hundred vertices
    /// and it moves with the camera, so caching it would cost more than it
    /// saves.
    overlay: Option<Vec<crate::gizmo_draw::Line>>,
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
            if let Some(lines) = &self.overlay {
                r.set_overlay(device, lines);
            } else {
                r.set_overlay(device, &[]);
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
            r.paint_overlay(pass);
        }
    }
}

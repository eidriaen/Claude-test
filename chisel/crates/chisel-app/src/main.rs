//! Chisel — a lightweight 3D editor.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod gizmo_draw;
mod panel;
mod renderer;
mod viewport;

use eframe::egui;
use mesh_core::selection::{Level, Selection};
use mesh_core::modifier::Stack;
use mesh_core::transform::{self, Pivot, Tool};
use mesh_core::{normals, pick, primitives, Camera, History, Mesh};
use panel::{CreateParams, Tab};
use std::path::PathBuf;

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1280.0, 800.0])
            .with_min_inner_size([800.0, 500.0])
            .with_title("Chisel"),
        renderer: eframe::Renderer::Wgpu,
        // eframe turns 32 into Depth32Float and attaches it to the pass egui
        // paints into, which is the same pass the viewport callback draws in.
        // Without it there is no depth test at all and back faces land on top
        // of front ones.
        depth_buffer: 32,
        ..Default::default()
    };
    eframe::run_native("Chisel", options, Box::new(|cc| Ok(Box::new(App::new(cc)))))
}

pub struct App {
    pub mesh: Mesh,
    camera: Camera,
    selection: Selection,
    history: History,
    /// The mesh as built. What the viewport shows is this with the stack run
    /// over it -- never edit `display` and expect it to survive.
    pub stack: Stack,
    pub display: Mesh,
    pub stack_dirty: bool,
    /// Bumped whenever the base mesh changes, so the stack's caches know.
    base_version: u64,
    pub tool: Tool,
    pub pivot: Pivot,
    pub tab: Tab,
    pub create: CreateParams,
    pub type_in_move: [f32; 3],
    pub type_in_rotate: [f32; 3],
    pub type_in_scale: [f32; 3],
    /// The handle under the cursor, and the drag in progress.
    pub hot_handle: Option<mesh_core::gizmo::Handle>,
    pub drag: Option<Drag>,
    shaded: bool,
    wireframe: bool,
    grid: bool,
    /// Last thing an operation reported, shown in the status bar. The point of
    /// orient() is what it tells you, so it needs somewhere to say it.
    status: String,
    dirty: bool,
    /// Where this mesh came from, so Save can go back there without asking.
    path: Option<PathBuf>,
}

/// A transform drag in progress.
pub struct Drag {
    pub handle: mesh_core::gizmo::Handle,
    /// Where the pointer was last frame, in NDC.
    pub last: glam::Vec2,
    /// Where the gizmo was when the drag began. Held still for the whole drag:
    /// recomputing it from the moving selection makes the gizmo chase the
    /// cursor and the motion accelerate away.
    pub origin: Vec3Wrapper,
    /// One token for the whole drag, so it undoes as a single step.
    pub token: String,
}

/// glam::Vec3 by another name, so the field above reads as what it is.
pub type Vec3Wrapper = glam::Vec3;

/// A filename to offer in the save dialog.
fn suggested_name(mesh: &Mesh) -> String {
    if mesh.name.is_empty() {
        "mesh.obj".to_string()
    } else {
        format!("{}.obj", mesh.name)
    }
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let mesh = primitives::cube(2.0);
        let wgpu = cc.wgpu_render_state.as_ref().expect("wgpu backend");
        let mut r = renderer::Renderer::new(&wgpu.device, wgpu.target_format);
        r.set_mesh(&wgpu.device, &mesh, &vec![false; mesh.vertex_count()]);
        let display = mesh.clone();
        wgpu.renderer.write().callback_resources.insert(r);

        let mut camera = Camera::default();
        if let Some(b) = mesh.bounds() {
            camera.frame(b, 16.0 / 9.0);
        }
        Self {
            mesh,
            camera,
            selection: Selection::new(Level::Object),
            history: History::default(),
            stack: Stack::default(),
            display,
            stack_dirty: false,
            base_version: 1,
            tool: Tool::Select,
            pivot: Pivot::default(),
            tab: Tab::Create,
            create: CreateParams::default(),
            type_in_move: [0.0; 3],
            type_in_rotate: [0.0; 3],
            type_in_scale: [100.0; 3],
            hot_handle: None,
            drag: None,
            shaded: true,
            wireframe: false,
            grid: true,
            status: "Ready".into(),
            dirty: false,
            path: None,
        }
    }

    fn open(&mut self, aspect: f32) {
        let Some(path) = rfd::FileDialog::new()
            .add_filter("Meshes", mesh_io::Format::extensions())
            .pick_file()
        else {
            return;
        };
        match mesh_io::load(&path) {
            Ok((mesh, report)) => {
                let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                self.status = format!("{name}: {}", report.summary());
                self.path = Some(path);
                self.replace_mesh(mesh, aspect);
            }
            // A failed import must say why and leave what you had alone. The
            // worst version of this replaces your model with an empty scene
            // and a shrug.
            Err(e) => self.status = format!("could not open {}: {e}", path.display()),
        }
    }

    fn save_as(&mut self) {
        let Some(path) = rfd::FileDialog::new()
            .add_filter("Wavefront OBJ", &["obj"])
            .add_filter("STL", &["stl"])
            .set_file_name(suggested_name(&self.mesh))
            .save_file()
        else {
            return;
        };
        self.save_to(path);
    }

    fn save_to(&mut self, path: PathBuf) {
        match mesh_io::save(&path, &self.mesh) {
            Ok(()) => {
                self.status = format!("saved {}", path.display());
                self.path = Some(path);
            }
            Err(e) => self.status = format!("could not save: {e}"),
        }
    }

    /// Where the gizmo sits: the middle of what a transform would affect.
    pub fn gizmo_origin(&self) -> Option<Vec3Wrapper> {
        if self.tool == Tool::Select {
            return None;
        }
        let verts = transform::affected(&self.mesh, &self.selection);
        if verts.is_empty() {
            return None;
        }
        Some(transform::pivot_point(&self.mesh, &verts, self.pivot))
    }

    /// Begin, continue, or finish a gizmo drag. Returns true when the gizmo
    /// consumed the input, so the viewport knows not to also select.
    pub fn gizmo_input(
        &mut self,
        aspect: f32,
        pointer: Option<glam::Vec2>,
        pressed: bool,
        released: bool,
    ) -> bool {
        use mesh_core::gizmo;

        let Some(origin) = self.gizmo_origin() else {
            self.drag = None;
            self.hot_handle = None;
            return false;
        };
        let scale = gizmo::screen_scale(&self.camera, origin, 0.11);

        if released {
            self.drag = None;
        }
        let Some(ndc) = pointer else {
            self.hot_handle = None;
            return self.drag.is_some();
        };

        if self.drag.is_none() {
            self.hot_handle =
                gizmo::pick_handle(&self.camera, aspect, origin, scale, ndc, self.tool);
            if pressed {
                if let Some(handle) = self.hot_handle {
                    self.drag = Some(Drag {
                        handle,
                        last: ndc,
                        origin,
                        token: format!("gizmo-{}", self.history.depth()),
                    });
                    return true;
                }
            }
            return false;
        }

        // A drag in progress.
        let Some(drag) = self.drag.as_ref() else { return false };
        let (handle, last, origin, token) =
            (drag.handle, drag.last, drag.origin, drag.token.clone());
        if (ndc - last).length() < 1e-6 {
            return true;
        }

        let verts = transform::affected(&self.mesh, &self.selection);
        if verts.is_empty() {
            return true;
        }
        // One history entry for the whole drag.
        self.history
            .push_coalescing(self.tool.label(), token, &self.mesh, &self.selection);

        let camera = self.camera;
        let tool = self.tool;
        match tool {
            Tool::Move => {
                let delta = if let Some(axis) = handle.axis() {
                    let a = gizmo::closest_point_on_axis(&camera, aspect, origin, axis, last);
                    let b = gizmo::closest_point_on_axis(&camera, aspect, origin, axis, ndc);
                    match (a, b) {
                        (Some(a), Some(b)) => b - a,
                        _ => glam::Vec3::ZERO,
                    }
                } else {
                    // A plane handle, or the centre box: drag in the plane
                    // facing the camera.
                    let normal = handle.plane_normal().unwrap_or(-camera.forward());
                    let a = gizmo::ray_plane(&camera, aspect, origin, normal, last);
                    let b = gizmo::ray_plane(&camera, aspect, origin, normal, ndc);
                    match (a, b) {
                        (Some(a), Some(b)) => b - a,
                        _ => glam::Vec3::ZERO,
                    }
                };
                transform::translate(&mut self.mesh, &verts, delta);
            }
            Tool::Rotate => {
                if let Some(axis) = handle.axis() {
                    if let Some(angle) =
                        gizmo::drag_angle(&camera, aspect, origin, axis, last, ndc)
                    {
                        transform::rotate(&mut self.mesh, &verts, origin, axis, angle);
                    }
                }
            }
            Tool::Scale => {
                if let Some(factor) =
                    gizmo::drag_scale_factor(&camera, aspect, origin, last, ndc)
                {
                    let f = match handle.axis() {
                        // An axis handle scales only along that axis; the rest
                        // stay at 1 or the model distorts in two directions.
                        Some(axis) => glam::Vec3::ONE + axis * (factor - 1.0),
                        None => glam::Vec3::splat(factor),
                    };
                    transform::scale(&mut self.mesh, &verts, origin, f);
                }
            }
            Tool::Select => {}
        }

        transform::finish(&mut self.mesh);
        self.base_version += 1;
        self.rebuild_display();
        if let Some(d) = self.drag.as_mut() {
            d.last = ndc;
        }
        self.status = format!("{} {} vertex(es)", tool.label(), verts.len());
        true
    }

    pub fn affected_count(&self) -> usize {
        transform::affected(&self.mesh, &self.selection).len()
    }

    /// Run the stack and hand the result to the viewport.
    fn rebuild_display(&mut self) {
        self.display = self.stack.evaluate(&self.mesh, self.base_version);
        self.stack_dirty = false;
        self.dirty = true;
    }

    pub fn collapse_stack(&mut self) {
        self.history.push("Collapse stack", &self.mesh, &self.selection);
        self.stack.collapse(&mut self.mesh, self.base_version);
        self.base_version += 1;
        self.selection.prune(&self.mesh);
        self.status = "stack collapsed into the mesh".into();
        self.rebuild_display();
    }

    pub fn apply_type_in(&mut self, tool: Tool) {
        let verts = transform::affected(&self.mesh, &self.selection);
        if verts.is_empty() {
            self.status = "nothing selected".into();
            return;
        }
        let label = tool.label().to_string();
        let (mv, rot, scl) = (self.type_in_move, self.type_in_rotate, self.type_in_scale);
        let pivot_mode = self.pivot;
        self.edit(&label, |m| {
            let pivot = transform::pivot_point(m, &verts, pivot_mode);
            match tool {
                Tool::Move => {
                    transform::translate(m, &verts, glam::Vec3::from(mv));
                    format!("moved {} vertex(es)", verts.len())
                }
                Tool::Rotate => {
                    for (axis, degrees) in [
                        (glam::Vec3::X, rot[0]),
                        (glam::Vec3::Y, rot[1]),
                        (glam::Vec3::Z, rot[2]),
                    ] {
                        if degrees != 0.0 {
                            transform::rotate(m, &verts, pivot, axis, degrees.to_radians());
                        }
                    }
                    format!("rotated {} vertex(es)", verts.len())
                }
                Tool::Scale => {
                    let f = glam::Vec3::new(scl[0], scl[1], scl[2]) / 100.0;
                    transform::scale(m, &verts, pivot, f);
                    format!("scaled {} vertex(es)", verts.len())
                }
                Tool::Select => String::new(),
            }
        });
    }

    fn set_level(&mut self, level: Level) {
        self.selection.convert(&self.mesh, level);
        self.status = self.selection.summary();
        self.dirty = true;
    }

    /// Record the state, then run the edit. Everything that changes the mesh
    /// goes through here, so nothing can quietly become un-undoable.
    fn edit(&mut self, label: &str, f: impl FnOnce(&mut Mesh) -> String) {
        self.history.push(label, &self.mesh, &self.selection);
        self.status = f(&mut self.mesh);
        // Topology may have changed under the selection.
        self.selection.prune(&self.mesh);
        // The stack's caches are keyed on this; without the bump they would
        // keep serving a mesh built from the shape before the edit.
        self.base_version += 1;
        transform::finish(&mut self.mesh);
        self.rebuild_display();
    }

    /// Which vertices of the *displayed* mesh to tint.
    ///
    /// Selection lives on the base mesh, which is what you edit. With a stack
    /// running, the displayed vertices are different ones entirely and there
    /// is no honest mapping back, so nothing is highlighted rather than
    /// highlighting the wrong things. Max solves this by letting you edit at
    /// the base level of the stack; that comes later.
    pub fn selected_mask(&self) -> Vec<bool> {
        let mut mask = vec![false; self.display.vertex_count()];
        if self.selection.level != Level::Object && self.stack.is_empty() {
            for v in self.selection.to_vertices(&self.mesh) {
                if let Some(slot) = mask.get_mut(v as usize) {
                    *slot = true;
                }
            }
        }
        mask
    }

    fn click(&mut self, ndc: glam::Vec2, aspect: f32, add: bool) {
        const RADIUS: f32 = 0.03;
        if !add {
            self.selection.clear();
        }
        match self.selection.level {
            Level::Object => return,
            Level::Vertex | Level::Element => {
                if let Some(v) = pick::pick_vertex(&self.mesh, &self.camera, aspect, ndc, RADIUS) {
                    if self.selection.level == Level::Element {
                        let mut one = Selection::new(Level::Vertex);
                        one.add(v);
                        one.convert(&self.mesh, Level::Element);
                        self.selection.items.extend(one.items);
                    } else {
                        self.selection.toggle(v);
                    }
                }
            }
            Level::Edge => {
                if let Some(e) = pick::pick_edge(&self.mesh, &self.camera, aspect, ndc, RADIUS) {
                    self.selection.toggle_edge(e);
                }
            }
            Level::Face => {
                let ray = self.camera.ray_through(ndc, aspect);
                if let Some(hit) = pick::raycast(&self.mesh, &ray) {
                    self.selection.toggle(hit.tri as u32);
                }
            }
        }
        self.status = self.selection.summary();
        self.dirty = true;
    }

    fn replace_mesh(&mut self, mesh: Mesh, aspect: f32) {
        self.mesh = mesh;
        self.base_version += 1;
        self.rebuild_display();
        if let Some(b) = self.display.bounds() {
            self.camera.frame(b, aspect);
        }
    }

    fn new_primitive(&mut self, mesh: Mesh, aspect: f32) {
        // A new model starts with a clean history: undoing into the previous
        // one would bring back a mesh this document no longer has.
        self.history.clear();
        self.selection.clear();
        self.stack.entries.clear();
        self.base_version += 1;
        // Forget where the last file came from, or Save would quietly
        // overwrite it with a brand new cube.
        self.path = None;
        self.status = format!("new {}", mesh.name.to_lowercase());
        self.replace_mesh(mesh, aspect);
    }

    fn keys(&mut self, ctx: &egui::Context, _aspect: f32) {
        // The dialog cannot be opened from inside ctx.input(): it blocks, and
        // the input lock would still be held when egui next asks for it.
        let mut open_requested = false;
        let mut save_requested = false;
        let mut undo_requested = false;
        let mut redo_requested = false;
        let mut select_all = false;
        let mut invert = false;
        let mut level_change = None;
        let mut tool_change = None;
        let mut frame_requested = false;
        ctx.input(|i| {
            use egui::Key;
            if i.modifiers.ctrl && i.key_pressed(Key::O) {
                open_requested = true;
            }
            if i.modifiers.ctrl && i.key_pressed(Key::S) {
                save_requested = true;
            }
            if i.key_pressed(Key::W) {
                tool_change = Some(Tool::Move);
            }
            if i.key_pressed(Key::E) {
                tool_change = Some(Tool::Rotate);
            }
            if i.key_pressed(Key::R) {
                tool_change = Some(Tool::Scale);
            }
            if i.key_pressed(Key::Q) {
                tool_change = Some(Tool::Select);
            }
            if i.key_pressed(Key::F3) {
                // Max: wireframe only.
                self.shaded = !self.shaded;
                if !self.shaded {
                    self.wireframe = true;
                }
            }
            if i.key_pressed(Key::F4) {
                self.wireframe = !self.wireframe;
                self.shaded = true;
            }
            if i.key_pressed(Key::Z) {
                frame_requested = true;
            }
            for (key, level) in [
                (Key::Num1, Level::Vertex),
                (Key::Num2, Level::Edge),
                (Key::Num3, Level::Face),
                (Key::Num4, Level::Element),
            ] {
                if i.key_pressed(key) {
                    // Pressing the level you are in leaves it, as Max does.
                    level_change = Some(if self.selection.level == level {
                        Level::Object
                    } else {
                        level
                    });
                }
            }
            if i.modifiers.ctrl && i.key_pressed(Key::Z) {
                undo_requested = true;
            }
            if i.modifiers.ctrl && (i.key_pressed(Key::Y)
                || (i.modifiers.shift && i.key_pressed(Key::Z)))
            {
                redo_requested = true;
            }
            if i.modifiers.ctrl && i.key_pressed(Key::A) {
                select_all = true;
            }
            if i.modifiers.ctrl && i.key_pressed(Key::I) {
                invert = true;
            }
        });
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        let aspect = {
            let r = ctx.screen_rect();
            (r.width() / r.height().max(1.0)).max(0.1)
        };
        self.keys(ctx, aspect);

        egui::TopBottomPanel::top("menu").show(ctx, |ui| {
            egui::menu::bar(ui, |ui| {
                ui.menu_button("File", |ui| {
                    if ui.button("Open…").clicked() {
                        self.open(aspect);
                        ui.close_menu();
                    }
                    let can_save = self.path.is_some();
                    if ui.add_enabled(can_save, egui::Button::new("Save")).clicked() {
                        if let Some(p) = self.path.clone() {
                            self.save_to(p);
                        }
                        ui.close_menu();
                    }
                    if ui.button("Save as…").clicked() {
                        self.save_as();
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.button("New cube").clicked() {
                        self.new_primitive(primitives::cube(2.0), aspect);
                        ui.close_menu();
                    }
                    if ui.button("New sphere").clicked() {
                        self.new_primitive(primitives::sphere(1.0, 32, 24), aspect);
                        ui.close_menu();
                    }
                    if ui.button("New plane").clicked() {
                        self.new_primitive(primitives::plane(4.0), aspect);
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.button("Quit").clicked() {
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                });
                ui.menu_button("Edit", |ui| {
                    let undo = self.history.undo_label().map(|l| format!("Undo {l}"));
                    let redo = self.history.redo_label().map(|l| format!("Redo {l}"));
                    if ui
                        .add_enabled(
                            undo.is_some(),
                            egui::Button::new(undo.as_deref().unwrap_or("Undo")),
                        )
                        .clicked()
                    {
                        self.history.undo(&mut self.mesh, &mut self.selection);
                        self.dirty = true;
                        ui.close_menu();
                    }
                    if ui
                        .add_enabled(
                            redo.is_some(),
                            egui::Button::new(redo.as_deref().unwrap_or("Redo")),
                        )
                        .clicked()
                    {
                        self.history.redo(&mut self.mesh, &mut self.selection);
                        self.dirty = true;
                        ui.close_menu();
                    }
                });
                ui.menu_button("Selection", |ui| {
                    let sub = self.selection.level != Level::Object;
                    if ui.add_enabled(sub, egui::Button::new("Select all")).clicked() {
                        self.selection.select_all(&self.mesh);
                        self.dirty = true;
                        ui.close_menu();
                    }
                    if ui.add_enabled(sub, egui::Button::new("Select none")).clicked() {
                        self.selection.clear();
                        self.dirty = true;
                        ui.close_menu();
                    }
                    if ui.add_enabled(sub, egui::Button::new("Invert")).clicked() {
                        self.selection.invert(&self.mesh);
                        self.dirty = true;
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.add_enabled(sub, egui::Button::new("Grow")).clicked() {
                        self.selection.grow(&self.mesh);
                        self.dirty = true;
                        ui.close_menu();
                    }
                    if ui.add_enabled(sub, egui::Button::new("Shrink")).clicked() {
                        self.selection.shrink(&self.mesh);
                        self.dirty = true;
                        ui.close_menu();
                    }
                });
                ui.menu_button("Mesh", |ui| {
                    if ui.button("Fix normals").clicked() {
                        self.edit("Fix normals", |m| normals::orient(m).summary());
                        ui.close_menu();
                    }
                    if ui.button("Recompute normals").clicked() {
                        self.edit("Recompute normals", |m| {
                            normals::recompute_vertex_normals(m);
                            "normals recomputed".into()
                        });
                        ui.close_menu();
                    }
                    if ui.button("Remove unused vertices").clicked() {
                        self.edit("Remove unused vertices", |m| {
                            format!("removed {} unused vertex(es)", m.compact())
                        });
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.button("Flip all normals").clicked() {
                        self.edit("Flip all normals", |m| {
                            for t in 0..m.tri_count() {
                                m.indices.swap(t * 3 + 1, t * 3 + 2);
                            }
                            normals::recompute_vertex_normals(m);
                            "flipped every face (now try Fix normals)".into()
                        });
                        ui.close_menu();
                    }
                });
                ui.menu_button("View", |ui| {
                    ui.checkbox(&mut self.shaded, "Shaded");
                    ui.checkbox(&mut self.wireframe, "Wireframe (F4)");
                    ui.checkbox(&mut self.grid, "Grid");
                });
                ui.separator();
                for level in [Level::Vertex, Level::Edge, Level::Face, Level::Element] {
                    let on = self.selection.level == level;
                    if ui.selectable_label(on, level.label()).clicked() {
                        self.set_level(if on { Level::Object } else { level });
                    }
                }
            });
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(&self.status);
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.label(format!(
                        "{} · {} verts · {} tris{} · {} · {} undo",
                        self.tool.label(),
                        self.display.vertex_count(),
                        self.display.tri_count(),
                        if self.stack.is_empty() {
                            String::new()
                        } else {
                            format!(" (base {})", self.mesh.tri_count())
                        },
                        self.selection.summary(),
                        self.history.depth(),
                    ));
                });
            });
        });

        panel::show(ctx, self, aspect);
        if self.stack_dirty {
            self.rebuild_display();
        }

        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(egui::Color32::from_rgb(38, 38, 42)))
            .show(ctx, |ui| {
                viewport::show(ui, self, frame);
            });
    }
}

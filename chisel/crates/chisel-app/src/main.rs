//! Chisel — a lightweight 3D editor.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod renderer;
mod viewport;

use eframe::egui;
use mesh_core::{normals, primitives, Camera, Mesh};

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

/// 3ds Max sub-object levels.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SubObject {
    Object,
    Vertex,
    Edge,
    Border,
    Polygon,
    Element,
}

impl SubObject {
    fn label(self) -> &'static str {
        match self {
            Self::Object => "Object",
            Self::Vertex => "Vertex",
            Self::Edge => "Edge",
            Self::Border => "Border",
            Self::Polygon => "Polygon",
            Self::Element => "Element",
        }
    }
}

pub struct App {
    mesh: Mesh,
    camera: Camera,
    sub_object: SubObject,
    shaded: bool,
    wireframe: bool,
    grid: bool,
    /// Last thing an operation reported, shown in the status bar. The point of
    /// orient() is what it tells you, so it needs somewhere to say it.
    status: String,
    dirty: bool,
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let mesh = primitives::cube(2.0);
        let wgpu = cc.wgpu_render_state.as_ref().expect("wgpu backend");
        let mut r = renderer::Renderer::new(&wgpu.device, wgpu.target_format);
        r.set_mesh(&wgpu.device, &mesh);
        wgpu.renderer.write().callback_resources.insert(r);

        let mut camera = Camera::default();
        if let Some(b) = mesh.bounds() {
            camera.frame(b, 16.0 / 9.0);
        }
        Self {
            mesh,
            camera,
            sub_object: SubObject::Object,
            shaded: true,
            wireframe: false,
            grid: true,
            status: "Ready".into(),
            dirty: false,
        }
    }

    fn replace_mesh(&mut self, mesh: Mesh, aspect: f32) {
        self.mesh = mesh;
        if let Some(b) = self.mesh.bounds() {
            self.camera.frame(b, aspect);
        }
        self.dirty = true;
    }

    fn keys(&mut self, ctx: &egui::Context, aspect: f32) {
        ctx.input(|i| {
            use egui::Key;
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
                if let Some(b) = self.mesh.bounds() {
                    self.camera.frame(b, aspect);
                }
            }
            for (key, level) in [
                (Key::Num1, SubObject::Vertex),
                (Key::Num2, SubObject::Edge),
                (Key::Num3, SubObject::Border),
                (Key::Num4, SubObject::Polygon),
                (Key::Num5, SubObject::Element),
            ] {
                if i.key_pressed(key) {
                    // Pressing the level you are in leaves it, as Max does.
                    self.sub_object =
                        if self.sub_object == level { SubObject::Object } else { level };
                }
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
                    if ui.button("New cube").clicked() {
                        self.replace_mesh(primitives::cube(2.0), aspect);
                        ui.close_menu();
                    }
                    if ui.button("New sphere").clicked() {
                        self.replace_mesh(primitives::sphere(1.0, 32, 24), aspect);
                        ui.close_menu();
                    }
                    if ui.button("New plane").clicked() {
                        self.replace_mesh(primitives::plane(4.0), aspect);
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.button("Quit").clicked() {
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                });
                ui.menu_button("Mesh", |ui| {
                    if ui.button("Fix normals").clicked() {
                        let report = normals::orient(&mut self.mesh);
                        self.status = report.summary();
                        self.dirty = true;
                        ui.close_menu();
                    }
                    if ui.button("Recompute normals").clicked() {
                        normals::recompute_vertex_normals(&mut self.mesh);
                        self.status = "normals recomputed".into();
                        self.dirty = true;
                        ui.close_menu();
                    }
                    if ui.button("Remove unused vertices").clicked() {
                        let n = self.mesh.compact();
                        self.status = format!("removed {n} unused vertex(es)");
                        self.dirty = true;
                        ui.close_menu();
                    }
                    ui.separator();
                    if ui.button("Flip all normals").clicked() {
                        for t in 0..self.mesh.tri_count() {
                            self.mesh.indices.swap(t * 3 + 1, t * 3 + 2);
                        }
                        normals::recompute_vertex_normals(&mut self.mesh);
                        self.status = "flipped every face (now try Fix normals)".into();
                        self.dirty = true;
                        ui.close_menu();
                    }
                });
                ui.menu_button("View", |ui| {
                    ui.checkbox(&mut self.shaded, "Shaded");
                    ui.checkbox(&mut self.wireframe, "Wireframe (F4)");
                    ui.checkbox(&mut self.grid, "Grid");
                });
                ui.separator();
                for level in [
                    SubObject::Vertex,
                    SubObject::Edge,
                    SubObject::Border,
                    SubObject::Polygon,
                    SubObject::Element,
                ] {
                    let on = self.sub_object == level;
                    if ui.selectable_label(on, level.label()).clicked() {
                        self.sub_object = if on { SubObject::Object } else { level };
                    }
                }
            });
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(&self.status);
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.label(format!(
                        "{} verts · {} tris · {}",
                        self.mesh.vertex_count(),
                        self.mesh.tri_count(),
                        self.sub_object.label()
                    ));
                });
            });
        });

        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(egui::Color32::from_rgb(38, 38, 42)))
            .show(ctx, |ui| {
                viewport::show(ui, self, frame);
            });
    }
}

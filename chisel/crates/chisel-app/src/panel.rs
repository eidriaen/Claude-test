//! The command panel: Max's right-hand column, trimmed to what exists.
//!
//! Create makes primitives with their parameters still editable, Modify holds
//! the stack, and the transform type-ins are always visible because typing an
//! exact number is half of why anyone opens a modelling tool.

use crate::App;
use eframe::egui;
use mesh_core::modifier::{FixNormals, Smooth, Subdivide};
use mesh_core::primitives;
use mesh_core::transform::{Pivot, Tool};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Create,
    Modify,
}

/// Parameters for the primitive being created, kept so they can be adjusted
/// without starting again.
pub struct CreateParams {
    pub kind: usize,
    pub size: f32,
    pub height: f32,
    pub radius: f32,
    pub segments: u32,
    pub sides: u32,
    pub caps: bool,
}

impl Default for CreateParams {
    fn default() -> Self {
        Self {
            kind: 0,
            size: 2.0,
            height: 2.0,
            radius: 1.0,
            segments: 16,
            sides: 24,
            caps: true,
        }
    }
}

pub const PRIMITIVES: &[&str] =
    &["Box", "Sphere", "Cylinder", "Cone", "Torus", "Plane"];

impl CreateParams {
    pub fn build(&self) -> mesh_core::Mesh {
        match self.kind {
            0 => primitives::box_mesh(
                self.size, self.size, self.size,
                self.segments.max(1), self.segments.max(1), self.segments.max(1),
            ),
            1 => primitives::sphere(self.radius, self.sides, self.sides / 2 + 2),
            2 => primitives::cylinder(self.radius, self.height, self.sides, self.caps),
            3 => primitives::cone(self.radius, 0.0, self.height, self.sides),
            4 => primitives::torus(self.radius, self.radius * 0.35, self.segments, self.sides),
            _ => primitives::grid(self.size, self.size, self.segments, self.segments),
        }
    }
}

pub fn show(ctx: &egui::Context, app: &mut App, aspect: f32) {
    egui::SidePanel::right("command")
        .resizable(true)
        .default_width(260.0)
        .show(ctx, |ui| {
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.selectable_value(&mut app.tab, Tab::Create, "Create");
                ui.selectable_value(&mut app.tab, Tab::Modify, "Modify");
            });
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| match app.tab {
                Tab::Create => create(ui, app, aspect),
                Tab::Modify => modify(ui, app),
            });
        });
}

fn create(ui: &mut egui::Ui, app: &mut App, aspect: f32) {
    ui.label(egui::RichText::new("Object type").strong());
    egui::Grid::new("prims").num_columns(2).spacing([4.0, 4.0]).show(ui, |ui| {
        for (i, name) in PRIMITIVES.iter().enumerate() {
            if ui.selectable_label(app.create.kind == i, *name).clicked() {
                app.create.kind = i;
            }
            if i % 2 == 1 {
                ui.end_row();
            }
        }
    });

    ui.add_space(8.0);
    ui.label(egui::RichText::new("Parameters").strong());
    let p = &mut app.create;
    match p.kind {
        0 | 5 => {
            ui.add(egui::Slider::new(&mut p.size, 0.1..=50.0).text("Size"));
            ui.add(egui::Slider::new(&mut p.segments, 1..=64).text("Segments"));
        }
        1 => {
            ui.add(egui::Slider::new(&mut p.radius, 0.1..=50.0).text("Radius"));
            ui.add(egui::Slider::new(&mut p.sides, 3..=128).text("Segments"));
        }
        2 | 3 => {
            ui.add(egui::Slider::new(&mut p.radius, 0.1..=50.0).text("Radius"));
            ui.add(egui::Slider::new(&mut p.height, 0.1..=50.0).text("Height"));
            ui.add(egui::Slider::new(&mut p.sides, 3..=128).text("Sides"));
            if p.kind == 2 {
                ui.checkbox(&mut p.caps, "Capped");
            }
        }
        _ => {
            ui.add(egui::Slider::new(&mut p.radius, 0.1..=50.0).text("Radius"));
            ui.add(egui::Slider::new(&mut p.segments, 3..=64).text("Segments"));
            ui.add(egui::Slider::new(&mut p.sides, 3..=64).text("Sides"));
        }
    }

    ui.add_space(8.0);
    if ui
        .add_sized([ui.available_width(), 28.0], egui::Button::new("Create"))
        .clicked()
    {
        let mesh = app.create.build();
        app.new_primitive(mesh, aspect);
    }
}

fn modify(ui: &mut egui::Ui, app: &mut App) {
    ui.label(egui::RichText::new("Modifier stack").strong());
    ui.label(
        egui::RichText::new("Top of the list runs last, as in Max.")
            .small()
            .weak(),
    );
    ui.add_space(4.0);

    let mut remove = None;
    let mut move_up = None;
    let mut move_down = None;
    let mut toggle = None;
    let count = app.stack.entries.len();

    // Shown top-down, evaluated bottom-up.
    for ui_index in 0..count {
        let i = count - 1 - ui_index;
        let entry = &app.stack.entries[i];
        let (name, enabled) = (entry.modifier.name(), entry.enabled);
        ui.horizontal(|ui| {
            let mut on = enabled;
            if ui.checkbox(&mut on, "").changed() {
                toggle = Some((i, on));
            }
            ui.label(if enabled {
                egui::RichText::new(&name)
            } else {
                egui::RichText::new(&name).weak().strikethrough()
            });
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.small_button("✕").on_hover_text("Remove").clicked() {
                    remove = Some(i);
                }
                if ui
                    .add_enabled(i + 1 < count, egui::Button::new("▲").small())
                    .clicked()
                {
                    move_up = Some(i);
                }
                if ui.add_enabled(i > 0, egui::Button::new("▼").small()).clicked() {
                    move_down = Some(i);
                }
            });
        });
    }
    if count == 0 {
        ui.label(egui::RichText::new("empty — the base mesh as built").weak());
    }

    if let Some((i, on)) = toggle {
        app.stack.set_enabled(i, on);
        app.stack_dirty = true;
    }
    if let Some(i) = remove {
        app.stack.remove(i);
        app.stack_dirty = true;
    }
    if let Some(i) = move_up {
        app.stack.move_entry(i, i + 1);
        app.stack_dirty = true;
    }
    if let Some(i) = move_down {
        app.stack.move_entry(i, i - 1);
        app.stack_dirty = true;
    }

    ui.add_space(6.0);
    ui.menu_button("Add modifier ▾", |ui| {
        if ui.button("Subdivide").clicked() {
            app.stack.push(Subdivide { iterations: 1 });
            app.stack_dirty = true;
            ui.close_menu();
        }
        if ui.button("Smooth").clicked() {
            app.stack.push(Smooth { iterations: 3, strength: 0.5 });
            app.stack_dirty = true;
            ui.close_menu();
        }
        if ui.button("Fix normals").clicked() {
            app.stack.push(FixNormals);
            app.stack_dirty = true;
            ui.close_menu();
        }
    });
    if ui
        .add_enabled(count > 0, egui::Button::new("Collapse stack"))
        .on_hover_text("Bake the result into the mesh. Not undoable by removing modifiers.")
        .clicked()
    {
        app.collapse_stack();
    }

    ui.add_space(12.0);
    ui.separator();
    transform_fields(ui, app);
}

fn transform_fields(ui: &mut egui::Ui, app: &mut App) {
    ui.label(egui::RichText::new("Transform").strong());
    ui.horizontal(|ui| {
        for tool in [Tool::Select, Tool::Move, Tool::Rotate, Tool::Scale] {
            if ui
                .selectable_label(app.tool == tool, tool.label())
                .clicked()
            {
                app.tool = tool;
            }
        }
    });
    ui.horizontal(|ui| {
        ui.label("Pivot");
        ui.selectable_value(&mut app.pivot, Pivot::SelectionCentre, "Selection");
        ui.selectable_value(&mut app.pivot, Pivot::Origin, "Origin");
    });

    ui.add_space(6.0);
    let n = app.affected_count();
    ui.label(
        egui::RichText::new(if n == 0 {
            "nothing selected".to_string()
        } else {
            format!("{n} vertex(es) affected")
        })
        .small()
        .weak(),
    );

    // Type-in: an amount to apply, not an absolute position. A sub-object
    // selection has no single "position" to show, and pretending otherwise
    // means deciding what to do when you type into it.
    ui.add_space(4.0);
    ui.label("Apply");
    let mut apply = None;
    egui::Grid::new("xform").num_columns(4).show(ui, |ui| {
        ui.label("");
        ui.label("X");
        ui.label("Y");
        ui.label("Z");
        ui.end_row();

        ui.label("Move");
        for i in 0..3 {
            ui.add(egui::DragValue::new(&mut app.type_in_move[i]).speed(0.05));
        }
        ui.end_row();

        ui.label("Rotate°");
        for i in 0..3 {
            ui.add(egui::DragValue::new(&mut app.type_in_rotate[i]).speed(1.0));
        }
        ui.end_row();

        ui.label("Scale %");
        for i in 0..3 {
            ui.add(egui::DragValue::new(&mut app.type_in_scale[i]).speed(1.0));
        }
        ui.end_row();
    });
    ui.horizontal(|ui| {
        if ui.add_enabled(n > 0, egui::Button::new("Move")).clicked() {
            apply = Some(Tool::Move);
        }
        if ui.add_enabled(n > 0, egui::Button::new("Rotate")).clicked() {
            apply = Some(Tool::Rotate);
        }
        if ui.add_enabled(n > 0, egui::Button::new("Scale")).clicked() {
            apply = Some(Tool::Scale);
        }
    });
    if let Some(tool) = apply {
        app.apply_type_in(tool);
    }
}

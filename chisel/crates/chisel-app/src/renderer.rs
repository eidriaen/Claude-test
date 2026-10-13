//! The wgpu side of the viewport.
//!
//! Lives behind an egui paint callback, so the 3D view is a widget inside the
//! normal UI rather than a separate window the UI has to dodge around.

use bytemuck::{Pod, Zeroable};
use glam::{Mat4, Vec3};
use mesh_core::Mesh;
use wgpu::util::DeviceExt;

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Uniforms {
    view_proj: [[f32; 4]; 4],
    eye: [f32; 4],
    flags: [f32; 4],
}

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Vertex {
    pos: [f32; 3],
    nrm: [f32; 3],
}

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct GridVertex {
    pos: [f32; 3],
    shade: f32,
}

pub struct Renderer {
    mesh_pipeline: wgpu::RenderPipeline,
    wire_pipeline: wgpu::RenderPipeline,
    grid_pipeline: wgpu::RenderPipeline,
    uniform_buf: wgpu::Buffer,
    bind_group: wgpu::BindGroup,

    vertices: wgpu::Buffer,
    indices: wgpu::Buffer,
    index_count: u32,
    /// Line-list indices for wire-on-shaded.
    wire_indices: wgpu::Buffer,
    wire_count: u32,

    grid: wgpu::Buffer,
    grid_count: u32,
}

impl Renderer {
    pub fn new(device: &wgpu::Device, format: wgpu::TextureFormat) -> Self {
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("viewport"),
            source: wgpu::ShaderSource::Wgsl(include_str!("shader.wgsl").into()),
        });

        let uniform_buf = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("uniforms"),
            size: std::mem::size_of::<Uniforms>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: None,
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }],
        });
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: uniform_buf.as_entire_binding(),
            }],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: None,
            bind_group_layouts: &[&layout],
            push_constant_ranges: &[],
        });

        let depth_state = Some(wgpu::DepthStencilState {
            format: wgpu::TextureFormat::Depth32Float,
            depth_write_enabled: true,
            depth_compare: wgpu::CompareFunction::Less,
            stencil: Default::default(),
            bias: Default::default(),
        });

        let mesh_layout = wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<Vertex>() as u64,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3],
        };
        let grid_layout = wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<GridVertex>() as u64,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32],
        };

        let make = |label: &str,
                    vs: &str,
                    fs: &str,
                    buffers: &[wgpu::VertexBufferLayout],
                    topology: wgpu::PrimitiveTopology,
                    bias: wgpu::DepthBiasState| {
            device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
                label: Some(label),
                layout: Some(&pipeline_layout),
                vertex: wgpu::VertexState {
                    module: &shader,
                    entry_point: vs,
                    buffers,
                    compilation_options: Default::default(),
                },
                fragment: Some(wgpu::FragmentState {
                    module: &shader,
                    entry_point: fs,
                    targets: &[Some(format.into())],
                    compilation_options: Default::default(),
                }),
                primitive: wgpu::PrimitiveState {
                    topology,
                    // No back-face culling. Half the point of this app is
                    // finding faces that point the wrong way, and culling them
                    // hides exactly what you are looking for.
                    cull_mode: None,
                    ..Default::default()
                },
                depth_stencil: depth_state.clone().map(|mut d| {
                    d.bias = bias;
                    d
                }),
                multisample: Default::default(),
                multiview: None,
                cache: None,
            })
        };

        // Wireframe sits slightly in front of the shaded surface, or it fights
        // with it and stipples as the camera moves.
        let wire_bias = wgpu::DepthBiasState { constant: -2, slope_scale: -1.0, clamp: 0.0 };

        let mesh_pipeline = make("mesh", "vs_main", "fs_main", std::slice::from_ref(&mesh_layout),
                                 wgpu::PrimitiveTopology::TriangleList, Default::default());
        let wire_pipeline = make("wire", "vs_main", "fs_main", &[mesh_layout],
                                 wgpu::PrimitiveTopology::LineList, wire_bias);
        let grid_pipeline = make("grid", "grid_vs", "grid_fs", &[grid_layout],
                                 wgpu::PrimitiveTopology::LineList, Default::default());

        let (grid, grid_count) = build_grid(device, 20, 1.0);
        let empty = |label| {
            device.create_buffer(&wgpu::BufferDescriptor {
                label: Some(label),
                size: 4,
                usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::INDEX,
                mapped_at_creation: false,
            })
        };

        Self {
            mesh_pipeline,
            wire_pipeline,
            grid_pipeline,
            uniform_buf,
            bind_group,
            vertices: empty("vertices"),
            indices: empty("indices"),
            index_count: 0,
            wire_indices: empty("wire"),
            wire_count: 0,
            grid,
            grid_count,
        }
    }

    /// Upload a mesh. Called when the mesh changes, not every frame.
    pub fn set_mesh(&mut self, device: &wgpu::Device, mesh: &Mesh) {
        let normals_ok = mesh.has_normals();
        let verts: Vec<Vertex> = mesh
            .positions
            .iter()
            .enumerate()
            .map(|(i, p)| Vertex {
                pos: p.to_array(),
                nrm: if normals_ok { mesh.normals[i].to_array() } else { [0.0, 1.0, 0.0] },
            })
            .collect();

        self.vertices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("vertices"),
            contents: bytemuck::cast_slice(&verts),
            usage: wgpu::BufferUsages::VERTEX,
        });
        self.indices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("indices"),
            contents: bytemuck::cast_slice(&mesh.indices),
            usage: wgpu::BufferUsages::INDEX,
        });
        self.index_count = mesh.indices.len() as u32;

        // Each triangle's three edges as lines. Shared edges are drawn twice;
        // de-duplicating them costs more than the lines do.
        let mut wire = Vec::with_capacity(mesh.indices.len() * 2);
        for t in 0..mesh.tri_count() {
            let [a, b, c] = mesh.tri(t);
            wire.extend_from_slice(&[a, b, b, c, c, a]);
        }
        self.wire_indices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("wire"),
            contents: bytemuck::cast_slice(&wire),
            usage: wgpu::BufferUsages::INDEX,
        });
        self.wire_count = wire.len() as u32;
    }

    pub fn prepare(&mut self, queue: &wgpu::Queue, view_proj: Mat4, eye: Vec3, wire: bool) {
        let u = Uniforms {
            view_proj: view_proj.to_cols_array_2d(),
            eye: [eye.x, eye.y, eye.z, 0.0],
            flags: [if wire { 1.0 } else { 0.0 }, 0.0, 0.0, 0.0],
        };
        queue.write_buffer(&self.uniform_buf, 0, bytemuck::bytes_of(&u));
    }

    pub fn paint(&self, pass: &mut wgpu::RenderPass<'static>, show_grid: bool,
                 shaded: bool, wire: bool) {
        pass.set_bind_group(0, &self.bind_group, &[]);
        if show_grid && self.grid_count > 0 {
            pass.set_pipeline(&self.grid_pipeline);
            pass.set_vertex_buffer(0, self.grid.slice(..));
            pass.draw(0..self.grid_count, 0..1);
        }
        if self.index_count == 0 {
            return;
        }
        pass.set_vertex_buffer(0, self.vertices.slice(..));
        if shaded {
            pass.set_pipeline(&self.mesh_pipeline);
            pass.set_index_buffer(self.indices.slice(..), wgpu::IndexFormat::Uint32);
            pass.draw_indexed(0..self.index_count, 0, 0..1);
        }
        if wire {
            pass.set_pipeline(&self.wire_pipeline);
            pass.set_index_buffer(self.wire_indices.slice(..), wgpu::IndexFormat::Uint32);
            pass.draw_indexed(0..self.wire_count, 0, 0..1);
        }
    }

}

/// Home grid: a line per unit, brighter on every tenth and on the axes.
fn build_grid(device: &wgpu::Device, half: i32, step: f32) -> (wgpu::Buffer, u32) {
    let mut v: Vec<GridVertex> = Vec::new();
    let extent = half as f32 * step;
    for i in -half..=half {
        let t = i as f32 * step;
        let shade = if i == 0 { 0.45 } else if i % 10 == 0 { 0.30 } else { 0.22 };
        v.push(GridVertex { pos: [-extent, 0.0, t], shade });
        v.push(GridVertex { pos: [extent, 0.0, t], shade });
        v.push(GridVertex { pos: [t, 0.0, -extent], shade });
        v.push(GridVertex { pos: [t, 0.0, extent], shade });
    }
    let buf = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("grid"),
        contents: bytemuck::cast_slice(&v),
        usage: wgpu::BufferUsages::VERTEX,
    });
    (buf, v.len() as u32)
}

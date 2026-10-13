// Lambert plus a little ambient, lit from the camera. Enough to read form
// while modelling; a material system is a later phase.

struct Uniforms {
    view_proj: mat4x4<f32>,
    eye: vec4<f32>,
    // x: 1 when drawing wireframe (flat colour), y: unused
    flags: vec4<f32>,
};

@group(0) @binding(0) var<uniform> u: Uniforms;

struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) world: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) selected: f32,
};

@vertex
fn vs_main(@location(0) pos: vec3<f32>, @location(1) nrm: vec3<f32>,
           @location(2) sel: f32) -> VsOut {
    var out: VsOut;
    out.clip = u.view_proj * vec4<f32>(pos, 1.0);
    out.world = pos;
    out.normal = nrm;
    out.selected = sel;
    return out;
}

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4<f32> {
    if (u.flags.x > 0.5) {
        return vec4<f32>(0.05, 0.05, 0.06, 1.0);
    }
    let n = normalize(in.normal);
    let to_eye = normalize(u.eye.xyz - in.world);
    // Two-sided: a back face lit as if it faced you reads as form, not as a
    // black hole. Normals being wrong is a thing this app fixes, not a thing
    // the viewport should punish you for mid-edit.
    let lambert = abs(dot(n, to_eye));
    let shade = 0.18 + 0.82 * lambert;
    // 3ds Max's selection red, mixed rather than replaced so the form still
    // reads under it.
    let base = mix(vec3<f32>(0.62, 0.64, 0.68), vec3<f32>(0.90, 0.35, 0.28), in.selected);
    return vec4<f32>(base * shade, 1.0);
}

// --- grid ------------------------------------------------------------------

struct LineOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) colour: vec3<f32>,
};

@vertex
fn line_vs(@location(0) pos: vec3<f32>, @location(1) colour: vec3<f32>) -> LineOut {
    var out: LineOut;
    out.clip = u.view_proj * vec4<f32>(pos, 1.0);
    out.colour = colour;
    return out;
}

@fragment
fn line_fs(in: LineOut) -> @location(0) vec4<f32> {
    return vec4<f32>(in.colour, 1.0);
}

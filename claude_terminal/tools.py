# SPDX-License-Identifier: GPL-3.0-or-later
"""Commands the bridge runs inside Blender (always on the main thread)."""

import base64
import contextlib
import io
import math
import os
import sys
import tempfile
import traceback

import bmesh
import bpy
import mathutils

MAX_OUTPUT = 30000
_namespace = None


def _round(values, digits=4):
    return [round(float(v), digits) for v in values]


def _view3d_context():
    """Context override for the biggest 3D Viewport, so operators behave as
    if they were run from it."""
    wm = bpy.context.window_manager
    best = None
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            size = area.width * area.height
            if best is None or size > best[0]:
                best = (size, win, area, region)
    if best:
        _size, win, area, region = best
        return {"window": win, "screen": win.screen, "area": area, "region": region}
    if wm.windows:
        win = wm.windows[0]
        return {"window": win, "screen": win.screen}
    return {}


def _undo_push(message, override):
    try:
        with bpy.context.temp_override(**override):
            bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


def _jsonable(value, depth=0):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if depth > 4:
        return repr(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:500]}
    if isinstance(value, (list, tuple, set)) or hasattr(value, "to_tuple"):
        seq = value.to_tuple() if hasattr(value, "to_tuple") else value
        return [_jsonable(v, depth + 1) for v in list(seq)[:1000]]
    if isinstance(value, bpy.types.ID):
        return "%s(%r)" % (type(value).__name__, value.name)
    return repr(value)


def _truncate(text):
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT // 2] + "\n... [%d characters cut] ...\n" % (len(text) - MAX_OUTPUT) \
        + text[-MAX_OUTPUT // 2:]


# -- commands ----------------------------------------------------------------


def hello(params):
    return {
        "blender": bpy.app.version_string,
        "file": bpy.data.filepath or None,
        "pid": os.getpid(),
    }


def get_scene_info(params):
    limit = int(params.get("limit", 100))
    override = _view3d_context()
    win = override.get("window")
    scene = win.scene if win else bpy.context.scene
    view_layer = win.view_layer if win else bpy.context.view_layer
    with bpy.context.temp_override(**override):
        mode = bpy.context.mode

    objects = []
    for obj in list(scene.objects)[:limit]:
        item = {
            "name": obj.name,
            "type": obj.type,
            "parent": obj.parent.name if obj.parent else None,
            "location": _round(obj.location),
            "rotation_deg": _round([math.degrees(a) for a in obj.rotation_euler], 2),
            "scale": _round(obj.scale),
            "dimensions": _round(obj.dimensions),
            "visible": obj.visible_get(view_layer=view_layer),
            "collection": obj.users_collection[0].name if obj.users_collection else None,
            "materials": [s.material.name for s in obj.material_slots if s.material],
        }
        if obj.type == 'MESH':
            item["vertices"] = len(obj.data.vertices)
            item["faces"] = len(obj.data.polygons)
        if obj.modifiers:
            item["modifiers"] = [m.type for m in obj.modifiers]
        objects.append(item)

    active = view_layer.objects.active
    return {
        "blender": bpy.app.version_string,
        "file": bpy.data.filepath or "(unsaved)",
        "unsaved_changes": bpy.data.is_dirty,
        "scene": scene.name,
        "frame": scene.frame_current,
        "render_engine": scene.render.engine,
        "unit_system": scene.unit_settings.system,
        "unit_scale": scene.unit_settings.scale_length,
        "mode": mode,
        "active_object": active.name if active else None,
        "selected_objects": [o.name for o in scene.objects if o.select_get(view_layer=view_layer)][:limit],
        "object_count": len(scene.objects),
        "objects": objects,
        "collections": [c.name for c in scene.collection.children_recursive],
        "datablocks": {
            "meshes": len(bpy.data.meshes),
            "materials": len(bpy.data.materials),
            "images": len(bpy.data.images),
            "node_groups": len(bpy.data.node_groups),
        },
    }


def _material_info(mat):
    info = {"name": mat.name, "use_nodes": mat.use_nodes}
    if mat.use_nodes and mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is not None:
            info["base_color"] = _round(bsdf.inputs["Base Color"].default_value, 3)
            info["roughness"] = round(bsdf.inputs["Roughness"].default_value, 3)
            info["metallic"] = round(bsdf.inputs["Metallic"].default_value, 3)
        info["nodes"] = [n.bl_idname for n in mat.node_tree.nodes][:40]
    else:
        info["diffuse_color"] = _round(mat.diffuse_color, 3)
    return info


def get_object_info(params):
    name = params.get("name", "")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError("No object named %r" % name)
    world = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    info = {
        "name": obj.name,
        "type": obj.type,
        "data": obj.data.name if obj.data else None,
        "parent": obj.parent.name if obj.parent else None,
        "children": [c.name for c in obj.children],
        "location": _round(obj.location),
        "rotation_mode": obj.rotation_mode,
        "rotation_deg": _round([math.degrees(a) for a in obj.rotation_euler], 2),
        "scale": _round(obj.scale),
        "dimensions": _round(obj.dimensions),
        "world_bbox_min": _round([min(v[i] for v in world) for i in range(3)]),
        "world_bbox_max": _round([max(v[i] for v in world) for i in range(3)]),
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
        "collections": [c.name for c in obj.users_collection],
        "modifiers": [{"name": m.name, "type": m.type, "show_viewport": m.show_viewport}
                      for m in obj.modifiers],
        "constraints": [{"name": c.name, "type": c.type} for c in obj.constraints],
        "materials": [_material_info(s.material) for s in obj.material_slots if s.material],
        "custom_properties": [k for k in obj.keys() if not k.startswith("_")],
    }
    if obj.type == 'MESH':
        mesh = obj.data
        info["mesh"] = {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "triangles": sum(len(p.vertices) - 2 for p in mesh.polygons),
            "uv_layers": [uv.name for uv in mesh.uv_layers],
            "vertex_groups": [g.name for g in obj.vertex_groups][:100],
            "shape_keys": [k.name for k in mesh.shape_keys.key_blocks] if mesh.shape_keys else [],
        }
    elif obj.type == 'ARMATURE':
        info["bones"] = [b.name for b in obj.data.bones][:300]
    elif obj.type == 'CAMERA':
        info["camera"] = {"lens": obj.data.lens, "type": obj.data.type}
    elif obj.type == 'LIGHT':
        info["light"] = {"type": obj.data.type, "energy": obj.data.energy,
                         "color": _round(obj.data.color, 3)}
    return info


def execute_python(params):
    global _namespace
    code = params.get("code", "")
    if _namespace is None or params.get("reset_namespace"):
        _namespace = {
            "__name__": "__claude__",
            "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "math": math,
            "Vector": mathutils.Vector, "Matrix": mathutils.Matrix,
            "Euler": mathutils.Euler, "Quaternion": mathutils.Quaternion,
        }
    _namespace.pop("result", None)
    out = io.StringIO()
    override = _view3d_context()
    ok = True
    changed = [False]

    def on_update(scene, depsgraph):
        changed[0] = True

    was_dirty = bpy.data.is_dirty
    bpy.app.handlers.depsgraph_update_post.append(on_update)
    try:
        compiled = compile(code, "<claude>", "exec")
        with bpy.context.temp_override(**override), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            exec(compiled, _namespace)
    except BaseException:  # includes SystemExit from sys.exit() in the snippet
        ok = False
        etype, value, tb = sys.exc_info()
        # Drop this function's frame so the traceback starts in Claude's code.
        out.write("".join(traceback.format_exception(etype, value, tb.tb_next if tb else None)))
    finally:
        try:
            # Evaluating now runs depsgraph_update_post if the code changed anything.
            with bpy.context.temp_override(**override):
                bpy.context.evaluated_depsgraph_get()
        except Exception:
            pass
        bpy.app.handlers.depsgraph_update_post.remove(on_update)
    # One undo step per call that changed something; read-only calls add none,
    # so Ctrl+Z always steps back over a real change.
    if changed[0] or (bpy.data.is_dirty and not was_dirty):
        _undo_push("Claude: Python", override)
    result = {"ok": ok, "output": _truncate(out.getvalue())}
    if "result" in _namespace:
        result["result"] = _jsonable(_namespace["result"])
    return result


_VIEWS = {"FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"}


def get_viewport_screenshot(params):
    max_size = max(128, min(int(params.get("max_size", 1024)), 2048))
    view = str(params.get("view") or "CURRENT").upper()
    frame = str(params.get("frame") or "NONE").upper()
    override = _view3d_context()
    if "area" not in override:
        raise RuntimeError("No 3D Viewport is open")

    prefs_view = bpy.context.preferences.view
    smooth = prefs_view.smooth_view
    prefs_view.smooth_view = 0  # jump straight to the new view
    folder = tempfile.mkdtemp(prefix="claude_shot_")
    path = os.path.join(folder, "window.png")
    try:
        with bpy.context.temp_override(**override):
            if view in _VIEWS:
                bpy.ops.view3d.view_axis(type=view)
            elif view == "CAMERA":
                bpy.ops.view3d.view_camera()
            if frame == "ALL":
                bpy.ops.view3d.view_all(center=False)
            elif frame == "SELECTED":
                bpy.ops.view3d.view_selected()
            # Draw now so the capture shows the scene as it is after the last change.
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            # screen.screenshot_area returns a 1x1 image when run from a script
            # (Blender 4.2), so capture the window and crop the viewport out.
            bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    finally:
        prefs_view.smooth_view = smooth
    if not os.path.exists(path):
        raise RuntimeError("Blender did not write the screenshot")

    region = override["region"]
    try:
        width, height, data = _crop_png(path, region.x, region.y, region.width, region.height,
                                        max_size)
    finally:
        try:
            os.remove(path)
            os.rmdir(folder)
        except OSError:
            pass
    return {"image_base64": base64.b64encode(data).decode("ascii"), "mime_type": "image/png",
            "width": width, "height": height, "view": view}


def _crop_png(path, x, y, w, h, max_size):
    """Crop a window screenshot to a region (window pixels, origin bottom left),
    shrink it to max_size and return (width, height, png_bytes)."""
    import numpy as np

    src = bpy.data.images.load(path, check_existing=False)
    try:
        sw, sh = src.size
        pixels = np.empty(sw * sh * 4, dtype=np.float32)
        src.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(src)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(sw, x + w), min(sh, y + h)
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError("The 3D Viewport is not visible in the window")
    crop = pixels.reshape(sh, sw, 4)[y0:y1, x0:x1].copy()
    cw, ch = x1 - x0, y1 - y0

    out = bpy.data.images.new("claude_viewport_shot", cw, ch, alpha=False)
    try:
        out.pixels.foreach_set(crop.ravel())
        scale = min(1.0, max_size / float(max(cw, ch)))
        if scale < 1.0:
            cw, ch = max(1, int(cw * scale)), max(1, int(ch * scale))
            out.scale(cw, ch)
        out_path = os.path.join(os.path.dirname(path), "viewport.png")
        out.filepath_raw = out_path
        out.file_format = 'PNG'
        out.save()
    finally:
        bpy.data.images.remove(out)
    with open(out_path, "rb") as f:
        data = f.read()
    os.remove(out_path)
    return cw, ch, data


COMMANDS = {
    "hello": hello,
    "get_scene_info": get_scene_info,
    "get_object_info": get_object_info,
    "execute_python": execute_python,
    "get_viewport_screenshot": get_viewport_screenshot,
}


def run(cmd, params):
    fn = COMMANDS.get(cmd)
    if fn is None:
        raise ValueError("Unknown command %r" % cmd)
    return fn(params if isinstance(params, dict) else {})

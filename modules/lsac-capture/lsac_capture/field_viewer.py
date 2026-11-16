"""Self-contained local Three.js field viewer; all samples stay in the HTML."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .calibration import checked_pose,normal
from .core import rotate,unit


def _dot(a,b):return sum(x*y for x,y in zip(a,b))
def _cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def _add(a,b):return [x+y for x,y in zip(a,b)]


def _matrix_quaternion(m):
    trace=m[0][0]+m[1][1]+m[2][2]
    if trace>0:
        s=math.sqrt(trace+1)*2
        q=[s/4,(m[2][1]-m[1][2])/s,(m[0][2]-m[2][0])/s,(m[1][0]-m[0][1])/s]
    else:
        i=max(range(3),key=lambda k:m[k][k]);j=(i+1)%3;k=(i+2)%3
        s=math.sqrt(max(0,1+m[i][i]-m[j][j]-m[k][k]))*2
        if s<1e-10:raise ValueError("Invalid orientation basis")
        xyz=[0.,0.,0.];xyz[i]=s/4;xyz[j]=(m[j][i]+m[i][j])/s;xyz[k]=(m[k][i]+m[i][k])/s
        q=[(m[k][j]-m[j][k])/s,*xyz]
    return unit(q)


def render_geometry(field,calibration=None):
    """Keep original field coordinates; derive one palm-horizontal display SE3.

    The raw Tracker-defined in-plane X/Y are unverified. A physical horizontal
    screen edge is determined by intersection of screen and palm planes.
    """
    screen_info=field.get("screen_reference",{})
    source=calibration
    if source is None and screen_info.get("world_from_screen") and screen_info.get("reference_world_pose"):
        source=dict(screen_world_pose=screen_info["world_from_screen"],reference_world_pose=screen_info["reference_world_pose"],
                    world_id=field.get("provenance",{}).get("world_id"),reference_kind="palm_rest")
    if source is None:
        return dict(enabled=False,coordinate_frame="raw_calibration_plane",legacy_inplane_axis_unverified=True,
                    raw_to_render_rotation_rows=[[1,0,0],[0,1,0],[0,0,1]],raw_to_render_quaternion_wxyz=[1,0,0,0],
                    raw_to_render_translation_m=[0,0,0],screen_render_pose=dict(position_m=[0,0,0],quaternion_wxyz=[1,0,0,0]))
    expected_world=field.get("provenance",{}).get("world_id")
    if not expected_world or source.get("world_id")!=expected_world:
        raise ValueError("Calibration world_id must match field provenance.world_id")
    screen=checked_pose(source["screen_world_pose"]);palm=checked_pose(source["reference_world_pose"])
    if screen_info.get("world_from_screen"):
        original=checked_pose(screen_info["world_from_screen"])
        if math.dist(original["position_m"],screen["position_m"])>1e-8 or 1-abs(_dot(original["quaternion_wxyz"],screen["quaternion_wxyz"]))>1e-8:
            raise ValueError("Screen calibration changed: rebuild the field before rendering this bundle")
    up=normal(rotate(palm["quaternion_wxyz"],[0,0,1]))
    screen_normal=normal(rotate(screen["quaternion_wxyz"],[0,0,1]))
    raw_axes=[rotate(screen["quaternion_wxyz"],axis) for axis in ([1,0,0],[0,1,0],[0,0,1])]
    right=normal(_cross(up,screen_normal))
    if _dot(right,raw_axes[0])<0:right=[-x for x in right]
    physical_up=normal(_cross(screen_normal,right))
    if _dot(physical_up,up)<=0:
        raise ValueError("Screen-plane intersection orientation conflicts with upward screen direction")
    toward=normal(_cross(right,up));basis=[right,up,toward]
    height=_dot([a-b for a,b in zip(screen["position_m"],palm["position_m"])],up)
    origin=[s-height*n for s,n in zip(screen["position_m"],up)]
    translation=[_dot(axis,[a-b for a,b in zip(screen["position_m"],origin)]) for axis in basis]
    raw_rotation=[[_dot(axis,raw_axis) for raw_axis in raw_axes] for axis in basis]
    physical_axes=[right,physical_up,screen_normal]
    physical_rotation=[[_dot(axis,physical_axis) for physical_axis in physical_axes] for axis in basis]
    width,height_screen=.30155,.18847
    def screen_point(x,y):return _add(translation,[_dot(row,[x,y,0]) for row in physical_rotation])
    bottom=[screen_point(-width/2,-height_screen/2),screen_point(width/2,-height_screen/2)]
    bottom_center=screen_point(0,-height_screen/2)
    opening=math.degrees(math.acos(max(-1,min(1,-_dot(up,screen_normal)))))
    return dict(enabled=True,coordinate_frame="palm_horizontal_render",world_id=expected_world,
                in_plane_heading_basis="intersection_of_screen_and_palm_planes",legacy_inplane_axis_unverified=True,
                origin_definition="screen_center_projected_onto_measured_palm_plane",origin_world_m=origin,
                basis_world_columns=basis,palm_normal_world=up,screen_normal_world=screen_normal,
                screen_physical_up_world=physical_up,raw_screen_x_dot_palm_up=_dot(raw_axes[0],up),
                raw_to_render_rotation_rows=raw_rotation,raw_to_render_quaternion_wxyz=_matrix_quaternion(raw_rotation),
                raw_to_render_translation_m=translation,screen_world_pose=screen,reference_world_pose=palm,
                screen_render_pose=dict(position_m=translation,quaternion_wxyz=_matrix_quaternion(physical_rotation)),
                screen_bottom_render_endpoints_m=bottom,base_hinge_render_m=[bottom_center[0],0,bottom_center[2]],
                opening_angle_degrees=opening,screen_center_height_m=height,
                nominal_screen_size_m=[width,height_screen],schematic_base_size_m=[.32355,.012,.22],
                dimensions_basis="screen nominal 14-inch 16:10; base/keyboard/touchpad schematic, not model CAD",
                coordinate_data_modified=False)


def table_geometry(table_reference,geometry):
    """Reproject the measured world plane, never reuse a cached old palm plane."""
    if table_reference is None:return None
    if not geometry.get("enabled") or table_reference.get("world_id")!=geometry.get("world_id"):
        raise ValueError("Table reference must share the calibrated render world")
    pose=checked_pose(table_reference["table_world_pose"])
    basis=geometry["basis_world_columns"];origin=geometry["origin_world_m"]
    normal_world=normal(rotate(pose["quaternion_wxyz"],[0,0,1]))
    n=normal([_dot(axis,normal_world) for axis in basis])
    point=[_dot(axis,[a-b for a,b in zip(pose["position_m"],origin)]) for axis in basis]
    if n[1]<0:n=[-v for v in n]
    if abs(n[1])<1e-6:raise ValueError("Table reference is nearly vertical in palm frame")
    offset=_dot(n,point)
    center=[0.,(offset-n[2]*.15)/n[1],.15]
    right=normal([a-n[0]*b for a,b in zip([1,0,0],n)])
    away=normal(_cross(n,right))
    rotation=[[right[i],away[i],n[i]] for i in range(3)]
    return dict(world_id=geometry["world_id"],unit_normal_render=n,offset_m=offset,
                equation="n dot render_position = offset_m",sample_point_render_m=point,
                table_height_at_palm_origin_m=offset/n[1],center_render_m=center,
                quaternion_wxyz=_matrix_quaternion(rotation),schematic_extent_m=[1.1,.85],
                horizontal_extent_basis="finite visual guide only; table boundaries not measured",
                basis="recomputed from table_world_pose and current palm render basis; cached table_plane_palm ignored",
                absolute_accuracy_validated=False,is_floor_reference=False)


def validate_field(field):
    if field.get("coordinate_frame")!="screen":
        raise ValueError("Viewer requires screen-frame positions")
    grid=field.get("grid",{})
    shape=grid.get("shape",[])
    if len(shape)!=3 or any(type(v) is not int or v<=0 for v in shape):
        raise ValueError("Invalid voxel grid shape")
    if type(grid.get("spacing_m")) not in (int,float) or not math.isfinite(grid["spacing_m"]) or grid["spacing_m"]<=0:
        raise ValueError("Invalid voxel spacing")
    origin=grid.get("origin_m",[])
    if len(origin)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in origin):
        raise ValueError("Invalid voxel center origin")
    for name,required in (("points",("x_m","y_m","z_m","energy_adc2")),
                          ("voxels",("flat_index","energy_adc2","nearest_distance_m"))):
        table=field.get(name,{})
        columns=table.get("columns",[]);rows=table.get("rows",[])
        if len(columns)!=len(set(columns)) or not all(key in columns for key in required):
            raise ValueError(f"Missing/duplicate {name} columns")
        if any(len(row)!=len(columns) for row in rows):
            raise ValueError(f"Malformed {name} rows")
        for row in rows:
            for key in required:
                value=row[columns.index(key)]
                if type(value) not in (int,float) or not math.isfinite(value):
                    raise ValueError(f"Nonfinite {name}.{key}")
            if row[columns.index("energy_adc2")]<0:
                raise ValueError("Negative energy")
    if not field["points"]["rows"]:
        raise ValueError("No measured points")
    maximum=math.prod(shape)
    index=field["voxels"]["columns"].index("flat_index")
    flat=[row[index] for row in field["voxels"]["rows"]]
    if any(type(v) is not int or not 0<=v<maximum for v in flat) or len(flat)!=len(set(flat)):
        raise ValueError("Voxel indices must be unique valid x-fastest grid indices")
    return field


def validate_source_volume(volume,geometry,directions=None):
    if volume is None:
        return
    if not geometry.get("enabled") or volume.get("coordinate_frame")!="palm_render":
        raise ValueError("Source support requires the calibrated palm render frame")
    if volume.get("world_id")!=geometry.get("world_id"):
        raise ValueError("Source support world_id differs from the field")
    if volume.get("foa_assumption_enabled") is not True:
        raise ValueError("Virtual-source support requires an explicit experimental FOA assumption")
    basis=volume.get("render_geometry",{})
    for key in ("origin_world_m","basis_world_columns"):
        if key not in basis:
            raise ValueError("Source support must record its palm basis and origin")
        def flatten(value):
            if isinstance(value,list):return [y for x in value for y in flatten(x)]
            return [value]
        expected=flatten(geometry[key]);actual=flatten(basis[key])
        if len(expected)!=len(actual) or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x-y)>1e-7 for x,y in zip(actual,expected)):
            raise ValueError("Source support render basis differs from current viewer")
    grid=volume.get("grid",{})
    shape=grid.get("shape",[]);origin=grid.get("origin_m",[]);spacing=grid.get("spacing_m")
    if len(shape)!=3 or any(type(v) is not int or v<=0 for v in shape) or len(origin)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in origin):
        raise ValueError("Invalid source support grid")
    if type(spacing) not in (int,float) or not math.isfinite(spacing) or spacing<=0:
        raise ValueError("Invalid source support spacing")
    table=volume.get("voxels",{});columns=table.get("columns",[])
    if not all(key in columns for key in ("flat_index","support_score")):
        raise ValueError("Source support is not an energy table: support_score required")
    total=math.prod(shape)
    for row in table.get("rows",[]):
        if len(row)!=len(columns):raise ValueError("Malformed source support row")
        i=row[columns.index("flat_index")];score=row[columns.index("support_score")]
        if type(i) is not int or not 0<=i<total or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError("Source support values must be valid indices and dimensionless scores 0..1")
    if directions is not None:
        if directions.get("coordinate_frame")!="palm_render" or directions.get("world_id")!=volume.get("world_id"):
            raise ValueError("Direction likelihood frame/world differs from source support")
        if directions.get("render_geometry",{}).get("origin_world_m")!=volume["render_geometry"].get("origin_world_m") or directions.get("render_geometry",{}).get("basis_world_columns")!=volume["render_geometry"].get("basis_world_columns"):
            raise ValueError("Direction likelihood palm basis differs from source support")
        for point in directions.get("points",[]):
            unit_directions=point.get("directions",[]);response=point.get("beam_response_over_sphere_max",[])
            if len(unit_directions)!=len(response) or not unit_directions:
                raise ValueError("Direction grid requires one normalized response per direction")
            if any(len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) or abs(sum(x*x for x in v)-1)>.01 for v in unit_directions):
                raise ValueError("Direction grid vectors must be finite unit directions")
            if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1.000001 for v in response):
                raise ValueError("Direction response must be normalized 0..1, not acoustic energy")


def validate_multisource_fit(fit,geometry):
    if fit is None:return
    if fit.get("coordinate_frame")!="palm_render" or not geometry.get("enabled") or fit.get("world_id")!=geometry.get("world_id"):
        raise ValueError("Multiregion fit must share the calibrated palm render world")
    recorded=fit.get("render_geometry",{})
    for key in ("origin_world_m","basis_world_columns"):
        if key not in recorded:raise ValueError("Multiregion fit is missing its physical render basis")
        expected=geometry[key];actual=recorded[key]
        if key=="basis_world_columns":expected=[x for row in expected for x in row];actual=[x for row in actual for x in row]
        if len(actual)!=len(expected) or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x-y)>1e-7 for x,y in zip(actual,expected)):
            raise ValueError("Multiregion fit palm basis differs from current viewer")
    regions=fit.get("regions",[])
    if len(regions)!=4 or len({region.get("name") for region in regions})!=4:
        raise ValueError("Four distinctly named nominal source regions are required")
    for region in regions:
        for key in ("center_m","size_m"):
            value=region.get(key,[])
            if len(value)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or (key=="size_m" and v<=0) for v in value):
                raise ValueError("Source-region centers/sizes must be finite metre vectors")
    for key in ("selected_model","diagonal_power_only_comparison"):
        model=fit.get(key,{})
        for metric in ("train_relative_covariance_error","spatial_holdout_relative_covariance_error"):
            value=model.get(metric)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:
                raise ValueError("Training/holdout relative covariance errors are required")


HTML_TEMPLATE=r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LSAC · 实测声场分布</title>
<style>
:root{color-scheme:dark;font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;background:#0a1019;color:#e8edf4;font-size:14px}*{box-sizing:border-box}body{margin:0;overflow:hidden}button,input,select{font:inherit}button,select{background:#172334;color:#dce7f5;border:1px solid #33445a;border-radius:7px;padding:7px 10px}button{cursor:pointer}button:hover{background:#263b53;border-color:#6b8aac}button.active{background:#24506c;border-color:#57b8e4;color:white}header{height:82px;border-bottom:1px solid #263447;display:flex;align-items:center;justify-content:space-between;padding:17px 25px;background:#0e1724}h1{font-size:22px;letter-spacing:.03em;margin:0 0 5px;font-weight:650}.subtitle{font-size:12px;color:#9baec5}.badge{font-size:12px;letter-spacing:.04em;background:#3b2d13;border:1px solid #876320;color:#f3ca73;border-radius:99px;padding:7px 12px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 332px;height:calc(100vh - 82px)}.stage{position:relative;min-width:0;overflow:hidden;background:radial-gradient(ellipse at 48% 42%,#172639,#0b121e 75%)}#viewport{position:absolute;inset:0}#viewport canvas{display:block;outline:none}.toolbar{position:absolute;z-index:2;top:17px;left:20px;display:flex;align-items:center;gap:7px;padding:7px;background:#0d1623d9;border:1px solid #334257;border-radius:11px;backdrop-filter:blur(9px)}.toolbar span{font-size:12px;color:#a6b7ce;margin:0 6px}.hint{position:absolute;z-index:2;left:22px;bottom:24px;color:#aabbd0;font-size:12px;line-height:1.8;pointer-events:none}.scene-tag{position:absolute;z-index:2;left:23px;top:86px;color:#9fb2ca;font-size:12px;letter-spacing:.02em}.legend{position:absolute;right:22px;bottom:25px;width:252px;background:#0d1725dc;border:1px solid #2a3c52;border-radius:11px;padding:12px 14px;backdrop-filter:blur(7px)}.legend-title{font-size:12px;margin-bottom:9px;color:#d6e4f4}.colorbar{height:11px;border-radius:5px;background:linear-gradient(90deg,#1e3a8a,#06b6d4 33.33%,#facc15 66.66%,#ef4444)}.ticks{display:flex;justify-content:space-between;color:#93a8c2;font-size:11px;margin-top:5px}.side{overflow:auto;background:#0e1724;border-left:1px solid #263447;padding:19px 20px 30px}.section{padding-bottom:17px;margin-bottom:17px;border-bottom:1px solid #253448}.section:last-child{border:0}.section h2{font-size:13px;font-weight:650;color:#c0d0e4;letter-spacing:.05em;margin:0 0 12px}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{padding:11px;background:#142132;border:1px solid #24364d;border-radius:9px}.metric strong{display:block;font-size:24px;font-weight:600;color:#f0f6ff;line-height:1.2}.metric span{font-size:11px;color:#93a7c2}.row{display:flex;align-items:center;gap:8px;margin:10px 0;font-size:13px}.row input[type=checkbox]{width:15px;height:15px;accent-color:#55b7dd}.fine{font-size:11px;line-height:1.7;color:#8fa3bd;margin-top:7px}.warn{background:#302616;border:1px solid #65512b;color:#e9c988;padding:10px 11px;border-radius:8px;font-size:11px;line-height:1.7}.control{margin:13px 0}.control label{display:flex;justify-content:space-between;font-size:12px;color:#afc0d7;margin-bottom:7px}.control input[type=range]{width:100%;accent-color:#59badf;cursor:pointer}.control select{width:100%}.inspector{font-size:12px;line-height:1.9;white-space:pre-line;color:#afc1d9;font-variant-numeric:tabular-nums}.axis-key{display:flex;gap:13px;font-size:11px;color:#94aac5}.axis-key b{font-size:15px}.error{position:absolute;inset:100px 30px auto;z-index:10;background:#4a1f24;color:#ffd9dc;border:1px solid #ae5964;padding:18px;white-space:pre-wrap;border-radius:9px;display:none}.mobile-note{display:none}@media(max-width:900px){.layout{grid-template-columns:minmax(0,1fr) 285px}.side{padding:15px}.legend{width:205px}.toolbar{gap:4px;left:12px}.toolbar span{display:none}header{padding:14px 18px}h1{font-size:19px}}@media(max-width:650px){.layout{grid-template-columns:1fr}.side{display:none}.badge{display:none}.legend{width:200px}.hint{bottom:100px}.mobile-note{display:block}}
</style>
<style>.toolbar{flex-wrap:wrap;max-width:calc(100% - 40px)}.hint{max-width:calc(100% - 44px)}@media(max-width:900px){.toolbar{max-width:calc(100% - 24px)}.toolbar button{font-size:12px;padding:6px 8px}.scene-tag{top:124px;max-width:calc(100% - 40px);line-height:1.5}.mobile-note{top:160px!important}}</style>
<style>.source-legend{margin-top:10px;border-top:1px solid #344058;padding-top:10px}.source-colorbar{background:linear-gradient(90deg,#4c1d95,#c026d3,#f9a8d4)}details.section>summary{font-size:13px;font-weight:650;color:#d7b4f7;cursor:pointer;list-style-position:inside;margin-bottom:12px}.source-metrics{font-size:11px;line-height:1.7;color:#b5a1cc}.source-note{border-left:2px solid #9c60c9;padding-left:8px;color:#ab9bbb;font-size:11px;line-height:1.7;margin-top:9px}</style>
<script type="importmap">{"imports":{"three":"./vendor/three.module.js"}}</script>
</head><body>
<header><div><h1>LSAC · 实测声场分布</h1><div class="subtitle" id="frameSubtitle">500 Hz · 1/3 倍频程 · 相对频带电平与空间覆盖</div></div><div class="badge">实验预览 / 非声压级 SPL</div></header>
<main class="layout"><section class="stage"><div id="viewport"></div>
<div class="toolbar"><span>视角</span><button id="view3d" class="active">3D</button><button id="viewXY">掌托正视 XY</button><button id="viewXZ">掌托俯视 XZ</button><button id="viewYZ">掌托侧视 YZ</button><button id="viewScreen">屏幕正视</button><button id="focusLaptop">聚焦笔记本</button><button id="reset">重置</button></div>
<div class="scene-tag" id="sceneTag">掌托水平参考 · 坐标单位 m</div><div id="error" class="error"></div>
<div class="mobile-note" role="note" style="position:absolute;z-index:2;left:18px;right:18px;top:115px;padding:9px 11px;border:1px solid #665132;border-radius:8px;background:#322717ed;color:#e8cb92;font-size:11px;line-height:1.7">实验预览 · 非 dB SPL。arrival-preview / nominal-sampleclock 同步未验证；USB 通道顺序与 SN3D 未核实。未覆盖区域为空白，方向 proxy 默认关闭。</div>
<div class="hint">左键旋转 · 滚轮缩放 · 右键平移<br>点击实测点或体素查看数据；未覆盖区域保持空白</div>
<div class="legend"><div class="legend-title" id="legendTitle">USB_ch1 · 相对频带能量</div><div class="colorbar"></div><div class="ticks"><span>≤ −30 dB</span><span>−20</span><span>−10</span><span>0 dB</span></div><div class="fine">0 dB = 本次所选时段的实测最大值</div><div class="source-legend" id="sourceLegend" style="display:none"><div class="legend-title">接收反投影支持 · 无量纲对照分数</div><div class="colorbar source-colorbar"></div><div class="ticks"><span>0</span><span>0.5</span><span>1</span></div><div class="fine">不是声能、概率、置信度或距离精度</div></div></div>
</section><aside class="side"><div class="section"><h2>本轮目标：描述实测声场</h2><div class="fine">观察强弱区域、电平起伏和空间覆盖。先按3–5 cm尺度看整体，局部变化明显时再加密；这是工作尺度，不是听觉阈值或定位精度。源定位、单元FR与绝对声强属于另外的任务。</div></div>
<div class="section"><h2>本次数据</h2><div class="metrics"><div class="metric"><strong id="pointCount">—</strong><span id="pointMeaning">实测窗口 / 一个声学中心</span></div><div class="metric"><strong>500 Hz</strong><span>当前测量频段</span><strong id="voxelCount" style="display:none">—</strong></div></div><div class="fine" id="gridInfo"></div><div class="fine" id="supportInfo"></div><div class="fine" id="fieldSummary"></div></div>
<details class="section" id="multisourcePanel" style="display:none"><summary>高级：四源区拟合对照</summary><label class="row"><input id="showRegions" type="checkbox">四个用户布局先验区（分类色）</label><div id="multisourceSummary" class="fine"></div><div class="source-note">单元数量与部位由用户确认，盒的位置/尺寸未实测，不是型号 CAD 或声学中心。多个复权等效单极子表达未知辐射，不强加全向或心形单元源型。当前残差仍大，不能据此识别 FR 或真实源位置。两行使用同一相对协方差指标；不能与旧 1/r² 残差直接比较。</div></details><details class="section" id="sourcePanel" style="display:none"><summary>接收方向反投影 · 单源假设对照</summary><div class="source-metrics" id="sourceSummary"></div><label class="row"><input id="showSourceVolume" type="checkbox">接收反投影对照体积（默认隐藏）</label><label class="row"><input id="showSourcePoints" type="checkbox">接收采样分箱 / 接收波束峰</label><div class="control"><label>支持分数显示下限 <span id="sourceThresholdValue">0.80</span></label><input id="sourceThreshold" type="range" min="0" max="1" step="0.01" value="0.8"></div><div class="control"><select id="sourcePoint"><option value="">选择接收采样分箱，查看朝屏幕的波束响应</option></select></div><label class="row"><input id="showHemisphere" type="checkbox" checked>所选测点接收波束响应色块</label><label class="row"><input id="showSourcePaths" type="checkbox">少量接收方向反投影示意</label><div class="source-note" id="directionNote">半球保持固定示意半径，颜色仅表示归一化方向响应；不是声压瓣。接收反投影不是笔记本心形发射模型；不是实体单元位置或真实来声，距离与范围未验证。</div></details>
<div class="section"><h2>显示图层</h2><label class="row"><input id="showPoints" type="checkbox" checked>实测能量点</label><label class="row"><input id="showPath" type="checkbox">相邻实测窗轨迹</label><label class="row"><input id="showVoxels" type="checkbox">有支持体素 / 透明</label><label class="row"><input id="showScreen" type="checkbox" checked>笔记本示意 / 实测开盖角</label><label class="row"><input id="showTable" type="checkbox" checked>桌面参考平面（范围示意）</label><label class="row"><input id="showProxy" type="checkbox">实验方向 proxy</label><div class="fine" id="proxyNote">方向默认关闭。未核实 USB 通道语义，不作为声强或真实传播方向。</div><div class="control"><label>体素透明度 <span id="opacityValue">20%</span></label><input id="opacity" type="range" min="0.05" max="0.65" step="0.05" value="0.2"></div></div>
<div class="section"><h2>切片与显示支持</h2><div class="control"><select id="sliceAxis"><option value="none">全部空间 / 不切片</option><option value="0">X 薄层</option><option value="1">Y 薄层</option><option value="2">Z 薄层</option></select></div><div class="control"><label>切片位置 <span id="slicePositionValue">—</span></label><input id="slicePosition" type="range" min="-50" max="50" step="0.5" value="0" disabled></div><div class="control"><label>切片半厚度 <span id="sliceThicknessValue">1.5 cm</span></label><input id="sliceThickness" type="range" min="0.5" max="10" step="0.5" value="1.5" disabled></div><div class="control"><label>最近支持分箱距离上限 <span id="supportValue">—</span></label><input id="support" type="range" min="0.5" max="10" step="0.5" value="10"></div><div class="fine">距离滑块仅过滤已有支持体素，不扩大模型范围，不填补空白区域。</div></div>
<div class="section"><h2>选中数据</h2><div class="inspector" id="inspector">点击图中的实测点或体素。<br>每个音频窗只有一个空间采样位置。</div></div>
<div class="section"><h2>参考与限制</h2><div class="axis-key"><span><b style="color:#f27b82">X</b> 交线向右</span><span><b style="color:#7bd3b0">Y</b> 掌托向上</span><span><b style="color:#78b8ef">Z</b> 朝用户</span></div><div class="fine" id="laptopInfo">屏幕名义 14 寸 16:10：301.55 × 188.47 mm；机身、键盘与触控板仅为示意，不是型号 CAD。</div><div class="fine" id="geometryInfo"></div><div class="fine" id="tableInfo"></div><div class="warn" style="margin-top:12px">arrival-preview / nominal-sampleclock：音频与位姿同步未验证。<br>USB 通道顺序、归一化和方向映射未证实。颜色为数字能量相对值，非 dB SPL；四路通道不等于四个空间测点。</div><div class="fine" id="modelNote"></div></div>
</aside></main>
<script id="field-data" type="application/json">__FIELD_JSON__</script>
<script id="render-data" type="application/json">__RENDER_JSON__</script>
<script id="source-data" type="application/json">__SOURCE_JSON__</script>
<script id="multisource-data" type="application/json">__MULTISOURCE_JSON__</script>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
const state=window.__LSAC_VIEWER__={ready:false,pointCount:0,voxelCount:0,visiblePointCount:0,visibleVoxelCount:0,frames:0,view:'3D',errors:[]};
function fail(error){state.errors.push(String(error));const box=document.getElementById('error');box.style.display='block';box.textContent='预览加载失败：'+error;}
window.addEventListener('error',event=>fail(event.error||event.message));
window.addEventListener('unhandledrejection',event=>fail(event.reason));
try{
const field=JSON.parse(document.getElementById('field-data').textContent);
const spatialSampling=field.sampling_mode==='spatial';state.samplingMode=field.sampling_mode||'legacy_windows';
if(spatialSampling){document.getElementById('pointMeaning').textContent='占用空间单元 / 一个声学中心';const checkbox=document.getElementById('showPath');checkbox.checked=false;checkbox.disabled=true;checkbox.parentElement.lastChild.textContent='空间单元无时间顺序（轨迹关闭）';document.getElementById('inspector').textContent='点击占用空间单元或支持体素。每个单元可累积多个不连续有效时间段；单元编号不表示时间顺序。';}
const geometry=JSON.parse(document.getElementById('render-data').textContent);state.geometry=geometry;state.viewFrame=geometry.basis_world_columns;state.palmNormal=geometry.palm_normal_world;state.screenPose=geometry.screen_render_pose;
const multisourceFit=JSON.parse(document.getElementById('multisource-data').textContent);
const sourcePayload=JSON.parse(document.getElementById('source-data').textContent);const sourceVolume=sourcePayload.volume,sourceDirections=sourcePayload.directions;
let updateSource=()=>{},selectSource=()=>{},sourceMarkers=null,sourceCloud=null,sourceVisible=[];
const dataRotation=new THREE.Quaternion(...geometry.raw_to_render_quaternion_wxyz.slice(1),geometry.raw_to_render_quaternion_wxyz[0]);
const dataTranslation=new THREE.Vector3(...geometry.raw_to_render_translation_m);
const tableReference=geometry.table_reference||null;state.tableReference=tableReference;
const toRender=position=>new THREE.Vector3(...position).applyQuaternion(dataRotation).add(dataTranslation).toArray();
document.getElementById('legendTitle').textContent=field.foa_assumption_enabled?'USB_ch1（假定 W）· 相对频带能量':'USB_ch1 · 相对频带能量';
const columnsToObjects=table=>table.rows.map(row=>Object.fromEntries(table.columns.map((key,i)=>[key,row[i]])));
const measured=columnsToObjects(field.points);
const voxels=columnsToObjects(field.voxels);
const grid=field.grid;const spacing=grid.spacing_m;const [nx,ny,nz]=grid.shape;
const positionOfVoxel=flat=>[grid.origin_m[0]+(flat%nx)*spacing,grid.origin_m[1]+(Math.floor(flat/nx)%ny)*spacing,grid.origin_m[2]+Math.floor(flat/(nx*ny))*spacing];
for(const point of measured){point.rawPosition=[point.x_m,point.y_m,point.z_m];point.position=toRender(point.rawPosition);}
for(const voxel of voxels){voxel.rawPosition=positionOfVoxel(voxel.flat_index);voxel.position=toRender(voxel.rawPosition);}
const referenceEnergy=Math.max(...measured.map(point=>point.energy_adc2));
if(!(referenceEnergy>0))throw new Error('实测参考能量必须大于零');
const db=energy=>10*Math.log10(Math.max(energy,referenceEnergy*1e-12)/referenceEnergy);
const stops=[[-30,'#1e3a8a'],[-20,'#06b6d4'],[-10,'#facc15'],[0,'#ef4444']].map(([v,c])=>[v,new THREE.Color(c)]);
function color(energy){const v=Math.min(0,Math.max(-30,db(energy)));for(let i=1;i<stops.length;i++){if(v<=stops[i][0])return stops[i-1][1].clone().lerp(stops[i][1],(v-stops[i-1][0])/(stops[i][0]-stops[i-1][0]));}return stops.at(-1)[1].clone();}
state.pointCount=measured.length;state.voxelCount=voxels.length;
document.getElementById('pointCount').textContent=measured.length.toLocaleString();
const measuredLevels=measured.map(p=>db(p.energy_adc2)).sort((a,b)=>a-b);const levelQuantile=q=>{const at=q*(measuredLevels.length-1),lo=Math.floor(at),hi=Math.ceil(at);return measuredLevels[lo]+(measuredLevels[hi]-measuredLevels[lo])*(at-lo);};document.getElementById('fieldSummary').textContent=`已覆盖测点的相对电平 P10–P90 跨度 ${(levelQuantile(.9)-levelQuantile(.1)).toFixed(1)} dB。仅描述本次覆盖位置，不代表整个体积的均匀度。`;document.getElementById('voxelCount').textContent=voxels.length.toLocaleString();
document.getElementById('gridInfo').textContent='仅沿已测位置显示；空白是未测区域，不要求把整个空间填满。';
document.getElementById('supportInfo').textContent='连续路径的有效录音均保留。先观察分布和起伏，再针对明显变化处复测。';
document.getElementById('modelNote').textContent=`支持体素仅在实测点附近。${(field.limitations||[]).filter(value=>typeof value==='string').slice(0,2).join(' ')}`;
const viewport=document.getElementById('viewport');
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=THREE.SRGBColorSpace;viewport.appendChild(renderer.domElement);
const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(40,1,0.001,100);camera.up.set(0,1,0);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.08;controls.minDistance=.08;controls.maxDistance=5;
const physicalScreenRotation=new THREE.Quaternion(...geometry.screen_render_pose.quaternion_wxyz.slice(1),geometry.screen_render_pose.quaternion_wxyz[0]);
const screenCenter=new THREE.Vector3(...geometry.screen_render_pose.position_m);
const physicalScreenPoint=(x,y,z=0)=>new THREE.Vector3(x,y,z).applyQuaternion(physicalScreenRotation).add(screenCenter);
const worldBounds=new THREE.Box3();for(const point of measured)worldBounds.expandByPoint(new THREE.Vector3(...point.position));for(const x of [-.150775,.150775])for(const y of [-.094235,.094235])worldBounds.expandByPoint(physicalScreenPoint(x,y));
if(geometry.enabled){const hinge=geometry.base_hinge_render_m;for(const x of [-.161775,.161775])for(const z of [0,.22])worldBounds.expandByPoint(new THREE.Vector3(hinge[0]+x,0,hinge[2]+z));}
const center=worldBounds.getCenter(new THREE.Vector3());const extent=worldBounds.getSize(new THREE.Vector3());const distance=Math.max(extent.x,extent.y,extent.z,.4)*2.3;
controls.target.copy(center);
function setView(view){const focused=state.frameMode==='laptop';const target=focused?laptopBounds.getCenter(new THREE.Vector3()):center;const d=focused?laptopDistance():distance;state.view=view;state.frameMode=focused?'laptop':'all';controls.target.copy(target);if(view==='XY'){camera.up.set(0,1,0);camera.position.copy(target).add(new THREE.Vector3(0,0,d));}else if(view==='XZ'){camera.up.set(0,0,-1);camera.position.copy(target).add(new THREE.Vector3(0,d,0));}else if(view==='YZ'){camera.up.set(0,1,0);camera.position.copy(target).add(new THREE.Vector3(d,0,0));}else if(view==='Screen'){controls.target.copy(screenCenter);camera.up.copy(new THREE.Vector3(0,1,0).applyQuaternion(physicalScreenRotation));camera.position.copy(screenCenter).add(new THREE.Vector3(0,0,d).applyQuaternion(physicalScreenRotation));}else{camera.up.set(0,1,0);camera.position.copy(target).add(new THREE.Vector3(.6,.35,1).multiplyScalar(d));}camera.lookAt(controls.target);controls.update();for(const id of ['view3d','viewXY','viewXZ','viewYZ','viewScreen'])document.getElementById(id).classList.toggle('active',id==='view'+(view==='3D'?'3d':view));}
for(const view of ['3D','XY','XZ','YZ','Screen'])document.getElementById('view'+(view==='3D'?'3d':view)).addEventListener('click',()=>setView(view));document.getElementById('reset').addEventListener('click',()=>{state.frameMode='all';setView('3D');});setView('3D');
function labelSprite(text,tint){const canvas=document.createElement('canvas');canvas.width=256;canvas.height=96;const context=canvas.getContext('2d');context.font='600 38px "Microsoft YaHei",sans-serif';context.fillStyle=tint;context.textAlign='center';context.textBaseline='middle';context.fillText(text,128,48);const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthTest:false}));sprite.scale.set(.11,.04125,1);return sprite;}
const axes=new THREE.Group();for(const [direction,tint,text] of [[new THREE.Vector3(1,0,0),0xf27b82,'X 交线右'],[new THREE.Vector3(0,1,0),0x7bd3b0,'Y 掌托上'],[new THREE.Vector3(0,0,1),0x78b8ef,'Z 朝用户']]){axes.add(new THREE.ArrowHelper(direction,new THREE.Vector3(),.15,tint,.015,.008));const sprite=labelSprite(text,'#'+tint.toString(16));sprite.position.copy(direction.clone().multiplyScalar(.19));axes.add(sprite);}scene.add(axes);
scene.add(new THREE.HemisphereLight(0xc6defa,0x182335,2));const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(.4,.9,1);scene.add(light);
let tablePlane=null;if(tableReference){tablePlane=new THREE.Group();tablePlane.position.set(...tableReference.center_render_m);const q=tableReference.quaternion_wxyz;tablePlane.quaternion.set(q[1],q[2],q[3],q[0]);const [w,d]=tableReference.schematic_extent_m;const plane=new THREE.Mesh(new THREE.PlaneGeometry(w,d),new THREE.MeshBasicMaterial({color:0x6b9f93,transparent:true,opacity:.12,side:THREE.DoubleSide,depthWrite:false}));tablePlane.add(plane);tablePlane.add(new THREE.LineSegments(new THREE.EdgesGeometry(plane.geometry),new THREE.LineBasicMaterial({color:0x5c9188,transparent:true,opacity:.45})));scene.add(tablePlane);const marker=labelSprite('桌面参考','#8cc5b9');marker.position.set(-.4,(tableReference.offset_m+.4*tableReference.unit_normal_render[0]-.15*tableReference.unit_normal_render[2])/tableReference.unit_normal_render[1]+.015,.15);marker.scale.set(.15,.056,1);tablePlane.userData.referenceLabel=marker;scene.add(marker);document.getElementById('tableInfo').textContent=`实测桌面：掌托原点处 ${ (tableReference.table_height_at_palm_origin_m*100).toFixed(2)} cm。桌面横向 ${ (w*100).toFixed(0)} × ${(d*100).toFixed(0)} cm 仅为有限显示范围，边界未测量；不是地面标定。`;}else{document.getElementById('showTable').checked=false;document.getElementById('showTable').disabled=true;document.getElementById('tableInfo').textContent='未载入桌面实测参考；不猜测桌面或地面高度。';}
const screen=new THREE.Group();const lid=new THREE.Group();lid.position.copy(screenCenter);lid.quaternion.copy(physicalScreenRotation);screen.add(lid);
const shell=new THREE.Mesh(new THREE.BoxGeometry(.31555,.20247,.006),new THREE.MeshStandardMaterial({color:0x1d2b3b,metalness:.35,roughness:.5}));shell.position.z=-.0035;lid.add(shell);
const screenPlane=new THREE.Mesh(new THREE.PlaneGeometry(.30155,.18847),new THREE.MeshBasicMaterial({color:0x18364c,transparent:true,opacity:.88,side:THREE.DoubleSide}));screenPlane.position.z=.0001;lid.add(screenPlane);lid.add(new THREE.LineSegments(new THREE.EdgesGeometry(shell.geometry),new THREE.LineBasicMaterial({color:0x778da8,transparent:true,opacity:.7})));
if(geometry.enabled){
 const hinge=new THREE.Vector3(...geometry.base_hinge_render_m);const [baseWidth,baseThickness,baseDepth]=geometry.schematic_base_size_m;
 const base=new THREE.Mesh(new THREE.BoxGeometry(baseWidth,baseThickness,baseDepth),new THREE.MeshStandardMaterial({color:0x33465b,metalness:.3,roughness:.6}));base.position.set(hinge.x,-baseThickness/2,hinge.z+baseDepth/2);screen.add(base);
 const baseEdges=new THREE.LineSegments(new THREE.EdgesGeometry(base.geometry),new THREE.LineBasicMaterial({color:0x7388a0}));baseEdges.position.copy(base.position);screen.add(baseEdges);
 const hingeBar=new THREE.Mesh(new THREE.CylinderGeometry(.003,.003,baseWidth*.91,16),new THREE.MeshStandardMaterial({color:0x8493a5,metalness:.6,roughness:.4}));hingeBar.rotation.z=Math.PI/2;hingeBar.position.set(hinge.x,.0015,hinge.z+.002);screen.add(hingeBar);
 const keyGeometry=new THREE.BoxGeometry(.016,.0009,.014);const keyMaterial=new THREE.MeshStandardMaterial({color:0x131e2d,roughness:.8});for(let row=0;row<4;row++)for(let col=0;col<13;col++){const key=new THREE.Mesh(keyGeometry,keyMaterial);key.position.set(hinge.x+(col-6)*.0193,.0005,hinge.z+.034+row*.018);screen.add(key);}
 const touchpad=new THREE.Mesh(new THREE.BoxGeometry(.102,.0006,.059),new THREE.MeshStandardMaterial({color:0x25384c,roughness:.65}));touchpad.position.set(hinge.x,.0004,hinge.z+.154);screen.add(touchpad);const padEdges=new THREE.LineSegments(new THREE.EdgesGeometry(touchpad.geometry),new THREE.LineBasicMaterial({color:0x8193a8,transparent:true,opacity:.65}));padEdges.position.copy(touchpad.position);screen.add(padEdges);
 const label=labelSprite(`${geometry.opening_angle_degrees.toFixed(2)}°`,'#b7d3ee');label.position.set(hinge.x+baseWidth/2+.055,.11,hinge.z);label.scale.set(.17,.064,1);screen.add(label);
 document.getElementById('sceneTag').textContent='掌托水平参考 · 原点 = 屏幕中心在掌托平面的投影 · 单位 m';
 document.getElementById('geometryInfo').textContent=`两平面测得开盖角 ${geometry.opening_angle_degrees.toFixed(3)}°。横向取两面交线；原校准平面内 X/Y 朝向未独立核实。切片在掌托水平坐标中进行。`;
}else{document.getElementById('frameSubtitle').textContent='500 Hz · 原校准平面坐标（未提供掌托基准）';document.getElementById('sceneTag').textContent='原校准平面坐标 · 面内朝向未核实 · 单位 m';}
scene.add(screen);
const laptopBounds=new THREE.Box3().setFromObject(screen);function laptopDistance(){const size=laptopBounds.getSize(new THREE.Vector3());return Math.max(size.x,size.y,size.z,.2)/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov)/2))*1.35;}function focusLaptop(){const target=laptopBounds.getCenter(new THREE.Vector3());const direction=camera.position.clone().sub(controls.target).normalize();controls.target.copy(target);camera.position.copy(target).add(direction.multiplyScalar(laptopDistance()));camera.lookAt(target);controls.update();state.frameMode='laptop';}state.focusLaptop=focusLaptop;document.getElementById('focusLaptop').addEventListener('click',focusLaptop);
if(multisourceFit){const group=new THREE.Group();group.visible=false;scene.add(group);const colors=[0x5aa9e6,0x8bd8c7,0xf4ad68,0xcc9ef7];const names={left_palm_side_lower_region:'掌托左区',right_palm_side_lower_region:'掌托右区',left_hinge_region:'转轴左区',right_hinge_region:'转轴右区'};for(let i=0;i<multisourceFit.regions.length;i++){const region=multisourceFit.regions[i],color=colors[i%colors.length];const box=new THREE.Mesh(new THREE.BoxGeometry(...region.size_m),new THREE.MeshBasicMaterial({color,transparent:true,opacity:.12,depthWrite:false}));box.position.set(...region.center_m);group.add(box);const edges=new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry),new THREE.LineBasicMaterial({color,transparent:true,opacity:.95}));edges.position.copy(box.position);group.add(edges);const label=labelSprite(names[region.name]||'先验区域','#'+color.toString(16).padStart(6,'0'));label.scale.set(.105,.0394,1);label.position.copy(box.position).add(new THREE.Vector3(0,region.size_m[1]/2+.021,0));group.add(label);}const coherent=multisourceFit.selected_model,diagonal=multisourceFit.diagonal_power_only_comparison;const percent=value=>(value*100).toFixed(2)+'%';const summary=document.getElementById('multisourceSummary');summary.style.whiteSpace='pre-line';summary.textContent=`复 PSD 等效基：训练 ${percent(coherent.train_relative_covariance_error)} / 空间留出 ${percent(coherent.spatial_holdout_relative_covariance_error)}\n无相干功率对照：训练 ${percent(diagonal.train_relative_covariance_error)} / 空间留出 ${percent(diagonal.spatial_holdout_relative_covariance_error)}\n分类色仅标识区域，不映射等效基系数、FR 或源功率。`;document.getElementById('multisourcePanel').style.display='block';document.getElementById('showRegions').addEventListener('input',event=>{group.visible=event.target.checked;state.multisource.regionsVisible=group.visible;});state.multisource={enabled:true,regionCount:multisourceFit.regions.length,regionsVisible:false,regions:multisourceFit.regions.map(r=>({name:r.name,center_m:r.center_m,size_m:r.size_m,placement_basis:r.placement_basis})),trainRelativeCovarianceError:coherent.train_relative_covariance_error,holdoutRelativeCovarianceError:coherent.spatial_holdout_relative_covariance_error,sourceFRKnown:false,sourceRegionsMeasured:false,coefficientColors:false};}else{state.multisource={enabled:false};}
const pointGeometry=new THREE.BufferGeometry();const pointMaterial=new THREE.PointsMaterial({size:5,sizeAttenuation:false,vertexColors:true,transparent:true,opacity:.95});const pointCloud=new THREE.Points(pointGeometry,pointMaterial);scene.add(pointCloud);let visiblePoints=[];
const lineGeometry=new THREE.BufferGeometry();const path=new THREE.LineSegments(lineGeometry,new THREE.LineBasicMaterial({color:0x829ab8,transparent:true,opacity:.35}));scene.add(path);
const voxelMaterial=new THREE.MeshBasicMaterial({transparent:true,opacity:.2,depthWrite:false,vertexColors:false});const voxelCloud=new THREE.InstancedMesh(new THREE.BoxGeometry(spacing*.82,spacing*.82,spacing*.82),voxelMaterial,Math.max(1,voxels.length));voxelCloud.count=0;scene.add(voxelCloud);let visibleVoxels=[];
const arrows=new THREE.Group();scene.add(arrows);
const proxyAvailable=Boolean(field.directional_experimental&&field.foa_assumption_enabled)&&measured.some(p=>['proxy_re_x_adc2','proxy_re_y_adc2','proxy_re_z_adc2'].every(key=>Number.isFinite(p[key])));
document.getElementById('showProxy').disabled=!proxyAvailable;
document.getElementById('proxyNote').textContent=proxyAvailable?'实验假设 AmbiX/SN3D：仅显示 Re(Cᵢ₀) 方向相关量，箭头等长；非声强、声源定位或已验证传播方向。':'未启用或缺少显式 FOA 假设，方向 proxy 不可用；能量点仍可查看。';
const nearestMaximum=Math.max(spacing,...voxels.map(v=>v.nearest_distance_m));const supportInput=document.getElementById('support');supportInput.max=Math.max(.5,Math.ceil(nearestMaximum*100*2)/2);supportInput.value=supportInput.max;
const sliceAxis=document.getElementById('sliceAxis'),slicePosition=document.getElementById('slicePosition'),sliceThickness=document.getElementById('sliceThickness');sliceThickness.value=Math.max(.5,spacing*100);
function insideSlice(position){if(sliceAxis.value==='none')return true;const axis=Number(sliceAxis.value);return Math.abs(position[axis]-Number(slicePosition.value)/100)<=Number(sliceThickness.value)/100;}
function update(){
 visiblePoints=measured.filter(point=>insideSlice(point.position));visibleVoxels=voxels.filter(voxel=>insideSlice(voxel.position)&&voxel.nearest_distance_m<=Number(supportInput.value)/100+1e-9);
 const positions=[],colors=[];for(const point of visiblePoints){positions.push(...point.position);const c=color(point.energy_adc2);colors.push(c.r,c.g,c.b);}pointGeometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));pointGeometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));pointGeometry.computeBoundingSphere();pointCloud.visible=document.getElementById('showPoints').checked;
 const segments=[];if(!spatialSampling)for(let i=1;i<measured.length;i++){const a=measured[i-1],b=measured[i];const contiguous=Number.isFinite(a.start_sample)&&Number.isFinite(a.frames)&&Number.isFinite(b.start_sample)?b.start_sample===a.start_sample+a.frames:!Number.isFinite(a.start_sample)&&!Number.isFinite(b.start_sample)&&Number.isFinite(a.window_index)&&b.window_index-a.window_index===1;if(insideSlice(a.position)&&insideSlice(b.position)&&contiguous)segments.push(...a.position,...b.position);}lineGeometry.setAttribute('position',new THREE.Float32BufferAttribute(segments,3));lineGeometry.computeBoundingSphere();path.visible=!spatialSampling&&document.getElementById('showPath').checked;state.pathSegmentCount=segments.length/6;
 const matrix=new THREE.Matrix4();for(let i=0;i<visibleVoxels.length;i++){const voxel=visibleVoxels[i];matrix.compose(new THREE.Vector3(...voxel.position),dataRotation,new THREE.Vector3(1,1,1));voxelCloud.setMatrixAt(i,matrix);voxelCloud.setColorAt(i,color(voxel.energy_adc2));}voxelCloud.count=visibleVoxels.length;voxelCloud.instanceMatrix.needsUpdate=true;if(voxelCloud.instanceColor)voxelCloud.instanceColor.needsUpdate=true;voxelCloud.computeBoundingSphere();voxelCloud.visible=document.getElementById('showVoxels').checked;
 screen.visible=document.getElementById('showScreen').checked;if(tablePlane){tablePlane.visible=document.getElementById('showTable').checked;if(tablePlane.userData.referenceLabel)tablePlane.userData.referenceLabel.visible=tablePlane.visible;}
 while(arrows.children.length){const child=arrows.children[0];arrows.remove(child);child.line?.material?.dispose();child.cone?.material?.dispose();}
 if(proxyAvailable&&document.getElementById('showProxy').checked){const stride=Math.max(1,Math.ceil(visiblePoints.length/70));for(let i=0;i<visiblePoints.length;i+=stride){const p=visiblePoints[i];const direction=new THREE.Vector3(p.proxy_re_x_adc2,p.proxy_re_y_adc2,p.proxy_re_z_adc2).applyQuaternion(dataRotation);if(direction.lengthSq()>0&&Number.isFinite(direction.lengthSq()))arrows.add(new THREE.ArrowHelper(direction.normalize(),new THREE.Vector3(...p.position),.04,0xb3f2d6,.009,.004));}}
 state.visiblePointCount=pointCloud.visible?visiblePoints.length:0;state.visibleVoxelCount=voxelCloud.visible?visibleVoxels.length:0;state.sliceAxis=sliceAxis.value;state.supportRadiusCm=Number(supportInput.value);state.directionProxyVisible=proxyAvailable&&document.getElementById('showProxy').checked;
 document.getElementById('slicePositionValue').textContent=sliceAxis.value==='none'?'—':Number(slicePosition.value).toFixed(1)+' cm';document.getElementById('sliceThicknessValue').textContent=Number(sliceThickness.value).toFixed(1)+' cm';document.getElementById('supportValue').textContent=Number(supportInput.value).toFixed(1)+' cm';
 updateSource();
}
sliceAxis.addEventListener('change',()=>{const enabled=sliceAxis.value!=='none';slicePosition.disabled=!enabled;sliceThickness.disabled=!enabled;if(enabled){const axis=Number(sliceAxis.value);slicePosition.min=Math.floor(worldBounds.min.getComponent(axis)*100);slicePosition.max=Math.ceil(worldBounds.max.getComponent(axis)*100);slicePosition.value=center.getComponent(axis)*100;}update();});
for(const id of ['showPoints','showPath','showVoxels','showScreen','showTable','showProxy','slicePosition','sliceThickness','support'])document.getElementById(id).addEventListener('input',update);
document.getElementById('opacity').addEventListener('input',event=>{voxelMaterial.opacity=Number(event.target.value);document.getElementById('opacityValue').textContent=Math.round(voxelMaterial.opacity*100)+'%';});update();
if(sourceVolume){
 document.getElementById('sourcePanel').style.display='block';document.getElementById('sourceLegend').style.display='block';
 const sourceGrid=sourceVolume.grid;const [snx,sny,snz]=sourceGrid.shape;const sourceRows=columnsToObjects(sourceVolume.voxels);for(const v of sourceRows){const flat=v.flat_index;v.position=[sourceGrid.origin_m[0]+flat%snx*sourceGrid.spacing_m,sourceGrid.origin_m[1]+Math.floor(flat/snx)%sny*sourceGrid.spacing_m,sourceGrid.origin_m[2]+Math.floor(flat/(snx*sny))*sourceGrid.spacing_m];}
 const sourcePoints=sourceVolume.selected_points||[];const directionPoints=sourceDirections?.points||[];const peaks=sourceVolume.peaks||[];
 state.source={enabled:true,interpretation:'receiver_direction_backprojection_single_source_comparison_not_emitter_model',scoreUnit:'dimensionless_relative_support_not_energy_or_probability',voxelCount:sourceRows.length,selectedSpatialBins:sourcePoints.length,selectedPointIndex:null,directionVertexCount:0,pathCount:0};
 const sourceGroup=new THREE.Group();scene.add(sourceGroup);const sourceMaterial=new THREE.MeshBasicMaterial({transparent:true,opacity:.12,depthWrite:false});sourceCloud=new THREE.InstancedMesh(new THREE.BoxGeometry(sourceGrid.spacing_m*.9,sourceGrid.spacing_m*.9,sourceGrid.spacing_m*.9),sourceMaterial,Math.max(1,sourceRows.length));sourceCloud.count=0;sourceGroup.add(sourceCloud);
 const supportColor=score=>new THREE.Color('#4c1d95').lerp(new THREE.Color('#c026d3'),Math.min(1,score*2)).lerp(new THREE.Color('#f9a8d4'),Math.max(0,(score-.5)*2));
 const markerGeometry=new THREE.BufferGeometry();markerGeometry.setAttribute('position',new THREE.Float32BufferAttribute(sourcePoints.flatMap(point=>point.position_m),3));sourceMarkers=new THREE.Points(markerGeometry,new THREE.PointsMaterial({color:0xe9d5ff,size:9,sizeAttenuation:false,transparent:true,opacity:.95}));sourceMarkers.userData.sourceBins=true;sourceGroup.add(sourceMarkers);
 const peakGroup=new THREE.Group();sourceGroup.add(peakGroup);for(const peak of peaks.slice(0,8)){if(!Array.isArray(peak.position_m))continue;const marker=new THREE.Mesh(new THREE.OctahedronGeometry(.008),new THREE.MeshBasicMaterial({color:0xf9a8d4,wireframe:true}));marker.position.set(...peak.position_m);peakGroup.add(marker);}
 const responseGroup=new THREE.Group();sourceGroup.add(responseGroup);const selectedMarker=new THREE.Mesh(new THREE.SphereGeometry(.006,12,8),new THREE.MeshBasicMaterial({color:0xffffff,wireframe:true}));selectedMarker.visible=false;sourceGroup.add(selectedMarker);
 const selector=document.getElementById('sourcePoint');for(const point of sourcePoints){const option=document.createElement('option');option.value=String(point.selection_index);option.textContent=`分箱 ${point.selection_index+1} · ${point.position_m.map(x=>(x*100).toFixed(1)).join(' / ')} cm`;selector.appendChild(option);}
 const threshold=document.getElementById('sourceThreshold');const minimum=sourceRows.length?Math.min(...sourceRows.map(v=>v.support_score)):0;threshold.min=Math.max(0,Math.floor(minimum*100)/100);threshold.value=Math.min(1,Math.max(Number(threshold.min),.8));
 const diagnostics=sourceVolume.diagnostics||{};const boundary=Boolean(diagnostics.support_touches_roi_boundary||sourceVolume.primary_peak?.touches_roi_boundary||peaks[0]?.touches_roi_boundary);const flat=Boolean(diagnostics.uninformative||diagnostics.score_distribution?.flat);const holdout=diagnostics.split_spatial_bins_holdout||[];const displacement=diagnostics.holdout_peak_displacement_m;const holdoutLabel=Number.isFinite(displacement)?`分箱留出两组峰相距 ${(displacement*100).toFixed(1)} cm（非精度认证）`:holdout.length?'留出未形成可比较的唯一峰':'暂无分箱留出摘要';
 const attenuationError=sourceVolume.attenuation_diagnostic?.relative_rmse;const attenuationLabel=Number.isFinite(attenuationError)?`条件 1/r² 衰减拟合误差 ${(attenuationError*100).toFixed(1)}%；模型失配不能当作定位准确。`:'未提供条件衰减拟合诊断。';
 document.getElementById('sourceSummary').textContent=`接收方向单源假设对照，不是笔记本心形发射模型。${sourcePoints.length} / ${diagnostics.all_spatial_bin_count??sourcePoints.length} 个空间分箱参与反演 · ${peaks.length} 个接收波束一致性峰。${flat?'方向信息不足 / 支持近乎平坦，不能唯一定位。':'保留宽支持与多峰，不对应实际发射单元。'}${boundary?'支持区触及 ROI 边界。':'范围受所选 ROI 限制。'}${holdoutLabel}。${attenuationLabel}距离未验证。`;
 function clearResponse(){while(responseGroup.children.length){const child=responseGroup.children[0];responseGroup.remove(child);child.geometry?.dispose();if(child.material)child.material.dispose();}state.source.directionVertexCount=0;state.source.pathCount=0;}
 function drawResponse(){clearResponse();const selected=sourcePoints.find(point=>point.selection_index===state.source.selectedPointIndex);const point=directionPoints.find(point=>point.selection_index===state.source.selectedPointIndex);selectedMarker.visible=Boolean(selected)&&document.getElementById('showSourcePoints').checked;if(selected)selectedMarker.position.set(...selected.position_m);if(!selected||!point)return;
  const directions=point.directions;const response=point.beam_response_over_sphere_max;const center=point.position_m;const angular=sourceDirections.angular_grid;const na=angular.azimuth_centers_degrees.length,ne=angular.elevation_centers_degrees.length;const radius=.09;
  if(document.getElementById('showHemisphere').checked){const positions=[],colors=[];for(let i=0;i<directions.length;i++){positions.push(...directions[i].map((v,k)=>center[k]+radius*v));const c=new THREE.Color('#17324e').lerp(new THREE.Color('#67e8f9'),Math.max(0,Math.min(1,response[i])));colors.push(c.r,c.g,c.b);}const indices=[];const wrap=Math.abs(angular.azimuth_edges_degrees.at(-1)-angular.azimuth_edges_degrees[0])>359;for(let e=0;e<ne-1;e++)for(let a=0;a<(wrap?na:na-1);a++){const b=(a+1)%na;const i=e*na+a,j=e*na+b,k=(e+1)*na+a,l=(e+1)*na+b;indices.push(i,k,j,j,k,l);}const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));g.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));g.setIndex(indices);g.computeVertexNormals();responseGroup.add(new THREE.Mesh(g,new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,transparent:true,opacity:.68,depthWrite:false})));state.source.directionVertexCount=directions.length;}
  if(document.getElementById('showSourcePaths').checked){const candidates=response.map((v,i)=>[v,i]).sort((a,b)=>b[0]-a[0]);const chosen=[];for(const [value,index] of candidates){if(value<=0)break;const d=new THREE.Vector3(...directions[index]).normalize();if(chosen.some(old=>old.dot(d)>Math.cos(THREE.MathUtils.degToRad(22))))continue;chosen.push(d);const length=.23;const cone=new THREE.Mesh(new THREE.ConeGeometry(.024,length,18,1,true),new THREE.MeshBasicMaterial({color:0x67e8f9,side:THREE.DoubleSide,transparent:true,opacity:.07,depthWrite:false}));cone.quaternion.setFromUnitVectors(new THREE.Vector3(0,-1,0),d);cone.position.set(...center).add(d.clone().multiplyScalar(length/2));responseGroup.add(cone);if(chosen.length===4)break;}state.source.pathCount=chosen.length;}
  const metrics=point.metrics||{};document.getElementById('directionNote').textContent=`分箱 ${selected.selection_index+1}：${directions.length} 个朝屏幕的相关波束采样，不代表独立射线或10°分辨率。半径固定为9cm示意，颜色为0..1归一化方向响应；宽路径最多4条，不是声束、声压瓣或能流。${spatialSampling?'占用段不代表新增独立空间观测。':'20ms子窗不等于新增独立观测。'}${metrics.directional_contrast!==undefined?' 方向对比度 '+Number(metrics.directional_contrast).toFixed(3):''}`;
 }
 selectSource=index=>{state.source.selectedPointIndex=index===null?null:Number(index);selector.value=index===null?'':String(index);drawResponse();};state.source.selectPoint=selectSource;
 updateSource=()=>{sourceVisible=sourceRows.filter(v=>v.support_score>=Number(threshold.value)&&insideSlice(v.position));const matrix=new THREE.Matrix4();for(let i=0;i<sourceVisible.length;i++){matrix.makeTranslation(...sourceVisible[i].position);sourceCloud.setMatrixAt(i,matrix);sourceCloud.setColorAt(i,supportColor(sourceVisible[i].support_score));}sourceCloud.count=sourceVisible.length;sourceCloud.instanceMatrix.needsUpdate=true;if(sourceCloud.instanceColor)sourceCloud.instanceColor.needsUpdate=true;sourceCloud.computeBoundingSphere();sourceCloud.visible=document.getElementById('showSourceVolume').checked;document.getElementById('sourceLegend').style.display=sourceCloud.visible?'block':'none';sourceMarkers.visible=document.getElementById('showSourcePoints').checked;peakGroup.visible=sourceMarkers.visible;state.source.visibleVoxelCount=sourceCloud.visible?sourceVisible.length:0;state.source.threshold=Number(threshold.value);document.getElementById('sourceThresholdValue').textContent=Number(threshold.value).toFixed(2);drawResponse();};
 selector.addEventListener('change',()=>selectSource(selector.value===''?null:Number(selector.value)));for(const id of ['showSourceVolume','showSourcePoints','sourceThreshold','showHemisphere','showSourcePaths'])document.getElementById(id).addEventListener('input',updateSource);updateSource();
}else{state.source={enabled:false};}
const raycaster=new THREE.Raycaster();raycaster.params.Points.threshold=.012;let down=null;renderer.domElement.addEventListener('pointerdown',event=>down=[event.clientX,event.clientY]);renderer.domElement.addEventListener('pointerup',event=>{if(!down||Math.hypot(event.clientX-down[0],event.clientY-down[1])>4)return;const rect=renderer.domElement.getBoundingClientRect();raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),camera);const objects=[];if(pointCloud.visible)objects.push(pointCloud);if(voxelCloud.visible)objects.push(voxelCloud);if(sourceMarkers?.visible)objects.push(sourceMarkers);if(sourceCloud?.visible)objects.push(sourceCloud);const hit=raycaster.intersectObjects(objects,false)[0];if(!hit)return;if(hit.object===sourceMarkers){const points=sourceVolume.selected_points||[];selectSource(points[hit.index]?.selection_index??null);return;}if(hit.object===sourceCloud){const selected=sourceVisible[hit.instanceId];if(selected){document.getElementById('inspector').textContent=`接收波束反投影对照（非发射能量）\n掌托坐标 ${selected.position.map(v=>(v*100).toFixed(2)).join(' / ')} cm\n无量纲相对支持 ${selected.support_score.toFixed(3)}\n不是能量、概率或置信度；距离未验证。`;state.source.selectedVoxel=selected;}return;}const voxel=hit.object===voxelCloud;const value=voxel?visibleVoxels[hit.instanceId]:visiblePoints[hit.index];if(!value)return;const xyz=value.position.map(v=>(v*100).toFixed(2));const raw=value.rawPosition.map(v=>(v*100).toFixed(2));let text=`${voxel?'支持体素':spatialSampling?'空间单元':'实测窗口'}\n掌托坐标 X ${xyz[0]} · Y ${xyz[1]} · Z ${xyz[2]} cm\n原校准平面坐标（面内朝向未核实）\nX ${raw[0]} · Y ${raw[1]} · Z ${raw[2]} cm\n相对能量 ${db(value.energy_adc2).toFixed(2)} dB\nC00 ${value.energy_adc2.toExponential(3)} ADC²`;if(voxel)text+=`\n最近支持分箱 ${(value.nearest_distance_m*100).toFixed(2)} cm\n空间分箱 ${value.spatial_bin_count??'—'} · 几何秩 ${value.geometry_rank??'—'}\n方差proxy ${Number.isFinite(value.energy_variance_adc4)?value.energy_variance_adc4.toExponential(2):'—'}`;else if(spatialSampling)text+=`\n空间单元 ${value.window_index} · cell ${[value.cell_i,value.cell_j,value.cell_k].join(' / ')}\n累计有效时长 ${Number.isFinite(value.duration_seconds)?(value.duration_seconds*1000).toFixed(1)+' ms':'未提供'}（可不连续）\n覆盖段数 ${value.segment_count??'未提供'} · 质量 ${value.motion_quality??'未提供'}\n占用范围直径 ${Number.isFinite(value.motion_diameter_m)?(value.motion_diameter_m*100).toFixed(2)+' cm':'未提供'}\n单元排序不表示时间轨迹`;else text+=`\n输出点 ${value.window_index} · 原父窗 ${value.source_window_index??value.window_index}\n时长 ${Number.isFinite(value.duration_seconds)?(value.duration_seconds*1000).toFixed(0)+' ms':'未提供'} · 运动质量 ${value.motion_quality??'未提供'}\n子窗不是新增独立观测\n窗内运动直径 ${Number.isFinite(value.motion_diameter_m)?(value.motion_diameter_m*100).toFixed(2)+' cm':'—'}\n插值支持 ${value.support_eligible===1||value.support_eligible===true?'合格':value.support_eligible===0||value.support_eligible===false?'不参与（运动足迹超限）':'未提供状态'}`;document.getElementById('inspector').textContent=text;state.selected={kind:voxel?'voxel':'point',render_position_m:value.position,raw_position_m:value.rawPosition,energy_adc2:value.energy_adc2,support_eligible:value.support_eligible};});
const resize=()=>{const w=viewport.clientWidth,h=viewport.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();};new ResizeObserver(resize).observe(viewport);resize();
state.cameraSnapshot=()=>({position:camera.position.toArray(),target:controls.target.toArray(),up:camera.up.toArray()});state.setView=setView;state.update=update;
function animate(){requestAnimationFrame(animate);controls.update();renderer.render(scene,camera);state.frames++;state.render={calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,points:renderer.info.render.points};state.ready=true;}animate();
}catch(error){fail(error);}
</script></body></html>'''


def render_viewer(field,calibration=None,source_volume=None,direction_likelihoods=None,table_reference=None,multisource_fit=None):
    field=validate_field(field)
    # Escape HTML-significant characters even inside an application/json block.
    # No field metadata is ever treated as executable HTML or JavaScript.
    encoded=json.dumps(field,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    geometry=render_geometry(field,calibration)
    geometry["table_reference"]=table_geometry(table_reference,geometry)
    validate_multisource_fit(multisource_fit,geometry)
    validate_source_volume(source_volume,geometry,direction_likelihoods)
    render_json=json.dumps(geometry,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    source_json=json.dumps(dict(volume=source_volume,directions=direction_likelihoods),ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    multi_json=json.dumps(multisource_fit,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    return HTML_TEMPLATE.replace("__FIELD_JSON__",encoded).replace("__RENDER_JSON__",render_json).replace("__SOURCE_JSON__",source_json).replace("__MULTISOURCE_JSON__",multi_json)


def build_viewer(field_path,output_html,calibration_path=None,source_volume_path=None,table_reference_path=None,multisource_fit_path=None):
    source=Path(field_path).resolve()
    field=json.loads(source.read_text(encoding="utf-8-sig"))
    calibration=json.loads(Path(calibration_path).read_text(encoding="utf-8-sig")) if calibration_path else None
    source_volume=json.loads(Path(source_volume_path).read_text(encoding="utf-8-sig")) if source_volume_path else None
    if source_volume is not None:
        expected=source_volume.get("provenance",{}).get("field_sha256")
        if expected and hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
            raise ValueError("Source support was built from a different field.json hash")
    directions_path=Path(source_volume_path).with_name("direction-likelihoods.json") if source_volume_path else None
    directions=json.loads(directions_path.read_text(encoding="utf-8-sig")) if directions_path and directions_path.is_file() else None
    table_reference=json.loads(Path(table_reference_path).read_text(encoding="utf-8-sig")) if table_reference_path else None
    multisource_fit=json.loads(Path(multisource_fit_path).read_text(encoding="utf-8-sig")) if multisource_fit_path else None
    if multisource_fit is not None:
        expected=multisource_fit.get("provenance",{}).get("field_sha256")
        if not expected or hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
            raise ValueError("Multiregion fit must match the current field.json provenance hash")
    result=render_viewer(field,calibration,source_volume,directions,table_reference,multisource_fit)
    output=Path(output_html).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(result,encoding="utf-8")
    result_geometry=render_geometry(field,calibration);result_geometry["table_reference"]=table_geometry(table_reference,result_geometry)
    return dict(output=str(output),point_count=len(field["points"]["rows"]),voxel_count=len(field["voxels"]["rows"]),
                embedded_data=True,network_data_fetch=False,vendor_imports=["./vendor/three.module.js","./vendor/OrbitControls.js"],
                render_geometry=result_geometry,source_volume_embedded=source_volume is not None,
                direction_likelihoods_embedded=directions is not None,multisource_fit_embedded=multisource_fit is not None)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field_path");parser.add_argument("--output",required=True)
    parser.add_argument("--calibration",help="Same-world calibration bundle; palm-horizontal render frame only, raw field unchanged")
    parser.add_argument("--source-volume",help="Independent dimensionless virtual-source support JSON; sibling direction-likelihoods.json is embedded when present")
    parser.add_argument("--table-reference",help="Measured table_world_pose; reproject into current palm render frame, finite extent is schematic")
    parser.add_argument("--multisource-fit",help="Same-world four nominal source-region fit; categorical boxes and residual summary only, not FR or localization")
    args=parser.parse_args(argv)
    result=build_viewer(args.field_path,args.output,args.calibration,args.source_volume,args.table_reference,args.multisource_fit)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=="__main__":main()

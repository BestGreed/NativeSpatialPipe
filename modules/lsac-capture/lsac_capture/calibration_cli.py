"""Spatial calibration tools; sample/planes/heading are offline and silent."""
import argparse
import json
from pathlib import Path
import time
import uuid

from .calibration import (A_SERIAL, B_SERIAL, calibrate_heading, calibrate_planes,
                          summarize_static_pose, target_geometry, target_svg)
from .calibration import sample_before_marker
from .calibration import scaled_target_geometry


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    with Path(path).open("x",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)


def read_events(path):
    # Fix a byte boundary at open: do not chase a growing live spool forever.
    # A final unterminated line is still being written and is retried on the
    # next invocation. Every complete line is parsed, so real corruption fails.
    with Path(path).open("rb") as f:
        boundary=f.seek(0,2)
        f.seek(0)
        while f.tell()<boundary:
            line=f.readline(boundary-f.tell())
            if not line.endswith(b"\n"):
                break
            if line.strip():
                yield json.loads(line.decode("utf-8-sig"))


class OneShotMarkerGate:
    """Lock before I/O so held-key repeats cannot enqueue extra samples.

    Failures stay locked too: an ambiguous partially completed request needs
    an explicit operator rearm, never an automatic retry.
    """
    def __init__(self):
        self.locked=False

    def claim(self):
        if self.locked:
            return False
        self.locked=True
        return True

    def rearm(self):
        self.locked=False


def enable_native_pixel_coordinates():
    import os
    if os.name == "nt":
        import ctypes as C
        C.windll.user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))


def list_monitors():
    """Read current Windows desktop monitor rectangles; does not change modes."""
    import os
    if os.name != "nt":
        raise OSError("Named-monitor enumeration currently requires Windows")
    import ctypes as C
    from ctypes import wintypes as W
    enable_native_pixel_coordinates()
    class MonitorInfo(C.Structure):
        _fields_=[("cbSize",W.DWORD),("rcMonitor",W.RECT),("rcWork",W.RECT),
                  ("dwFlags",W.DWORD),("szDevice",W.WCHAR*32)]
    user=C.WinDLL("user32",use_last_error=True)
    get_info=user.GetMonitorInfoW
    get_info.argtypes=[W.HMONITOR,C.POINTER(MonitorInfo)]; get_info.restype=W.BOOL
    rows=[]; errors=[]
    callback_type=C.WINFUNCTYPE(W.BOOL,W.HMONITOR,W.HDC,C.POINTER(W.RECT),W.LPARAM)
    def visit(handle,dc,rect,data):
        info=MonitorInfo();info.cbSize=C.sizeof(info)
        if not get_info(handle,C.byref(info)):
            errors.append(C.get_last_error());return False
        r=info.rcMonitor
        rows.append(dict(device=info.szDevice,left=r.left,top=r.top,right=r.right,bottom=r.bottom,
                         width_px=r.right-r.left,height_px=r.bottom-r.top,primary=bool(info.dwFlags&1)))
        return True
    callback=callback_type(visit)
    user.EnumDisplayMonitors.argtypes=[W.HDC,C.POINTER(W.RECT),callback_type,W.LPARAM]
    user.EnumDisplayMonitors.restype=W.BOOL
    if not user.EnumDisplayMonitors(None,None,callback,0) or errors:
        raise OSError(f"Monitor enumeration failed: {errors or C.get_last_error()}")
    for i,row in enumerate(rows):row["index"]=i
    return rows


def choose_monitor(monitors,selector=None):
    if selector is None:
        selected=[m for m in monitors if m["primary"]]
    elif str(selector).isdigit():
        selected=[m for m in monitors if m.get("index")==int(selector)]
    else:
        name=str(selector).casefold()
        selected=[m for m in monitors if m["device"].casefold()==name or m["device"].split("\\")[-1].casefold()==name]
    if len(selected)!=1:
        raise ValueError("Monitor selector must resolve to exactly one active monitor")
    return selected[0]


def show_target(g,rig_folder=None,label="screen_contact",monitor=None):
    """Explicit monitor placement, native pixels; no tracker/audio handles."""
    import os
    enable_native_pixel_coordinates()
    selected=choose_monitor(list_monitors(),monitor) if os.name=="nt" else None
    import tkinter as tk
    root=tk.Tk()
    actual=(selected["width_px"],selected["height_px"]) if selected else (root.winfo_screenwidth(),root.winfo_screenheight())
    if actual != (g["width_px"],g["height_px"]):
        root.destroy()
        raise ValueError(f"Selected native screen is {actual}; supplied target resolution differs")
    if selected:
        root.overrideredirect(True)
        root.geometry(f"{actual[0]}x{actual[1]}+0+0")
    else:
        if monitor is not None:
            root.destroy();raise OSError("Explicit monitor selection requires Windows")
        root.attributes("-fullscreen",True)
    root.configure(bg="#111827")
    canvas=tk.Canvas(root,bg="#111827",highlightthickness=0)
    canvas.pack(fill="both",expand=True)
    x,y=g["center_px"];rx,ry=g["radius_px"]
    circle=canvas.create_oval(x-rx,y-ry,x+rx,y+ry,outline="white",width=2)
    canvas.create_line(x-12,y,x+12,y,fill="#60a5fa",width=2)
    canvas.create_line(x,y-12,x,y+12,fill="#60a5fa",width=2)
    arrow=canvas.create_line(x,y-ry-12,x,y-ry-40,fill="white",width=3,arrow=tk.LAST)
    heading_text=canvas.create_text(x,max(28,y-ry-65),text="TRACKER A / SCREEN UP",fill="white",font=("Arial",20))
    usb_text=canvas.create_text(x,min(g["height_px"]-140,y+ry+25),text="USB PORT TOWARD SCREEN BOTTOM",fill="white",font=("Arial",14))
    dimensions_text=canvas.create_text(x,min(g["height_px"]-115,y+ry+50),
                       text=f"Tracker {g['diameter_mm']:g} mm (fixed) / circle scale 100%",fill="white",font=("Arial",14))
    canvas.create_text(x,g["height_px"]-80,text="− 缩小 / + 放大 / 滚轮微调 / 0 尺寸复位 / R 解锁重采 / Esc 退出",fill="white",font=("Microsoft YaHei",14))
    fit_state=dict(scale=1.,adjustment_count=0,latest_fit_path=None)
    marker_gate=OneShotMarkerGate()
    adjustment_status=canvas.create_text(x,g["height_px"]-52,text="只调整圈的像素大小，Tracker 实际直径不变",fill="#93c5fd",font=("Microsoft YaHei",12))
    def redraw_scale(step):
        if marker_gate.locked:
            canvas.itemconfigure(adjustment_status,text="本次标记已锁定。Esc 退出；需要调整或重采请先按 R。",fill="#86efac")
            return
        candidate=1. if step==0 else fit_state["scale"]*(1.005**step)
        try:
            fit=scaled_target_geometry(g,candidate)
        except ValueError as exc:
            canvas.itemconfigure(adjustment_status,text=str(exc),fill="#fca5a5");return
        fit_state.update(scale=candidate,adjustment_count=fit_state["adjustment_count"]+1)
        rx,ry=fit["radius_px"]
        canvas.coords(circle,x-rx,y-ry,x+rx,y+ry)
        canvas.coords(arrow,x,y-ry-12,x,y-ry-40)
        canvas.coords(heading_text,x,max(28,y-ry-65))
        canvas.coords(usb_text,x,min(g["height_px"]-140,y+ry+25))
        canvas.coords(dimensions_text,x,min(g["height_px"]-115,y+ry+50))
        canvas.itemconfigure(dimensions_text,text=f"Tracker {g['diameter_mm']:g} mm (fixed) / circle scale {candidate*100:.2f}%")
        canvas.itemconfigure(adjustment_status,text=f"有效屏幕尺寸 {fit['effective_width_mm']:.2f} × {fit['effective_height_mm']:.2f} mm（由贴合调整反推）",fill="#93c5fd")
    for key in ("<KeyPress-plus>","<KeyPress-equal>","<KeyPress-KP_Add>"):
        root.bind(key,lambda event:redraw_scale(1))
    for key in ("<KeyPress-minus>","<KeyPress-KP_Subtract>"):
        root.bind(key,lambda event:redraw_scale(-1))
    root.bind("<MouseWheel>",lambda event:redraw_scale(1 if event.delta>0 else -1))
    root.bind("<KeyPress-0>",lambda event:redraw_scale(0))
    root.bind("<Escape>",lambda event:root.destroy())
    marker_status=None
    if rig_folder:
        marker_status=canvas.create_text(x,g["height_px"]-18,text="Hold still >3 seconds; Space records contact marker",fill="white",font=("Arial",12))
        def record_marker(event):
            if not marker_gate.claim():
                return "break"
            try:
                from .rig_session import mark
                result=mark(rig_folder,label)
                fit=scaled_target_geometry(g,fit_state["scale"])
                fit_record=dict(schema=1,kind="screen_target_fit",created_unix=time.time(),label=label,
                                marker_request=result,monitor=selected,target_geometry=fit,
                                nominal_target_geometry=g,adjustment_count=fit_state["adjustment_count"],
                                user_adjusted=fit["user_adjusted"],user_adjusted_once=fit_state["adjustment_count"]>0)
                fit_path=Path(rig_folder).resolve()/("target-fit-"+uuid.uuid4().hex+".json")
                write_json(fit_path,fit_record)
                fit_state["latest_fit_path"]=str(fit_path)
                canvas.itemconfigure(marker_status,text=f"标记已发送：{label}。Esc 退出；重采请按 R 解锁。",fill="#86efac")
                print(json.dumps(dict(marker_request=result,label=label,target_fit_path=str(fit_path),target_geometry=fit),ensure_ascii=False),flush=True)
            except Exception as exc:
                canvas.itemconfigure(marker_status,text=f"标记未完成：{exc}；检查后按 R 解锁重试。",fill="#fca5a5")
                print(json.dumps(dict(marker_error=str(exc),label=label),ensure_ascii=False),flush=True)
            return "break"
        root.bind("<space>",record_marker)
    def rearm_marker(event):
        marker_gate.rearm()
        canvas.itemconfigure(adjustment_status,text="已解锁，保留当前圈尺寸。放稳至少3秒后按 Space 重采。",fill="#93c5fd")
        if marker_status is not None:
            canvas.itemconfigure(marker_status,text="已解锁；放稳至少3秒后按 Space。",fill="white")
        return "break"
    root.bind("<KeyPress-r>",rearm_marker)
    root.bind("<KeyPress-R>",rearm_marker)
    if selected:
        import ctypes as C
        from ctypes import wintypes as W
        user=C.WinDLL("user32",use_last_error=True)
        user.GetAncestor.argtypes=[W.HWND,W.UINT];user.GetAncestor.restype=W.HWND
        user.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT];user.SetWindowPos.restype=W.BOOL
        user.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)];user.GetClientRect.restype=W.BOOL
        root.update_idletasks()
        hwnd=user.GetAncestor(root.winfo_id(),2)
        if not user.SetWindowPos(hwnd,None,selected["left"],selected["top"],actual[0],actual[1],0x0040):
            root.destroy();raise OSError(C.get_last_error(),"Could not place target on selected monitor")
        root.update()
        rect=W.RECT()
        if not user.GetClientRect(hwnd,C.byref(rect)) or (rect.right-rect.left,rect.bottom-rect.top)!=actual:
            root.destroy();raise ValueError("Target client area is not native 1:1 monitor pixels")
    root.focus_force()
    root.mainloop()
    return dict(scaled_target_geometry(g,fit_state["scale"]),latest_fit_path=fit_state["latest_fit_path"])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    commands.add_parser("list-monitors",help="Read-only Windows monitor names and native desktop pixel rectangles")
    sample=commands.add_parser("sample",help="Average one explicitly bounded static pose window")
    sample.add_argument("events_jsonl");sample.add_argument("output_json")
    sample.add_argument("--serial",choices=(A_SERIAL,B_SERIAL),required=True)
    sample.add_argument("--world-id",required=True)
    sample.add_argument("--start-lsl",type=float,required=True)
    sample.add_argument("--end-lsl",type=float,required=True)
    sample.add_argument("--max-position-mm",type=float,default=5.)
    sample.add_argument("--max-orientation-deg",type=float,default=2.)
    marker=commands.add_parser("sample-marker",help="Sample static two seconds before last rig operator marker")
    marker.add_argument("events_jsonl");marker.add_argument("label");marker.add_argument("output_json")
    marker.add_argument("--serial",choices=(A_SERIAL,B_SERIAL),required=True)
    marker.add_argument("--pre-seconds",type=float,default=2.)
    marker.add_argument("--end-gap-seconds",type=float,default=.1)
    plane=commands.add_parser("planes",help="Combine A screen and palm-rest/desk snapshots")
    plane.add_argument("screen_sample_json");plane.add_argument("reference_sample_json")
    plane.add_argument("surface_transforms_json",help="Explicit tracker_from_screen/reference_surface poses")
    plane.add_argument("output_json");plane.add_argument("--reference-kind",choices=("palm_rest","desk"),required=True)
    heading=commands.add_parser("heading",help="Fit one common-axis angle after manual B forward alignment")
    heading.add_argument("sample_json");heading.add_argument("output_json")
    heading.add_argument("--reference-world-direction",nargs=3,type=float,required=True)
    heading.add_argument("--nominal-rotation-wxyz",nargs=4,type=float,required=True)
    heading.add_argument("--mic-forward-local",nargs=3,type=float,required=True)
    heading.add_argument("--max-residual-deg",type=float,default=5.)
    for name in ("target","show-target"):
        target=commands.add_parser(name,help="Physical-size screen target; native pixel rendering required")
        target.add_argument("--width-px",type=int,required=True);target.add_argument("--height-px",type=int,required=True)
        target.add_argument("--width-mm",type=float,required=True);target.add_argument("--height-mm",type=float,required=True)
        target.add_argument("--diameter-mm",type=float,required=True)
        if name=="target":target.add_argument("output_svg")
        else:
            target.add_argument("--rig-folder");target.add_argument("--label",default="screen_contact")
            target.add_argument("--monitor",help="Exact device name, DISPLAY5-style suffix, or list-monitors index; default primary")
    args=parser.parse_args(argv)
    if args.command=="list-monitors":
        result=list_monitors()
    elif args.command=="sample":
        result=summarize_static_pose(read_events(args.events_jsonl),args.serial,args.start_lsl,args.end_lsl,args.world_id,
                                     max_position_deviation_m=args.max_position_mm/1000,
                                     max_orientation_deviation_degrees=args.max_orientation_deg)
        result["source_events_file"]=str(Path(args.events_jsonl).resolve())
        write_json(args.output_json,result)
    elif args.command=="sample-marker":
        result=sample_before_marker(read_events(args.events_jsonl),args.serial,args.label,
                                    pre_seconds=args.pre_seconds,end_gap_seconds=args.end_gap_seconds)
        result["source_events_file"]=str(Path(args.events_jsonl).resolve())
        write_json(args.output_json,result)
    elif args.command=="planes":
        transforms=read_json(args.surface_transforms_json)
        result=calibrate_planes(read_json(args.screen_sample_json),read_json(args.reference_sample_json),args.reference_kind,
                                transforms["tracker_from_screen_surface"],transforms["tracker_from_reference_surface"])
        write_json(args.output_json,result)
    elif args.command=="heading":
        result=calibrate_heading(read_json(args.sample_json),args.reference_world_direction,args.nominal_rotation_wxyz,
                                 args.mic_forward_local,max_residual_degrees=args.max_residual_deg)
        write_json(args.output_json,result)
    else:
        result=target_geometry(args.width_px,args.height_px,args.width_mm,args.height_mm,args.diameter_mm)
        if args.command=="show-target":
            result=show_target(result,args.rig_folder,args.label,args.monitor)
        else:
            path=Path(args.output_svg)
            with path.open("x",encoding="utf-8") as f:f.write(target_svg(result))
            write_json(str(path)+".json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return result


if __name__=="__main__":main()

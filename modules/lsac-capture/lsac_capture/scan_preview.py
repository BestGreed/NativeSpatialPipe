"""Render measured scan points as native SVG; never interpolate or claim SPL."""
import argparse
import html
import json
import math
from pathlib import Path


NOMINAL_SCREEN_MM=(301.55,188.47)  # 14 inch, 16:10 visual guide only
PALETTE=((-30.,(30,58,138)),(-20.,(6,182,212)),(-10.,(250,204,21)),(0.,(239,68,68)))


def color_for_db(level):
    if type(level) not in (int,float) or not math.isfinite(level):
        raise ValueError("Relative level must be finite")
    value=max(-30.,min(0.,level))
    for (a,ca),(b,cb) in zip(PALETTE,PALETTE[1:]):
        if value<=b:
            t=(value-a)/(b-a)
            rgb=[round(x+(y-x)*t) for x,y in zip(ca,cb)]
            return "#"+"".join(f"{x:02x}" for x in rgb)
    return "#ef4444"


def read_points(data,channel_index=0):
    if data.get("selection",{}).get("scope")!="selected_playback_interval":
        raise ValueError("Preview requires an explicitly selected playback interval")
    if data.get("relative_level_reference_scope")!="selected_playback_interval":
        raise ValueError("Relative levels must reference selected playback maximum")
    labels=data.get("channel_labels",[])
    if type(channel_index) is not int or not 0<=channel_index<len(labels):
        raise ValueError("Channel index is outside recorded channel labels")
    points=[]
    for window in data.get("windows",[]):
        if window.get("accepted") is not True:
            continue
        if window.get("coordinate_frame")!="screen":
            raise ValueError("All plotted positions must use the screen coordinate frame")
        xyz=window.get("acoustic_center_position_m")
        levels=window.get("band_db_re_selected_max")
        if not isinstance(xyz,list) or len(xyz)!=3 or not all(type(v) in (int,float) and math.isfinite(v) for v in xyz):
            raise ValueError("Accepted point has invalid acoustic center position")
        if not isinstance(levels,list) or channel_index>=len(levels):
            raise ValueError("Accepted point lacks selected-playback relative level")
        level=levels[channel_index]
        color_for_db(level)
        points.append(dict(cm=[v*100 for v in xyz],db=level,
                           lsl=window.get("audio_center_lsl"),sample=window.get("start_sample")))
    if not points:
        raise ValueError("No accepted path points to plot")
    return labels[channel_index],points


def axis_domains(points,show_screen=True):
    values=[[p["cm"][axis] for p in points]+[0.] for axis in range(3)]
    if show_screen:
        values[0]+=[-NOMINAL_SCREEN_MM[0]/20,NOMINAL_SCREEN_MM[0]/20]
        values[1]+=[-NOMINAL_SCREEN_MM[1]/20,NOMINAL_SCREEN_MM[1]/20]
    lows=[math.floor((min(v)-2)/10)*10 for v in values]
    highs=[math.ceil((max(v)+2)/10)*10 for v in values]
    span=max(20,max(b-a for a,b in zip(lows,highs)))
    return [(low,low+span) for low in lows]


def render_svg(data,channel_index=0,show_screen=True,source_name="scan-analysis.json"):
    label,points=read_points(data,channel_index)
    domains=axis_domains(points,show_screen)
    width,height=1500,950
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         '<rect width="1500" height="950" fill="#ffffff"/>',
         '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;fill:#172033}.tick{font-size:13px;fill:#475569}.axis{font-size:16px}.small{font-size:14px}</style>']
    def text(x,y,value,size=16,anchor="start",fill=None,extra=""):
        style=f' style="fill:{fill}"' if fill else ""
        svg.append(f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}"{style} {extra}>{html.escape(str(value))}</text>')
    band=data.get("band_hz",[445.45,561.23])
    text(60,47,"500 Hz · one-third-octave path samples",30)
    selection=data["selection"]
    duration=selection.get("selected_host_duration_seconds")
    duration_label=f"{duration:.2f} s" if type(duration) in (int,float) and math.isfinite(duration) else "selected interval"
    frames=data.get("window_frames");rate=data.get("sample_rate")
    window_label=f"{frames/rate*1000:g} ms/window" if type(frames) is int and type(rate) is int and rate>0 else "finite audio windows"
    text(60,79,f"{len(points)} accepted / {data.get('window_count','?')} windows  ·  {duration_label}  ·  {window_label}  ·  {band[0]:.1f}–{band[1]:.1f} Hz",17)
    svg.append('<rect x="60" y="103" width="1380" height="76" rx="8" fill="#fff7df" stroke="#e7bb42"/>')
    text(78,132,f"TIMING PREVIEW: {data.get('mode','unknown')} + {data.get('audio_time_basis','unknown')}; synchronization is UNVALIDATED.",18)
    text(78,160,"Digital relative band level, NOT dB SPL. USB channel semantics unverified. Measured dots only; no interpolation.",16)
    text(60,205,"Origin = calibrated screen center (0, 0, 0).  +X right · +Y screen up · +Z toward the viewer.  All axes in cm.",17)
    panels=((0,1,"Front projection · XY","X · screen right (cm)","Y · screen up (cm)"),
            (0,2,"Top projection · XZ","X · screen right (cm)","Z · toward viewer (cm)"),
            (1,2,"Side projection · YZ","Y · screen up (cm)","Z · toward viewer (cm)"))
    plot_y=260;size=380
    for index,(horizontal,vertical,title,x_label,y_label) in enumerate(panels):
        left=80+index*490
        xlow,xhigh=domains[horizontal];ylow,yhigh=domains[vertical]
        def pixel(a,b):
            return left+(a-xlow)/(xhigh-xlow)*size,plot_y+size-(b-ylow)/(yhigh-ylow)*size
        text(left+size/2,239,title,21,"middle")
        svg.append(f'<rect x="{left}" y="{plot_y}" width="{size}" height="{size}" fill="#f8fafc" stroke="#cbd5e1"/>')
        for value in range(int(xlow),int(xhigh)+1,5):
            x,_=pixel(value,ylow)
            stroke="#cbd5e1" if value%10==0 else "#e8edf3"
            svg.append(f'<path d="M{x:.2f},{plot_y} V{plot_y+size}" stroke="{stroke}" stroke-width="1"/>')
            if value%10==0:text(x,plot_y+size+23,value,13,"middle",fill="#475569")
        for value in range(int(ylow),int(yhigh)+1,5):
            _,y=pixel(xlow,value)
            stroke="#cbd5e1" if value%10==0 else "#e8edf3"
            svg.append(f'<path d="M{left},{y:.2f} H{left+size}" stroke="{stroke}" stroke-width="1"/>')
            if value%10==0:text(left-10,y+4,value,13,"end",fill="#475569")
        xzero,yzero=pixel(0,0)
        svg.append(f'<path d="M{xzero:.2f},{plot_y} V{plot_y+size} M{left},{yzero:.2f} H{left+size}" stroke="#8c9bac" stroke-width="1.6"/>')
        if show_screen:
            half_width,half_height=NOMINAL_SCREEN_MM[0]/20,NOMINAL_SCREEN_MM[1]/20
            if (horizontal,vertical)==(0,1):
                x1,y1=pixel(-half_width,half_height);x2,y2=pixel(half_width,-half_height)
                svg.append(f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{x2-x1:.2f}" height="{y2-y1:.2f}" fill="#e2e8f0" fill-opacity="0.55" stroke="#64748b" stroke-width="2" stroke-dasharray="6 4"/>')
            else:
                half=half_width if horizontal==0 else half_height
                x1,y1=pixel(-half,0);x2,y2=pixel(half,0)
                svg.append(f'<path d="M{x1:.2f},{y1:.2f} L{x2:.2f},{y2:.2f}" stroke="#64748b" stroke-width="3" stroke-dasharray="6 4"/>')
        for point in points:
            x,y=pixel(point["cm"][horizontal],point["cm"][vertical])
            xyz=point["cm"]
            tooltip=f"X {xyz[0]:.2f}, Y {xyz[1]:.2f}, Z {xyz[2]:.2f} cm | {label} {point['db']:.2f} dB re selected max | sample {point['sample']}"
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.1" fill="{color_for_db(point["db"])}" fill-opacity="0.9" stroke="white" stroke-width="0.35"><title>{html.escape(tooltip)}</title></circle>')
        for point,letter,dy in ((points[0],"S",-8),(points[-1],"E",16)):
            x,y=pixel(point["cm"][horizontal],point["cm"][vertical])
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5.2" fill="none" stroke="#111827" stroke-width="1.3"/>')
            text(x+8,y+dy,letter,14,fill="#111827",extra='stroke="white" stroke-width="3" paint-order="stroke"')
        text(left+size/2,plot_y+size+55,x_label,16,"middle")
        text(left-48,plot_y+size/2,y_label,16,"middle",extra=f'transform="rotate(-90 {left-48} {plot_y+size/2})"')
    text(750,733,f"{label} relative to its selected-playback maximum",18,"middle")
    bar_x,bar_y,bar_w,bar_h=440,750,620,22
    for i in range(124):
        level=-30+30*(i+.5)/124
        svg.append(f'<rect x="{bar_x+i*bar_w/124:.2f}" y="{bar_y}" width="{bar_w/124+.1:.2f}" height="{bar_h}" fill="{color_for_db(level)}"/>')
    svg.append(f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" fill="none" stroke="#64748b"/>')
    for value in (-30,-20,-10,0):
        x=bar_x+(value+30)/30*bar_w
        text(x,bar_y+44,"≤−30 dB" if value==-30 else f"{value} dB",15,"middle")
    text(60,835,"S / E = first / last accepted audio window. Color is clamped below −30 dB; positions are measured path samples.",15)
    if show_screen:
        text(60,862,"Dashed gray screen: NOMINAL 14-inch 16:10, 30.155 × 18.847 cm — visual guide only, not target-fit inferred dimensions.",15)
    text(60,889,"Timing, channel orientation and absolute acoustic accuracy remain unvalidated; a moving audio window is not a point measurement.",15)
    text(60,918,f"Source: {source_name}  |  No values estimated outside measured paths.",13,fill="#64748b")
    svg.append("</svg>")
    return "\n".join(svg)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_json")
    parser.add_argument("--output",help="Default: scan-preview.svg next to source JSON")
    parser.add_argument("--channel-index",type=int,default=0)
    parser.add_argument("--no-screen-frame",action="store_true")
    args=parser.parse_args(argv)
    source=Path(args.analysis_json).resolve()
    data=json.loads(source.read_text(encoding="utf-8-sig"))
    output=Path(args.output).resolve() if args.output else source.with_name("scan-preview.svg")
    svg=render_svg(data,args.channel_index,not args.no_screen_frame,source_name=source.parent.name+"/"+source.name)
    with output.open("x",encoding="utf-8") as f:f.write(svg)
    print(json.dumps(dict(output=str(output),point_count=len(read_points(data,args.channel_index)[1]),
                         channel=data["channel_labels"][args.channel_index],mode=data.get("mode"),
                         interpolation="none",db_spl=False),indent=2))
    return output


if __name__=="__main__":main()

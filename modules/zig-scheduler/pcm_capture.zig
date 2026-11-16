const std=@import("std");
const u=@import("common.zig");
const n=@import("native.zig");
const d=@import("desktop_playback.zig");
const window_identity=@import("pcm_window_identity.zig");
const OpenIngress=*const fn([*:0]const u16,[*:0]const u16,?*anyopaque,Submit) callconv(.C) ?*anyopaque;
const SubmitLeased = *const fn(?*anyopaque, u64, u32, [*]const u8, usize, u64, u64) callconv(.C) c_int;
const OpenIngressLeased = *const fn([*:0]const u16, [*:0]const u16, ?*anyopaque, SubmitLeased) callconv(.C) ?*anyopaque;
const ActivateSource = *const fn(?*anyopaque, u32) callconv(.C) c_int;
const SourceMask = *const fn(?*anyopaque) callconv(.C) u32;
const Close=*const fn(?*anyopaque) callconv(.C) void;
const Stats=*const fn(?*anyopaque,[*]u8,u32) callconv(.C) void;
const Submit=*const fn(?*anyopaque,u64,u32,[*]const u8,usize) callconv(.C) c_int;
const first_pcm_lane = 8;
const pcm_sources = 8;
pub const Session=struct {
 captureLib:std.DynLib,ingressLib:std.DynLib,capture:?*anyopaque,
 ingress:[pcm_sources]?*anyopaque = [_]?*anyopaque{null} ** pcm_sources,
 closeCapture:Close,closeIngress:Close,captureStats:Stats,ingressStats:Stats,
 pipeline:?n.Pipeline,shared:?*n.Pipeline,stopped:bool=false,openIngress:OpenIngress,ingressName:[:0]u16,ingressEvent:[:0]u16,
 openLeasedIngress: ?OpenIngressLeased = null,
 sourceMask: ?SourceMask = null,
 activateSource: ?ActivateSource = null,
 ingressErrors: [pcm_sources]?anyerror = [_]?anyerror{null} ** pcm_sources,
 windowLabels: [pcm_sources]?window_identity.Result = [_]?window_identity.Result{null} ** pcm_sources,
 identity:?d.BrowserIdentity=null,agent:?u.Process=null,agentStopPath:?[]const u8=null,
 pub fn init(settings:d.PcmCapture,config:d.Config,pid:u32,out:[]const u8,scaled:bool,reference:f64,lira:?[]const u8,layout:u32,shared:?*n.Pipeline) !Session {
  var cap=try u.library(settings.captureDll);errdefer cap.close();
  var ing=try u.library(settings.ingressDll);errdefer ing.close();
  const openCap=try u.symbol(*const fn(u32,[*:0]const u16,u32,[*:0]const u16,[*:0]const u16) callconv(.C) ?*anyopaque,&cap,"nsp_pcm_capture_open");
  const openIng=try u.symbol(*const fn([*:0]const u16,[*:0]const u16,?*anyopaque,Submit) callconv(.C) ?*anyopaque,&ing,"nsp_pcm_ingress_open");
  const closeCap=try u.symbol(Close,&cap,"nsp_pcm_capture_close");
  const closeIng=try u.symbol(Close,&ing,"nsp_pcm_ingress_close");
  const cs=try u.symbol(Stats,&cap,"nsp_pcm_capture_stats");
  const is=try u.symbol(Stats,&ing,"nsp_pcm_ingress_stats");
  var pipeline: ?n.Pipeline = null;
  if (shared == null) {
   const path = try u.join(&.{out, "pcm-stream/renderer.f32"});
   defer u.a.free(path);
   try std.fs.cwd().makePath(std.fs.path.dirname(path).?);
   pipeline = if (config.filterFile()) |filter|
    try n.Pipeline.initNotebookLive(config.bridgeDll, path, 32, scaled, reference, filter, false, config.virtualBass(), lira)
   else
    try n.Pipeline.initLive(config.bridgeDll, path, 32, scaled, reference, lira, layout, false);
  }
  errdefer if (pipeline) |*owned| owned.close();
  var nonce:[8]u8=undefined;std.crypto.random.bytes(&nonce);
  const name=try u.fmt("Local\\NSP-PCM-{d}-{s}",.{u.c.GetCurrentProcessId(),std.fmt.fmtSliceHexLower(&nonce)});defer u.a.free(name);
  const event=try u.fmt("{s}-ready",.{name});defer u.a.free(event);
  const wn=try u.wide(name);errdefer u.a.free(wn);
  const we=try u.wide(event);errdefer u.a.free(we);
  const endpoint=try u.wide(settings.inputEndpointId);defer u.a.free(endpoint);
  const binding=config.browserBinding();
  const openLeased: ?OpenIngressLeased = if (shared != null)
   try u.symbol(OpenIngressLeased, &ing, "nsp_pcm_ingress_open_leased") else null;
  const sourceMask: ?SourceMask = if (shared != null)
   try u.symbol(SourceMask, &cap, "nsp_pcm_capture_source_mask") else null;
  const activateSource: ?ActivateSource = if (shared != null)
   try u.symbol(ActivateSource, &cap, "nsp_pcm_capture_activate_source") else null;
  const capture=if(shared != null) blk: {
   const OpenMulti = *const fn(u32, [*:0]const u16, u32, [*:0]const u16, [*:0]const u16,
    ?[*:0]const u16, u32, u32) callconv(.C) ?*anyopaque;
   const openMulti = try u.symbol(OpenMulti, &cap, "nsp_pcm_capture_open_multi");
   const identity = if (binding) |value| if (value.enabled) value else null else null;
   const file = if (identity) |value| try u.wide(value.identityFile) else null;
   defer if (file) |value| u.a.free(value);
   break :blk openMulti(pid, endpoint.ptr, settings.sourcePid, wn.ptr, we.ptr,
    if (file) |value| value.ptr else null,
    if (identity) |value| value.leaseMs else 0,
    if (identity) |value| value.sourceTabId else 0) orelse return error.PcmCaptureOpen;
  } else if(binding!=null and binding.?.enabled) blk:{
   const identity=binding.?;
   const openBrowser=try u.symbol(*const fn(u32,[*:0]const u16,u32,[*:0]const u16,[*:0]const u16,[*:0]const u16,u32,u32) callconv(.C) ?*anyopaque,&cap,"nsp_pcm_capture_open_browser");
   const file=try u.wide(identity.identityFile);defer u.a.free(file);
   break :blk openBrowser(pid,endpoint.ptr,settings.sourcePid,wn.ptr,we.ptr,file.ptr,identity.leaseMs,identity.sourceTabId) orelse return error.PcmCaptureOpen;
  } else openCap(pid,endpoint.ptr,settings.sourcePid,wn.ptr,we.ptr) orelse return error.PcmCaptureOpen;
  errdefer closeCap(capture);
  // Output callbacks must be bound before the first PCM submission.
  var agent:?u.Process=null;var stopPath:?[]const u8=null;
  if(binding) |identity| {if(identity.enabled){
   const file=try u.join(&.{out,"browser-identity-config.json"});defer u.a.free(file);try u.writeJson(file,.{.inputEndpointId=settings.inputEndpointId,.browserIdentity=identity});
   const host=try u.fmt("{d}",.{pid});defer u.a.free(host);const owner=try u.fmt("{d}",.{u.c.GetCurrentProcessId()});defer u.a.free(owner);
   const log=try u.join(&.{out,"browser-identity-agent.log"});defer u.a.free(log);
   stopPath=try u.join(&.{out,"browser-identity.stop"});u.remove(stopPath.?);
   agent=u.spawnLog(&.{identity.nodeExe,identity.agentScript,"--config",file,"--host-pid",host,"--owner-pid",owner,"--output",out},log) catch null;
  }}
  return .{.captureLib=cap,.ingressLib=ing,.capture=capture,.closeCapture=closeCap,.closeIngress=closeIng,.captureStats=cs,.ingressStats=is,.pipeline=pipeline,.shared=shared,.openIngress=openIng,.ingressName=wn,.ingressEvent=we,.identity=binding,.agent=agent,.agentStopPath=stopPath,.openLeasedIngress=openLeased,.sourceMask=sourceMask,.activateSource=activateSource};
 }
 pub fn startIngress(self:*Session) !void {
  if (self.sourceMask != null) {
   try self.syncSources();
   for (self.ingress) |handle| { if (handle != null) return; }
   return error.PcmIngressOpen;
  } else {
   try self.attachSource(0, self.ingressName, self.ingressEvent);
  }
 }
 /// Control-plane discovery attaches a consumer only after its source ring exists.
 fn syncSources(self: *Session) !void {
  const query = self.sourceMask orelse return;
  const ready = query(self.capture);
  for (0..pcm_sources) |source| {
   if (ready & (@as(u32, 1) << @intCast(source)) == 0 or self.ingress[source] != null) continue;
   const name = try sourceName(self.ingressName, source);
   defer u.a.free(name);
   const event = try sourceName(self.ingressEvent, source);
   defer u.a.free(event);
   self.attachSource(@intCast(source), name, event) catch |err| {
    self.ingressErrors[source] = err;
    continue;
   };
   self.ingressErrors[source] = null;
  }
 }
 /// A source ring has one consumer and retains its own 480-frame assembly state.
 /// Called by the control thread after the source has created its mapping.
 pub fn attachSource(self: *Session, source: u32, name: [:0]const u16, event: [:0]const u16) !void {
  if (source >= self.ingress.len) return error.PcmSourceUnavailable;
  if (self.ingress[source] != null or self.stopped) return error.PcmIngressAlreadyStarted;
  if (self.shared) |pipeline| {
   const submit = try u.symbol(SubmitLeased, &pipeline.lib, "nsp_pipeline_submit_input_leased");
   const handle = try pipeline.input(first_pcm_lane + source);
   self.ingress[source] = self.openLeasedIngress.?(name.ptr, event.ptr, handle, submit) orelse return error.PcmIngressOpen;
  } else {
   if (source != 0) return error.PcmSourceUnavailable;
   const pipeline = &self.pipeline.?;
   self.ingress[source] = self.openIngress(name.ptr, event.ptr, pipeline.handle, pipeline.submit) orelse return error.PcmIngressOpen;
  }
  if (self.activateSource) |activate| {
   if (activate(self.capture, source) != 0) {
    self.closeIngress(self.ingress[source]);
    self.ingress[source] = null;
    return error.PcmSourceActivation;
   }
  }
 }
 pub fn report(self:*Session,out:[]const u8,settings:d.PcmCapture) !void {
  if(self.stopped)return;
  try self.syncSources();
  var cb: [16384]u8 = std.mem.zeroes([16384]u8);
  self.captureStats(self.capture, &cb, cb.len);
  var cs = try u.parse(std.mem.sliceTo(&cb, 0));
  defer cs.deinit();
  var reports: [pcm_sources]?std.json.Parsed(u.Value) = [_]?std.json.Parsed(u.Value){null} ** pcm_sources;
  defer for (&reports) |*entry| { if (entry.*) |*parsed| parsed.deinit(); };
  var ingress: [pcm_sources]u.Value = [_]u.Value{.null} ** pcm_sources;
  var errors: [pcm_sources]?[]const u8 = [_]?[]const u8{null} ** pcm_sources;
  var attached: u32 = 0;
  for (self.ingress, 0..) |handle, source| {
   if (self.ingressErrors[source]) |err| errors[source] = @errorName(err);
   if (handle == null) continue;
   attached += 1;
   var buffer: [4096]u8 = std.mem.zeroes([4096]u8);
   self.ingressStats(handle, &buffer, buffer.len);
   reports[source] = try u.parse(std.mem.sliceTo(&buffer, 0));
   ingress[source] = reports[source].?.value;
  }
  const sources = u.field(cs.value, "sources");
  const now = u.c.GetTickCount64();
  for (&self.windowLabels, 0..) |*label, source| {
   const row = if (sources == .array and source < sources.array.items.len) sources.array.items[source] else .null;
   const pid_value = if (self.sourceMask != null) u.field(row, "pid") else if (source == 0) u.field(cs.value, "selectedPid") else .null;
   const started_value = if (self.sourceMask != null) u.field(row, "processStarted") else if (source == 0) u.field(cs.value, "selectedProcessStarted") else .null;
   const pid = std.math.cast(u32, u.number(pid_value) catch 0) orelse 0;
   const started = u.number(started_value) catch 0;
   if (label.* == null or label.*.?.pid != pid or label.*.?.created_filetime != started or
       now -| label.*.?.observed_monotonic_ms >= 500) {
    const resolved = try window_identity.resolve(u.a, .{ .pid = pid, .created_filetime = started });
    if (label.*) |*previous| previous.deinit(u.a);
    label.* = resolved;
   }
  }
  const path=try u.join(&.{out,"pcm-capture.json"});defer u.a.free(path);
  try u.writeJson(path,.{.inputEndpointId=settings.inputEndpointId,.routeToOutput=settings.routeToOutput,
   .sharedMemoryVersion=@as(u32,2),.browserIdentity=if(self.identity) |identity| identity.enabled else false,
   .capture=cs.value,.ingress=ingress[0],.ingressSources=ingress,.ingressErrors=errors,.attachedSources=attached,
   .applicationWindow=self.windowLabels[0],.applicationWindows=self.windowLabels,
   .rendererProgress=if(self.shared) |p| p.progress() else self.pipeline.?.progress(),
   .mixesSpatialAndPcm=self.shared!=null,.appliesWindowsSessionVolume=false});
 }
 pub fn stopAndFinish(self:*Session) void {
  if(self.stopped)return;
  if(self.agent) |*agent| {
   if(self.agentStopPath) |path| {u.writeJson(path,.{.stop=true}) catch {};}
   if(u.c.WaitForSingleObject(agent.handle,2500)!=u.c.WAIT_OBJECT_0){_=u.c.TerminateProcess(agent.handle,1);_=u.c.WaitForSingleObject(agent.handle,1000);}
   agent.close();self.agent=null;
  }
  self.closeCapture(self.capture);self.capture=null;
  for (&self.ingress) |*ingress| {
   if (ingress.*) |handle| self.closeIngress(handle);
   ingress.* = null;
  }
  self.stopped=true;
  if (self.pipeline) |*owned| {var result = owned.finish() catch return;result.deinit();}
 }
 pub fn deinit(self:*Session) void {self.stopAndFinish();for (&self.windowLabels) |*label| {if(label.*) |*value| value.deinit(u.a);}if(self.pipeline) |*owned| owned.close();self.ingressLib.close();self.captureLib.close();u.a.free(self.ingressName);u.a.free(self.ingressEvent);if(self.agentStopPath) |path| u.a.free(path);}
};

fn sourceName(base: [:0]const u16, source: usize) ![:0]u16 {
 if (source == 0) return u.a.dupeZ(u16, base);
 if (source >= pcm_sources) return error.PcmSourceUnavailable;
 const result = try u.a.allocSentinel(u16, base.len + 2, 0);
 @memcpy(result[0..base.len], base);
 result[base.len] = '-';
 result[base.len + 1] = @as(u16, '0') + @as(u16, @intCast(source));
 return result;
}

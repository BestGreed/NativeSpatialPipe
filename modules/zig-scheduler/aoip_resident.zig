//! FSA-owned audio host. Only control messages cross process boundaries.
//! Renderer pools and Inferno live in this process across Hook sessions.
const std=@import("std");
const u=@import("common.zig");
const a=u.a;
const c=u.c;
const Request=struct{id:[]const u8,argv:[][]const u8};
const Ready=struct{pid:u32,created:u64,owner:u32,ownerCreated:u64};
const Status=*const fn(?*anyopaque,*[8]u64) callconv(.C) c_int;
const Health=struct{
    stop:std.atomic.Value(bool)=std.atomic.Value(bool).init(false),service:?*anyopaque,status:Status,path:[]const u8,
    fn run(self:*Health) void {
        while(!self.stop.load(.acquire)){
            var v:[8]u64=undefined;
            if(self.status(self.service,&v)==0)u.writeJson(self.path,.{.pid=c.GetCurrentProcessId(),.fault=v[0],.sentPackets=v[1],.acceptedBlocks=v[2],.recycledBlocks=v[3],.silencePackets=v[4],.sourceUnderflows=v[5],.clockDiscontinuities=v[6],.pcmAttached=v[7]!=0}) catch {};
            c.Sleep(500);
        }
    }
};
fn directory(home:[]const u8) ![]u8{return u.join(&.{home,"aoip-resident"});}
fn alive(home:[]const u8,owner:u32) bool {
    const path=u.join(&.{home,"aoip-resident","ready.json"}) catch return false;defer a.free(path);
    const bytes=u.read(path) catch return false;defer a.free(bytes);
    const parsed=std.json.parseFromSlice(Ready,a,bytes,.{}) catch return false;defer parsed.deinit();
    const r=parsed.value;
    return r.owner==owner and (u.processIdentity(owner) catch return false)==r.ownerCreated and
        (u.processIdentity(r.pid) catch return false)==r.created;
}
pub fn start(args:[]const []const u8,root:[]const u8,home:[]const u8) !void {
    const path=u.option(args,"--playback-config") orelse return;
    const bytes=try u.read(path);defer a.free(bytes);
    var profile=try @import("desktop_playback.zig").parse(a,bytes);defer profile.deinit();
    if(!std.mem.eql(u8,profile.value.outputBackend,"aoip"))return;
    const ownerText=u.option(args,"--owner-pid") orelse return error.OwnerRequired;
    const owner=try std.fmt.parseInt(u32,ownerText,10);
    if(alive(home,owner))return;
    const dir=try directory(home);defer a.free(dir);try std.fs.cwd().makePath(dir);
    const exe=try std.fs.selfExePathAlloc(a);defer a.free(exe);
    const log=try u.join(&.{dir,"resident.log"});defer a.free(log);
    var child=try u.spawnLog(&.{exe,"aoip-resident","--root",root,"--home",home,"--owner-pid",ownerText,"--playback-config",path},log);
    defer child.close();
    const deadline=c.GetTickCount64()+20000;
    while(c.GetTickCount64()<deadline){
        if(alive(home,owner))return;
        if(try u.done(&child)) |_| return error.AoipResidentStartup;
        c.Sleep(50);
    }
    return error.AoipResidentTimeout;
}
pub fn serve(args:[]const []const u8,root:[]const u8,home:[]const u8) !void {
    const owner=try std.fmt.parseInt(u32,u.option(args,"--owner-pid") orelse return error.OwnerRequired,10);
    const ownerCreated=(try u.processIdentity(owner)) orelse return error.OwnerExited;
    const mutexName=try u.fmt("Local\\NSP.AoipResident.{d}.{d}",.{owner,ownerCreated});defer a.free(mutexName);
    const mutex=c.CreateMutexW(null,0,(try u.wide(mutexName)).ptr) orelse return error.ResidentMutex;
    defer _=c.CloseHandle(mutex);
    if(c.GetLastError()==c.ERROR_ALREADY_EXISTS)return;
    const bytes=try u.read(u.option(args,"--playback-config") orelse return error.PlaybackConfigRequired);defer a.free(bytes);
    var profile=try @import("desktop_playback.zig").parse(a,bytes);defer profile.deinit();
    const network=try @import("aoip_config.zig").native(profile.value.aoip orelse return error.AoipConfigRequired);
    var lib=try u.library(profile.value.bridgeDll);defer lib.close();
    const Create=*const fn(*const @import("aoip_config.zig").Native,u32) callconv(.C) ?*anyopaque;
    const Destroy=*const fn(?*anyopaque) callconv(.C) void;
    const create=try u.symbol(Create,&lib,"nsp_aoip_standby_create");
    const destroy=try u.symbol(Destroy,&lib,"nsp_aoip_standby_destroy");
    const service=create(&network,@intCast(profile.value.channelCount())) orelse return error.AoipStandbyCreate;
    defer destroy(service);
    const dir=try directory(home);defer a.free(dir);try std.fs.cwd().makePath(dir);
    const ready=try u.join(&.{dir,"ready.json"});defer a.free(ready);
    const healthPath=try u.join(&.{dir,"health.json"});defer a.free(healthPath);
    var health=Health{.service=service,.status=try u.symbol(Status,&lib,"nsp_aoip_standby_status"),.path=healthPath};
    const healthThread=try std.Thread.spawn(.{},Health.run,.{&health});
    defer {health.stop.store(true,.release);healthThread.join();}
    try u.writeJson(ready,Ready{.pid=c.GetCurrentProcessId(),.created=(try u.processIdentity(c.GetCurrentProcessId())).?,.owner=owner,.ownerCreated=ownerCreated});
    const request=try u.join(&.{dir,"request.json"});defer a.free(request);
    if(u.exists(request)){
        const previous=try u.fmt("{s}/request-before-{d}.json",.{dir,ownerCreated});defer a.free(previous);
        try std.fs.cwd().rename(request,previous);
    }
    var last:[]const u8="";defer if(last.len!=0)a.free(last);
    while((try u.processIdentity(owner))==ownerCreated){
        if(!u.exists(request)){c.Sleep(50);continue;}
        const raw=try u.read(request);defer a.free(raw);
        var parsed=try std.json.parseFromSlice(Request,a,raw,.{.allocate=.alloc_always});defer parsed.deinit();
        const r=parsed.value;
        if(std.mem.eql(u8,last,r.id)){c.Sleep(50);continue;}
        if(r.id.len!=32)return error.InvalidResidentRequest;
        for(r.id) |b| if(!std.ascii.isHex(b))return error.InvalidResidentRequest;
        if((try std.fmt.parseInt(u32,u.option(r.argv,"--owner-pid") orelse return error.OwnerRequired,10))!=owner)return error.OwnerConflict;
        const out=try std.fs.path.resolve(a,&.{u.option(r.argv,"--out") orelse return error.OutputRequired});defer a.free(out);
        const allowed=try u.join(&.{home,"sessions"});defer a.free(allowed);
        if(!std.mem.startsWith(u8,out,allowed) or out.len<=allowed.len or out[allowed.len]!='\\')return error.InvalidResidentRequest;
        if(last.len!=0)a.free(last);last=try a.dupe(u8,r.id);
        const result=try u.fmt("{s}/{s}.result.json",.{dir,r.id});defer a.free(result);
        var code:u32=0;var failure:?[]const u8=null;
        @import("host.zig").run(r.argv,root) catch |err| {code=20;failure=@errorName(err);};
        try u.writeJson(result,.{.id=r.id,.code=code,.errorName=failure});
    }
}
pub fn session(args:[]const []const u8,root:[]const u8,home:[]const u8) !u8 {
    try start(args,root,home);
    const owner=try std.fmt.parseInt(u32,u.option(args,"--owner-pid") orelse return error.OwnerRequired,10);
    const dir=try directory(home);defer a.free(dir);
    const request=try u.join(&.{dir,"request.json"});defer a.free(request);
    if(u.exists(request)){
        var prior=try u.load(request);defer prior.deinit();
        const id=try u.string(u.field(prior.value,"id"));
        const result=try u.fmt("{s}/{s}.result.json",.{dir,id});defer a.free(result);
        if(!u.exists(result))return error.AoipResidentBusy;
    }
    var nonce:[16]u8=undefined;std.crypto.random.bytes(&nonce);
    const id=try u.fmt("{s}",.{std.fmt.fmtSliceHexLower(&nonce)});defer a.free(id);
    const result=try u.fmt("{s}/{s}.result.json",.{dir,id});defer a.free(result);
    try u.writeJson(request,.{.id=id,.argv=args});
    while(alive(home,owner)){
        if(u.exists(result)){
            var parsed=try u.load(result);defer parsed.deinit();
            return @intCast(try u.number(u.field(parsed.value,"code")));
        }
        c.Sleep(100);
    }
    return error.AoipResidentExited;
}

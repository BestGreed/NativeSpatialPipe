//! HTTP configuration/status only. Never invokes backend ensure or controls.
const std = @import("std");
const u = @import("common.zig");
const backend = @import("backend.zig");
const playback = @import("desktop_playback.zig");
const SocketIO = @import("socket_io.zig").SocketIO;
const c = @cImport({
    @cDefine("_WIN32_WINNT", "0x0A00");
    @cDefine("UNICODE", "1");
    @cDefine("_UNICODE", "1");
    @cInclude("../fsa-integration/native/web_contract.h");
});
const a = u.a;
// Predefined registry handles are tagged integers, not aligned HKEY objects.
extern "advapi32" fn RegOpenKeyExW(key: ?*anyopaque, subkey: [*:0]const u16, options: u32, access: u32, result: *c.HKEY) callconv(std.os.windows.WINAPI) c_long;
const Context = struct { root: []const u8, home: []const u8, ini: []const u8, port: u16, token: []const u8, owner: u32 };

fn send(stream: std.net.Stream, code: u16, value: anytype) !void {
    const bytes = try u.json(value);
    defer a.free(bytes);
    try backend.sendResponse(stream, code, "application/json; charset=utf-8", bytes);
}
fn revision(bytes: ?[]const u8) [64]u8 {
    var digest: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(bytes orelse "<missing>", &digest, .{});
    return std.fmt.bytesToHex(digest, .lower);
}
fn iniPlayback(ctx: Context) ![]u8 {
    const path = try u.wide(ctx.ini);
    defer a.free(path);
    var result: [32768]u16 = undefined;
    const count = c.GetPrivateProfileStringW(std.unicode.utf8ToUtf16LeStringLiteral("NSP"), std.unicode.utf8ToUtf16LeStringLiteral("PlaybackConfig"), std.unicode.utf8ToUtf16LeStringLiteral(""), &result, result.len, path.ptr);
    if (count == result.len - 1) return error.ConfigPathTooLong;
    if (count > 0) {
        const text = try std.unicode.utf16LeToUtf8Alloc(a, result[0..count]);
        errdefer a.free(text);
        if (!std.fs.path.isAbsolute(text)) return error.InvalidConfigPath;
        const legacy=try u.join(&.{std.fs.path.dirname(ctx.ini) orelse return error.InvalidConfigPath,"playback.json"});
        defer a.free(legacy);
        const normalized=try std.fs.path.resolve(a,&.{text});defer a.free(normalized);
        const old=try std.fs.path.resolve(a,&.{legacy});defer a.free(old);
        if(!std.ascii.eqlIgnoreCase(normalized,old))return text;
        a.free(text);
    }
    return u.join(&.{ctx.home,"playback.json"});
}
fn migratePlayback(ctx:Context) !void {
    const target=try iniPlayback(ctx);defer a.free(target);
    const default=try u.join(&.{ctx.home,"playback.json"});defer a.free(default);
    if(!std.mem.eql(u8,target,default) or u.exists(target))return;
    const legacy=try u.join(&.{std.fs.path.dirname(ctx.ini) orelse return error.InvalidConfigPath,"playback.json"});defer a.free(legacy);
    const bytes=u.read(legacy) catch |err| switch(err){error.FileNotFound=>return,else=>return err};defer a.free(bytes);
    // Preserve even malformed JSON for repair through the existing API. Never
    // delete the source or replace an existing user configuration.
    try std.fs.cwd().makePath(ctx.home);
    var nonce:[8]u8=undefined;std.crypto.random.bytes(&nonce);
    const temp=try u.fmt("{s}.migrate-{s}",.{target,std.fmt.fmtSliceHexLower(&nonce)});defer a.free(temp);
    defer u.remove(temp);
    try u.write(temp,bytes);
    const from=try u.wide(temp);defer a.free(from);
    const to=try u.wide(target);defer a.free(to);
    if(c.MoveFileExW(from.ptr,to.ptr,c.MOVEFILE_WRITE_THROUGH)==0){
        const code=c.GetLastError();
        if(code!=c.ERROR_FILE_EXISTS and code!=c.ERROR_ALREADY_EXISTS)return error.PlaybackMigrationFailed;
    }
}
fn configPath(ctx: Context, id: []const u8) ![]u8 {
    if (std.mem.eql(u8, id, "playback")) return iniPlayback(ctx);
    if (std.mem.eql(u8, id, "backend")) return u.join(&.{ ctx.home, "config.json" });
    return error.UnknownConfiguration;
}
fn validate(id: []const u8, bytes: []const u8) !void {
    if (std.mem.eql(u8, id, "playback")) {
        var parsed = try playback.parse(a, bytes);
        parsed.deinit();
    } else if (std.mem.eql(u8, id, "backend")) {
        var parsed = try std.json.parseFromSlice(backend.Config, a, bytes, .{ .allocate = .alloc_always });
        defer parsed.deinit();
        if (parsed.value.version != 1 or !backend.validFormat(parsed.value.spatialFormat)) return error.InvalidConfig;
    } else return error.UnknownConfiguration;
}
fn defaultPlayback(ctx: Context) ![]u8 {
    const bridge = try u.join(&.{ctx.root, @import("native.zig").default_render_bridge});
    defer a.free(bridge);
    const asio = try u.join(&.{ctx.root, "build/audio-backends/NSP.AsioOutput.dll"});
    defer a.free(asio);
    const lira = try u.join(&.{ctx.root, "build/spatial-renderer/NSP.SpatialRenderer.dll"});
    defer a.free(lira);
    return u.json(playback.Config{ .bridgeDll = bridge, .asioDll = asio, .rendererDll = lira, .driver = "" });
}
fn getConfig(ctx: Context, id: []const u8, stream: std.net.Stream) !void {
    const path = try configPath(ctx, id);
    defer a.free(path);
    const saved = u.read(path) catch |err| switch (err) { error.FileNotFound => null, else => return err };
    defer if (saved) |bytes| a.free(bytes);
    const defaults = if (saved == null) (if (std.mem.eql(u8, id, "playback")) try defaultPlayback(ctx) else try u.json(backend.Config{})) else null;
    defer if (defaults) |bytes| a.free(bytes);
    const bytes = saved orelse defaults.?;
    // Preserve malformed files for repair rather than replacing them with defaults.
    var parsed = u.parse(bytes) catch {
        return send(stream, 200, .{ .id = id, .path = path, .exists = saved != null, .revision = &revision(saved), .config = @as(?u.Value, null), .raw = bytes, .validationError = "InvalidJson", .apply = "saved-only" });
    };
    defer parsed.deinit();
    var validation_error: ?[]const u8 = null;
    validate(id, bytes) catch |err| { validation_error = @errorName(err); };
    try send(stream, 200, .{ .id = id, .path = path, .exists = saved != null, .revision = &revision(saved), .config = parsed.value, .validationError = validation_error,
        .apply = if (std.mem.eql(u8, id, "playback")) "next-session" else "backend-restart" });
}
fn putConfig(ctx: Context, id: []const u8, bytes: []const u8, stream: std.net.Stream) !void {
    const Request = struct { revision: []const u8, config: u.Value };
    var parsed = try std.json.parseFromSlice(Request, a, bytes, .{ .allocate = .alloc_always });
    defer parsed.deinit();
    const encoded = try u.json(parsed.value.config);
    defer a.free(encoded);
    try validate(id, encoded);
    const path = try configPath(ctx, id);
    defer a.free(path);
    // Serialize Web saves and require external editors to respect revisions.
    try std.fs.cwd().makePath(std.fs.path.dirname(path) orelse return error.InvalidConfigPath);
    const lockpath = try u.fmt("{s}.web.lock", .{path});
    defer a.free(lockpath);
    const lockw = try u.wide(lockpath);
    defer a.free(lockw);
    const lock = c.CreateFileW(lockw.ptr, c.GENERIC_WRITE, 0, null, c.OPEN_ALWAYS, c.FILE_ATTRIBUTE_NORMAL, null);
    if (lock == c.INVALID_HANDLE_VALUE) return error.ConfigurationBusy;
    defer _ = c.CloseHandle(lock);
    const prior = u.read(path) catch |err| switch (err) { error.FileNotFound => null, else => return err };
    defer if (prior) |value| a.free(value);
    if (!std.mem.eql(u8, parsed.value.revision, &revision(prior))) return send(stream, 409, .{ .@"error" = "RevisionConflict" });
    try u.write(path, encoded);
    // FSA resolves the same user default; default saves do not require write
    // access to the installation INI. Explicit custom paths are already set.
    try getConfig(ctx, id, stream);
}
fn status(ctx: Context, stream: std.net.Stream) !void {
    var snapshot: c.FSA_WebSnapshot = std.mem.zeroes(c.FSA_WebSnapshot);
    var known = false;
    const mapping = c.OpenFileMappingW(c.FILE_MAP_READ, 0, std.unicode.utf8ToUtf16LeStringLiteral("Local\\NSP-FSA-Web-v1"));
    if (mapping != null) {
        defer _ = c.CloseHandle(mapping);
        const view = c.MapViewOfFile(mapping, c.FILE_MAP_READ, 0, 0, @sizeOf(c.FSA_WebSnapshot));
        if (view != null) {
            defer _ = c.UnmapViewOfFile(view);
            const src: *volatile c.FSA_WebSnapshot = @ptrCast(@alignCast(view.?));
            for (0..4) |_| {
                const seq = src.sequence;
                if (seq & 1 != 0) continue;
                @fence(.acquire);
                snapshot = src.*;
                @fence(.acquire);
                if (src.sequence == seq and snapshot.magic == c.FSA_WEB_MAGIC and snapshot.version == 1 and snapshot.size == @sizeOf(c.FSA_WebSnapshot) and (ctx.owner == 0 or snapshot.pid == ctx.owner)) { known = true; break; }
            }
        }
    }
    if (!known) snapshot = std.mem.zeroes(c.FSA_WebSnapshot);
    const endpoint = try std.unicode.utf16LeToUtf8Alloc(a, std.mem.sliceTo(&snapshot.endpoint, 0));
    defer a.free(endpoint);
    const message = try std.unicode.utf16LeToUtf8Alloc(a, std.mem.sliceTo(&snapshot.status, 0));
    defer a.free(message);
    const reply = backend.request(ctx.home, "status", .{}) catch null;
    defer if (reply) |bytes| a.free(bytes);
    var observed: ?std.json.Parsed(u.Value) = if (reply) |bytes| u.parse(bytes) catch null else null;
    defer if (observed) |*value| value.deinit();
    try send(stream, 200, .{ .control = false, .fsa = .{ .known = known, .stale = !known or c.GetTickCount64() -| snapshot.observedMs > 10000,
        .pid = snapshot.pid, .mode = snapshot.mode, .availableModes = snapshot.available, .locked = snapshot.locked != 0, .starting = snapshot.starting != 0,
        .nspCode = snapshot.nspCode, .endpoint = endpoint, .message = message }, .backend = if (observed) |value| value.value else @as(u.Value, .null), .activeConfigurationRevision = @as(?[]const u8, null) });
}
fn drivers(stream: std.net.Stream) !void {
    var result = std.ArrayList(struct { name: []const u8, driver: []const u8 }).init(a);
    defer { for (result.items) |item| { a.free(item.name); a.free(item.driver); } result.deinit(); }
    var key: c.HKEY = null;
    if (RegOpenKeyExW(@ptrFromInt(@as(usize, 0xffffffff80000002)), std.unicode.utf8ToUtf16LeStringLiteral("SOFTWARE\\ASIO"), 0, c.KEY_READ, &key) == 0) {
        defer _ = c.RegCloseKey(key);
        var index: u32 = 0;
        while (index < 256) : (index += 1) {
            var name: [256]u16 = undefined;
            var length: u32 = name.len;
            const rc = c.RegEnumKeyExW(key, index, &name, &length, null, null, null, null);
            if (rc == c.ERROR_NO_MORE_ITEMS) break;
            if (rc != 0) continue;
            name[length] = 0;
            var child: c.HKEY = null;
            if (c.RegOpenKeyExW(key, &name, 0, c.KEY_READ, &child) != 0) continue;
            defer _ = c.RegCloseKey(child);
            var clsid: [128]u16 = std.mem.zeroes([128]u16);
            var size: u32 = @sizeOf(@TypeOf(clsid));
            var kind: u32 = 0;
            if (c.RegQueryValueExW(child, std.unicode.utf8ToUtf16LeStringLiteral("CLSID"), null, &kind, @ptrCast(&clsid), &size) != 0 or kind != c.REG_SZ or size < 4 or size > @sizeOf(@TypeOf(clsid))) continue;
            clsid[127] = 0;
            const label = try std.unicode.utf16LeToUtf8Alloc(a, name[0..length]);
            errdefer a.free(label);
            const guid = try std.unicode.utf16LeToUtf8Alloc(a, std.mem.sliceTo(&clsid, 0));
            defer a.free(guid);
            const driver = try u.fmt("asio:{s}", .{guid});
            errdefer a.free(driver);
            try result.append(.{ .name = label, .driver = driver });
        }
    }
    try send(stream, 200, .{ .drivers = result.items });
}
fn handle(ctx: Context, stream: std.net.Stream) !void {
    var io = try SocketIO.init(stream, 2000);
    var buffer: [73728]u8 = undefined;
    var used: usize = 0;
    var end: usize = 0;
    while (used < 8192) {
        const count = try io.read(buffer[used..8192]);
        if (count == 0) return error.TruncatedRequest;
        used += count;
        if (std.mem.indexOf(u8, buffer[0..used], "\r\n\r\n")) |i| { end = i + 4; break; }
    }
    if (end == 0) return error.RequestTooLarge;
    var lines = std.mem.splitSequence(u8, buffer[0..end], "\r\n");
    var words = std.mem.splitScalar(u8, lines.next() orelse return error.BadRequest, ' ');
    const method = words.next() orelse return error.BadRequest;
    const path = words.next() orelse return error.BadRequest;
    if (path.len == 0 or path[0] != '/') return error.BadRequest;
    const authority = try u.fmt("127.0.0.1:{d}", .{ctx.port});
    defer a.free(authority);
    const origin_expected = try u.fmt("http://{s}", .{authority});
    defer a.free(origin_expected);
    var host_ok = false;
    var host_seen = false;
    var origin_ok = true;
    var authorized = false;
    var size: usize = 0;
    var length_seen = false;
    var json_type = false;
    while (lines.next()) |line| {
        const colon = std.mem.indexOfScalar(u8, line, ':') orelse continue;
        const name = line[0..colon];
        const value = std.mem.trim(u8, line[colon + 1 ..], " \t");
        if (std.ascii.eqlIgnoreCase(name, "Host")) { if (host_seen) return error.BadRequest; host_seen = true; host_ok = std.mem.eql(u8, value, authority); }
        if (std.ascii.eqlIgnoreCase(name, "Origin")) origin_ok = origin_ok and std.mem.eql(u8, value, origin_expected);
        if (std.ascii.eqlIgnoreCase(name, "X-NSP-Token")) authorized = std.mem.eql(u8, value, ctx.token);
        if (std.ascii.eqlIgnoreCase(name, "Sec-Fetch-Site") and std.mem.eql(u8, value, "cross-site")) origin_ok = false;
        if (std.ascii.eqlIgnoreCase(name, "Transfer-Encoding")) return error.BadRequest;
        if (std.ascii.eqlIgnoreCase(name, "Content-Type")) json_type = std.mem.eql(u8, value, "application/json");
        if (std.ascii.eqlIgnoreCase(name, "Content-Length")) { if (length_seen) return error.BadRequest; length_seen = true; size = try std.fmt.parseInt(usize, value, 10); }
    }
    if (!host_ok or !origin_ok) return send(stream, 403, .{ .@"error" = "OriginRejected" });
    if (std.mem.eql(u8, method, "GET")) {
        if (std.mem.eql(u8, path, "/api/web/bootstrap")) return send(stream, 200, .{ .token = ctx.token, .control = false, .pid = c.GetCurrentProcessId(), .ownerPid = ctx.owner, .elevated = false, .configs = .{ "playback", "backend" } });
        if (std.mem.eql(u8, path, "/api/web/status")) return status(ctx, stream);
        if (std.mem.eql(u8, path, "/api/web/drivers")) return drivers(stream);
        if (std.mem.startsWith(u8, path, "/api/web/config/")) return getConfig(ctx, path[16..], stream);
        if (std.mem.startsWith(u8, path, "/api/")) return send(stream, 404, .{ .@"error" = "NotFound" });
        const file = backend.staticFile(ctx.root, path) catch return send(stream, 404, .{ .@"error" = "NotFound" });
        defer a.free(file.body);
        return backend.sendResponse(stream, 200, file.mime, file.body);
    }
    if (!std.mem.eql(u8, method, "PUT") or !std.mem.startsWith(u8, path, "/api/web/config/")) return send(stream, 405, .{ .@"error" = "ControlNotExposed" });
    if (!authorized or !json_type) return send(stream, 403, .{ .@"error" = "WriteAuthorizationRequired" });
    if (size == 0 or size > 65536) return send(stream, 413, .{ .@"error" = "BodyTooLarge" });
    while (used < end + size) {
        const count = try io.read(buffer[used..end + size]);
        if (count == 0) return error.TruncatedRequest;
        used += count;
    }
    try putConfig(ctx, path[16..], buffer[end..end + size], stream);
}
pub fn serve(args: []const []const u8, root: []const u8, home: []const u8) !void {
    var process_token: c.HANDLE = null;
    if (c.OpenProcessToken(c.GetCurrentProcess(), c.TOKEN_QUERY, &process_token) == 0) return error.TokenUnavailable;
    defer _ = c.CloseHandle(process_token);
    var elevation = std.mem.zeroes(c.TOKEN_ELEVATION);
    var token_size: u32 = 0;
    if (c.GetTokenInformation(process_token, c.TokenElevation, &elevation, @sizeOf(c.TOKEN_ELEVATION), &token_size) == 0) return error.TokenUnavailable;
    if (elevation.TokenIsElevated != 0) return error.WebHostRequiresUserToken;
    const ini = u.option(args, "--fsa-config") orelse return error.FsaConfigRequired;
    const port = try std.fmt.parseInt(u16, u.option(args, "--port") orelse "8767", 10);
    const owner = try std.fmt.parseInt(u32, u.option(args, "--owner-pid") orelse "0", 10);
    var nonce: [32]u8 = undefined;
    std.crypto.random.bytes(&nonce);
    const token = std.fmt.bytesToHex(nonce, .lower);
    const ctx = Context{ .root = root, .home = home, .ini = ini, .port = port, .token = &token, .owner = owner };
    try migratePlayback(ctx);
    const process = if (owner != 0) c.OpenProcess(c.PROCESS_QUERY_LIMITED_INFORMATION, 0, owner) else null;
    if (owner != 0 and process == null) return error.OwnerUnavailable;
    defer if (process != null) { _ = c.CloseHandle(process); };
    const address = try std.net.Address.parseIp4("127.0.0.1", port);
    const bind_deadline = c.GetTickCount64() + 3000;
    var server = while (true) {
        const opened = address.listen(.{ .force_nonblocking = true }) catch |err| {
            if (err != error.AddressInUse or owner == 0 or c.GetTickCount64() >= bind_deadline) return err;
            c.Sleep(100); // Previous owner's host may still be observing FSA exit.
            continue;
        };
        break opened;
    };
    defer server.deinit();
    while (true) {
        if (process != null) {
            var code: u32 = 0;
            if (c.GetExitCodeProcess(process, &code) == 0 or code != c.STILL_ACTIVE) break;
        }
        const connection = server.accept() catch |err| {
            if (err != error.WouldBlock) return err;
            c.Sleep(50);
            continue;
        };
        defer connection.stream.close();
        handle(ctx, connection.stream) catch |err| { send(connection.stream, 400, .{ .@"error" = @errorName(err) }) catch {}; };
        // An unread rejected body followed by closesocket can turn the reply
        // into a TCP reset on Windows. Half-close and drain within fixed bounds.
        _ = u.c.shutdown(@intFromPtr(connection.stream.handle), u.c.SD_SEND);
        var drain = SocketIO.init(connection.stream, 100) catch continue;
        var discarded: usize = 0;
        var discard: [4096]u8 = undefined;
        while (discarded < 131072) {
            const count = drain.read(&discard) catch break;
            if (count == 0) break;
            discarded += count;
        }
    }
}

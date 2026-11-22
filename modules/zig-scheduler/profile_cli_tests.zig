const std = @import("std");
const cli = @import("profile_cli.zig");

test "an import requires an absolute file with a supported extension" {
    try std.testing.expectError(error.ImportFileRequired, cli.importProfile(&.{"profile-import"}, "."));
    try std.testing.expectError(error.ImportFileRequiresAbsolutePath, cli.importProfile(&.{ "profile-import", "--file", "relative.json" }, "."));
    try std.testing.expectError(error.UnsupportedImportFile, cli.importProfile(&.{ "profile-import", "--file", "C:\\Windows\\System32\\audiodg.exe" }, "."));
    try std.testing.expectError(error.UnsupportedImportFile, cli.importProfile(&.{ "profile-import", "--file", "C:\\capture-profile.txt" }, "."));
}

test "a picked layout must be a single line" {
    try std.testing.expectError(error.InvalidLayout, cli.resolve(&.{ "profile-resolve", "--pick", "L1\r\nL2" }, "."));
}

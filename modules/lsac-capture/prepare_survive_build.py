"""Stage a pinned source copy and narrowly scoped Windows research patches.

Never edits the original checkout, project product files, or USB drivers.
"""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
ORIGINAL=ROOT/"work/lsac-upstream/libsurvive"
STAGED=ROOT/"work/lsac-libsurvive-source"
COMMIT="878c74e45b46b470aee93fcb35d391c761180669"


def stage():
    actual=subprocess.check_output(["git","-C",str(ORIGINAL),"rev-parse","HEAD"],text=True).strip()
    if actual!=COMMIT: raise RuntimeError("Research source commit changed; review first")
    subprocess.run(["git","-C",str(ORIGINAL),"diff","--exit-code"],check=True,stdout=subprocess.DEVNULL)
    STAGED.mkdir(exist_ok=False)
    archive=ROOT/"work/lsac-libsurvive-source.tar"
    subprocess.run(["git","-C",str(ORIGINAL),"archive","--format=tar","--output",str(archive),"HEAD"],check=True)
    with tarfile.open(archive) as source: source.extractall(STAGED,filter="data")
    changes=[]; patches=[]
    def replace(file,old,new):
        path=STAGED/file; before=path.read_text(encoding="utf-8")
        if before.count(old)!=1: raise RuntimeError("Patch context not unique: "+file)
        after=before.replace(old,new)
        path.write_text(after,encoding="utf-8")
        changes.append(dict(file=file,before_sha256=hashlib.sha256(before.encode()).hexdigest(),after_sha256=hashlib.sha256(after.encode()).hexdigest()))
        patches.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile="a/"+file,tofile="b/"+file))
    replace("CMakeLists.txt","IF(WIN32)\n    find_program(NUGET nuget)","IF(WIN32 AND MSVC)\n    find_program(NUGET nuget)")
    replace("src/survive_config.c","#include <assert.h>","#include <assert.h>\n#ifdef _WIN32\n#include <direct.h>\n#endif")
    replace("src/lfsr_lh2.c",'#include "alloca.h"',"#ifdef _WIN32\n#include <malloc.h>\n#else\n#include <alloca.h>\n#endif")
    replace("libs/cnmatrix/CMakeLists.txt","  add_compile_options(/wd4244 /wd4996 /wd4018 /wd4101 /wd4477 /wd4068 /wd4217)",
            "  if(MSVC)\n    add_compile_options(/wd4244 /wd4996 /wd4018 /wd4101 /wd4477 /wd4068 /wd4217)\n  endif()")
    replace("libs/cnmatrix/CMakeLists.txt","find_package (Eigen3 3.3 QUIET)",
            "if(EXISTS \"${EIGEN3_INCLUDE_DIR}/Eigen/Core\")\n  set(EIGEN3_FOUND ON)\nelse()\n  find_package (Eigen3 3.3 QUIET)\nendif()")
    replace("libs/cnmatrix/src/CMakeLists.txt","  if(WIN32)\n    target_compile_options(cnmatrix PRIVATE -bigobj)\n  else()",
            "  if(WIN32 AND MSVC)\n    target_compile_options(cnmatrix PRIVATE -bigobj)\n  elseif(NOT WIN32)")
    replace("src/driver_vive.c","#ifdef HIDAPI\n#include \"driver_vive.hidapi.h\"",
            "STATIC_CONFIG_ITEM(USB_SERIAL_ALLOWLIST, \"usb-serial-allowlist\", 's', \"Exact comma-separated USB serial allowlist; empty means upstream behaviour\", \"\")\n#ifdef HIDAPI\n#include \"driver_vive.hidapi.h\"")
    old="\tconst struct DeviceInfo *info = find_known_device(ctx, idVendor, idProduct);"
    new='''#ifdef HIDAPI
	// LSAC: filter before opening any interface or sending feature reports.
	const char *allow = survive_configs(ctx, "usb-serial-allowlist", SC_GET, "");
	if (allow && *allow) {
		char serial[256] = {0};
		if (!d->serial_number || wcstombs(serial, d->serial_number, sizeof(serial)-1) == (size_t)-1)
			return -1;
		bool accepted = false;
		for (const char *p = allow; *p;) {
			const char *end = strchr(p, ',');
			size_t length = end ? (size_t)(end-p) : strlen(p);
			if (strlen(serial) == length && strncmp(p, serial, length) == 0) { accepted = true; break; }
			if (!end) break;
			p = end+1;
		}
		if (!accepted) return -1;
	}
#endif
'''+old
    replace("src/driver_vive.c",old,new)
    evidence=ROOT/"modules/lsac-capture"
    (evidence/"libsurvive-windows-research.patch").write_text("".join(patches),encoding="utf-8")
    record=dict(source_commit=COMMIT,staged=str(STAGED),changes=changes,
                purpose="Portable build compatibility plus exact USB serial allowlist before opening/configuring devices; pairing stays disabled")
    (evidence/"libsurvive-build-source.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    (STAGED/"VERSION").write_text(COMMIT+"-lsac-research",encoding="utf-8")
    return record


if __name__=="__main__": print(json.dumps(stage(),indent=2))

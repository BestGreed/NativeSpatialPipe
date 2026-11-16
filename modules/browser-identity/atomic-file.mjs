import fs from 'node:fs';

// A transient Windows sharing violation must not kill the metadata bridge.
// Keep the previous complete file; its validation timestamp still expires.
export function atomic(file, data, io = fs) {
  const temporary = file + '.tmp-' + process.pid;
  try {
    io.writeFileSync(temporary, data);
    io.renameSync(temporary, file);
    return true;
  } catch (error) {
    try { io.unlinkSync(temporary); } catch {}
    if (['EPERM', 'EACCES', 'EBUSY'].includes(error.code)) return false;
    throw error;
  }
}

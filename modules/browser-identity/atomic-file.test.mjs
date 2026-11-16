import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {atomic} from './atomic-file.mjs';

test('sharing violation preserves the complete old lease and next publication recovers', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'nsp-identity-'));
  const file = path.join(directory, 'identities.bin');
  try {
    assert.equal(atomic(file, Buffer.from('old-complete-record')), true);
    const locked = {...fs, renameSync() {throw Object.assign(new Error('sharing violation'), {code:'EPERM'});}};
    assert.equal(atomic(file, Buffer.from('new-record'), locked), false);
    assert.equal(fs.readFileSync(file, 'utf8'), 'old-complete-record');
    assert.equal(fs.existsSync(file + '.tmp-' + process.pid), false);
    assert.equal(atomic(file, Buffer.from('fresh-complete-record')), true);
    assert.equal(fs.readFileSync(file, 'utf8'), 'fresh-complete-record');
    const fatal = {...fs, renameSync() {throw Object.assign(new Error('disk failure'), {code:'EIO'});}};
    assert.throws(() => atomic(file, Buffer.from('bad'), fatal), {code:'EIO'});
  } finally {fs.rmSync(directory, {recursive:true, force:true});}
});

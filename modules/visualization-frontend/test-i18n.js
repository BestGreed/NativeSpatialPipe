import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {english} from './i18n-en.js';
import {translate,normalizeLanguage} from './i18n.js';

assert.equal(normalizeLanguage('zh'),'zh');
for(const value of ['en','fr','',null,undefined])assert.equal(normalizeLanguage(value),'en');
assert.equal(translate('配置与状态','en'),'Configuration and status');
assert.equal(translate('配置与状态','zh'),'配置与状态');
assert.equal(translate('自动跟随活动流','en'),'Follow active stream');
assert.equal(translate('AES67 默认使用固定源流。','en'),'AES67 defaults to a fixed source stream.');
assert.equal(translate('输出槽 3 的设备输出通道','en'),'Output slot 3 device output channel');
assert.equal(translate('asio:{1234} 48000 7.1.4','en'),'asio:{1234} 48000 7.1.4');
assert.equal(translate('C → 第 2 路','en'),'C → channel 2');
for(const name of ['index.html','configuration.html']){
  const html=readFileSync(new URL(name,import.meta.url),'utf8');
  assert.ok(html.indexOf('/ui-language.js')<html.indexOf('<body'));
  assert.ok(html.includes('data-ui-language="pending"'));
}
const bootstrap=readFileSync(new URL('./i18n.js',import.meta.url),'utf8');
assert.ok(!bootstrap.includes("fetch('/api/web/bootstrap'"),'Language is supplied before the body, not fetched after rendering');

// Ensure every built-in UI phrase, including interpolated/error text, has an
// English entry. A new label is added to the same page and translated here.
const directory=fileURLToPath(new URL('.',import.meta.url));
const literals=/("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\/\/[^\n]*|\/\*[\s\S]*?\*\//g;
const missing=new Set();let count=0;
for(const name of readdirSync(directory)){
  if(name.startsWith('test-')||name.startsWith('i18n'))continue;
  if(!name.endsWith('.js')&&!name.endsWith('.html'))continue;
  const source=readFileSync(join(directory,name),{encoding:'utf8',flag:'r'});
  let text='';
  if(name.endsWith('.js'))text=Array.from(source.matchAll(literals),match=>match[1]??'').join('\n');
  else if(name.endsWith('.html'))text=source.replace(/&#(x[0-9a-f]+|\d+);/gi,(_,n)=>String.fromCodePoint(n[0].toLowerCase()==='x'?parseInt(n.slice(1),16):Number(n)));
  else continue;
  for(const match of text.matchAll(/[\u3400-\u9fff]+/gu)){count++;if(english[match[0]]===undefined)missing.add(match[0]);}
}
assert.deepEqual([...missing],[],'Missing English phrases');
assert.ok(count>=709,`Scanned ${count} phrase occurrences`);
console.log(`PASS inherited language, English fallback, interpolation, and ${Object.keys(english).length} UI phrases`);

import {english} from './i18n-en.js';

// The host receives FSA's resolved language. There is one language authority;
// no browser preference, saved override or language-selector UI is used.
let language='en';
export function normalizeLanguage(value){return value==='zh'?'zh':'en';}
export function translate(text,locale=language){
  if(normalizeLanguage(locale)==='zh'||typeof text!=='string')return text;
  const exact=english[text.trim()];
  return (exact??text.replace(/第\s*(\d+)\s*路/gu,'channel $1').replace(/[\u3400-\u9fff]+/gu,phrase=>english[phrase]===undefined?phrase:` ${english[phrase]} `))
    .replace(/[：，、。；（）？]/gu,ch=>({'：':': ','，':', ','、':', ','。':'. ','；':'; ','（':'(','）':')','？':'?'}[ch]))
    .replace(/[ \t]+/g,' ').replace(/\s+([,.;:!?])/g,'$1').replace(/\(\s+/g,'(').replace(/\s+\)/g,')').replace(/\b1 hours\b/g,'1 hour').trim();
}
const attributes=['title','aria-label','placeholder','alt','label'];
const skip='script,style,textarea,pre,code,[data-i18n-preserve],#fileInfo,#endpointInfo,#runtimeConfig';
export function localize(root=document){
  if(language==='zh')return;
  const element=root.nodeType===1?root:root.parentElement;
  if(element?.closest?.(skip))return;
  if(root.nodeType===3){
    if(!/[\u3400-\u9fff]/u.test(root.nodeValue??''))return;
    const value=translate(root.nodeValue);if(value!==root.nodeValue)root.nodeValue=value;
    return;
  }
  if(root.nodeType===1){
    for(const key of attributes){const value=root.getAttribute(key);if(value&&/[\u3400-\u9fff]/u.test(value)){const next=translate(value);if(value!==next)root.setAttribute(key,next);}}
  }
  for(const child of root.childNodes??[])localize(child);
}
export async function initializeLocalization(){
  // Non-browser contract fixtures exercise the form independently.
  if(!globalThis.document?.documentElement||!globalThis.MutationObserver)return;
  // Parser-blocking host script sets this before body parsing. The resolved
  // value stays fixed for this host; pages never poll or select a language.
  language=document.documentElement.lang==='zh-CN'?'zh':'en';
  document.documentElement.lang=language==='zh'?'zh-CN':'en';
  const title=document.querySelector('title[data-ui-title]');
  if(title)document.title=translate(title.dataset.uiTitle);
  localize(document.documentElement);
  if(language==='en'){
    const observer=new MutationObserver(records=>{
      const roots=new Set();
      for(const record of records){
        if(record.type==='childList')for(const node of record.addedNodes)roots.add(node);
        else roots.add(record.target);
      }
      for(const node of roots)localize(node);
    });
    observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attributes});
  }
  document.documentElement.dataset.uiLanguage='ready';
}
export const uiReady=initializeLocalization();

import assert from 'node:assert/strict';
import {createVoiceOverlay} from './voice-labels.js';

const manifest={frameRateHz:10,frameCount:600};
const labels={version:1,label:'speech-candidate',frameRateHz:10,frameCount:600,
  analyzedTracks:128,intervalsBySlot:{'12':[[4,8]]}};
const overlay=createVoiceOverlay(labels,manifest);
const frame=index=>({telemetrySeq:index,taps:{'decoded-input':{objects:[
  {kind:'dynamic',objectKey:{slot:12},sourceCategory:null},
  {kind:'dynamic',objectKey:{slot:13},sourceCategory:null},
]}}});
assert.equal(overlay(frame(3)).taps['decoded-input'].objects[0].sourceCategory,null);
assert.equal(overlay(frame(4)).taps['decoded-input'].objects[0].sourceCategory,'speech-candidate');
assert.equal(overlay(frame(7)).taps['decoded-input'].objects[0].sourceCategory,'speech-candidate');
assert.equal(overlay(frame(8)).taps['decoded-input'].objects[0].sourceCategory,null);
assert.equal(overlay(frame(5)).taps['decoded-input'].objects[1].sourceCategory,null);
assert.throws(()=>createVoiceOverlay({...labels,frameCount:599},manifest));
console.log('voice-labels tests passed');

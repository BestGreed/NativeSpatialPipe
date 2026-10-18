import assert from 'node:assert/strict';
import {isVoiceCategory,isVoiceVisualCategory,visualLayer,voiceArcVisible,voiceArcRadius,voiceArcSegments} from './voice-visual.js';

assert(isVoiceCategory('voice')&&isVoiceCategory('speech'));
assert(!isVoiceCategory(null)&&!isVoiceCategory('music')&&!isVoiceCategory('speech-candidate'));
assert(isVoiceVisualCategory('speech-candidate'));
assert.equal(visualLayer({kind:'dynamic',sourceCategory:'voice'}),'voice');
assert.equal(visualLayer({kind:'dynamic',sourceCategory:'speech-candidate'}),'voice');
assert.equal(visualLayer({kind:'dynamic',sourceCategory:null}),'ordinary');
assert.equal(visualLayer({kind:'static',sourceCategory:'voice'}),'ordinary');
assert(voiceArcVisible({kind:'dynamic',sourceCategory:'voice'},.2));
assert(!voiceArcVisible({kind:'dynamic',sourceCategory:'voice'},0));
assert(voiceArcRadius(-18)>voiceArcRadius(-48),'stronger voices get a larger arc');
assert.equal(voiceArcSegments([0,0,0],.3).length,0);
const segments=voiceArcSegments([0,0,1],.3,1);
assert.equal(segments.length,136,'mesh cost is bounded');
assert(segments.every(s=>s.a.every(Number.isFinite)&&s.b.every(Number.isFinite)));
assert.deepEqual(voiceArcSegments([1,0,0],.3,1,[0,0,0]),[]);
console.log('voice-visual tests passed');

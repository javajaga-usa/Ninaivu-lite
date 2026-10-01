import {defaults, validate} from './adjustments.mjs';
import {failure} from './messages.mjs';

//: Loaded by Node tests and the image worker, so it names its English with a
//: local key() rather than importing i18n.js; the component showing the text
//: translates it with i18n.t() (see ../utils/messages.mjs).
const i18n = {key: (s) => s};

export function checkRequest(text) {
  if(typeof text!=='string')throw new Error(i18n.key('Describe the edit you want.'));
  const q=text.normalize('NFKC').toLowerCase().trim();
  if(!q||q.length>2000)throw new Error(i18n.key('Enter an editing request up to 2,000 characters.'));
  if(/nude|naked|undress|sexual|intimate|deepfake|impersonat|face\s*swap|swap.*face|bypass|ignore.*(rule|safety)/.test(q))
    throw new Error(i18n.key('I can help with photography, but cannot create intimate images, impersonate someone, or alter identities.'));
  return q;
}

const presets={
  cinematic:{contrast:20,saturation:-12,warmth:-8,shadows:12,highlights:-25,vignette:20},
  vintage:{contrast:-12,saturation:-25,warmth:22,shadows:20,vignette:18},
  'black and white':{saturation:-100,contrast:15},
  'golden hour':{warmth:28,exposure:8,highlights:-15},
  'product photo':{exposure:12,contrast:10,saturation:-5,shadows:15},
  'natural look':{contrast:5,vibrance:8,sharpness:10},
  'hdr':{shadows:35,highlights:-35,clarity:25,vibrance:15},
};
const fields={'exposure':'exposure','brightness':'exposure','contrast':'contrast','saturation':'saturation','color':'saturation','colors':'saturation','vibrance':'vibrance','warmth':'warmth','white balance':'warmth','sharpness':'sharpness','noise reduction':'noise','shadows':'shadows','highlights':'highlights','clarity':'clarity','texture':'clarity','dehaze':'dehaze','haze':'dehaze','vignette':'vignette','angle':'angle','straighten':'angle','rotation':'angle'};

export function planRequest(text,current=defaults(),analysis={}) {
  let q=checkRequest(text).replace(/[.!?]+$/,'');
  q=q.replace(/black (?:and|&) white/g,'monochrome');
  const clauses=q.split(/\s*(?:,|;|\n|\band then\b|\band\b|\bthen\b|\bbut\b)\s*/).filter(Boolean);
  if(clauses.length>16)throw new Error(i18n.key('Use up to 16 editing steps per request.'));
  const state=validate(current), patch={};
  const assign=values=>{Object.assign(state,values);Object.assign(patch,values);validate(state);};
  for(let raw of clauses) {
    let c=raw.trim().replace(/^(please\s+|can you\s+|could you\s+)/,'').replace(/^please\s+/,'').replace(/\s+please$/,'');
    c=c.replace(/^(make|give) (this|the|my) (photo|image|picture)( look)?\s*/,'').replace(/^make (it|this)\s*/,'').replace(/^give (it|this)\s*/,'').trim();
    c=c.replace(/^(a|an)\s+/,'').replace(/\s+(look|style|feel)$/,'').trim();
    const intensity=/\b(slightly|gently|a little|subtle)\b/.test(c)?.5:/\b(strongly|dramatically|much)\b/.test(c)?1.6:1;
    c=c.replace(/\b(slightly|gently|a little|subtle|strongly|dramatically|much)\b/g,'').replace(/\s+/g,' ').trim();
    if(c==='monochrome')c='black and white';
    if(presets[c]){assign(presets[c]);continue;}
    if(/^(suitable for linkedin|profile picture|social media crop|square crop|crop (it |this )?(to )?(a )?(square|1:1))$/.test(c)){assign({crop:'square'});continue;}
    if(/^crop (it |this )?(to )?(16:9|landscape)$/.test(c)){assign({crop:'landscape'});continue;}
    if(/^crop (it |this )?(to )?(4:5|portrait)$/.test(c)){assign({crop:'portrait'});continue;}
    if(/^(crop (it |this )?(to |for )?(a )?(9:16|story|stories|reel|reels|vertical( video)?)|story crop|reel crop)$/.test(c)){assign({crop:'story'});continue;}
    if(/^(auto enhance|enhance( this( photo)?)?|improve( this)? photo)$/.test(c)){assign({exposure:analysis.brightness>170?-8:12,contrast:10,vibrance:10,clarity:6,highlights:-10});continue;}
    if(/^(like a professional product photo|professional product photo|product photo enhancement)$/.test(c)){assign(presets['product photo']);continue;}
    let m=c.match(/^(?:set |adjust )?(.+?) (to|by) ([+-]?\d+(?:\.\d+)?)\s*%?$/);
    if(m&&Object.hasOwn(fields,m[1])){const key=fields[m[1]],v=Number(m[3]);assign({[key]:m[2]==='by'?state[key]+v:v});continue;}
    m=c.match(/^(?:keep|leave|preserve) (.+?)(?: unchanged| as (?:it is|they are))?$/);
    if(m&&Object.hasOwn(fields,m[1])){const key=fields[m[1]];assign({[key]:current[key]??0});continue;}
    const simple=[
      [/^(brighter|brighten( (it|this|the photo))?|improve lighting|fix exposure)$/, 'exposure',25],
      [/^(darker|darken( (it|this|the photo))?)$/, 'exposure',-25],
      [/^(warmer|warm( it)? up)$/, 'warmth',20],
      [/^(cooler|cool( it)? down)$/, 'warmth',-20],
      [/^(sharpen( this( photo)?)?|increase sharpness|crisper)$/, 'sharpness',30],
      [/^(reduce noise|denoise|remove grain)$/, 'noise',30],
      [/^(improve colors|boost colors|more vibrant|more colorful|richer colors)$/, 'vibrance',20],
      [/^(more saturated|more saturation)$/, 'saturation',20],
      [/^(less saturated|less colorful|muted colors)$/, 'saturation',-20],
      [/^(adjust contrast|more contrast)$/, 'contrast',20],
      [/^(dehaze|remove( the)? haze|clear( the)? haze|less hazy|cut through the mist)$/, 'dehaze',30],
      [/^(add haze|hazier|misty|dreamy)$/, 'dehaze',-20],
      [/^(add clarity|more clarity|more texture|punchier|more punch|crisper details)$/, 'clarity',20],
      [/^(softer|less clarity|smoother)$/, 'clarity',-20],
      [/^(lift|brighten|open up)( the)? shadows$/, 'shadows',30],
      [/^(recover|protect|soften|reduce)( the)? highlights$/, 'highlights',-30],
      [/^(add( a)? vignette)$/, 'vignette',25],
    ];
    const match=simple.find(([pattern])=>pattern.test(c));
    if(match){const [,key,delta]=match;assign({[key]:state[key]+delta*intensity});continue;}
    m=c.match(/^(increase|decrease|reduce|boost|lower) (.+?)(?: by ([\d.]+)\s*%?)?$/);
    if(m&&Object.hasOwn(fields,m[2])){const key=fields[m[2]],direction=/decrease|reduce|lower/.test(m[1])?-1:1;assign({[key]:state[key]+direction*(m[3]?Number(m[3]):20*intensity)});continue;}
    if(/background|remove|replace|person|people|face|body|sky|restore|restoration|old photo/.test(c))
      throw new Error(i18n.key('Sudar adjusts light, colour, framing and clothing colour; it does not remove or replace what is in the picture. No part of your request was applied.'));
    throw failure(i18n.key('I could not reliably interpret “{request}”. Use explicit steps such as “lift shadows, reduce highlights, make it warmer”. No changes were applied.'), {request: raw});
  }
  return {patch,summary:i18n.key('Review the combined adjustments before applying.'),provider:i18n.key('Built-in local planner')};
}

export function interpret(text) {return planRequest(text).patch;}

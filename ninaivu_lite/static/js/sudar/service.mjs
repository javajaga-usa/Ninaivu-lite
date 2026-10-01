import {analyze, suggestions} from './adjustments.mjs';
import {planRequest, checkRequest} from './commands.mjs';
import {render} from './processor.mjs';
import * as i18n from '../i18n.js';

/**
 * Sudar's work, all of it in the browser (Ninaivu's AIPhotoService without
 * the parts that spoke to a model): measure the picture, suggest, plan a
 * request in plain words, and render the adjustments in a worker.
 */
export class AIPhotoService {
  analyzeImage(bitmap) {
    const scale=Math.min(1,256/Math.max(bitmap.width,bitmap.height));
    const c=document.createElement('canvas'); c.width=Math.max(1,Math.round(bitmap.width*scale)); c.height=Math.max(1,Math.round(bitmap.height*scale));
    const ctx=c.getContext('2d',{willReadFrequently:true}); ctx.drawImage(bitmap,0,0,c.width,c.height);
    return analyze(ctx.getImageData(0,0,c.width,c.height).data,bitmap.width,bitmap.height);
  }
  getSuggestions(analysis) { return suggestions(analysis); }
  /** Plain words into a patch of adjustments, reviewed before it is applied. */
  async planEdit(text,current,analysis) {
    checkRequest(text);
    return planRequest(text,current,analysis);
  }
  async applyAdjustments(bitmap, adjustments, {maxSide=1400,type='image/png'}={}) {
    if (typeof Worker !== 'undefined' && typeof OffscreenCanvas !== 'undefined') {
      const copy=await createImageBitmap(bitmap);
      try {
        return await new Promise((resolve,reject)=>{
          const worker=new Worker(new URL('./worker.mjs',import.meta.url),{type:'module'});
          const timer=setTimeout(()=>{worker.terminate();reject(new Error(i18n.t('Processing timed out. Try a smaller image.')));},120000);
          const finish=()=>{clearTimeout(timer);worker.terminate();};
          worker.onmessage=({data})=>{finish();data.error?reject(new Error(data.error)):resolve(data.blob);};
          worker.onerror=()=>{finish();reject(new Error('Worker unavailable'));};
          worker.postMessage({bitmap:copy,adjustments,maxSide,type},[copy]);
        });
      } catch { copy.close(); /* Cooperative CPU fallback, without network calls. */ }
    }
    const canvas=await render(bitmap,adjustments,maxSide);
    if(canvas.convertToBlob) return canvas.convertToBlob({type,quality:.94});
    return new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error(i18n.t('Export failed.'))),type,.94));
  }
}

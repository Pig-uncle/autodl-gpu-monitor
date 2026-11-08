const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8');
const js=html.slice(html.indexOf('function listValues('),html.indexOf('async function catalog()'));
class E{constructor(){this.value='';this.children=[];this.checked=false;this.textContent='';this.listeners={};}replaceChildren(...v){this.children=v}append(...v){this.children.push(...v)}querySelectorAll(){return this.children.flatMap(c=>c.children.filter(i=>i.type==='checkbox'))}setAttribute(){}addEventListener(name,cb){this.listeners[name]=cb}}
const ids={};for(const id of ['gpu_models','region_sign_list','gpuChoices','gpu-list','gpuRegionHint','regionChoices','clearRegions'])ids[id]=new E();
const timers=new Map(),pending=[];let next=0;
const ctx=vm.createContext({document:{createElement:()=>new E()},$:id=>ids[id],active:false,toast:()=>{},setTimeout:fn=>{timers.set(++next,fn);return next},clearTimeout:id=>timers.delete(id),api:(_path,body)=>new Promise((resolve,reject)=>pending.push({regions:body.regions,resolve,reject}))});
vm.runInContext(js,ctx);vm.runInContext("gpuCatalog=['A','B','C'];",ctx);ids.gpu_models.value='B';vm.runInContext('scheduleRegionModels()',ctx);assert.equal(ids.gpuChoices.children.length,3);
ids.region_sign_list.value='west-D';vm.runInContext('scheduleRegionModels()',ctx);const first=[...timers.values()].pop();timers.clear();first();
ids.region_sign_list.value='bj-B1';vm.runInContext('scheduleRegionModels()',ctx);const second=[...timers.values()].pop();timers.clear();second();
(async()=>{pending[1].resolve({models:['C']});await new Promise(setImmediate);assert.equal(ids.gpu_models.value,'B');assert.deepEqual(ids.gpuChoices.children.map(x=>x.children[1].textContent),['C','B（当前地区未列出）']);pending[0].resolve({models:['A']});await new Promise(setImmediate);assert.deepEqual(ids.gpuChoices.children.map(x=>x.children[1].textContent),['C','B（当前地区未列出）']);ids.clearRegions.onclick();assert.deepEqual(ids.gpuChoices.children.map(x=>x.children[1].textContent),['A','B','C']);console.log('region change, stale response, selection preservation and clear: OK')})().catch(e=>{console.error(e);process.exitCode=1});

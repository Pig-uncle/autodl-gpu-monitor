const fs=require('fs'),vm=require('vm'),assert=require('assert');
const h=fs.readFileSync('web/index.html','utf8');
assert(h.includes('https://www.autodl.com/console/center/settings/token'));
const code=h.slice(h.indexOf('async function loadWechat()'),h.indexOf("$('resetOrder').onclick"));
class E{constructor(){this.value='';this.checked=false;this.disabled=false;this.textContent='';this.listeners={};this.open=false}addEventListener(n,f){this.listeners[n]=f}}
const els={};const calls=[];const ctx=vm.createContext({$:id=>els[id]||(els[id]=new E()),confirm:()=>false,action:f=>f(),api:async(path,body)=>{calls.push({path,body});return path==='wechat-notification'&&body===undefined?{enabled:true,has_token:true}:{ok:true}}});
vm.runInContext(code,ctx);
(async()=>{await vm.runInContext('loadWechat()',ctx);assert.equal(els.wechatEnabled.checked,true);assert.equal(els.wechatToken.value,'');els.wechatToken.value='new-token';await assert.rejects(els.testWechat.onclick(),/先保存/);assert(!calls.some(x=>x.path==='wechat-notification/test'));await els.saveWechat.onclick();assert.equal(calls.find(x=>x.body&&x.body.token==='new-token').path,'wechat-notification');assert.equal(els.wechatToken.value,'');await els.testWechat.onclick();assert.equal(calls.filter(x=>x.path==='wechat-notification/test').length,1);console.log('notification UI save, hidden token and explicit test: OK')})().catch(e=>{console.error(e);process.exitCode=1});

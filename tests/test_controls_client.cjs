const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('act_controls.py','utf8').split('USAGE_JS = r"""')[1].split('"""')[0];
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function page({app='bedflow',admin=false,standalone=false,storage=new Map(),ok=true,enabled=true}={}){
 const calls=[],events={},intervals=[],navigation=[];
 const ctx={document:{currentScript:{dataset:{app,admin:admin?'1':'0'}}},Intl,Date,JSON,encodeURIComponent,
  sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
  navigator:{standalone:false},matchMedia:()=>({matches:standalone}),
  addEventListener:(name,callback)=>{events[name]=callback;},setInterval:callback=>intervals.push(callback),
  location:{reload:()=>navigation.push('reload')},
  fetch:async(url,options={})=>{calls.push({url,options});return {ok,status:ok?200:503,json:async()=>({enabled})};}};
 vm.createContext(ctx);vm.runInContext(source,ctx);return{calls,events,intervals,navigation,storage};
}
(async()=>{
 const p=page();await flush();assert.equal(p.calls.length,1);assert.deepEqual(JSON.parse(p.calls[0].options.body),{app:'bedflow',event:'visit'});
 assert.equal(p.events.beforeinstallprompt,undefined);p.events.appinstalled();await flush();assert.equal(JSON.parse(p.calls[1].options.body).event,'install');
 const again=page({storage:p.storage});await flush();assert.equal(again.calls.length,0,'Reloads in the same tab/day do not inflate visits');
 const standalone=page({standalone:true});await flush();assert.deepEqual(standalone.calls.map(x=>JSON.parse(x.options.body).event),['visit','launch']);
 const other=page({app:'doctor_call',storage:p.storage});await flush();assert.equal(JSON.parse(other.calls[0].options.body).app,'doctor_call');
 const failed=page({ok:false});await flush();assert.equal(failed.storage.size,0,'An unsuccessful metric request is not marked as recorded');
 const paused=page({enabled:false});await flush();await paused.intervals[0]();assert.deepEqual(paused.navigation,['reload']);
 const admin=page({admin:true,enabled:false});await flush();assert.equal(admin.intervals.length,0,'The administrator is not forced out of a paused app');
 console.log('Usage client: visit/launch deduplication, real install events, service isolation, failed writes and pause refresh passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Execute only inside Codex functions.exec. Existing MCP tools are injected by Codex.
// Project snapshots to task controls BEFORE writing files; never store chat bodies.
(async function(task, count=8) {
if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(task))throw Error("Invalid task ID");
for(let i=0;i<Math.min(count,10);i++){
const r=await tools.exec_command({cmd:"$d = (Get-Content -Encoding UTF8 tasks/"+task+"/active-bridge.json | ConvertFrom-Json).directory\nfor ($i=0; $i -lt 150; $i++) { if (Test-Path (Join-Path $d 'done.json')) { Get-Content -Raw -Encoding UTF8 (Join-Path $d 'done.json'); break }; if (Test-Path (Join-Path $d 'request.json')) { $q = Get-Content -Raw -Encoding UTF8 (Join-Path $d 'request.json') | ConvertFrom-Json; @{ directory=$d; request=$q } | ConvertTo-Json -Depth 15 -Compress; break }; Start-Sleep -Milliseconds 100 }",max_output_tokens:5000});
if(!r.output.trim()){text("No request");break}
const envelope=JSON.parse(r.output);
if(!envelope.request){text(envelope);break}
const q=envelope.request;
const key="mcp__chrome_devtools__"+q.tool;
if(!["list_pages","take_snapshot","fill","click","evaluate_script","wait_for","navigate_page"].includes(q.tool))throw Error("Unexpected tool");
if(q.expires_at && Date.now()/1000 >= q.expires_at)throw Error("Expired request; do not execute");
if(q.tool==="navigate_page" && !/^file:\/\/\/[^?#]+\/tests\/fixtures\/(model-router|ai-local)\.html\?model=(chatgpt|claude|gemini)(&mode=[a-z-]+)?$/.test(q.arguments.url))throw Error("Only local fixture navigation allowed");
let data;try{
const result=await tools[key](q.arguments);
if(q.tool==="take_snapshot" && !result.isError){
 const raw=(result.content||[]).filter(c=>c.type==="text").map(c=>c.text).join("\n");
 const kept=[];
 for(const line of raw.split("\n")){
  const m=line.match(/uid=(\S+)\s+(button|combobox|textbox|menuitemradio|menuitem|option|radio|alert|dialog|status)\s+"([^"]*)"(.*)/);
  if(!m)continue;
  const [,uid,role,label,tail]=m;
  const isInput=role==="textbox" && /prompt|message|ask|reply|输入|询问|提问|消息|回复/i.test(label);
  const relevant=/model selector|current model|choose model|select model|mode picker|选择.*模型|模型[:：]|思考强度|^强度$|reasoning|thinking level|^(High|Medium|Low|Default|Send|Submit|发送|提交|Log in|Sign in|登录)$|^(?:Selected )?(?:GPT[- ]\d|(?:Claude )?(?:Sonnet|Opus|Haiku|Fable)\s+\d|(?:Gemini )?\d+(?:\.\d+)* [A-Za-z])/i.test(label);
  const notice=["alert","dialog","status"].includes(role) && /quota|usage limit|limit reached|upgrade|subscribe|额度|上限|升级|订阅/i.test(label);
  if(!isInput&&!relevant&&!notice)continue;
  const flags=(tail.match(/(?:checked|selected)(?:=(?:true|false))?|disabled|expandable|expanded|haspopup="[^"]*"|multiline/g)||[]).join(" ");
  const desc=label==="强度" ? (tail.match(/description="[^"]*"/)||[""])[0] : "";
  kept.push('uid='+uid+' '+role+' "'+label+'" '+flags+' '+desc);
 }
 result.content=[{type:"text",text:kept.join("\n")}];
}
data={id:q.id,result};
}catch(e){data={id:q.id,error:String(e)}}
const path=envelope.directory.replaceAll("\\","/")+"/response-"+q.id+".json";
await tools.apply_patch("*** Begin Patch\n*** Add File: "+path+"\n+"+JSON.stringify(data)+"\n*** End Patch");
text({tool:q.tool,page:q.arguments.pageId,error:data.error});
await new Promise(resolve => setTimeout(resolve, 150));
}
})

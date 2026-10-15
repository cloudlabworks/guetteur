// Records the public AWS dashboard. No account pages, cookies, or private camera frames.
// Requires Playwright Core (PLAYWRIGHT_CORE), Chrome (CHROME_BIN), ffmpeg and ffprobe.
const { chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const { once } = require('node:events');
const scenes = JSON.parse(fs.readFileSync('docs/submission/demo/storyboard.json'));
const out = path.resolve(process.env.DEMO_OUTPUT || 'tmp/submission-video-20261015');
const endpoint = 'https://nm4p814bp0.execute-api.us-west-2.amazonaws.com/';
const delay = ms => new Promise(r => setTimeout(r, ms));
const escape = s => s.replace(/[&<>"']/g, x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const baseCSS = `*{box-sizing:border-box}body{margin:0;background:#111b23;color:#ecf1ee;font:27px/1.5 system-ui;padding:70px 90px}h1{font-size:64px;line-height:1.13;margin:28px 0}h2{font-size:38px}p{color:#b8c9cf}small{font-size:22px;color:#f2bd69}.tag{font-size:22px;text-transform:uppercase;letter-spacing:3px;color:#eaa754}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.box{padding:22px;border:1px solid #486272;border-radius:12px;background:#192b36}.num{font-size:56px;font-weight:750;color:#eaa754}footer{position:absolute;bottom:36px;font-size:19px;color:#8ea5af}`;
const cards = {
 intro:`<div class="tag">Cloud Lab Works · Team Drift · Alex Alvord</div><h1>Guetteur</h1><h2>Active perception.<br>Claims you can remeasure.</h2><p>OpenCV 5 + AWS Lambda arm64<br>A perception → decision → action loop with receipts.</p><small>Recorded AWS demo · Synthetic depth scenes · Synthetic narration</small><footer>OpenCV AI Competition 2026, powered by AWS</footer>`,
 architecture:`<div class="tag">Deployed demo architecture</div><h1>Evidence changes the next action.</h1><div class="box">Browser dashboard → API Gateway → Lambda arm64</div><p style="margin:16px 0">Inside Lambda:</p><div class="box">Synthetic depth → OpenCV 5 → JSON evidence → Rule planner<br>↳ Recheck / pause / compensate / request approval → next perception</div><div class="grid" style="margin-top:24px"><div class="box">Claim + pinned input<br>→ NumPy estimator<br>→ OpenCV estimator</div><div class="box">Gate simulation<br>Approve / deny / timeout<br>No answer → no alert</div></div><footer>Physical depth recorder is separate. Authenticated edge/cloud approvals remain phase 2.</footer>`,
 results:`<div class="tag">Synthetic development-set results · Not real-world accuracy</div><h1>Keep the misses in the result.</h1><div class="grid"><div class="box"><span class="num">30 / 33</span><br>Rule-planner task success<br>Raw baseline: 12 / 33<br>Precision 1.00 · Recall 0.86</div><div class="box"><span class="num">24 / 24</span><br>Corrected tamper cases rejected<br>21 / 21 honest ledgers verified<br>Initial result: 23 / 24, documented</div></div><p>Physical capture: 3 depth-only takes × 300 frames.<br>Integrity + clean shutdown verified; calibrated detection evaluation remains open.</p><small>github.com/cloudlabworks/guetteur · MIT · Pinned dependencies + report + raw results</small><footer>AI coding assistance disclosed. Narrator: Kokoro-82M am_michael (Apache-2.0), not a voice clone.</footer>`
};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const ctx=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,colorScheme:'dark'});
 const p=await ctx.newPage();
 const responses=[];
 p.on('response',async r=>{if(r.url().includes('/api/run')||r.url().includes('/api/verify')){try{responses.push({url:r.url(),status:r.status(),body:await r.json()});}catch{}}});
 async function run(sc,pl='rule',gt='approve'){
  await p.evaluate(()=>window.scrollTo(0,0));
  await p.locator('#sc').selectOption(sc);await p.locator('#pl').selectOption(pl);await p.locator('#gt').selectOption(gt);
  await delay(1100);await p.locator('#go').click();
  await p.waitForResponse(r=>r.url().endsWith('/api/run'),{timeout:60000});
  await p.locator('#out .card').first().waitFor();await delay(1000);
 }
 const manifest=[];
 for(let i=0;i<scenes.length;i++){
  const scene=scenes[i],wav=path.join(out,`p${String(i).padStart(2,'0')}.wav`);
  const duration=Number(execFileSync('ffprobe',['-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',wav],{encoding:'utf8'}).trim());
  if(cards[scene.id])await p.setContent(`<html><head><style>${baseCSS}</style></head><body>${cards[scene.id]}</body></html>`);
  else {await p.goto(endpoint);await p.locator('#sc option').first().waitFor({state:'attached'});}
  const target=path.join(out,`${scene.id}-silent.mp4`);
  const enc=spawn('ffmpeg',['-y','-loglevel','error','-f','image2pipe','-framerate','5','-vcodec','mjpeg','-i','pipe:0','-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-r','25',target],{stdio:['pipe','ignore','inherit']});
  const ended=once(enc,'close');let recording=true,frames=0;
  const capture=(async()=>{while(recording){const t=Date.now();const bytes=await p.screenshot({type:'jpeg',quality:88});if(!enc.stdin.write(bytes))await once(enc.stdin,'drain');frames++;await delay(Math.max(0,200-(Date.now()-t)));}})();
  const start=Date.now();
  if(scene.id==='adaptive')await run('subtle_item_removed');
  if(scene.id==='baseline')await run('subtle_item_removed','naive');
  if(scene.id==='timeout'){await run('shelf_item_removed','rule','timeout');await delay(4000);await p.getByText('Alerts that left the system',{exact:false}).scrollIntoViewIfNeeded();}
  if(scene.id==='tamper'){await run('subtle_item_removed');await p.getByRole('button',{name:'Tamper: double the claim'}).first().scrollIntoViewIfNeeded();await delay(3500);await p.getByRole('button',{name:'Tamper: double the claim'}).first().click();await p.locator('#tm').filter({hasText:'rejected'}).waitFor({timeout:60000});}
  if(scene.id==='failure')await run('subthreshold_item_removed');
  await delay(1500);await p.screenshot({path:path.join(out,`${scene.id}-review.png`)});
  await delay(Math.max(0,(duration+1)*1000-(Date.now()-start)));
  recording=false;await capture;enc.stdin.end();const [code]=await ended;if(code!==0)throw Error('encoder failed '+code);
  // Real capture frames are kept in order; duration is conformed to the narration, with no result changes.
  const final=path.join(out,`${scene.id}.mp4`);
  execFileSync('ffmpeg',['-y','-loglevel','error','-i',target,'-i',wav,'-vf',`setpts=${((duration+1)/(frames/5)).toFixed(8)}*PTS`,'-af','apad=pad_dur=1','-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac','-b:a','160k','-t',String(duration+1),'-pix_fmt','yuv420p',final]);
  manifest.push({id:scene.id,title:scene.title,duration:duration+1,frames,file:path.basename(final)});
  console.log('Recorded',scene.id,(duration+1).toFixed(2),'seconds');
 }
 fs.writeFileSync(path.join(out,'recorded-responses.json'),JSON.stringify(responses,null,2));
 fs.writeFileSync(path.join(out,'manifest.json'),JSON.stringify(manifest,null,2));
 fs.writeFileSync(path.join(out,'concat.txt'),manifest.map(x=>`file '${x.file}'`).join('\n'));
 execFileSync('ffmpeg',['-y','-loglevel','error','-f','concat','-safe','0','-i',path.join(out,'concat.txt'),'-c','copy','-movflags','+faststart',path.join(out,'guetteur-demo.mp4')]);
 await browser.close();console.log('Complete',path.join(out,'guetteur-demo.mp4'));
})().catch(e=>{console.error(e);process.exit(1)});

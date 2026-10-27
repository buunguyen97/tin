import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
// node demo/render.mjs [props.json] [out.mp4]
// node demo/render.mjs --batch jobs.json      jobs.json: [{"props": "...", "output": "...mp4"}, ...]
const root=process.cwd();
const jobs=process.argv[2]==='--batch'
 ? JSON.parse(fs.readFileSync(process.argv[3],'utf8'))
 : [{props:process.argv[2] || 'demo/props-vieneu.json', output:process.argv[3] || 'output/diem-tin-2026-10-27-vieneu.mp4'}];
const MIME={'.mp3':'audio/mpeg','.wav':'audio/wav','.svg':'image/svg+xml','.jpg':'image/jpeg','.png':'image/png','.webp':'image/webp'};
const server=http.createServer((req,res)=>{
 const relative=decodeURIComponent((req.url||'/').split('?')[0]);
 const file=path.resolve(root,'demo','.'+relative);
 if(!file.startsWith(path.join(root,'demo')+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
 const stat=fs.statSync(file); const mime=MIME[path.extname(file)]||'application/octet-stream';
 res.setHeader('Access-Control-Allow-Origin','*'); res.setHeader('Content-Type',mime); res.setHeader('Accept-Ranges','bytes');
 const range=req.headers.range;
 if(range){const m=/bytes=(\d+)-(\d*)/.exec(range); const start=Number(m[1]);const end=m[2]?Number(m[2]):stat.size-1;res.writeHead(206,{'Content-Range':`bytes ${start}-${end}/${stat.size}`,'Content-Length':end-start+1});fs.createReadStream(file,{start,end}).pipe(res);}
 else {res.setHeader('Content-Length',stat.size);fs.createReadStream(file).pipe(res);}
});
await new Promise(r=>server.listen(8765,'127.0.0.1',r));
const failed=[];
try{
 console.log('Bundling original Videofy player');
 const serveUrl=await bundle({entryPoint:path.join(root,'player/src/studio-index.ts'),webpackOverride:c=>c});
 for(const [n,job] of jobs.entries()){
  const outputFile=path.resolve(job.output);
  console.log(`\n[${n+1}/${jobs.length}] ${path.basename(outputFile)}`);
  try{
   fs.mkdirSync(path.dirname(outputFile),{recursive:true});
   const props=JSON.parse(fs.readFileSync(job.props,'utf8'));
   const composition=await selectComposition({serveUrl,id:'ArticlesSeries',inputProps:props,timeoutInMilliseconds:120000});
   console.log('Composition',composition.width,composition.height,composition.durationInFrames / composition.fps,'seconds');
   await renderStill({serveUrl,composition,inputProps:props,frame:50,output:outputFile.replace(/\.mp4$/, '-preview.png'),imageFormat:'png',timeoutInMilliseconds:120000});
   let last=-1;
   await renderMedia({serveUrl,composition,inputProps:props,codec:'h264',audioCodec:'aac',outputLocation:outputFile,imageFormat:'jpeg',crf:20,concurrency:4,timeoutInMilliseconds:120000,onProgress:({progress})=>{const pct=Math.floor(progress*10)*10;if(pct>last){console.log('Render',pct+'%');last=pct;}}});
   console.log('DONE',outputFile);
  }catch(err){
   // One broken video must not stop the rest of the day's batch.
   console.error('RENDER FAILED',outputFile,err?.message||err);
   failed.push(outputFile);
  }
 }
}finally{server.close();}
if(failed.length) process.exitCode=1;

import argparse, asyncio, json, math, html, time, wave, os
from pathlib import Path
from tts_audio import audio_duration, cache_path
from datetime import date as Date
from edition import load_edition, display_date, WEEKDAYS
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
root=Path(__file__).resolve().parent
assets=root/'assets'; assets.mkdir(exist_ok=True)
BASE='http://127.0.0.1:8765'  # render.mjs serves demo/ here
W,H=1080,1920; PHOTO_TOP,PHOTO_MAX=300,640
def url(path): return f'{BASE}/{Path(path).resolve().relative_to(root).as_posix()}'
def text(x,y,value,size=40,color='#F6F8FC',weight=400,anchor='start'):
 return f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{html.escape(value)}</text>'
def wrap(value,limit):
 lines=[]; line=''
 for w in value.split():
  if line and len(line+' '+w)>limit: lines.append(line); line=w
  else: line=(line+' '+w).strip()
 return lines+[line]
def background(item,photo,out):
 """1080x1920 JPEG: blurred cover fill + sharp landscape photo in the top half.
 og:images are ~1200x630, so cropping one to full portrait would upscale it ~3x."""
 if photo:
  src=Image.open(photo).convert('RGB')
  bg=ImageEnhance.Brightness(ImageOps.fit(src,(W//4,H//4)).filter(ImageFilter.GaussianBlur(10))).enhance(.45).resize((W,H),Image.LANCZOS)
  h=min(round(W*src.height/src.width),PHOTO_MAX)
  bg.paste(ImageOps.fit(src,(W,h),Image.LANCZOS),(0,PHOTO_TOP))
 else:
  # No relevant photo: dark gradient with a soft accent glow where the photo would be.
  bg=Image.new('RGB',(W,H),'#060D18'); d=ImageDraw.Draw(bg)
  for y in range(H): d.line([(0,y),(W,y)],fill=tuple(round(a+(b-a)*y/H) for a,b in zip((16,30,50),(6,13,24))))
  glow=Image.new('L',(W,H),0); ImageDraw.Draw(glow).ellipse((90,320,990,900),fill=190)
  bg=Image.composite(Image.new('RGB',(W,H),item['accent']),bg,glow.filter(ImageFilter.GaussianBlur(110)))
 bg.save(out,quality=90); return out
def card(edition,item,i,credit):
 """Transparent overlay drawn by the player above the (zooming) background."""
 a=item['accent']; total=len(edition['stories']); s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">','<defs><linearGradient id="top" x2="0" y2="1"><stop stop-color="#060D18" stop-opacity=".9"/><stop offset="1" stop-color="#060D18" stop-opacity="0"/></linearGradient><linearGradient id="bottom" x2="0" y2="1"><stop stop-color="#060D18" stop-opacity="0"/><stop offset=".13" stop-color="#060D18" stop-opacity=".93"/><stop offset="1" stop-color="#060D18" stop-opacity=".97"/></linearGradient></defs>','<rect width="1080" height="330" fill="url(#top)"/><rect y="830" width="1080" height="1090" fill="url(#bottom)"/>']
 s += [f'<rect x="80" y="108" width="12" height="44" rx="6" fill="{a}"/>',text(114,143,edition['title'],32,weight=700),text(80,215,display_date(edition['date'])[0],27,'#B6C4D5'),text(1000,143,f'{i+1:02d} / {total:02d}',30,a,700,'end')]
 if credit: s.append(text(1000,940,f'Ảnh: {credit}',21,'#AAB8CA',anchor='end'))
 s.append(text(80,985,item['category'],27,a,700))
 for j,line in enumerate(item['title']): s.append(text(80,1065+j*72,line,58,weight=700))
 s += [f'<rect x="80" y="1200" width="920" height="270" rx="28" fill="{a}" fill-opacity=".1" stroke="{a}" stroke-opacity=".35"/>',text(120,1345,item['number'],130 if len(item['number'])<6 else 112,a,700),text(120,1425,item['unit'],30,a,700)]
 for j,line in enumerate(item['detail']): s.append(text(80,1545+j*55,line,38,weight=700))
 for j,line in enumerate(wrap(item['note'],55)[:2]): s.append(text(80,1665+j*38,line,27,'#AAB8CA'))
 s += ['<path d="M80 1745H1000" stroke="#334155"/>',text(80,1790,'NGUỒN',22,'#788CA5',700),text(80,1830,item['source'],26,'#B6C4D5'),text(1000,1790,'NỘI BỘ • GIỌNG ĐỌC AI',20,'#788CA5',anchor='end')]
 for j in range(total): s.append(f'<rect x="{80+j*(940//total)}" y="1868" width="{940//total-20}" height="5" rx="2" fill="{a if j==i else "#283449"}"/>')
 s.append('</svg>'); return ''.join(s)
def spoken(edition,i):
 """What the voice reads for story i; the outro closes the last one."""
 parts=[edition['stories'][i]['script'],edition.get('outro','') if i==len(edition['stories'])-1 else '']
 return ' '.join(p.strip() for p in parts if p.strip())
def intro_frame(edition,photos,out):
 """Intro background: intro_image if set, else the video's own story photos stacked."""
 if edition.get('intro_image'):
  path=Path(edition['intro_image']); path=path if path.is_absolute() else root/path
  bg=ImageOps.fit(Image.open(path).convert('RGB'),(W,H),Image.LANCZOS)
 else:
  files=[assets/photos[k]['file'] for k in sorted(photos,key=int)][:3]
  if len(files)<2: return background({'accent':edition['intro_accent']},files[0] if files else None,out)
  bg=Image.new('RGB',(W,H)); h=H//len(files)
  for j,f in enumerate(files): bg.paste(ImageOps.fit(Image.open(f).convert('RGB'),(W,h if j<len(files)-1 else H-h*j),Image.LANCZOS),(0,h*j))
 ImageEnhance.Brightness(bg).enhance(.8).save(out,quality=90); return out
def intro_card(edition):
 """Title card: 'BẢN TIN | THỨ BA', headline, big date, tagline, brand."""
 a=edition['intro_accent']; y,m,d=edition['date'].split('-'); lines=edition['intro_title']
 size=min(92,int(900/(.66*max(len(l) for l in lines))))
 head='font-family="Arial Black, Arial, sans-serif" font-weight="900" text-anchor="middle" filter="url(#sh)"'
 s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">','<defs><filter id="sh" x="-10%" y="-20%" width="120%" height="140%"><feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity=".7"/></filter><linearGradient id="v" x2="0" y2="1"><stop stop-color="#060D18" stop-opacity=".55"/><stop offset=".35" stop-color="#060D18" stop-opacity=".1"/><stop offset=".7" stop-color="#060D18" stop-opacity=".25"/><stop offset="1" stop-color="#060D18" stop-opacity=".95"/></linearGradient></defs>',f'<rect width="{W}" height="{H}" fill="url(#v)"/>','<rect x="60" y="770" width="960" height="640" rx="36" fill="#060D18" fill-opacity=".5"/>']
 chip=f'BẢN TIN  |  {WEEKDAYS[Date(int(y),int(m),int(d)).weekday()]}'
 s+=[f'<rect x="100" y="680" width="{int(len(chip)*21+70)}" height="72" rx="36" fill="#0B1220" fill-opacity=".85"/>',text(135,728,chip,34,weight=700)]
 cy=840
 for line in lines: cy+=int(size*1.08); s.append(f'<text x="540" y="{cy}" fill="#FFFFFF" font-size="{size}" {head}>{html.escape(line)}</text>')
 cy+=190; s.append(f'<text x="540" y="{cy}" fill="{a}" font-size="180" {head}>{d}/{m}</text>')
 if edition.get('intro_tagline'): cy+=95; s.append(f'<text x="540" y="{cy}" fill="#FFFFFF" font-size="50" {head}>{html.escape(edition["intro_tagline"])}</text>')
 s+=[text(100,1640,edition.get('brand',''),30,'#E2E8F0',700),text(80,1810,f'TỔNG HỢP NGÀY {d}.{m}.{y}',30,'#94A3B8',700),'</svg>']
 return ''.join(s)
class Voice:
 def __init__(self,provider,voice,threads): self.provider,self.voice,self.threads,self.tts=provider,voice,threads,None
 async def say(self,script,out,name=None):
  name=name or self.voice
  if self.provider=='vieneu':
   if self.tts is None:
    # ORT 1.24 rejects external weights reached via cross-directory HF symlinks.
    # Use a dedicated cache with regular files, without changing the shared cache.
    os.environ['HF_HUB_CACHE']=str(root/'.model-cache'/'hub')
    os.environ['HF_HUB_DISABLE_SYMLINKS']='1'
    from vieneu import Vieneu
    print('Loading VieNeu v3 Turbo, CPU/ONNX fp32; voice:',self.voice,flush=True)
    self.tts=Vieneu(mode='v3turbo',backend='onnx',device='cpu',precision='fp32',threads=self.threads)
   if name not in [v[1] for v in self.tts.list_preset_voices()]:
    raise ValueError(f'Unknown voice: {name}. Available: {self.tts.list_preset_voices()}')
   self.tts.save(self.tts.infer(script,voice=name),str(out))
  else:
   import edge_tts
   for attempt in range(4):
    try:
     await edge_tts.Communicate(script,name).save(str(out))
     break
    except edge_tts.exceptions.NoAudioReceived:
     if attempt == 3: raise
     print('TTS retry',attempt+1,flush=True)
     await asyncio.sleep(2)
async def prepare(workdir,legacy,voice,model,force):
 edition=load_edition(root/'news.json' if legacy else workdir/'news.json'); news=edition['stories']
 out_dir=assets if legacy else workdir  # legacy demo keeps frames and cards in demo/assets
 photos=json.loads((workdir/'images.json').read_text()) if (workdir/'images.json').exists() else {}
 manuscripts=[]; total=0; timings=[]; name=edition.get('voice') or voice.voice
 async def record(script,label,pad=.35):
  audio_file=cache_path(assets,provider=voice.provider,voice=name,model=model,text=script)
  started=time.monotonic(); cached=False
  if audio_file.exists() and not force:
   try:
    audio_duration(audio_file); cached=True
   except (ValueError, EOFError, wave.Error):
    pass
  if not cached:
   # Write to a temporary file: interrupted synthesis never becomes a cache hit.
   temporary=audio_file.with_name(audio_file.stem+'.part'+audio_file.suffix)
   await voice.say(script,temporary,name)
   audio_duration(temporary)
   temporary.replace(audio_file)
  duration=math.ceil((audio_duration(audio_file)+pad)*25)/25
  timings.append({'story':label,'audio':audio_file.name,'cached':cached,'generation_seconds':round(time.monotonic()-started,2),'video_seconds':duration})
  return audio_file,duration,cached
 def manuscript(key,title,byline,script,audio_file,duration,frame,overlay):
  return {'meta':{'title':title,'pubdate':edition['date']+'T06:00:00Z','byline':byline,'id':key,'uniqueId':f'news-{key}','description':script,'audio':{'src':url(audio_file)}},'segments':[{'id':1,'mood':'neutral','type':'text','style':'bottom','texts':[{'type':'text','text':script,'line_id':1,'who':'default','start':0,'end':duration}],'cameraMovement':'zoom-in','images':[{'type':'image','imageAsset':{'id':str(key),'size':{'width':W,'height':H}},'url':url(frame)}],'overlay':{'url':url(overlay)},'start':0,'end':duration}]}
 if edition.get('intro_title') and edition.get('intro'):
  audio_file,duration,cached=await record(edition['intro'],'intro',pad=.6)
  frame=intro_frame(edition,{k:v for k,v in photos.items() if news[int(k)-1].get('image') is not False},out_dir/'intro-frame.jpg')
  overlay=out_dir/'intro.svg'; overlay.write_text(intro_card(edition))
  manuscripts.append(manuscript(0,edition['title'],edition.get('brand',''),edition['intro'],audio_file,duration,frame,overlay))
  total+=duration; print(f'  intro: {duration}s{" (giọng có sẵn)" if cached else ""}',flush=True)
 for i,item in enumerate(news):
  script=spoken(edition,i)
  audio_file,duration,cached=await record(script,i+1)
  entry=photos.get(str(i+1)) if item.get('image') is not False else None
  frame=background(item,assets/entry['file'] if entry else None,out_dir/f'bg-{i+1}-frame.jpg')
  credit=item.get('image_credit') or item['source'].split(' • ')[0] if entry else None
  overlay=out_dir/f'{i+1}.svg'; overlay.write_text(card(edition,item,i,credit))
  manuscripts.append(manuscript(i+1,' '.join(item['title']),item['source'],script,audio_file,duration,frame,overlay))
  total+=duration; print(f'  {i+1}. {" ".join(item["title"])}: {duration}s{" (giọng có sẵn)" if cached else ""}',flush=True)
 props={'manuscripts':manuscripts,'width':1080,'height':1920,'voice':True,'backgroundMusic':False,'disabledLogo':True,'playerConfig':{'assetBaseUrl':BASE,'logo':'unused.svg','defaultCameraMovements':['zoom-in'],'styles':{'all':{'captions':{'container':'display:none;'},'progress':{'active':'display:none;','inactive':'display:none;'}}}}}
 meta_dir=root if legacy else workdir
 (meta_dir/f'props-{voice.provider}.json').write_text(json.dumps(props,ensure_ascii=False,indent=2))
 (meta_dir/'sources.md').write_text(f"# {edition['title'].capitalize()} — {display_date(edition['date'])[1]}\n\n"+'\n\n'.join(f"## {i+1}. {' '.join(n['title'])}\n\n{n['script']}\n\nNguồn: [{n['source']}]({n['url']})" for i,n in enumerate(news)))
 (meta_dir/f'tts-{voice.provider}.json').write_text(json.dumps({'provider':voice.provider,'voice':name,'model':model,'stories':timings,'total_seconds':total},ensure_ascii=False,indent=2))
 print(f'  TOTAL {total:.2f}s -> {meta_dir/f"props-{voice.provider}.json"}',flush=True)
async def main():
 parser=argparse.ArgumentParser(description='Prepare news video audio and Videofy props')
 parser.add_argument('--provider', choices=['vieneu','edge'], default='vieneu')
 parser.add_argument('--voice', help='VieNeu preset name or Edge voice ID')
 parser.add_argument('--threads', type=int, default=4, help='VieNeu ONNX CPU threads')
 parser.add_argument('--force', action='store_true', help='Regenerate audio, ignoring the cache')
 parser.add_argument('--workdir', nargs='+', type=Path, help='demo/work/<date>/<programme>/ folders (default: legacy demo/news.json)')
 args=parser.parse_args()
 voice=Voice(args.provider,args.voice or ('Hải Đăng' if args.provider=='vieneu' else 'vi-VN-HoaiMyNeural'),args.threads)
 model='v3turbo-fp32-vieneu-3.8.3' if args.provider=='vieneu' else 'edge-tts'
 # One process for every video of the day: the VieNeu model loads once.
 for wd in args.workdir or [root]:
  print(f'[{wd.name}]',flush=True)
  await prepare(wd.resolve(),args.workdir is None,voice,model,args.force)
if __name__ == '__main__':
 asyncio.run(main())

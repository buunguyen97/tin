import argparse, asyncio, base64, functools, hashlib, json, math, html, time, wave, os
from pathlib import Path
from tts_audio import audio_duration, cache_path
from datetime import date as Date
from edition import load_edition, display_date, WEEKDAYS, VOICE_SAMPLES
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
root=Path(__file__).resolve().parent
assets=root/'assets'; assets.mkdir(exist_ok=True)
BASE='http://127.0.0.1:8765'  # render.mjs serves demo/ here
W,H=1080,1920
INK,YELLOW='#0B0B0B','#FFD21F'
FRAME=(44,270,992,570,40)  # photo window on the story card: x, y, width, height, corner radius
def url(path): return f'{BASE}/{Path(path).resolve().relative_to(root).as_posix()}'
# Be Vietnam Pro (OFL, demo/fonts) is embedded in every card: the player draws cards as <img>,
# which cannot load outside fonts, and system fonts differ between machines.
FAMILY="'Be Vietnam Pro', sans-serif"
FONT_FILES=[('Regular',400,'normal'),('Bold',700,'normal'),('ExtraBold',800,'normal'),('ExtraBoldItalic',800,'italic'),('Black',900,'normal')]
@functools.cache
def font_style():
 faces=''.join(f"@font-face{{font-family:'Be Vietnam Pro';font-weight:{weight};font-style:{style};src:url(data:font/ttf;base64,{base64.b64encode((root/'fonts'/f'BeVietnamPro-{name}.ttf').read_bytes()).decode()}) format('truetype')}}" for name,weight,style in FONT_FILES)
 return f'<style>{faces}</style>'
def text(x,y,value,size=40,color='#F6F8FC',weight=400,anchor='start',extra=''):
 return f'<text x="{x}" y="{y}" fill="{color}" font-family="{FAMILY}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"{extra}>{html.escape(value)}</text>'
def rounded(x,y,w,h,r):
 return f'M{x+r} {y}H{x+w-r}A{r} {r} 0 0 1 {x+w} {y+r}V{y+h-r}A{r} {r} 0 0 1 {x+w-r} {y+h}H{x+r}A{r} {r} 0 0 1 {x} {y+h-r}V{y+r}A{r} {r} 0 0 1 {x+r} {y}Z'
def text_width(value,size,bold=True): return len(value)*size*(.64 if bold else .56)  # Be Vietnam Pro, rough
def wrap(value,limit):
 lines=[]; line=''
 for w in value.split():
  if line and len(line+' '+w)>limit: lines.append(line); line=w
  else: line=(line+' '+w).strip()
 return lines+[line]
def background(item,photo,out):
 """1080x1920 JPEG: the landscape photo exactly behind the card's photo window, black elsewhere.
 Images stay still (cameraMovement "none"), so the photo needs no margin around the window."""
 x,y,w,h,_=FRAME; band=(x,y,x+w,y+h)
 bg=Image.new('RGB',(W,H),INK)
 if photo:
  bg.paste(ImageOps.fit(Image.open(photo).convert('RGB'),(band[2]-band[0],band[3]-band[1]),Image.LANCZOS),band[:2])
 else:
  # No relevant photo: dark panel with a soft yellow glow in the window.
  d=ImageDraw.Draw(bg); d.rectangle(band,fill='#1A1A1A')
  glow=Image.new('L',(W,H),0); ImageDraw.Draw(glow).ellipse((x+150,y+80,x+w-150,y+h-80),fill=120)
  bg=Image.composite(Image.new('RGB',(W,H),YELLOW),bg,glow.filter(ImageFilter.GaussianBlur(110)))
 bg.save(out,quality=90); return out
def card(edition,item,i,credit):
 """Overlay drawn by the player above the (zooming) background: black page with a yellow-framed
 photo window, category tag, headline, figure box, details and source."""
 x,y,w,h,r=FRAME; window=rounded(x,y,w,h,r)
 s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',font_style(),
    '<defs><linearGradient id="box"><stop stop-color="#161616"/><stop offset="1" stop-color="#2E2710"/></linearGradient></defs>',
    f'<path d="M0 0H{W}V{H}H0Z{window}" fill="{INK}" fill-rule="evenodd"/>',f'<path d="{window}" fill="none" stroke="{YELLOW}" stroke-width="7"/>']
 if credit: s.append(text(x+w,y+h+38,f'Ảnh: {credit}',20,'#8A8A8A',anchor='end'))
 # Category tag: slanted yellow block plus a thin stripe after it.
 cat=item['category']; tw=round(text_width(cat,40)+70); tx,ty,th,sk=74,y+h+40,74,22
 s+=[f'<path d="M{tx+sk} {ty}H{tx+sk+tw}L{tx+tw} {ty+th}H{tx}Z" fill="{YELLOW}"/>',
     f'<path d="M{tx+tw+14+sk} {ty}h16L{tx+tw+30} {ty+th}h-16Z" fill="{YELLOW}"/>',
     text(round(tx+sk/2+tw/2),ty+52,cat,40,INK,800,'middle',' font-style="italic"')]
 lines=item['title']; size=min(76,int(920/text_width(max(lines,key=len),1))); cy=ty+th+18
 for line in lines: cy+=round(size*1.15); s.append(text(86,cy,line,size,'#FFFFFF',700))
 # Figure box: big yellow number | unit, wrapped to at most two lines.
 bx,by,bw,bh=76,cy+50,928,200; mid=by+bh//2; num=item['number']
 ns=min(150,int(380/text_width(num,1))); dx=round(bx+60+text_width(num,ns)+45); ux=dx+45
 for us in range(44,28,-2):
  unit=wrap(item['unit'],max(1,int((bx+bw-40-ux)/(us*.62))))
  if len(unit)<=2: break
 s+=[f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="30" fill="url(#box)" stroke="{YELLOW}" stroke-width="5"/>',
     text(bx+60,round(mid+ns*.36),num,ns,YELLOW,800),f'<rect x="{dx}" y="{mid-55}" width="6" height="110" rx="3" fill="{YELLOW}"/>']
 for j,line in enumerate(unit): s.append(text(ux,round(mid+us*.36+(j-(len(unit)-1)/2)*us*1.3),line,us,'#FFFFFF'))
 cy=by+bh+30
 for line in item['detail']: cy+=70; s.append(text(86,cy,line,46,'#FFFFFF',700))
 if item['note']: cy+=12
 for line in wrap(item['note'],60)[:2]: cy+=42; s.append(text(86,cy,line,28,'#9A9A9A'))
 ly=cy+70; source=item['source'].split(' • ')[0]
 s+=[f'<path d="M96 {ly-4}L500 {ly}L96 {ly+4}Z" fill="{YELLOW}"/>',
     f'<text x="96" y="{ly+75}" fill="#FFFFFF" font-family="{FAMILY}" font-size="32">Nguồn: <tspan font-weight="700">{html.escape(source)}</tspan></text>','</svg>']
 return ''.join(s)
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
  bg=Image.new('RGB',(W,H),INK); h=H//max(len(files),1)
  for j,f in enumerate(files): bg.paste(ImageOps.fit(Image.open(f).convert('RGB'),(W,h if j<len(files)-1 else H-h*j),Image.LANCZOS),(0,h*j))
 ImageEnhance.Brightness(bg).enhance(.8).save(out,quality=90); return out
def intro_card(edition):
 """Title card: 'BẢN TIN | THỨ BA', headline, big date, tagline, brand."""
 a=edition['intro_accent']; y,m,d=edition['date'].split('-'); lines=edition['intro_title']
 size=min(92,int(900/(.76*max(len(l) for l in lines))))
 head=f'font-family="{FAMILY}" font-weight="900" text-anchor="middle" filter="url(#sh)"'
 s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',font_style(),'<defs><filter id="sh" x="-10%" y="-20%" width="120%" height="140%"><feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity=".7"/></filter><linearGradient id="v" x2="0" y2="1"><stop stop-color="#060D18" stop-opacity=".55"/><stop offset=".35" stop-color="#060D18" stop-opacity=".1"/><stop offset=".7" stop-color="#060D18" stop-opacity=".25"/><stop offset="1" stop-color="#060D18" stop-opacity=".95"/></linearGradient></defs>',f'<rect width="{W}" height="{H}" fill="url(#v)"/>','<rect x="60" y="770" width="960" height="640" rx="36" fill="#060D18" fill-opacity=".5"/>']
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
   sample=VOICE_SAMPLES/f'{name}.wav'
   if sample.exists():  # cloned voice
    self.tts.save(self.tts.infer(script,ref_audio=str(sample)),str(out)); return
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
  sample=VOICE_SAMPLES/f'{name}.wav'  # a re-recorded sample must not reuse the old voice's audio
  key=f'{name}:{hashlib.sha256(sample.read_bytes()).hexdigest()[:12]}' if voice.provider=='vieneu' and sample.exists() else name
  audio_file=cache_path(assets,provider=voice.provider,voice=key,model=model,text=script)
  started=time.monotonic(); cached=False
  if audio_file.exists() and not force:
   try:
    audio_duration(audio_file); cached=True
    os.utime(audio_file)  # auto_render.py keeps recently used cache files
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
  return {'meta':{'title':title,'pubdate':edition['date']+'T06:00:00Z','byline':byline,'id':key,'uniqueId':f'news-{key}','description':script,'audio':{'src':url(audio_file)}},'segments':[{'id':1,'mood':'neutral','type':'text','style':'bottom','texts':[{'type':'text','text':script,'line_id':1,'who':'default','start':0,'end':duration}],'cameraMovement':'none','images':[{'type':'image','imageAsset':{'id':str(key),'size':{'width':W,'height':H}},'url':url(frame)}],'overlay':{'url':url(overlay)},'start':0,'end':duration}]}
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
 props={'manuscripts':manuscripts,'width':1080,'height':1920,'voice':True,'backgroundMusic':False,'disabledLogo':True,'playerConfig':{'assetBaseUrl':BASE,'logo':'unused.svg','defaultCameraMovements':['none'],'styles':{'all':{'captions':{'container':'display:none;'},'progress':{'active':'display:none;','inactive':'display:none;'}}}}}
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

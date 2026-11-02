import argparse, asyncio, base64, functools, hashlib, json, math, html, random, shutil, subprocess, time, wave, os
from pathlib import Path
from tts_audio import audio_duration, cache_path
from datetime import date as Date
from edition import load_edition, display_date, WEEKDAYS, VOICE_SAMPLES
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
root=Path(__file__).resolve().parent
assets=root/'assets'; assets.mkdir(exist_ok=True)
BASE='http://127.0.0.1:8765'  # render.mjs serves demo/ here
W,H=1080,1920
INK,YELLOW='#0B0B0B','#FFD21F'
# TikTok covers the top tabs, the caption and music below, and the button rail on the right;
# text stays inside this box (commonly used safe-zone margins for 1080x1920).
SAFE={'top':160,'bottom':1440,'left':80,'right':920}
# Story card layouts, used in turn so a video doesn't repeat one look (the programme config can
# pick its own list with "layouts"). Windowed layouts show the photo through a hole in the overlay:
# x, y, width, height, corner radius. Full-bleed ones put it across the top, fading to black
# (height, fade start, fade length).
LAYOUTS=['goc','anh-tran-tren','anh-tran-vien','the-kem','phu-de']
WINDOWS={'goc':(44,170,992,530,36),'the-kem':(67,180,946,440,26)}
BLEEDS={'anh-tran-tren':dict(height=820,fade=(440,360),left=True),'anh-tran-vien':dict(height=1160,fade=(460,580),left=False)}
# 'phu-de': the photo in a band between black bars, the headline as an outlined subtitle on it.
BAND=(300,1620)
PHOTO_ONLY=set(BLEEDS)|{'phu-de'}  # layouts that fall back to 'goc' for a story without a photo
CREAM='#FFF8E6'
FIGURE_WORDS={'tỷ','triệu','nghìn','ngàn','USD','đồng','%','người','tấn'}  # highlighted with the number in a headline
def url(path): return f'{BASE}/{Path(path).resolve().relative_to(root).as_posix()}'
# Be Vietnam Pro (OFL, demo/fonts) is embedded in every card: the player draws cards as <img>,
# which cannot load outside fonts, and system fonts differ between machines.
FAMILY="'Be Vietnam Pro', sans-serif"
FONT_FILES=[('Regular',400,'normal'),('Bold',700,'normal'),('ExtraBold',800,'normal'),('ExtraBoldItalic',800,'italic'),('Black',900,'normal')]
@functools.cache
def font_style():
 faces=''.join(f"@font-face{{font-family:'Be Vietnam Pro';font-weight:{weight};font-style:{style};src:url(data:font/ttf;base64,{base64.b64encode((root/'fonts'/f'BeVietnamPro-{name}.ttf').read_bytes()).decode()}) format('truetype')}}" for name,weight,style in FONT_FILES)
 return f'<style>{faces}</style>'
# Barlow Condensed (OFL, demo/fonts): the narrow subtitle face of 'phu-de', embedded only there.
CONDENSED="'Barlow Condensed', sans-serif"
@functools.cache
def condensed_style():
 faces=''.join(f"@font-face{{font-family:'Barlow Condensed';font-weight:{weight};src:url(data:font/ttf;base64,{base64.b64encode((root/'fonts'/f'BarlowCondensed-{name}.ttf').read_bytes()).decode()}) format('truetype')}}" for name,weight in [('SemiBold',600),('Bold',700)])
 return f'<style>{faces}</style>'
@functools.cache
def _condensed(size,weight):
 return ImageFont.truetype(str(root/'fonts'/f'BarlowCondensed-{ {600:"SemiBold",700:"Bold"}[weight]}.ttf'),size)
def text(x,y,value,size=40,color='#F6F8FC',weight=400,anchor='start',extra=''):
 return f'<text x="{x}" y="{y}" fill="{color}" font-family="{FAMILY}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"{extra}>{html.escape(value)}</text>'
def rounded(x,y,w,h,r):
 return f'M{x+r} {y}H{x+w-r}A{r} {r} 0 0 1 {x+w} {y+r}V{y+h-r}A{r} {r} 0 0 1 {x+w-r} {y+h}H{x+r}A{r} {r} 0 0 1 {x} {y+h-r}V{y+r}A{r} {r} 0 0 1 {x+r} {y}Z'
@functools.cache
def _font(size,weight,italic):
 name={400:'Regular',700:'Bold',800:'ExtraBold',900:'Black'}[weight]+('Italic' if italic else '')
 return ImageFont.truetype(str(root/'fonts'/f'BeVietnamPro-{name}.ttf'),size)
def text_width(value,size,weight=700,italic=False):
 """Advance width in px of `value` set in the embedded Be Vietnam Pro, as the browser lays it out."""
 return _font(size,weight,italic).getlength(value)
def fit(value,size,weight,width):
 """Largest font size up to `size` at which `value` fits `width` px."""
 return min(size,int(size*width/max(text_width(value,size,weight),1)))
def wrap(value,limit,size=None,weight=400):
 """Split into lines of at most `limit` characters, or `limit` px when a font `size` is given."""
 too_long=(lambda t:text_width(t,size,weight)>limit) if size else (lambda t:len(t)>limit)
 lines=[]; line=''
 for w in value.split():
  if line and too_long(line+' '+w): lines.append(line); line=w
  else: line=(line+' '+w).strip()
 return lines+[line]
def background(item,photo,out,layout='goc'):
 """1080x1920 JPEG behind the card overlay. Windowed layouts: the landscape photo exactly behind
 the window, black elsewhere. Full-bleed layouts: the photo across the top, faded into black.
 Images stay still (cameraMovement "none"), so the photo needs no margin."""
 bg=Image.new('RGB',(W,H),INK)
 if layout=='phu-de':
  top,bottom=BAND
  bg.paste(ImageOps.fit(Image.open(photo).convert('RGB'),(W,bottom-top),Image.LANCZOS),(0,top))
  bg.save(out,quality=90); return out
 if layout in BLEEDS:
  b=BLEEDS[layout]; h=b['height']
  bg.paste(ImageOps.fit(Image.open(photo).convert('RGB'),(W,h),Image.LANCZOS,centering=(0 if b['left'] else .5,.5)),(0,0))
  start,length=b['fade']
  fade=Image.new('L',(1,h)); fade.putdata([round(255*min(1,max(0,(j-start)/length))**1.3) for j in range(h)])
  bg.paste(Image.new('RGB',(W,h),INK),(0,0),fade.resize((W,h)))
  bg.save(out,quality=90); return out
 x,y,w,h,_=WINDOWS[layout]; band=(x,y,x+w,y+h)
 if photo:
  bg.paste(ImageOps.fit(Image.open(photo).convert('RGB'),(band[2]-band[0],band[3]-band[1]),Image.LANCZOS),band[:2])
 else:
  # No relevant photo: dark panel with a soft yellow glow in the window.
  d=ImageDraw.Draw(bg); d.rectangle(band,fill='#1A1A1A')
  glow=Image.new('L',(W,H),0); ImageDraw.Draw(glow).ellipse((x+150,y+80,x+w-150,y+h-80),fill=120)
  bg=Image.composite(Image.new('RGB',(W,H),YELLOW),bg,glow.filter(ImageFilter.GaussianBlur(110)))
 bg.save(out,quality=90); return out
def ascent(size,weight):
 """Baseline offset below the top of a line, so blocks can be placed by their top edge."""
 return _font(size,weight,False).getmetrics()[0]
def figure(num,unit,x,mid,ns,color,unit_color,us,unit_weight,unit_right,gap=36,bar=YELLOW):
 """Big number, divider and unit (at most two lines), centred vertically on `mid`."""
 dx=round(x+text_width(num,ns,800)+gap); ux=dx+38
 for u in range(us,us-14,-2):
  unit_lines=wrap(unit,unit_right-ux,u,unit_weight)
  if len(unit_lines)<=2: break
 s=[text(x,round(mid+ns*.36),num,ns,color,800)]
 if bar: s.append(f'<rect x="{dx}" y="{mid-46}" width="5" height="92" rx="3" fill="{bar}"/>')
 for j,line in enumerate(unit_lines): s.append(text(ux,round(mid+u*.36+(j-(len(unit_lines)-1)/2)*u*1.25),line,u,unit_color,unit_weight))
 return s
def source_line(item,x,y,color='#FFFFFF'):
 """Tapered yellow rule and 'Nguồn: <outlet>'; returns the parts and the last baseline."""
 name=item['source'].split(' • ')[0]
 return [f'<path d="M{x} {y-3}L{x+320} {y}L{x} {y+3}Z" fill="{YELLOW}"/>',
         f'<text x="{x}" y="{y+56}" fill="{color}" font-family="{FAMILY}" font-size="30">Nguồn: <tspan font-weight="700">{html.escape(name)}</tspan></text>'],y+56
def headline(item,size,width,hot=False):
 """The two sheet headline lines joined and re-wrapped to `width` px (at most three lines, shrinking
 the font if needed). With `hot`, the figure in it (e.g. "6 tỷ USD") is set in yellow."""
 while len(lines:=wrap(' '.join(item['title']),width,size,800))>3: size-=2
 words=' '.join(lines).split(); marked=set()
 if hot:
  first=next((k for k,w in enumerate(words) if any(c.isdigit() for c in w)),None)
  if first is not None:
   marked.add(first); k=first+1
   while k<len(words) and words[k].strip(',.') in FIGURE_WORDS: marked.add(k); k+=1
 out=[]; k=0
 for line in lines:
  parts=[]
  for w in line.split():
   parts.append(f'<tspan fill="{YELLOW}">{html.escape(w)}</tspan>' if k in marked else html.escape(w)); k+=1
  out.append(' '.join(parts))
 return out,size
def _card_goc(item,size):
 """Black page, yellow-framed photo window, headline, figure box, details, source. Spare height
 above SAFE['bottom'] is shared between the blocks, so short stories don't leave a gap."""
 x,y,w,h,r=WINDOWS['goc']; left,right=SAFE['left'],SAFE['right']; width=right-left
 lines=item['title']; size=min(fit(l,size,800,width) for l in lines)
 def body(gap):
  s=[]; cy=y+h+10+gap
  for line in lines: cy+=round(size*1.18); s.append(text(left,cy,line,size,'#FFFFFF',800))
  bx,by,bw,bh=left-14,cy+36+gap,width+14,160
  s.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="26" fill="url(#box)" stroke="{YELLOW}" stroke-width="5"/>')
  s+=figure(item['number'],item['unit'],bx+44,by+bh//2,fit(item['number'],130,800,330),YELLOW,'#FFFFFF',40,400,bx+bw-30)
  cy=by+bh+6+gap
  for line in item['detail']: cy+=56; s.append(text(left,cy,line,fit(line,38,700,width),'#FFFFFF',700))
  src,end=source_line(item,left,cy+42+gap)
  return s+src,end
 s,end=body(0)
 if end<SAFE['bottom']-16: s,end=body(min(40,(SAFE['bottom']-16-end)//4))
 return s,end
def _card_photo_top(item,size):
 """Photo across the top fading to black, three-line headline over the fade, outlined figure box."""
 left,right=SAFE['left'],SAFE['right']; width=right-left
 lines,size=headline(item,size,800); y=600; s=[]
 for line in lines: s.append(f'<text x="{left}" y="{y+ascent(size,800)}" fill="#FFFFFF" font-family="{FAMILY}" font-size="{size}" font-weight="800">{line}</text>'); y+=round(size*1.17)
 bx,by,bw,bh=left-14,y+26,width+14,168
 s.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="24" fill="#111111" stroke="{YELLOW}" stroke-width="5"/>')
 s+=figure(item['number'],item['unit'],bx+46,by+bh//2,fit(item['number'],150,800,260),YELLOW,'#FFFFFF',38,400,bx+bw-24,gap=40)
 y=by+bh+30
 for line in item['detail']: ds=fit(line,38,700,width); s.append(text(left,y+ascent(ds,700),line,ds,'#FFFFFF',700)); y+=56
 src,end=source_line(item,left,y+50)
 return s+src,end
def _card_photo_bleed(item,size):
 """Edge-to-edge photo fading to black, headline with a yellow bar on the photo, yellow figure pill,
 bulleted details."""
 left,right=SAFE['left'],SAFE['right']; width=right-left
 lines=item['title']; size=min(fit(l,size,800,width-30) for l in lines); lh=round(size*1.2)
 y=930-len(lines)*lh-10
 s=[f'<rect x="{left}" y="{y+8}" width="10" height="{len(lines)*lh-14}" fill="{YELLOW}"/>']
 for line in lines: s.append(text(left+30,y+ascent(size,800),line,size,'#FFFFFF',800)); y+=lh
 y+=40; ns=fit(item['number'],110,800,260); nw=text_width(item['number'],ns,800)
 s.append(f'<rect x="{left}" y="{y}" width="{round(nw+60)}" height="150" rx="24" fill="{YELLOW}"/>')
 s+=figure(item['number'],item['unit'],left+30,y+75,ns,INK,'#FFFFFF',38,700,right,gap=28,bar=None)
 y+=200
 for line in item['detail']:
  ds=fit(line,38,700,width-40)
  s+=[f'<rect x="{left}" y="{round(y+ds*.45)}" width="16" height="16" rx="4" fill="{YELLOW}"/>',text(left+36,y+ascent(ds,700),line,ds,'#FFFFFF',700)]; y+=60
 src,end=source_line(item,left,y+40)
 return s+src,end
def _card_cream(item,size):
 """Yellow-framed photo, headline with the figure in yellow, cream card holding figure and details."""
 x,y,w,h,_=WINDOWS['the-kem']; left,right=SAFE['left'],SAFE['right']; width=right-left
 lines,size=headline(item,size,width,hot=True); y+=h+30; s=[]
 for line in lines: s.append(f'<text x="{left}" y="{y+ascent(size,800)}" fill="#FFFFFF" font-family="{FAMILY}" font-size="{size}" font-weight="800">{line}</text>'); y+=round(size*1.17)
 cx,cw,top=left-14,width+14,y+20; mid=top+95; dy=mid+98
 details=[(line,fit(line,38,700,cw-70)) for line in item['detail']]
 bottom=dy+22+len(details)*54+20
 s.append(f'<rect x="{cx}" y="{top}" width="{cw}" height="{bottom-top}" rx="26" fill="{CREAM}" stroke="{YELLOW}" stroke-width="5"/>')
 s+=figure(item['number'],item['unit'],cx+40,mid,fit(item['number'],150,800,280),INK,INK,34,700,cx+cw-24,gap=44)
 s.append(f'<rect x="{cx+34}" y="{dy}" width="{cw-68}" height="3" fill="{YELLOW}"/>')
 yy=dy+22
 for line,ds in details: s.append(text(cx+34,yy+ascent(ds,700),line,ds,INK,700)); yy+=54
 src,end=source_line(item,left,bottom+40)
 return s+src,end
def _wrap_condensed(value,width,size):
 """`value` in lines of at most `width` px of Barlow Condensed Bold at `size`."""
 lines=[]; line=''
 for w in value.split():
  if line and _condensed(size,700).getlength(line+' '+w)>width: lines.append(line); line=w
  else: line=(line+' '+w).strip()
 return lines+[line]
def _card_subtitle(item,size):
 """Photo between black bars, the headline in white condensed type with a black outline like a
 burned-in subtitle, and a small source line under it, right-aligned."""
 left,right=56,SAFE['right']; width=right-left  # condensed lines run wider than the other cards', as in the subtitle look
 while len(lines:=_wrap_condensed(' '.join(item['title']),width,size))>3: size-=2
 outline=f'font-family="{CONDENSED}" font-weight="700" fill="#FFFFFF" stroke="#000000" stroke-linejoin="round" paint-order="stroke"'
 lh=round(size*1.22); y=1220-(len(lines)-1)*lh; s=[]
 for line in lines: s.append(f'<text x="{left}" y="{y}" font-size="{size}" stroke-width="{round(size*.18)}" {outline}>{html.escape(line)}</text>'); y+=lh
 name=item['source'].split(' • ')[0]; end=y-lh+135
 s.append(f'<text x="{right-100}" y="{end}" font-size="36" stroke-width="7" text-anchor="end" {outline}>Nguồn: {html.escape(name)}</text>')
 return s,end
CARDS={'goc':(_card_goc,68),'anh-tran-tren':(_card_photo_top,80),'anh-tran-vien':(_card_photo_bleed,72),'the-kem':(_card_cream,80),'phu-de':(_card_subtitle,56)}
def card(edition,item,i,layout='goc'):
 """Overlay drawn by the player above the still background, in one of LAYOUTS. Every text stays in
 TikTok's safe area (SAFE): clear of the tabs on top, the caption below and the button rail; a
 story too long for it gets a smaller headline."""
 build,size=CARDS[layout]
 for size in range(size,size-20,-4):
  s,end=build(item,size)
  if end<=SAFE['bottom']-16: break
 else: print(f'  CẢNH BÁO: thẻ "{" ".join(item["title"])}" chạm vùng bị TikTok che phía dưới',flush=True)
 head=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',font_style(),
       '<defs><linearGradient id="box"><stop stop-color="#161616"/><stop offset="1" stop-color="#2E2710"/></linearGradient></defs>']
 if layout=='phu-de': head.append(condensed_style())
 if layout in WINDOWS:
  x,y,w,h,r=WINDOWS[layout]; window=rounded(x,y,w,h,r)
  head+=[f'<path d="M0 0H{W}V{H}H0Z{window}" fill="{INK}" fill-rule="evenodd"/>',f'<path d="{window}" fill="none" stroke="{YELLOW}" stroke-width="{6 if layout=="goc" else 5}"/>']
 return ''.join(head+s+['</svg>'])
def shuffled_layouts(edition):
 """The layouts in a random order, one after another so a video doesn't repeat one look. Seeded by
 the video's date and title: re-rendering the same video keeps its layouts."""
 layouts=list(edition.get('layouts') or LAYOUTS)
 random.Random(f"{edition['date']}|{edition.get('title','')}").shuffle(layouts)
 return layouts
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
 """Title card in the story cards' black and yellow: slanted 'BẢN TIN | THỨ BA' tag, headline,
 big yellow date, tagline, brand. The panel keeps fixed inner padding, and the whole block is
 centred vertically in TikTok's safe area."""
 y,m,d=edition['date'].split('-'); lines=edition['intro_title']
 left,right=SAFE['left'],SAFE['right']; px,py=70,56  # panel inner padding
 bx,bw=left-20,right-left+40; cx,inner=bx+bw//2,bw-2*px
 size=min(fit(l,90,900,inner) for l in lines); ds=fit(f'{d}/{m}',180,900,inner)
 head=f'font-family="{FAMILY}" font-weight="900" text-anchor="middle" filter="url(#sh)"'
 chip=f'BẢN TIN  |  {WEEKDAYS[Date(int(y),int(m),int(d)).weekday()]}'; tw,th,sk=round(text_width(chip,32,800,True)+56),62,18
 # Lay the block out from y=0, then shift it so it sits in the middle of the safe area.
 panel=th+28; body=[]; cy=panel+py
 for j,line in enumerate(lines): cy+=round(size*(.74 if j==0 else 1.14)); body.append(f'<text x="{cx}" y="{cy}" fill="#FFFFFF" font-size="{size}" {head}>{html.escape(line)}</text>')
 cy+=round(ds*1.02); body.append(f'<text x="{cx}" y="{cy}" fill="{YELLOW}" font-size="{ds}" {head}>{d}/{m}</text>')
 tag=edition.get('intro_tagline')
 if tag: ts=fit(tag,48,900,inner); cy+=round(ts*1.7); body.append(f'<text x="{cx}" y="{cy}" fill="#FFFFFF" font-size="{ts}" {head}>{html.escape(tag)}</text>')
 bottom=cy+py; ly=bottom+70; end=ly+120
 dy=max(SAFE['top']+20,(SAFE['top']+SAFE['bottom']-end)//2)
 s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',font_style(),
    f'<defs><filter id="sh" x="-10%" y="-20%" width="120%" height="140%"><feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity=".7"/></filter><linearGradient id="v" x2="0" y2="1"><stop stop-color="{INK}" stop-opacity=".7"/><stop offset=".35" stop-color="{INK}" stop-opacity=".35"/><stop offset=".7" stop-color="{INK}" stop-opacity=".55"/><stop offset="1" stop-color="{INK}" stop-opacity=".97"/></linearGradient></defs>',
    f'<rect width="{W}" height="{H}" fill="url(#v)"/>',f'<g transform="translate(0 {dy})">',
    f'<rect x="{bx}" y="{panel}" width="{bw}" height="{bottom-panel}" rx="36" fill="{INK}" fill-opacity=".82" stroke="{YELLOW}" stroke-width="5"/>',
    f'<path d="M{left+sk} 0H{left+sk+tw}L{left+tw} {th}H{left}Z" fill="{YELLOW}"/>',  # same slanted tag as the story cards
    f'<path d="M{left+tw+12+sk} 0h14L{left+tw+26} {th}h-14Z" fill="{YELLOW}"/>',
    text(round(left+sk/2+tw/2),43,chip,32,INK,800,'middle',' font-style="italic"'),*body,
    f'<path d="M{left} {ly-3}L{left+320} {ly}L{left} {ly+3}Z" fill="{YELLOW}"/>',
    text(left,ly+62,edition.get('brand',''),32,'#FFFFFF',700),
    text(left,ly+112,f'TỔNG HỢP NGÀY {d}.{m}.{y}',28,'#B0B0B0',700),'</g></svg>']
 return ''.join(s)
def speed_up(path,speed):
 """Read faster without raising the pitch (ffmpeg atempo); run_daily.py passes its ffmpeg in FFMPEG_BINARY."""
 ffmpeg=os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg')
 if not ffmpeg: raise RuntimeError('Cần ffmpeg để đổi tốc độ đọc: đặt FFMPEG_BINARY hoặc chạy qua run_daily.py')
 fast=path.with_name(path.stem+'.fast'+path.suffix)
 subprocess.run([ffmpeg,'-y','-loglevel','error','-i',str(path),'-filter:a',f'atempo={speed}',str(fast)],check=True)
 fast.replace(path)
class Voice:
 def __init__(self,provider,voice,threads,speed=1.0): self.provider,self.voice,self.threads,self.speed,self.tts=provider,voice,threads,speed,None
 async def say(self,script,out,name=None):
  await self._say(script,out,name)
  if self.provider=='vieneu' and self.speed!=1: speed_up(Path(out),self.speed)
 async def _say(self,script,out,name=None):
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
     await edge_tts.Communicate(script,name,rate=f'{round((self.speed-1)*100):+d}%').save(str(out))
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
 layouts=shuffled_layouts(edition)
 for i,item in enumerate(news):
  script=spoken(edition,i)
  audio_file,duration,cached=await record(script,i+1)
  entry=photos.get(str(i+1)) if item.get('image') is not False else None
  layout=layouts[i%len(layouts)]
  if layout in PHOTO_ONLY and not entry: layout='goc'  # a photo layout without a photo would leave the frame empty
  frame=background(item,assets/entry['file'] if entry else None,out_dir/f'bg-{i+1}-frame.jpg',layout)
  overlay=out_dir/f'{i+1}.svg'; overlay.write_text(card(edition,item,i,layout))
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
 parser.add_argument('--speed', type=float, default=1.0, help='Reading speed, e.g. 1.25')
 parser.add_argument('--force', action='store_true', help='Regenerate audio, ignoring the cache')
 parser.add_argument('--workdir', nargs='+', type=Path, help='demo/work/<date>/<programme>/ folders (default: legacy demo/news.json)')
 args=parser.parse_args()
 voice=Voice(args.provider,args.voice or ('Hải Đăng' if args.provider=='vieneu' else 'vi-VN-HoaiMyNeural'),args.threads,args.speed)
 model='v3turbo-fp32-vieneu-3.8.3' if args.provider=='vieneu' else 'edge-tts'
 if args.speed!=1: model+=f'-x{args.speed:g}'  # audio at another speed is a different cache entry
 # One process for every video of the day: the VieNeu model loads once.
 for wd in args.workdir or [root]:
  print(f'[{wd.name}]',flush=True)
  await prepare(wd.resolve(),args.workdir is None,voice,model,args.force)
if __name__ == '__main__':
 asyncio.run(main())

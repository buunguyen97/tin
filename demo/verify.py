import json,subprocess,sys
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw
import imageio_ffmpeg
root=Path.cwd(); output=root/'output'; ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
video=Path(sys.argv[1]) if len(sys.argv)>1 else output/'diem-tin-2026-10-27-vieneu.mp4'
props_path=Path(sys.argv[2]) if len(sys.argv)>2 else root/'demo/props-vieneu.json'
prefix=video.stem; output=video.parent  # QA files sit next to the video
p=subprocess.run([ffmpeg,'-hide_banner','-i',str(video),'-af','volumedetect','-f','null','-'],capture_output=True,text=True)
(output/f'{prefix}-verification.txt').write_text(p.stderr)
if p.returncode: raise RuntimeError(p.stderr)
props=json.loads(props_path.read_text()); t=0; images=[]
for i,m in enumerate(props['manuscripts']):
 duration=sum(s['end']-s['start'] for s in m['segments'])
 target=output/f'{prefix}-frame-{i+1}.jpg'
 subprocess.run([ffmpeg,'-y','-loglevel','error','-ss',str(t+duration/2),'-i',str(video),'-frames:v','1',str(target)],check=True)
 im=Image.open(target).convert('RGB'); im.thumbnail((270,480)); images.append(im);t+=duration
sheet=Image.new('RGB',(270*len(images),480),'#0B1422')
for i,im in enumerate(images):sheet.paste(im,(270*i,0))
sheet.save(output/f'{prefix}-contact-sheet.jpg',quality=92)
print('Verified decoded video, duration expected:',round(t,2),'seconds')
print('\n'.join(l for l in p.stderr.splitlines() if any(x in l for x in ['Duration:', 'Video:', 'Audio:', 'mean_volume:', 'max_volume:'])))

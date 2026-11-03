# Video điểm tin tiếng Việt với VieNeu

Tích hợp VieNeu vào pipeline demo của Videofy: kịch bản → giọng đọc → đo thời lượng → `ArticlesSeries` → MP4 dọc 1080×1920.

- Mặc định: **VieNeu v3 Turbo, giọng nhân bản Ngọc Huyền** (mẫu ở `demo/voices/`), đọc nhanh 1,25 lần.
- Chạy local bằng ONNX CPU, fp32, 4 threads; không cần API key.
- VieNeu 3.8.3, repo sibling `../VieNeu-TTS`, commit `04044e1b8f822f946bafb2c88b716459f4164152`.
- Videofy Minimal commit `5652e70981b1f45df43d7e6c6f38793ed3a947ad`.
- Tích hợp trong `demo/prepare.py`; chưa thêm provider vào giao diện CMS/FastAPI của upstream.

## Chạy trên Windows

Các lệnh dưới đây chạy trong PowerShell, từ thư mục gốc của dự án.

### 1. Cài công cụ (một lần)

- [Git](https://git-scm.com/download/win), [Node.js](https://nodejs.org) 20 trở lên, và [uv](https://docs.astral.sh/uv/getting-started/installation/) (uv tự tải Python 3.12). Không cần cài ffmpeg riêng: dự án dùng bản có sẵn trong gói `imageio-ffmpeg`.
- Đặt dự án ở đường dẫn ngắn, ví dụ `C:\Users\<tên>\Documents\tin`. Windows giới hạn đường dẫn 260 ký tự, nên thư mục quá sâu sẽ làm `npm` và `uv` báo lỗi.

### 2. Tải dự án và cài thư viện (một lần)

```powershell
git -c core.longpaths=true clone https://github.com/buunguyen97/tin.git
cd tin
git config core.longpaths true
npm ci
uv venv .venv-demo --python 3.12
uv pip install --python .venv-demo -r demo/requirements-demo.txt
git clone https://github.com/pnnbao97/VieNeu-TTS.git VieNeu-TTS
git -C VieNeu-TTS checkout 04044e1b8f822f946bafb2c88b716459f4164152
uv sync --project VieNeu-TTS --python 3.12
```

`VieNeu-TTS` nằm trong thư mục dự án và đã được `.gitignore` bỏ qua. Lần đọc giọng đầu tiên tải model khoảng 2 GB về `demo/.model-cache/hub`.

### 3. Cấu hình

1. Chép `demo/config.example.json` thành `demo/config.json`, rồi điền `sheet_id` và `sheet_gid` theo mục [Kết nối Google Sheet](#kết-nối-google-sheet).
2. Chọn giọng bằng `voice` và tốc độ bằng `speed`. Các tuỳ chọn khác xem ở [Tùy chỉnh](#tùy-chỉnh-trong-democonfigjson).
3. Giọng nhân bản (không bắt buộc): đặt một đoạn ghi âm sạch dài 3–8 giây vào `demo/voices/<Tên giọng>.wav`, rồi đặt `"voice": "<Tên giọng>"`. Repo có sẵn hai giọng nhân bản: `Ngọc Huyền` (mặc định) và `Châu Thảo`.

### 4. Làm video

Dùng giao diện Studio, rồi mở http://127.0.0.1:8790 và bấm “Tạo tất cả video”:

```powershell
.venv-demo\Scripts\python.exe demo\studio.py
```

Hoặc chạy bằng dòng lệnh:

```powershell
.venv-demo\Scripts\python.exe demo\run_daily.py                                      # hôm nay, mọi video
.venv-demo\Scripts\python.exe demo\run_daily.py --date 2026-11-02 --programmes tong-hop
.venv-demo\Scripts\python.exe demo\run_daily.py --steps prepare,render,verify --programmes tong-hop
```

Video nằm ở `output\daily\<ngày>\`. Bước `prepare` chỉ đọc lại những câu mới; giọng đã đọc trước đó lấy từ cache nên chạy lại rất nhanh.

Chạy test:

```powershell
.venv-demo\Scripts\python.exe -X utf8 -m unittest discover -s demo -p "test_*.py"
```

### Lỗi thường gặp

- **`Filename too long`**: thiếu `core.longpaths`, hoặc thư mục dự án nằm quá sâu. Chuyển dự án ra đường dẫn ngắn hơn.
- **Lỗi mã hoá ký tự (`UnicodeDecodeError`, `cp1252`)**: các script tự chuyển sang UTF-8. Nếu chạy riêng `verify.py` mà vẫn gặp lỗi, đặt `$env:PYTHONUTF8 = '1'` trước khi chạy.
- **`Không thấy Python của VieNeu`**: chưa chạy `uv sync --project VieNeu-TTS`, hoặc VieNeu đang ở chỗ khác. Khi đó đặt `$env:VIENEU_PYTHON` thành đường dẫn tới `python.exe` của VieNeu.
- **Render báo cổng 8765 đang bận**: một lần render trước chưa thoát. Đóng tiến trình `node` đó rồi chạy lại.
- **“Đang có một lượt tạo video khác chạy”**: Studio hoặc một lệnh khác đang render. Đợi lượt đó xong.
- Tự render theo lịch: cài bằng `demo\windows\install.ps1` (Task Scheduler), xem mục "Tự làm video" bên dưới.

## Quy trình mỗi ngày

1. **7:00 – Claude tổng hợp tin** vào tab `Tin` của Google Sheet, trạng thái `Nháp`. Mỗi tin ghi kèm Ngành, đánh dấu Hot nếu là tin nổi bật trong ngày. Prompt và đặc tả cột nằm ở [claude-prompt.md](claude-prompt.md).
2. **Biên tập viên duyệt** trên Sheet: sửa nội dung, đổi trạng thái thành `Đã duyệt` hoặc `Bỏ`, chỉnh tick Hot nếu cần.
3. **Mở Studio, bấm “Tạo tất cả video”**:

```sh
.venv-demo/bin/python demo/studio.py
```

Studio chạy tại http://127.0.0.1:8790, chỉ trên máy này. Mỗi lần bấm “Tạo tất cả video”, Studio đọc lại Sheet, chia tin theo video, tải ảnh, đọc giọng, render rồi kiểm tra. Mỗi video có nút **Tạo lại** riêng: dùng khi muốn đổi giọng đọc, không đọc lại Sheet.

Mỗi ngày repo tạo:
- **Một video cho mỗi ngành** có ít nhất `min` tin đã duyệt (mặc định 2, tối đa 5 tin). Ngành không đủ tin thì bỏ qua và ghi lý do.
- **Một video “Tin nổi bật trong ngày”** gồm các tin đánh dấu Hot, sắp theo `Thứ tự hot` (3–6 tin).

Mỗi video mở đầu bằng **màn hình intro có giọng đọc**, ví dụ “Sự kiện nổi bật hôm nay, ngày hai mươi bảy tháng mười.” Intro gồm nhãn “BẢN TIN | THỨ BA”, tiêu đề lớn, ngày, dòng phụ và dòng thương hiệu. Nền intro ghép 3 ảnh của chính các tin trong video, hoặc dùng ảnh riêng nếu đặt `intro_image`.

Kết quả lưu ở `output/daily/<ngày>/<chương-trình>.mp4`, kèm ảnh tổng hợp các khung và file nguồn tin `-nguon.md`. Dữ liệu trung gian nằm trong `demo/work/<ngày>/`. Ảnh và giọng đọc dùng cache chung trong `demo/assets/`, nên tin xuất hiện ở nhiều video chỉ phải tải ảnh và đọc giọng một lần.

Chạy bằng dòng lệnh, không cần giao diện:

```sh
.venv-demo/bin/python demo/run_daily.py                          # hôm nay, mọi video
.venv-demo/bin/python demo/run_daily.py --date 2026-10-27 --csv demo/sheet-template.csv
.venv-demo/bin/python demo/run_daily.py --steps sheet,images     # chỉ chia tin và tải ảnh
.venv-demo/bin/python demo/run_daily.py --steps images,prepare,render,verify --programmes tong-hop --voice 'Mai Anh'
```

### Tự làm video (không cần bấm)

Routine Claude ghi tin thẳng ở trạng thái "Đã duyệt". Trên máy Mac, cài tác vụ nền một lần:

```sh
sh demo/launchd/install.sh            # gỡ: sh demo/launchd/install.sh --uninstall
```

Trên Windows, mở PowerShell trong thư mục dự án và chạy (gỡ: thêm `-Uninstall`):

```powershell
powershell -ExecutionPolicy Bypass -File demo\windows\install.ps1
```

Lệnh này tạo tác vụ "TinTuc AutoRender" trong Task Scheduler. Tác vụ chạy ẩn, không mở cửa sổ nào, và báo kết quả bằng thông báo Windows. Nếu máy đang ngủ, tác vụ đánh thức máy, và chạy bù khi máy vừa bật lại.

Tác vụ chạy `demo/auto_render.py` mỗi 10 phút trong khung `auto_render.window` (mặc định 7:00–12:00). Script chỉ render khi tin đã duyệt của hôm nay thay đổi so với lần trước, rồi hiện thông báo macOS (kèm số tin bị loại). Nếu render lỗi, script thử lại tối đa `auto_render.max_attempts` lần (mặc định 2) rồi chờ tới khi Sheet thay đổi. Nếu tới giờ cuối của khung mà Sheet vẫn chưa có tin hợp lệ, máy báo một lần. Sau mỗi lần render xong, máy xoá video và thư mục làm việc cũ hơn `retention_days` ngày (mặc định 14), cùng ảnh và giọng đọc cache lâu không dùng. Studio và tác vụ tự động dùng chung một khoá, nên không bao giờ chạy chồng nhau. Nhật ký ở `output/auto-render.log`. Máy phải bật và không ngủ trong khung giờ đó. Chạy tay ngoài khung giờ: `.venv-demo/bin/python demo/auto_render.py --force`.

### Tùy chỉnh trong `demo/config.json`

- `programmes`: danh sách video. Mỗi mục có các trường:
  - `id`: tên file video.
  - `title`: tiêu đề trên thẻ tin.
  - `filter`: `{"nganh": "Bất động sản"}` cho video ngành, hoặc `{"hot": true}` cho video nổi bật.
  - `min` / `max`: số tin tối thiểu / tối đa.
  - `accent`: màu nhấn của thẻ tin.
  - Phần intro: `intro` (câu đọc, chương trình tự thêm “hôm nay, ngày…”), `intro_title` (các dòng tiêu đề lớn), `intro_tagline`, `intro_accent`.
  - `voice` (không bắt buộc): giọng riêng cho video đó, ví dụ `"voice": "Mai Anh"` cho video nổi bật.
- `voice`: giọng mặc định. Có 25 giọng VieNeu, chọn được trong Studio.
- `speed`: tốc độ đọc (mặc định 1.25; 1 là tốc độ gốc). Đổi tốc độ thì giọng được đọc lại.
- `layouts`: bố cục thẻ tin (mặc định `["goc", "anh-tran-tren", "anh-tran-vien", "the-kem", "phu-de"]`). Mỗi video xáo trộn danh sách này một lần, rồi dùng lần lượt cho từng tin, nên hai tin liền nhau không trùng bố cục. Thứ tự xáo trộn cố định theo ngày và tên video, nên tạo lại video vẫn giữ nguyên bố cục. Đặt ở cấp ngoài cùng cho mọi video, hoặc trong một chương trình chỉ cho video đó. Tin không có ảnh thì không dùng các bố cục ảnh tràn (`anh-tran-tren`, `anh-tran-vien`, `phu-de`) mà chuyển về `goc`.
  - `phu-de`: ảnh ở giữa khung, hai dải đen trên dưới, tiêu đề chữ trắng viền đen kiểu phụ đề (font Barlow Condensed), dòng nguồn nhỏ bên dưới. Không có ô số liệu.
- `brand`: dòng thương hiệu trên intro; một chương trình có thể đặt `brand` riêng (ví dụ video Pháp luật).
- `outro`: câu đọc cuối video (để trống thì không có).
- `intro_image`: ảnh nền intro dùng chung, đường dẫn tương đối từ `demo/`.
- `image_rights`: `"kiem-tra"` (mặc định) chỉ dùng ảnh được phép, xem [Kiểm tra nguồn ảnh](#kiểm-tra-nguồn-ảnh); `"tat"` dùng mọi ảnh như trước (chỉ cho video nội bộ).

Thêm ngành: thêm một mục vào `programmes`, thêm tên ngành vào dropdown cột Ngành trên Sheet và vào danh sách ngành trong [claude-prompt.md](claude-prompt.md).

### Kết nối Google Sheet

1. Tạo Sheet và đặt tên tab là `Tin`. Import `demo/sheet-template.csv` bằng **Tệp → Nhập → Tải lên → Thay thế trang tính hiện tại**, và **bỏ chọn** “Chuyển đổi văn bản thành số, ngày tháng và công thức” để giữ nguyên `129,3%` và `25.10`. File mẫu có 5 tin ngày 27/10/2026, chia thành 3 video.
2. Đặt định dạng cột **Số liệu** là Văn bản thuần túy. Thêm dropdown cho **Trạng thái** (Nháp / Đã duyệt / Bỏ) và **Ngành** (9 ngành trong config). Đổi cột **Hot** thành ô tick (Chèn → Hộp đánh dấu). Tab mặc định chỉ có 1000 dòng (khoảng một tháng tin), nên thêm dòng ở cuối trang tính (ví dụ lên 10000); routine cũng tự thêm dòng khi sắp hết.
3. Chép `demo/config.example.json` thành `demo/config.json` và điền `sheet_id`, tức đoạn giữa `/d/` và `/edit` trong link Sheet.
4. Chọn một trong hai cách để repo đọc được Sheet:
   - **Đơn giản:** Share → “Bất kỳ ai có đường liên kết đều xem được”. Điền `sheet_gid` là số sau `#gid=` của tab `Tin`.
   - **Riêng tư:** tạo service account trên Google Cloud, bật Google Sheets API, tải file JSON key vào `demo/service-account.json`. Share Sheet cho email của service account với quyền Người xem, rồi điền `"service_account_file": "service-account.json"`.

`sheet_to_news.py` chỉ lấy các dòng **Đã duyệt** của đúng ngày đã chọn.
- Dòng thiếu thông tin bắt buộc: bỏ riêng dòng đó và báo lỗi, các tin khác vẫn được làm.
- Tin cũ hoặc trùng: bỏ dòng có thời gian đăng trước mốc `freshness`, dòng có URL đã dùng ở ngày trước hoặc trùng URL trong ngày (URL được chuẩn hoá: bỏ `http/https`, `www.`, `/` cuối, `utm_*`...), và dòng trùng sự kiện với tin trong 2 ngày gần nhất (cùng số liệu, đơn vị và tiêu đề gần giống).
- Video của ngành không còn đủ tin sau khi đọc lại Sheet bị xoá khỏi `output/daily/<ngày>/`.
- Chữ quá dài so với khung thẻ, hoặc kịch bản còn chữ số: chỉ hiện cảnh báo.

### Bản đơn lẻ cũ (demo/news.json)

```sh
.venv-demo/bin/python demo/fetch_images.py
../VieNeu-TTS/.venv/bin/python demo/prepare.py --provider vieneu --voice 'Hải Đăng'
node demo/render.mjs
.venv-demo/bin/python demo/verify.py
```

## Đăng lại video (reup) qua Telegram

Gửi link video (X, TikTok, YouTube, Facebook…) cho bot Telegram. Máy tải video, Claude xem các khung hình và viết tiêu đề, kịch bản và caption, rồi bot gửi kịch bản cho bạn duyệt. Bấm **Làm video**: VieNeu đọc giọng, máy render khung dọc 1080×1920 và bot gửi file MP4 lại trong Telegram.

- Bố cục: nhãn vàng, tiêu đề lớn và dòng nguồn ở trên; video gốc trong khung viền vàng, nền là chính video đó phóng to làm mờ; phụ đề chạy theo từng câu đang đọc.
- Phần cuối video chỉ có logo, không tiếng (thường gặp ở bài của các trang tin), tự bị cắt bỏ.
- Giọng đọc dài hơn video một chút thì video chậm lại cho khớp. Dài hơn nhiều thì phát chậm lại đoạn ồn nhất (ví dụ tiếng nổ), rồi dừng ở khung cuối. Tiếng gốc vặn nhỏ dưới giọng đọc.
- Đang xem kịch bản thì nhắn một câu để yêu cầu sửa, ví dụ "ngắn lại", "tiêu đề giật hơn". Nút **Viết lại** cho một bản khác hẳn.
- Claude chạy bằng lệnh `claude -p` với gói Claude đang đăng nhập, không dùng API key. Claude chỉ được đọc file, không được sửa hay chạy lệnh trên máy.
- Kịch bản chỉ dựa vào khung hình và chữ trong bài đăng. Kiểm tra thông tin và quyền dùng video trước khi đăng công khai.

### Cài đặt (một lần)

1. Thêm `yt-dlp` vào môi trường demo: `uv pip install --python .venv-demo -r demo/requirements-demo.txt`.
2. Tạo bot với @BotFather trên Telegram (`/newbot`), ghi token vào `.env`: `TELEGRAM_BOT_TOKEN=...`.
3. Cho bot một hồ sơ Claude Code riêng, đăng nhập bằng tài khoản Claude: thêm `CLAUDE_CONFIG_DIR=%USERPROFILE%\.claude-reup` vào `.env`, rồi chạy một lần trong PowerShell, gõ `/login`, chọn tài khoản Claude, xong gõ `/exit`:

   ```powershell
   $env:CLAUDE_CONFIG_DIR = "$HOME\.claude-reup"; claude
   ```

   Hồ sơ riêng giúp bot không bị ảnh hưởng bởi cài đặt chung trong `~/.claude/settings.json` (ví dụ `apiKeyHelper` hay `ANTHROPIC_BASE_URL` của dịch vụ khác). Cách khác: `claude setup-token`, rồi ghi token vào `.env` thành `CLAUDE_CODE_OAUTH_TOKEN=...`.
4. Chạy bot nền, tự bật lại khi đăng nhập Windows (gỡ: thêm `-Uninstall`):

   ```powershell
   powershell -ExecutionPolicy Bypass -File demo\windows\install-bot.ps1
   ```

5. Mở bot trên Telegram và gửi `/start`. Chat đầu tiên gửi `/start` thành chủ bot.

### Cho người khác dùng

- Người khác (tài khoản Telegram khác, máy nào cũng được) tìm bot và gửi `/start`. Chủ bot nhận tin "… muốn dùng bot" kèm nút **Cho phép** / **Từ chối**. Chưa được cho phép thì bot bỏ qua mọi tin nhắn của người đó.
- Dùng chung trong nhóm: thêm bot vào nhóm Telegram, gửi `/start` trong nhóm, chủ bot cho phép. Để bot đọc được link gửi thường trong nhóm, tắt chế độ riêng tư một lần: @BotFather → `/setprivacy` → chọn bot → **Disable** (rồi xoá bot khỏi nhóm và thêm lại).
- Chủ bot gõ `/ds` để xem danh sách, `/xoa <id>` để bỏ quyền một người hay một nhóm. Danh sách lưu ở `TELEGRAM_CHAT_ID` trong `.env` (các id cách nhau dấu phẩy, id đầu là chủ).
- Mỗi người nhận kịch bản và video trong chat của mình. Máy làm lần lượt từng video, người gửi sau thấy số việc đang chờ.

Nhật ký ở `output/telegram-bot.log`. Video ở `output/reup/`, dữ liệu trung gian ở `demo/work/reup/<mã>/`. Bot làm từng việc một, và chờ nếu máy đang làm video điểm tin hằng ngày.

Tuỳ chỉnh trong mục `reup` của `demo/config.json` (không bắt buộc): `voice`, `speed` (mặc định lấy theo video điểm tin), `original_volume` (mặc định 0.25), `label` (mặc định "TIN NÓNG").

### Chạy bằng dòng lệnh

```powershell
.venv-demo\Scripts\python.exe demo\reup.py all <link>                  # tải, viết kịch bản, render
.venv-demo\Scripts\python.exe demo\reup.py script <mã> --note "ngắn lại"
.venv-demo\Scripts\python.exe demo\reup.py build <mã>                  # sau khi sửa tay script.json
.venv-demo\Scripts\python.exe demo\reup.py render <mã>
```

## Nền ảnh tin tức (chỉ dùng nội bộ)

`fetch_images.py` tải `og:image` của từng bài báo về `assets/bg-N.*`, ghi lại trong `images.json`. Ảnh thuộc bản quyền của báo gốc, nên video chỉ dùng nội bộ và không đăng công khai.

### Kiểm tra nguồn ảnh

Mỗi lần tạo video (Studio, `run_daily.py`, tự render), bước tải ảnh xem ai sở hữu ảnh của từng tin trước khi dùng (`image_rights.py`):

- Nguồn ảnh lấy từ cột **Ảnh nguồn** nếu biên tập viên đã điền. Nếu cột trống, máy đọc chú thích ảnh trong bài gốc ("… Ảnh: VGP"). Ảnh đại diện thường là bản cắt từ ảnh đầu bài, khi đó dùng chú thích của ảnh đầu bài. Kết quả lưu ở `assets/credit-<mã>.txt`.
- **Dùng**: ảnh cơ quan nhà nước (VGP, Quốc hội, Bộ, Sở, UBND, Công an…), ban tổ chức, doanh nghiệp (mã cổ phiếu, tên công ty, “… cung cấp”), thư viện ảnh miễn phí (Unsplash, Pexels, Pixabay, Wikimedia), ảnh tự chụp.
- **Bỏ**: ảnh phóng viên (tên người, ví dụ “Ngọc Thành”, “HỮU HẠNH”), hãng thông tấn (Reuters, AFP, TTXVN…), báo khác, ảnh AI, ảnh không ghi nguồn hoặc không mở được bài. Nhật ký ghi “BỎ ẢNH” kèm lý do.
- **Ảnh dự phòng**: tin bị bỏ ảnh, tin có Ảnh URL “none” hoặc tin tải ảnh lỗi đều lấy một ảnh CC0/public domain cùng ngành trong `stock/<ngành>/`. Nguồn từng ảnh ghi ở `stock/credits.json`. Mỗi tin trong một video nhận một ảnh khác nhau, và tin giữ nguyên ảnh khi render lại. Ảnh này có nhãn nhỏ “Ảnh minh họa” trên khung tin. Muốn thêm ảnh, chép file `.jpg` khổ ngang vào thư mục ngành và ghi nguồn vào `credits.json`.
- Muốn dùng một ảnh bị bỏ nhầm, điền nguồn đúng vào cột **Ảnh nguồn**, ví dụ “Công an cung cấp”. Muốn thay ảnh, dán link ảnh hợp lệ vào **Ảnh URL** và ghi nguồn của ảnh đó.

Đây là kiểm tra tự động dựa trên chú thích, không thay cho việc xem lại trước khi đăng công khai.

- Ghim ảnh khác: `"image": "<url>"` trong `news.json`. Bỏ ảnh khi bài chỉ có logo: `"image": false`, khi đó tin dùng nền gradient theo màu nhấn.
- Ghi chú ảnh: mặc định lấy tên nguồn; đổi bằng `"image_credit"`.
- `prepare.py` dựng nền 1080×1920 gồm ảnh mờ phủ kín và ảnh nét đặt ngang ở nửa trên. Lý do: og:image chỉ khoảng 1200×630, phóng phủ kín khung dọc sẽ vỡ hạt. Player zoom chậm phần nền; thẻ chữ là SVG trong suốt, truyền qua trường `overlay` của segment nên không bị zoom theo.

Kết quả của các bước riêng lẻ: `output/diem-tin-2026-10-27-vieneu.mp4`.

Thay `--voice 'Hải Đăng'` bằng `--voice 'Minh Đức'` (nam Bắc), `--voice 'Thùy Dung'` (nữ Nam), hoặc `--voice 'Minh Triết'` (nam Nam). Các giọng này là preset được repo mô tả theo phong cách tin tức. VieNeu v3 Turbo không sử dụng tham số `style`; sắc thái đến từ preset.

`--force` tạo lại âm thanh. Mặc định cache được định danh theo provider, giọng, model và nội dung; đổi một trong các trường này sẽ tạo file mới. Cache WAV và MP3 tách biệt. Thời lượng mỗi thẻ tin được đo từ file audio thực tế, cộng 0,35 giây và làm tròn lên theo 25 fps.

## Edge TTS để so sánh

```sh
.venv-demo/bin/python demo/prepare.py --provider edge
node demo/render.mjs demo/props-edge.json output/diem-tin-2026-10-27-edge.mp4
.venv-demo/bin/python demo/verify.py output/diem-tin-2026-10-27-edge.mp4 demo/props-edge.json
```

Bản Edge gốc vẫn ở `output/diem-tin-2026-10-27.mp4`.

## Cài đặt lại nếu cần

```sh
npm ci
python3 -m venv .venv-demo
.venv-demo/bin/pip install -r demo/requirements-demo.txt
# Chỉ clone nếu chưa có thư mục VieNeu-TTS bên cạnh:
git clone https://github.com/pnnbao97/VieNeu-TTS.git ../VieNeu-TTS
git -C ../VieNeu-TTS checkout 04044e1b8f822f946bafb2c88b716459f4164152
.venv-demo/bin/uv sync --project ../VieNeu-TTS --python 3.12
```

Lần chạy đầu tải model từ Hugging Face, cache riêng khoảng 2 GB tại `demo/.model-cache/hub`. CPU dùng fp32; không dùng int8 vốn tối ưu cho CPU có VNNI. Chương trình đặt `HF_HUB_DISABLE_SYMLINKS=1` trước khi import VieNeu để tránh lỗi ORT 1.24 kiểm tra external weights qua symlink. Không thay đổi cache Hugging Face dùng chung.

Render phục vụ asset tại `127.0.0.1:8765`, đóng server khi hoàn tất. Không đăng video lên dịch vụ bên ngoài.

## Nội dung bản mẫu và kiểm tra

Kịch bản 27/10/2026 được biên tập từ nguồn ở `sources.md`, dữ liệu trong `news.json`. Bao gồm tin công bố 26–27/10, có ngày sự kiện riêng. Vingroup được ghi là phê duyệt điều khoản, chưa xác nhận huy động hoàn tất; đề xuất thuế được ghi rõ chưa có hiệu lực. Đồ họa SVG là thẻ số liệu, không phải ảnh sự kiện.

```sh
.venv-demo/bin/python -m unittest discover -s demo -p 'test_*.py'
```

Bốn kiểm tra bao gồm thời lượng WAV theo số sample, chống tái sử dụng cache sau khi đổi đầu vào, WAV rỗng và MP3 hỏng. `verify.py` giải mã toàn bộ MP4, đo âm lượng và trích khung hình từng tin. File QA có tên bắt đầu bằng tên video. `tts-vieneu-first-run.json` lưu thời gian chạy đầu tiên trên máy này; không phải benchmark cho mọi máy.

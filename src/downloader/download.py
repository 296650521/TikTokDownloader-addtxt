import os
import re
import requests
import base64
import logging
from datetime import datetime
from collections import defaultdict

# ===================== 日志配置 =====================
log_filename = f"wp_publish_{datetime.now().strftime('%Y%m%d_%H%M')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===================== 你的配置 =====================
WORDPRESS_SITE = "https://xiaodecheji.cc.cd/"
WP_POST_API = f"{WORDPRESS_SITE}/wp-json/wp/v2/posts"
WP_MEDIA_API = f"{WORDPRESS_SITE}/wp-json/wp/v2/media"
WP_USER = "yunpanzhidazhan"
WP_PASSWORD = "XDv0 1xbH aYFj FRJZ TUxa TBYL"
DOWNLOADS_FOLDER = r"C:\Users\OSPF\Desktop\DouK-Downloader_Windows_X64_20260303(1)\_internal\Volume\UID1643132638988764"
POST_STATUS = "publish"
ADMIN_USER_ID = 1

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif')
VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.flv')
TIME_REGEX = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})')

requests.packages.urllib3.disable_warnings()

# ===================== 工具函数 =====================
def get_time_str(filename):
    match = TIME_REGEX.match(filename)
    return match.group(1) if match else None

def read_txt(txt_path):
    try:
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            with open(txt_path, 'r', encoding='gbk') as f:
                content = f.read()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if not lines:
            return None, None
        return lines[0], '\n'.join(lines[1:])
    except:
        return None, None

def find_txt_by_time(time_str, folder):
    for f in os.listdir(folder):
        if f.lower().endswith('.txt') and get_time_str(f) == time_str:
            return os.path.join(folder, f)
    return None

def image_to_tag(img_path):
    try:
        ext = os.path.splitext(img_path)[1].lower().strip('.')
        if ext == 'jpg': ext = 'jpeg'
        with open(img_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'<p><img src="data:image/{ext};base64,{b64}" style="max-width:100%;"></p>'
    except:
        return ''

def upload_video_get_tag(vid_path):
    try:
        fname = os.path.basename(vid_path)
        with open(vid_path, 'rb') as f:
            data = f.read()
        headers = {
            'Content-Disposition': f'attachment; filename="{fname}"',
            'Content-Type': 'video/mp4'
        }
        r = requests.post(WP_MEDIA_API, headers=headers, data=data, 
                          auth=(WP_USER, WP_PASSWORD), verify=False, timeout=300)
        if r.status_code in (200, 201):
            url = r.json()['guid']['rendered']
            return f'''
<p><video controls style="max-width:100%;">
<source src="{url}" type="video/mp4">您的浏览器不支持播放
</video></p>'''
    except:
        pass
    return ''

def publish_one_group(time_str, images, videos):
    txt_path = find_txt_by_time(time_str, DOWNLOADS_FOLDER)
    if not txt_path:
        logger.warning(f"{time_str} 无txt，跳过")
        return False

    title, content = read_txt(txt_path)
    if not title:
        logger.error(f"{time_str} txt无标题，跳过")
        return False

    img_tags = '\n'.join([image_to_tag(p) for p in images])
    vid_tags = '\n'.join([upload_video_get_tag(p) for p in videos])
    full_content = '\n\n'.join([content, img_tags, vid_tags]).strip()

    payload = {
        "title": title,
        "content": full_content,
        "status": POST_STATUS,
        "author": ADMIN_USER_ID
    }

    try:
        r = requests.post(WP_POST_API, json=payload, auth=(WP_USER, WP_PASSWORD), 
                          verify=False, timeout=60)
        if r.status_code in (200, 201):
            logger.info(f"✅ 发布成功：{time_str}")
            return True
        else:
            logger.error(f"❌ 发布失败 {time_str} 状态码：{r.status_code}")
    except Exception as e:
        logger.error(f"💥 异常 {time_str}: {str(e)}")
    return False

# ===================== 主程序：按时间 远 → 近 发布 =====================
if __name__ == '__main__':
    groups = defaultdict(lambda: {"images": [], "videos": []})
    time_str_to_file_time = {}

    for filename in os.listdir(DOWNLOADS_FOLDER):
        path = os.path.join(DOWNLOADS_FOLDER, filename)
        t_str = get_time_str(filename)
        if not t_str:
            continue

        # 取文件的创建时间，用来排序
        file_time = os.path.getctime(path)
        time_str_to_file_time[t_str] = file_time

        if filename.lower().endswith(IMAGE_EXTS):
            groups[t_str]["images"].append(path)
        elif filename.lower().endswith(VIDEO_EXTS):
            groups[t_str]["videos"].append(path)

    # 按「文件时间 从远到近」排序
    sorted_time_strs = sorted(groups.keys(), key=lambda ts: time_str_to_file_time[ts])

    logger.info("="*60)
    logger.info(f"共 {len(sorted_time_strs)} 组，按文件时间 远 → 近 发布")
    logger.info("="*60)

    success = 0
    fail = 0
    for ts in sorted_time_strs:
        if publish_one_group(ts, groups[ts]["images"], groups[ts]["videos"]):
            success +=1
        else:
            fail +=1

    logger.info(f"\n✅ 全部完成：成功 {success} 组，失败 {fail} 组")
    logger.info(f"日志：{log_filename}")
    input("\n按回车退出")

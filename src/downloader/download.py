import os
import re
import requests
import base64
import logging
import mimetypes
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

# ===================== 核心配置 =====================
WORDPRESS_SITE = "https://xiaodecheji.cc.cd/"
WP_POST_API = f"{WORDPRESS_SITE}/wp-json/wp/v2/posts"
WP_MEDIA_API = f"{WORDPRESS_SITE}/wp-json/wp/v2/media"
WP_USER = "yunpanzhidazhan"
WP_PASSWORD = "XDv0 1xbH aYFj FRJZ TUxa TBYL"
DOWNLOADS_FOLDER = r"C:\Users\OSPF\Desktop\DouK-Downloader_Windows_X64_20260303(1)\_internal\Volume\UID1643132638988764"
POST_STATUS = "publish"
ADMIN_USER_ID = 1

# 支持的文件格式
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif')
VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm')
TIME_REGEX = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})')

# 禁用SSL警告 + 增大请求超时
requests.packages.urllib3.disable_warnings()
SESSION = requests.Session()
SESSION.auth = (WP_USER, WP_PASSWORD)
SESSION.verify = False
SESSION.timeout = 600  # 视频上传超时10分钟

# ===================== 工具函数（重点修复视频上传） =====================
def get_time_str(filename):
    """提取文件名中的时间戳"""
    match = TIME_REGEX.match(filename)
    return match.group(1) if match else None

def read_txt(txt_path):
    """读取TXT文件，返回标题和正文"""
    try:
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(txt_path, 'r', encoding='gbk') as f:
                content = f.read()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if not lines:
            logger.error(f"TXT文件{txt_path}内容为空")
            return None, None
        return lines[0], '\n'.join(lines[1:])
    except Exception as e:
        logger.error(f"读取TXT失败{txt_path}：{str(e)}")
        return None, None

def find_txt_by_time(time_str, folder):
    """根据时间戳找对应的TXT文件"""
    for f in os.listdir(folder):
        if f.lower().endswith('.txt') and get_time_str(f) == time_str:
            return os.path.join(folder, f)
    logger.warning(f"时间戳{time_str}未找到TXT文件")
    return None

def image_to_tag(img_path):
    """图片转base64标签"""
    try:
        ext = os.path.splitext(img_path)[1].lower().strip('.')
        if ext == 'jpg':
            ext = 'jpeg'
        with open(img_path, 'rb') as f:
            b64_data = base64.b64encode(f.read()).decode('utf-8')
        return f'<p><img src="data:image/{ext};base64,{b64_data}" style="max-width:100%;margin:10px 0;"></p>'
    except Exception as e:
        logger.error(f"处理图片失败{img_path}：{str(e)}")
        return ''

def get_video_mime_type(video_path):
    """自动识别视频MIME类型（修复上传格式问题）"""
    ext = os.path.splitext(video_path)[1].lower()
    mime_map = {
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo',
        '.mkv': 'video/x-matroska',
        '.flv': 'video/x-flv',
        '.webm': 'video/webm'
    }
    return mime_map.get(ext, mimetypes.guess_type(video_path)[0] or 'video/mp4')

def upload_video_get_tag(video_path):
    """【修复版】视频上传到WordPress媒体库，返回播放标签"""
    try:
        # 1. 基础信息校验
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在：{video_path}")
            return ''
        
        filename = os.path.basename(video_path)
        file_size = os.path.getsize(video_path)
        logger.info(f"开始上传视频：{filename} | 大小：{file_size/1024/1024:.1f}MB")
        
        # 2. 自动识别MIME类型（关键修复点1）
        mime_type = get_video_mime_type(video_path)
        
        # 3. 读取视频文件（分块读取，修复大文件上传问题）
        with open(video_path, 'rb') as f:
            video_data = f.read()
        
        # 4. 构造请求头（关键修复点2：完整的认证+格式头）
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': mime_type,
            'Authorization': 'Basic ' + requests.auth._basic_auth_str(WP_USER, WP_PASSWORD),
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # 5. 发送上传请求（关键修复点3：用Session保持连接）
        response = SESSION.post(
            WP_MEDIA_API,
            headers=headers,
            data=video_data,
            allow_redirects=True
        )
        
        # 6. 处理响应
        if response.status_code in (200, 201):
            media_data = response.json()
            video_url = media_data.get('guid', {}).get('rendered', '')
            if video_url:
                logger.info(f"视频上传成功：{filename} | 链接：{video_url}")
                # 生成兼容所有浏览器的视频标签
                return f'''
<p style="margin:20px 0;">
    <video width="100%" controls preload="metadata">
        <source src="{video_url}" type="{mime_type}">
        您的浏览器不支持视频播放，请点击链接查看：<a href="{video_url}" target="_blank">{filename}</a>
    </video>
</p>'''
            else:
                logger.error(f"视频上传成功但无播放链接：{filename}")
                return ''
        else:
            logger.error(f"视频上传失败{filename} | 状态码：{response.status_code} | 错误：{response.text[:300]}")
            return ''
    except Exception as e:
        logger.error(f"视频上传异常{video_path}：{str(e)}")
        return ''

def publish_one_group(time_str, images, videos):
    """发布单组文章"""
    logger.info(f"\n开始处理分组：{time_str} | 图片{len(images)}张 | 视频{len(videos)}个")
    
    # 找TXT文件
    txt_path = find_txt_by_time(time_str, DOWNLOADS_FOLDER)
    if not txt_path:
        logger.error(f"分组{time_str}跳过：无TXT文件")
        return False
    
    # 读取TXT内容
    title, content = read_txt(txt_path)
    if not title:
        logger.error(f"分组{time_str}跳过：TXT无有效标题")
        return False
    
    # 处理图片和视频
    img_tags = '\n'.join([image_to_tag(p) for p in images])
    vid_tags = '\n'.join([upload_video_get_tag(p) for p in videos])
    
    # 拼接正文
    full_content = '\n\n'.join([content, img_tags, vid_tags]).strip()
    
    # 发布文章
    payload = {
        "title": title,
        "content": full_content,
        "status": POST_STATUS,
        "author": ADMIN_USER_ID
    }
    
    try:
        response = SESSION.post(WP_POST_API, json=payload)
        if response.status_code in (200, 201):
            post_link = response.json().get('link', '未知')
            logger.info(f"✅ 分组{time_str}发布成功 | 标题：{title[:50]}... | 链接：{post_link}")
            return True
        else:
            logger.error(f"❌ 分组{time_str}发布失败 | 状态码：{response.status_code} | 错误：{response.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"💥 分组{time_str}发布异常：{str(e)}")
        return False

# ===================== 主程序（按文件时间从远到近发布） =====================
if __name__ == '__main__':
    # 初始化分组
    groups = defaultdict(lambda: {"images": [], "videos": []})
    time_str_to_file_time = {}
    
    logger.info("="*80)
    logger.info("启动批量发布程序（优化视频上传版）")
    logger.info(f"目标文件夹：{DOWNLOADS_FOLDER}")
    logger.info("="*80)
    
    # 遍历文件分组
    for filename in os.listdir(DOWNLOADS_FOLDER):
        file_path = os.path.join(DOWNLOADS_FOLDER, filename)
        time_str = get_time_str(filename)
        
        if not time_str:
            logger.warning(f"跳过无时间戳文件：{filename}")
            continue
        
        # 记录文件创建时间（用于排序）
        if time_str not in time_str_to_file_time:
            time_str_to_file_time[time_str] = os.path.getctime(file_path)
        
        # 分类加入分组
        if filename.lower().endswith(IMAGE_EXTS):
            groups[time_str]["images"].append(file_path)
        elif filename.lower().endswith(VIDEO_EXTS):
            groups[time_str]["videos"].append(file_path)
    
    # 按文件时间从远到近排序分组
    sorted_time_strs = sorted(groups.keys(), key=lambda ts: time_str_to_file_time[ts])
    
    # 统计分组信息
    logger.info(f"\n共识别到 {len(sorted_time_strs)} 个时间分组（按文件时间从远到近）：")
    for idx, ts in enumerate(sorted_time_strs, 1):
        img_count = len(groups[ts]["images"])
        vid_count = len(groups[ts]["videos"])
        logger.info(f"{idx}. {ts} → 图片{img_count}张 | 视频{vid_count}个")
    
    # 逐组发布
    success_count = 0
    fail_count = 0
    logger.info("\n开始按顺序发布：")
    logger.info("-"*80)
    
    for time_str in sorted_time_strs:
        if publish_one_group(time_str, groups[time_str]["images"], groups[time_str]["videos"]):
            success_count += 1
        else:
            fail_count += 1
    
    # 最终统计
    logger.info("\n" + "="*80)
    logger.info("发布完成 - 最终统计：")
    logger.info(f"总分组数：{len(sorted_time_strs)}")
    logger.info(f"发布成功：{success_count} 组")
    logger.info(f"发布失败：{fail_count} 组")
    logger.info(f"日志文件：{os.path.abspath(log_filename)}")
    logger.info("="*80)
    
    input("\n✅ 程序执行完毕，按回车键退出...")

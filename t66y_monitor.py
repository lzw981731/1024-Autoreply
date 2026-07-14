#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# t66y 论坛视频监控 → Telegram 推送（每10分钟）
# .mp4 直接URL上传，其他格式推送链接

import requests
import re
import time
import json
import os
from datetime import datetime

# ========== 配置 ==========
TG_BOT_TOKEN = "8475466502:AAGc11111111111111111" #机器人密钥
TG_CHAT_ID_VIDEO = "-100111111111111"  # 视频推送到这个群
TG_CHAT_ID_PHOTO = "-100111111111111"  # 图片推送到这个群
FORUM_BASE = "https://t66y.com"
SEARCH_URL = f"{FORUM_BASE}/thread0806.php?fid=7&search=today"

# 关键词规则：每条规则独立匹配，匹配到的帖子会被抓取推送
# match_type: "title" = 标题包含关键词, "mark" = 积分标签包含关键词
KEYWORD_RULES = [
    {"keyword": "積分+", "match_type": "mark",  "push_video": True, "push_photo": True},
    {"keyword": "[原创]", "match_type": "title", "push_video": True, "push_photo": True},
    {"keyword": "[分享]", "match_type": "title", "push_video": True, "push_photo": True},
]

FETCH_DELAY = 2.5       # 论坛请求间隔
TG_DELAY = 5            # TG推送间隔
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "t66y_processed.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137.0.0.0 Safari/537.36",
    "Referer": FORUM_BASE,
}


# ========== 数据持久化 ==========
def load_processed():
    """加载已处理的帖子 htm_url 列表"""
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return []


def save_processed(processed):
    with open(DATA_FILE, "w") as f:
        json.dump(processed, f)


# ========== 抓取 ==========
def fetch_page(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        return resp.text
    except Exception as e:
        print(f"  ❌ 抓取失败: {e}")
        return ""


def extract_threads(html):
    """提取帖子，根据 KEYWORD_RULES 匹配标题或积分标签"""
    threads = []
    # 提取所有帖子：<h3><a href="htm_data/...">标题</a></h3> 后面可能有 <span>積分+X</span>
    pattern = r'<h3><a\s+href="(/htm_data/[^"]+)"[^>]*>([^<]+)</a></h3>(?:\s*(?:&nbsp;)?\s*<span[^>]*>.*?積分\+(\d+).*?</span>)?'
    for match in re.finditer(pattern, html):
        htm_path = match.group(1)
        title = match.group(2).strip()
        score = match.group(3) or ""
        htm_url = f"{FORUM_BASE}{htm_path}"

        # 检查每条规则是否匹配
        matched_rules = []
        for rule in KEYWORD_RULES:
            if rule["match_type"] == "title" and rule["keyword"] in title:
                matched_rules.append(rule)
            elif rule["match_type"] == "mark" and score and rule["keyword"] in f"積分+{score}":
                matched_rules.append(rule)

        if matched_rules:
            # 合并推送配置：任一规则允许推视频就推，任一允许推图片就推
            push_video = any(r["push_video"] for r in matched_rules)
            push_photo = any(r["push_photo"] for r in matched_rules)
            threads.append({
                "title": title,
                "htm_url": htm_url,
                "score": score,
                "push_video": push_video,
                "push_photo": push_photo,
                "matched": [r["keyword"] for r in matched_rules],
            })
    return threads


def extract_videos(html):
    """提取视频链接，返回 [(url, format), ...]"""
    videos = []
    pattern = r"<video[^>]+src=['\"]([^'\"]+)['\"]"
    for match in re.finditer(pattern, html):
        url = match.group(1)
        ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
        if url not in [v[0] for v in videos]:
            videos.append((url, ext))
    return videos


def extract_images(html):
    """提取帖子正文区域的图片（从标题到点赞按钮之间）"""
    images = []
    # 先限定正文范围：<h4 class='f16'> 到 <div class='t_like'
    m = re.search(r"<h4 class=.f16.>.*?</h4>(.*?)<div[^>]*class=.t_like", html, re.DOTALL)
    content = m.group(1) if m else html  # 找不到就用整个页面

    # 从 ess-data 属性提取（论坛懒加载的真实图片URL）
    pattern = r"ess-data=['\"]([^'\"]+)['\"]"
    for match in re.finditer(pattern, content):
        url = match.group(1)
        url = re.sub(r'\[/?(img|url|color)[^\]]*\]', '', url)
        if url.startswith('//'):
            url = 'https:' + url
        if url not in images:
            images.append(url)

    # 备用：如果 ess-data 没找到，回退到 src
    if not images:
        pattern = r"<img[^>]+src=['\"]([^'\"]+)['\"]"
        for match in re.finditer(pattern, content):
            url = match.group(1)
            url = re.sub(r'\[/?(img|url|color)[^\]]*\]', '', url)
            if url.startswith('//'):
                url = 'https:' + url
            if not re.search(r'\.(jpg|jpeg|png|webp|bmp|gif)(\?.*)?$', url, re.I):
                continue
            if any(skip in url.lower() for skip in ['smiley', 'emoji', 'icon', 'avatar', 'face', 'static/image', 'static.redircdn', 'logo', 'thumbsnap', 'im.ge']):
                continue
            if any(skip in url.lower() for skip in ['.js', '.css', '.php', 'encodeURIComponent']):
                continue
            if url not in images:
                images.append(url)
    return images


# ========== TG 推送 ==========
def send_video_url(url, caption=""):
    """URL 直接发送视频到视频群"""
    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendVideo"
    try:
        resp = requests.post(api_url, data={
            "chat_id": TG_CHAT_ID_VIDEO,
            "video": url,
            "caption": caption[:1024],
        }, timeout=60)
        result = resp.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except:
        return None


def send_message(text, chat_id=None):
    """发送文本消息"""
    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(api_url, data={
            "chat_id": chat_id or TG_CHAT_ID_VIDEO,
            "text": text[:4096],
        }, timeout=10)
        result = resp.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except:
        return None


def send_photos_batch(urls, caption=""):
    """批量发送图片到图片群（每批最多10张，后续批次回复第一条消息）"""
    api_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMediaGroup"
    result_ids = []
    first_msg_id = None

    # 每10张一批
    for i in range(0, len(urls), 10):
        batch = urls[i:i+10]

        # 直传尝试
        media = []
        for j, url in enumerate(batch):
            media_item = {"type": "photo", "media": url}
            if i == 0 and j == 0:
                media_item["caption"] = caption[:1024]
            media.append(media_item)

        payload = {"chat_id": TG_CHAT_ID_PHOTO, "media": media}
        if first_msg_id:
            payload["reply_to_message_id"] = first_msg_id

        try:
            resp = requests.post(api_url, json=payload, timeout=60)
            result = resp.json()
            if result.get("ok"):
                batch_ids = [m["message_id"] for m in result["result"]]
                result_ids.extend(batch_ids)
                if not first_msg_id:
                    first_msg_id = batch_ids[0]
                time.sleep(TG_DELAY)
                continue
        except:
            pass

        # 直传失败，下载后批量上传
        import tempfile
        media = []
        files = {}
        need_download = []
        for j, url in enumerate(batch):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
                if resp.status_code == 200 and 'image' in resp.headers.get('content-type', ''):
                    tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
                    for chunk in resp.iter_content(8192):
                        tmp.write(chunk)
                    tmp.close()
                    need_download.append(tmp.name)
                    media_item = {"type": "photo", "media": f"attach://photo{j}"}
                    if i == 0 and j == 0:
                        media_item["caption"] = caption[:1024]
                    media.append(media_item)
                    files[f"photo{j}"] = open(tmp.name, 'rb')
                else:
                    text = f"📌 {caption}\n🖼️ {url}"
                    send_message(text, chat_id=TG_CHAT_ID_PHOTO)
            except:
                text = f"📌 {caption}\n🖼️ {url}"
                send_message(text, chat_id=TG_CHAT_ID_PHOTO)

        if media:
            form_data = {"chat_id": TG_CHAT_ID_PHOTO, "media": json.dumps(media)}
            if first_msg_id:
                form_data["reply_to_message_id"] = first_msg_id
            try:
                resp = requests.post(api_url, data=form_data, files=files, timeout=60)
                result = resp.json()
                if result.get("ok"):
                    batch_ids = [m["message_id"] for m in result["result"]]
                    result_ids.extend(batch_ids)
                    if not first_msg_id:
                        first_msg_id = batch_ids[0]
            except:
                pass
            finally:
                for f in files.values():
                    f.close()
                for path in need_download:
                    try:
                        os.unlink(path)
                    except:
                        pass

        time.sleep(TG_DELAY)

    return result_ids


# ========== 主逻辑 ==========
def check_and_push(processed):
    """检查新帖子并推送，返回更新后的 processed 列表"""
    html = fetch_page(SEARCH_URL)
    if not html:
        return processed

    threads = extract_threads(html)
    new_threads = [t for t in threads if t["htm_url"] not in processed]

    if not new_threads:
        print(f"  📭 没有新帖子")
        return processed

    print(f"  🆕 发现 {len(new_threads)} 条新帖子")

    for thread in new_threads:
        matched_str = "+".join(thread["matched"])
        print(f"  📄 {thread['title']} [{matched_str}]")
        time.sleep(FETCH_DELAY)

        content_html = fetch_page(thread["htm_url"])
        videos = extract_videos(content_html)
        images = extract_images(content_html)

        # 清理标题
        clean_title = re.sub(r'積分\+\d+', '', thread['title']).strip()
        clean_title = re.sub(r'\[\d+V?\]', '', clean_title).strip()
        clean_title = re.sub(r'\[\d+P?\]', '', clean_title).strip()

        # 推送视频到视频群
        if videos and thread["push_video"]:
            print(f"     🎬 找到 {len(videos)} 个视频 → 视频群")
            sent_count = 0
            for url, ext in videos:
                if ext == "mp4":
                    mid = send_video_url(url, f"📌 {clean_title}")
                    if mid:
                        sent_count += 1
                else:
                    text = f"📌 {clean_title}\n🎬 视频: {url}"
                    mid = send_message(text)
                    if mid:
                        sent_count += 1
                time.sleep(TG_DELAY)
            print(f"     ✅ 视频推送 {sent_count}/{len(videos)}")

        # 推送图片到图片群（批量上传）
        if images and thread["push_photo"]:
            print(f"     🖼️ 找到 {len(images)} 张图片 → 图片群（批量上传）")
            result_ids = send_photos_batch(images, f"📌 {clean_title}")
            print(f"     ✅ 图片推送 {len(result_ids)}/{len(images)}")

        if not videos and not images:
            print(f"     ⚠️ 无视频无图片，跳过")

        processed.append(thread["htm_url"])

    return processed


def main():
    print("=" * 50)
    print(f"🚀 t66y 论坛视频监控")
    print(f"🔑 关键词规则:")
    for rule in KEYWORD_RULES:
        tags = []
        if rule["push_video"]: tags.append("视频")
        if rule["push_photo"]: tags.append("图片")
        print(f"   • \"{rule['keyword']}\" ({rule['match_type']}) → {'/'.join(tags)}")
    print(f"🎬 视频群: {TG_CHAT_ID_VIDEO}")
    print(f"🖼️ 图片群: {TG_CHAT_ID_PHOTO}")
    print(f"📋 已处理: {len(load_processed())} 条帖子")
    print("=" * 50)

    processed = load_processed()

    try:
        processed = check_and_push(processed)
        save_processed(processed)
    except Exception as e:
        print(f"❌ 异常: {e}")

    print(f"✅ 本轮检查完成")


if __name__ == "__main__":
    main()

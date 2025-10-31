import os, time, logging, requests, feedparser, schedule, pytz, threading, re
from datetime import datetime, timedelta
from dotenv import load_dotenv

# ======================
# CONFIG
# ======================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = [i.strip() for i in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if i.strip()]
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

DATA_DIR = "data"
SENT_FILE = os.path.join(DATA_DIR, "sent_links.txt")
LOG_FILE = "miza_news_v19.log"
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s - %(message)s")

# ======================
# TELEGRAM
# ======================
def send_telegram(msg, image_url=None):
    """Gửi tin nhắn hoặc ảnh thumbnail kèm caption"""
    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    for chat_id in CHAT_IDS:
        try:
            if image_url:
                requests.post(
                    f"{base_url}/sendPhoto",
                    json={"chat_id": chat_id, "photo": image_url, "caption": msg, "parse_mode": "HTML"},
                    timeout=10
                )
            else:
                requests.post(
                    f"{base_url}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=10
                )
            logging.info(f"✅ Sent to {chat_id}")
        except Exception as e:
            logging.error(f"❌ Telegram error: {e}")

# ======================
# STORAGE
# ======================
def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    with open(SENT_FILE, encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_sent(link):
    with open(SENT_FILE, "a", encoding="utf-8") as f:
        f.write(link.strip() + "\n")

# ======================
# RSS SOURCES
# ======================
RSS_FEEDS = {
    "Google News": "https://news.google.com/rss/search?q=(Miza+OR+MZG+OR+Giấy+Miza)&hl=vi&gl=VN&ceid=VN:vi",
    "Bing News": "https://www.bing.com/news/search?q=Miza+MZG&format=rss",
    "YouTube": "https://www.youtube.com/feeds/videos.xml?channel_id=UCd2aU53aTTxxLONczZc34BA",
    "VNExpress": "https://vnexpress.net/rss/doanh-nghiep.rss",
    "Cafef": "https://cafef.vn/rss/tai-chinh-doanh-nghiep.rss",
    "VietnamBiz": "https://vietnambiz.vn/kinh-doanh.rss"
}

# ======================
# UTILS
# ======================
def normalize_link(url):
    return re.sub(r"(&utm_[^=]+=[^&]+)", "", url).strip()

def normalize_title(title):
    title = title.lower()
    title = re.sub(r"[^a-z0-9áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ ]", "", title)
    return re.sub(r"\s+", " ", title).strip()

def parse_date(entry):
    try:
        if entry.get("published_parsed"):
            dt = datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
        elif entry.get("updated_parsed"):
            dt = datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
        else:
            dt = datetime.now(pytz.utc)
        return dt.astimezone(VN_TZ)
    except Exception:
        return datetime.now(VN_TZ)

def shorten_url(url):
    try:
        res = requests.get(f"https://is.gd/create.php?format=simple&url={url}", timeout=5)
        return res.text if res.status_code == 200 else url
    except:
        return url

# ======================
# YOUTUBE THUMBNAIL
# ======================
def get_youtube_thumbnail(link):
    """Lấy ảnh thumbnail từ link YouTube"""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", link)
    if match:
        video_id = match.group(1)
        return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    return None

# ======================
# FETCH FEEDS
# ======================
def fetch_new_items(hours=48):
    cutoff = datetime.now(VN_TZ) - timedelta(hours=hours)
    sent_links = load_sent()
    seen_titles = set()
    new_items = []

    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                title = e.get("title", "").strip()
                if not title:
                    continue
                link = normalize_link(e.get("link", ""))
                norm_title = normalize_title(title)

                if link in sent_links or norm_title in seen_titles:
                    continue

                pub = parse_date(e)
                if pub >= cutoff and re.search(r"\b(Miza|MZG|Giấy Miza)\b", title, re.IGNORECASE):
                    seen_titles.add(norm_title)
                    new_items.append({
                        "title": title,
                        "link": link,
                        "date": pub,
                        "source": source
                    })
                    save_sent(link)
        except Exception as e:
            logging.error(f"RSS lỗi {source}: {e}")

    new_items.sort(key=lambda x: x["date"], reverse=True)
    return new_items

# ======================
# JOBS
# ======================
def job_realtime_check():
    new_items = fetch_new_items(hours=48)
    if not new_items:
        logging.info("⏳ Không có tin mới trong 48h qua.")
        return

    for item in new_items:
        link = shorten_url(item["link"])
        caption = f"🆕 <b>{item['title']}</b>\n🗓️ {item['date'].strftime('%H:%M %d/%m/%Y')}\n({item['source']})\n🔗 {link}"
        thumbnail = None
        if "youtube.com" in link:
            thumbnail = get_youtube_thumbnail(link)
        send_telegram(caption, image_url=thumbnail)
        logging.info(f"🚀 Đã gửi tin: {item['title']}")

def job_daily_summary():
    news = fetch_new_items(hours=168)
    now = datetime.now(VN_TZ)
    header = f"📢 <b>Tổng hợp tin Miza - {now.strftime('%d/%m')}</b>\n\n"
    if not news:
        send_telegram(header + "⚠️ Không có tin mới trong 7 ngày.")
        return
    body = "\n\n".join([
        f"{i+1}. <b>{n['title']}</b> ({n['source']})\n🗓️ {n['date'].strftime('%d/%m %H:%M')}\n🔗 {shorten_url(n['link'])}"
        for i, n in enumerate(news[:20])
    ])
    send_telegram(header + body)

# ======================
# MAIN LOOP
# ======================
def main():
    send_telegram("🚀 Miza News Bot v19 khởi động! (Hỗ trợ preview video YouTube 🎥)")
    logging.info("Bot started.")

    job_realtime_check()
    job_daily_summary()

    schedule.every().day.at("09:00").do(job_daily_summary)
    schedule.every(20).minutes.do(job_realtime_check)

    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()

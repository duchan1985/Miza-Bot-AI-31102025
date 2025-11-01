import os, time, logging, requests, feedparser, schedule, pytz, re, threading, json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask

# ======================
# CONFIG
# ======================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = [i.strip() for i in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if i.strip()]
RAPID_KEY = os.getenv("RAPID_API_KEY")
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

DATA_DIR = "data"
SENT_FILE = os.path.join(DATA_DIR, "sent_links.txt")
LOG_FILE = "miza_monitor_v29.log"
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s - %(message)s")

# ======================
# TELEGRAM
# ======================
def send_telegram(msg, image_url=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    for chat_id in CHAT_IDS:
        try:
            if image_url:
                requests.post(f"{base_url}/sendPhoto",
                              json={"chat_id": chat_id, "photo": image_url, "caption": msg, "parse_mode": "HTML"},
                              timeout=10)
            else:
                requests.post(f"{base_url}/sendMessage",
                              json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                              timeout=10)
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
    "Google News": "https://news.google.com/rss/search?q=(Miza+OR+MZG+OR+Giấy+Miza+OR+Miza+Corp)&hl=vi&gl=VN&ceid=VN:vi",
    "YouTube Channel": "https://www.youtube.com/feeds/videos.xml?channel_id=UCd2aU53aTTxxLONczZc34BA",
    "YouTube Search": "https://www.youtube.com/feeds/videos.xml?search_query=Miza+Group+MZG+Miza+Corp+Giấy+Miza"
}

# ======================
# UTILS
# ======================
def shorten_url(url):
    try:
        res = requests.get(f"https://is.gd/create.php?format=simple&url={url}", timeout=5)
        return res.text if res.status_code == 200 else url
    except:
        return url

def get_youtube_thumbnail(link):
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", link)
    if match:
        return f"https://img.youtube.com/vi/{match.group(1)}/hqdefault.jpg"
    return None

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

# ======================
# FETCH RSS
# ======================
def fetch_rss_items(days=5):
    cutoff = datetime.now(VN_TZ) - timedelta(days=days)
    sent_links = load_sent()
    new_items = []
    keyword_pattern = re.compile(r"(Miza|MZG|Miza\s*Group|Giấy\s*Miza|Miza\s*Corp)", re.IGNORECASE)

    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                title = e.get("title", "").strip()
                if not title:
                    continue
                link = e.get("link", "").strip()
                pub = parse_date(e)
                if link in sent_links or pub < cutoff or not keyword_pattern.search(title):
                    continue
                new_items.append({"title": title, "link": link, "date": pub, "source": source})
                save_sent(link)
                logging.info(f"[{source}] ✅ Phát hiện: {title}")
        except Exception as e:
            logging.error(f"RSS error {source}: {e}")
    return new_items

# ======================
# FACEBOOK (RSSHub)
# ======================
def fetch_facebook_posts(page="mizagroup.vn"):
    try:
        url = f"https://rsshub.app/facebook/page/{page}"
        feed = feedparser.parse(url)
        items = []
        cutoff = datetime.now(VN_TZ) - timedelta(days=5)
        for e in feed.entries:
            title = e.get("title", "Bài đăng Facebook")
            link = e.get("link", "")
            pub = parse_date(e)
            if pub < cutoff:
                continue
            items.append({"title": title, "link": link, "date": pub, "source": "Facebook"})
            save_sent(link)
        logging.info(f"✅ Facebook: {len(items)} bài.")
        return items
    except Exception as e:
        logging.error(f"Facebook error: {e}")
        return []

# ======================
# TIKTOK (RapidAPI)
# ======================
def fetch_tiktok_videos():
    headers = {
        "x-rapidapi-key": RAPID_KEY,
        "x-rapidapi-host": "tiktok-scraper7.p.rapidapi.com"
    }
    results = []
    try:
        r = requests.get("https://tiktok-scraper7.p.rapidapi.com/feed/search",
                         params={"keywords": "Miza MZG MizaCorp Giấy Miza Việt Nam", "count": "20"},
                         headers=headers, timeout=10)
        data = r.json()
        for v in data.get("data", {}).get("videos", []):
            link = v.get("webVideoUrl")
            title = v.get("title") or v.get("desc", "")
            pub = datetime.fromtimestamp(v.get("createTime", 0), tz=VN_TZ)
            if not link:
                continue
            results.append({"title": title, "link": link, "date": pub, "source": "TikTok"})
            save_sent(link)
        logging.info(f"✅ TikTok: {len(results)} video mới.")
        return results
    except Exception as e:
        logging.error(f"TikTok error: {e}")
        return []

# ======================
# DELAYED SEND (20 phút)
# ======================
def schedule_delayed_send(item):
    """Gửi tin sau 20 phút."""
    time.sleep(1200)
    link = shorten_url(item["link"])
    msg = f"🆕 <b>{item['title']}</b>\n🗓️ {item['date'].strftime('%H:%M %d/%m')}\n({item['source']})\n🔗 {link}"
    thumb = get_youtube_thumbnail(link) if "youtube.com" in link else None
    send_telegram(msg, image_url=thumb)
    logging.info(f"🚀 Đã gửi sau 20 phút: {item['title']}")

# ======================
# DAILY REPORT 9AM
# ======================
def job_daily_report():
    logging.info("📢 Tạo báo cáo 9h sáng...")
    rss_items = fetch_rss_items()
    fb_items = fetch_facebook_posts()
    tiktok_items = fetch_tiktok_videos()
    all_items = rss_items + fb_items + tiktok_items

    now = datetime.now(VN_TZ)
    header = f"🗞️ <b>BÁO CÁO MIZA – {now.strftime('%d/%m/%Y')}</b>\n⏰ {now.strftime('%H:%M')} | Tổng hợp YouTube, TikTok, Facebook, Google News\n\n"

    if not all_items:
        send_telegram(header + "❗ Không có tin mới trong 5 ngày qua.")
        logging.info("❗ Không có tin mới để báo cáo.")
        return

    # Gửi báo cáo tổng hợp
    all_items.sort(key=lambda x: x["date"], reverse=True)
    body = ""
    for i, item in enumerate(all_items[:20], 1):
        link = shorten_url(item["link"])
        body += f"{i}. <b>{item['title']}</b> ({item['source']})\n🗓️ {item['date'].strftime('%H:%M %d/%m')}\n🔗 {link}\n\n"
    send_telegram(header + body)

    # Gửi riêng từng tin mới sau 20 phút
    for item in all_items:
        threading.Thread(target=schedule_delayed_send, args=(item,)).start()

    logging.info("✅ Báo cáo 9h sáng & schedule gửi 20 phút đã hoàn tất.")

# ======================
# FLASK SERVER
# ======================
app = Flask(__name__)

@app.route("/")
def index():
    return "🚀 Miza Monitor v29 đang hoạt động ổn định (YouTube + TikTok + Facebook + News + Delay 20 phút)", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ======================
# MAIN
# ======================
def main():
    send_telegram("🚀 Miza Monitor v29 khởi động (YouTube + TikTok + Facebook + News + Gửi sau 20 phút).")
    logging.info("Bot started.")
    job_daily_report()
    schedule.every().day.at("09:00").do(job_daily_report)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    main()

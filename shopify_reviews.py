#!/usr/bin/env python3
"""
Shopify App Store Review Scraper v3

Features:
- Set number of reviews per app (or "all")
- Set number of apps to scrape
- Filter by star rating (1,2,3,4,5 or all)
- Pause / Resume / Export anytime during scrape
- playwright-stealth anti-detection
- Fast loading (block images/fonts, domcontentloaded)
- Random human-like delays

Usage:
    pip install playwright playwright-stealth
    playwright install chromium
    caffeinate -dims python3 shopify_reviews.py

Controls during scraping:
    p + Enter = Pause
    r + Enter = Resume
    e + Enter = Export CSV now (and continue)
    q + Enter = Export CSV and quit
"""

import csv
import re
import time
import sys
import os
import random
import threading
from datetime import datetime
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

stealth = Stealth()


BASE_URL = "https://apps.shopify.com"
NAV_TIMEOUT = 60000

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

BLOCKED_RESOURCES = {"image", "font", "stylesheet", "media"}


# ============================================================
#  Scrape State - shared state for pause/resume/export
# ============================================================
class ScrapeState:
    def __init__(self, keyword, star_filter, max_reviews_per_app):
        self.keyword = keyword
        self.star_filter = star_filter          # set of ints, e.g. {1,2,3,4,5} or {1}
        self.max_reviews_per_app = max_reviews_per_app  # None = all

        # Collected data
        self.all_data = []           # list of app dicts with reviews
        self.current_app = None      # app currently being scraped
        self.current_app_reviews = []  # reviews for current app

        # Control
        self.paused = threading.Event()
        self.paused.clear()          # not paused initially
        self.quit_requested = False
        self.export_requested = False
        self.lock = threading.Lock()

        # Stats
        self.total_reviews = 0
        self.export_count = 0

    def add_review(self, review):
        """Add a review if it passes the star filter."""
        star = review.get('rating', '')
        if star:
            try:
                if int(star) not in self.star_filter:
                    return False
            except ValueError:
                pass

        with self.lock:
            self.current_app_reviews.append(review)
            self.total_reviews += 1
        return True

    def reached_limit(self):
        """Check if current app reached the review limit."""
        if self.max_reviews_per_app is None:
            return False
        return len(self.current_app_reviews) >= self.max_reviews_per_app

    def start_app(self, app_info):
        """Start scraping a new app."""
        self.finish_current_app()
        with self.lock:
            self.current_app = app_info
            self.current_app_reviews = []

    def finish_current_app(self):
        """Finish current app and store results."""
        with self.lock:
            if self.current_app:
                self.all_data.append({
                    'name': self.current_app['name'],
                    'rating': self.current_app['rating'],
                    'review_count': self.current_app['review_count'],
                    'url': f"{BASE_URL}/{self.current_app['slug']}",
                    'reviews': list(self.current_app_reviews)
                })
                self.current_app = None
                self.current_app_reviews = []

    def get_all_data(self):
        """Get all data including current in-progress app."""
        with self.lock:
            result = list(self.all_data)
            if self.current_app and self.current_app_reviews:
                result.append({
                    'name': self.current_app['name'],
                    'rating': self.current_app['rating'],
                    'review_count': self.current_app['review_count'],
                    'url': f"{BASE_URL}/{self.current_app['slug']}",
                    'reviews': list(self.current_app_reviews)
                })
            return result

    def wait_if_paused(self):
        """Block if paused. Returns True if should continue, False if quit."""
        while self.paused.is_set():
            if self.quit_requested:
                return False
            time.sleep(0.2)
        return not self.quit_requested


# ============================================================
#  Control Thread - keyboard listener for pause/resume/export
# ============================================================
def control_thread(state):
    """Listen for keyboard commands in a separate thread."""
    while not state.quit_requested:
        try:
            cmd = input().strip().lower()
        except EOFError:
            break

        if cmd == 'p':
            state.paused.set()
            print("\n   ⏸️  PAUSED - Nhấn [r] resume, [e] export, [q] quit")
        elif cmd == 'r':
            state.paused.clear()
            print("\n   ▶️  RESUMED - Tiếp tục scraping...")
        elif cmd == 'e':
            state.export_requested = True
            print("\n   💾 Đang export CSV...")
        elif cmd == 'q':
            state.quit_requested = True
            state.paused.clear()  # unpause so main thread can exit
            print("\n   🛑 Đang dừng và export...")
            break


# ============================================================
#  Browser helpers
# ============================================================
def human_delay(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))

def short_delay():
    time.sleep(random.uniform(0.3, 0.8))

def block_resources(route, request):
    if request.resource_type in BLOCKED_RESOURCES:
        route.abort()
    else:
        route.continue_()

def create_stealth_context(browser):
    context = browser.new_context(
        viewport={"width": random.randint(1280, 1440), "height": random.randint(800, 900)},
        user_agent=random.choice(USER_AGENTS),
        locale="en-US",
        timezone_id="America/New_York",
    )
    return context

def create_stealth_page(context):
    page = context.new_page()
    stealth.apply_stealth_sync(page)
    page.route("**/*", block_resources)
    return page

def fast_goto(page, url, wait_selector=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                wait = random.uniform(3, 8) * (attempt + 1)
                print(f"\n   ⚠️  Timeout, thử lại sau {wait:.0f}s... (lần {attempt+2}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"\n   ❌ Không thể truy cập sau {max_retries} lần: {e}")
                raise


# ============================================================
#  Search apps
# ============================================================
def search_apps(page, keyword):
    url = f"{BASE_URL}/search?q={keyword.replace(' ', '+')}"
    print(f"\n🔍 Đang tìm kiếm: '{keyword}'...")
    fast_goto(page, url, wait_selector='a[href*="/apps/"]')
    human_delay(1.5, 2.5)

    for _ in range(5):
        scroll_amount = random.randint(600, 1000)
        page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        short_delay()

    apps = page.evaluate("""
    () => {
        const grid = document.querySelector('#search_app_grid-content, .search-results-component');
        if (!grid) return [];

        const allLinks = grid.querySelectorAll('a');
        const results = [];
        const seen = new Set();

        for (const a of allLinks) {
            const href = a.getAttribute('href') || '';
            const match = href.match(/apps\\.shopify\\.com\\/([a-z0-9][a-z0-9-]*)/) ||
                          href.match(/\\/apps\\/([a-z0-9][a-z0-9-]*)/);
            if (!match) continue;
            const slug = match[1];
            if (['search', 'categories', 'collections', 'partners'].includes(slug)) continue;
            if (seen.has(slug)) continue;

            const name = a.innerText.trim();
            if (!name || name.length < 2) continue;
            if (/learn more|opens in new|log in|start for free|browse/i.test(name)) continue;
            seen.add(slug);

            let card = a;
            for (let i = 0; i < 8; i++) {
                if (!card.parentElement) break;
                card = card.parentElement;
                if (card.innerText.includes('out of 5 stars')) break;
            }
            const cardText = card.innerText;

            const ratingMatch = cardText.match(/(\\d+\\.\\d+)\\s*\\n?\\s*out of 5 stars/) ||
                                cardText.match(/Overall rating\\s*\\n?(\\d+\\.\\d+)/) ||
                                cardText.match(/(\\d+\\.\\d+)\\s*out of 5/);
            const countMatch = cardText.match(/\\(([\\d,]+)\\)\\s*\\n?\\s*[\\d,]+ total reviews/) ||
                               cardText.match(/Reviews\\s*\\(([\\d,]+)\\)/) ||
                               cardText.match(/(\\d[\\d,]+)\\s*total reviews/);

            results.push({
                slug: slug,
                name: name,
                rating: ratingMatch ? ratingMatch[1] : '',
                review_count: countMatch ? countMatch[1].replace(/,/g, '') : ''
            });
        }
        return results;
    }
    """)
    return apps


# ============================================================
#  Extract & parse reviews
# ============================================================
def get_app_info_from_review_page(page):
    """Extract app rating and review count from the review page header."""
    return page.evaluate("""
    () => {
        const body = document.body.innerText;
        let rating = '';
        let reviewCount = '';

        const ariaEl = document.querySelector('[aria-label*="out of 5 stars"]');
        if (ariaEl) {
            const m = ariaEl.getAttribute('aria-label').match(/(\\d+\\.?\\d*)\\s*out of 5/);
            if (m) rating = m[1];
        }
        if (!rating) {
            const m = body.match(/Overall rating\\s*\\n?(\\d+\\.\\d+)/) ||
                      body.match(/(\\d+\\.\\d+)\\s*\\n?\\s*out of 5 stars/) ||
                      body.match(/(\\d+\\.\\d+)\\s*out of 5/);
            if (m) rating = m[1];
        }

        const cm = body.match(/Reviews\\s*\\(([\\d,]+)\\)/) ||
                   body.match(/\\(([\\d,]+)\\)\\s*\\n?\\s*[\\d,]+ total reviews/) ||
                   body.match(/(\\d[\\d,]+)\\s*total reviews/);
        if (cm) reviewCount = cm[1].replace(/,/g, '');

        return { rating: rating, review_count: reviewCount };
    }
    """)


def get_reviews_from_page(page):
    """Extract raw reviews from current page DOM."""
    reviews = page.evaluate("""
    () => {
        const results = [];
        const main = document.querySelector('main') || document.body;
        const allDivs = main.querySelectorAll('div');

        const dateRe = /(?:January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d{1,2},\\s+\\d{4}/;

        let bestContainer = null;
        let bestCount = 0;
        for (const div of allDivs) {
            let count = 0;
            for (const child of div.children) {
                const t = child.innerText || '';
                if (t.length > 50 && t.length < 5000 && dateRe.test(t) && t.includes('using the app')) {
                    count++;
                }
            }
            if (count > bestCount) {
                bestCount = count;
                bestContainer = div;
            }
        }

        if (!bestContainer || bestCount === 0) return results;

        for (const child of bestContainer.children) {
            const text = child.innerText || '';
            if (text.length < 50 || !dateRe.test(text)) continue;

            let stars = '';
            const starEl = child.querySelector('[aria-label*="out of 5 stars"]');
            if (starEl) {
                const m = starEl.getAttribute('aria-label').match(/(\\d) out of 5/);
                if (m) stars = m[1];
            }
            if (!stars) {
                const starEls = child.querySelectorAll('[aria-label*="star"]');
                for (const el of starEls) {
                    const m = el.getAttribute('aria-label').match(/(\\d)/);
                    if (m) { stars = m[1]; break; }
                }
            }

            results.push({ stars: stars, text: text });
        }
        return results;
    }
    """)
    return reviews


def parse_single_review(text, stars):
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 3:
        return None

    review = {
        'rating': stars,
        'date': '',
        'content': '',
        'reviewer': '',
        'location': '',
        'usage_time': ''
    }

    date_pattern = r'((?:Edited\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})'

    for i, line in enumerate(lines[:3]):
        m = re.match(date_pattern, line)
        if m:
            review['date'] = m.group(1)
            break

    for i, line in enumerate(lines):
        if 'using the app' in line.lower():
            review['usage_time'] = line.strip()
            if i >= 2:
                candidate = lines[i - 2]
                if len(candidate) < 100 and 'show more' not in candidate.lower() and 'show less' not in candidate.lower():
                    review['reviewer'] = candidate
            if i >= 1:
                review['location'] = lines[i - 1]
            break

    content_start = -1
    content_end = -1
    for i, line in enumerate(lines):
        if re.match(date_pattern, line) and content_start == -1:
            content_start = i + 1
        if ('show more' in line.lower() or 'show less' in line.lower()) and content_start != -1:
            content_end = i
            break

    if content_start == -1:
        content_start = 0

    if content_end == -1:
        for i, line in enumerate(lines):
            if 'using the app' in line.lower():
                content_end = i - 2
                break

    if content_end == -1:
        content_end = len(lines)

    if content_start < content_end:
        content_lines = []
        for line in lines[content_start:content_end]:
            if any(skip in line.lower() for skip in [
                'was this review helpful',
                'replied', 'reply',
                'out of 5 stars',
                'show more', 'show less'
            ]):
                continue
            content_lines.append(line)
        review['content'] = ' '.join(content_lines)

    return review if (review['content'] or review['date']) else None


# ============================================================
#  Scrape reviews for one app (with state awareness)
# ============================================================
def scrape_app_reviews(page, app_slug, state):
    """Scrape reviews for one app, respecting state (pause/quit/limit)."""
    review_url = f"{BASE_URL}/{app_slug}/reviews"

    print(f"   📖 Đang lấy reviews...")
    fast_goto(page, review_url, wait_selector='text=using the app')
    human_delay(1.0, 2.0)

    # Get rating/count from review page (fills in if search missed it)
    page_info = get_app_info_from_review_page(page)
    if state.current_app:
        if not state.current_app.get('rating') and page_info.get('rating'):
            state.current_app['rating'] = page_info['rating']
        if not state.current_app.get('review_count') and page_info.get('review_count'):
            state.current_app['review_count'] = page_info['review_count']

    page_num = 1
    max_pages = 500  # safety limit

    while page_num <= max_pages:
        # Check pause/quit
        if not state.wait_if_paused():
            return  # quit requested

        # Handle mid-scrape export
        if state.export_requested:
            do_export(state)
            state.export_requested = False

        # Check review limit
        if state.reached_limit():
            limit = state.max_reviews_per_app
            print(f"   ✅ Đã đạt giới hạn {limit} reviews")
            return

        print(f"      Trang {page_num}...", end=" ", flush=True)

        raw_reviews = get_reviews_from_page(page)
        added = 0
        skipped = 0

        for r in raw_reviews:
            if state.reached_limit():
                break
            if state.quit_requested:
                break

            parsed = parse_single_review(r['text'], r['stars'])
            if parsed:
                if state.add_review(parsed):
                    added += 1
                else:
                    skipped += 1

        total_app = len(state.current_app_reviews)
        if skipped > 0:
            print(f"+{added} (bỏ {skipped} ko đúng sao) (tổng app: {total_app})")
        else:
            print(f"+{added} (tổng app: {total_app})")

        if len(raw_reviews) == 0:
            break

        if state.reached_limit() or state.quit_requested:
            break

        # Check for next page
        next_url = page.evaluate("""
        () => {
            const links = document.querySelectorAll('a[aria-label="Go to Next Page"]');
            for (const link of links) {
                if (!link.hasAttribute('disabled') &&
                    link.getAttribute('aria-disabled') !== 'true') {
                    return link.getAttribute('href');
                }
            }
            return null;
        }
        """)

        if not next_url:
            print("   ✅ Hết trang review")
            break

        human_delay(1.0, 2.5)
        full_url = next_url if next_url.startswith('http') else BASE_URL + next_url
        fast_goto(page, full_url, wait_selector='text=using the app')
        short_delay()
        page_num += 1


# ============================================================
#  CSV Export
# ============================================================
def do_export(state, final=False):
    """Export current data to CSV."""
    data = state.get_all_data()
    if not data:
        print("   ⚠️  Chưa có dữ liệu để export")
        return None

    state.export_count += 1
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_keyword = re.sub(r'[^\w\s-]', '', state.keyword).replace(' ', '_')

    if final:
        filename = f"shopify_reviews_{safe_keyword}_{timestamp}_FINAL.csv"
    else:
        filename = f"shopify_reviews_{safe_keyword}_{timestamp}_part{state.export_count}.csv"

    total = 0
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow([
            'App Name', 'App Rating', 'App Review Count', 'App URL',
            'Reviewer', 'Location', 'Review Date', 'Star Rating',
            'Usage Time', 'Review Content'
        ])

        for app in data:
            if not app['reviews']:
                writer.writerow([
                    app['name'], app['rating'], app['review_count'],
                    app['url'], '', '', '', '', '', ''
                ])
                continue

            for review in app['reviews']:
                writer.writerow([
                    app['name'],
                    app['rating'],
                    app['review_count'],
                    app['url'],
                    review.get('reviewer', ''),
                    review.get('location', ''),
                    review.get('date', ''),
                    review.get('rating', ''),
                    review.get('usage_time', ''),
                    review.get('content', '')
                ])
                total += 1

    print(f"   💾 Exported: {filename} ({total} reviews)")
    return filename


# ============================================================
#  User Input Helpers
# ============================================================
def ask_num_apps(total):
    """Ask how many apps to scrape."""
    while True:
        raw = input(f"\n🔢 Số lượng app muốn lấy review (1-{total}, Enter = tất cả): ").strip()
        if not raw:
            return total
        try:
            n = int(raw)
            if 1 <= n <= total:
                return n
            print(f"   Nhập số từ 1 đến {total}")
        except ValueError:
            print("   Nhập một số hợp lệ!")


def ask_max_reviews():
    """Ask max reviews per app."""
    while True:
        raw = input("\n📊 Số review tối đa mỗi app (Enter = lấy tất cả): ").strip()
        if not raw:
            return None  # None = unlimited
        try:
            n = int(raw)
            if n > 0:
                return n
            print("   Nhập số lớn hơn 0")
        except ValueError:
            print("   Nhập một số hợp lệ!")


def ask_star_filter():
    """Ask which star ratings to include."""
    print("\n⭐ Lọc theo số sao:")
    print("   [Enter] Tất cả (1-5 sao)")
    print("   [5]     Chỉ 5 sao")
    print("   [1]     Chỉ 1 sao")
    print("   [1,2]   1 và 2 sao")
    print("   [1,2,3] 1, 2 và 3 sao")
    print("   ... bất kỳ tổ hợp nào")

    while True:
        raw = input("   Chọn: ").strip()
        if not raw:
            return {1, 2, 3, 4, 5}
        try:
            stars = set()
            for s in raw.replace(' ', '').split(','):
                n = int(s)
                if 1 <= n <= 5:
                    stars.add(n)
                else:
                    raise ValueError
            if stars:
                return stars
            print("   Nhập ít nhất 1 giá trị")
        except ValueError:
            print("   Nhập số từ 1-5, cách nhau bằng dấu phẩy. VD: 1,2,3")


# ============================================================
#  Main
# ============================================================
def main():
    print("=" * 60)
    print("  SHOPIFY APP STORE REVIEW SCRAPER v3")
    print("  (Stealth + Speed + Pause/Resume/Export)")
    print("=" * 60)

    keyword = input("\n📝 Nhập từ khóa tìm kiếm: ").strip()
    if not keyword:
        print("❌ Từ khóa không được để trống!")
        sys.exit(1)

    with sync_playwright() as p:
        print("\n🚀 Đang khởi động trình duyệt (stealth mode)...")
        browser = p.chromium.launch(headless=True)

        # --- Search ---
        context = create_stealth_context(browser)
        page = create_stealth_page(context)
        apps = search_apps(page, keyword)
        page.close()
        context.close()

        if not apps:
            print("❌ Không tìm thấy app nào!")
            browser.close()
            sys.exit(1)

        # --- Display apps ---
        print(f"\n📋 Tìm thấy {len(apps)} apps (từ trên xuống dưới):\n")
        for i, app in enumerate(apps):
            rating = app['rating'] or 'N/A'
            count = app['review_count'] or 'N/A'
            print(f"  {i+1:>2}. {app['name']}")
            print(f"      ⭐ {rating}/5  |  📝 {count} reviews")

        # --- Settings ---
        num_apps = ask_num_apps(len(apps))
        max_reviews = ask_max_reviews()
        star_filter = ask_star_filter()

        selected = apps[:num_apps]

        # --- Summary ---
        star_str = ','.join(str(s) for s in sorted(star_filter))
        review_str = str(max_reviews) if max_reviews else "tất cả"
        print(f"\n{'='*60}")
        print(f"  📱 Apps: {num_apps}")
        print(f"  📊 Reviews/app: {review_str}")
        print(f"  ⭐ Lọc sao: {star_str}")
        print(f"{'='*60}")
        print(f"\n  ⌨️  ĐIỀU KHIỂN trong khi chạy:")
        print(f"     p + Enter = Tạm dừng")
        print(f"     r + Enter = Tiếp tục")
        print(f"     e + Enter = Export CSV (tiếp tục chạy)")
        print(f"     q + Enter = Dừng + Export CSV")
        print(f"{'='*60}\n")

        # --- Create state ---
        state = ScrapeState(keyword, star_filter, max_reviews)

        # --- Start control thread ---
        ctrl = threading.Thread(target=control_thread, args=(state,), daemon=True)
        ctrl.start()

        # --- Scrape ---
        for i, app in enumerate(selected):
            if state.quit_requested:
                break

            print(f"\n[{i+1}/{num_apps}] 📱 {app['name']}")
            state.start_app(app)

            try:
                context = create_stealth_context(browser)
                page = create_stealth_page(context)

                scrape_app_reviews(page, app['slug'], state)

                page.close()
                context.close()
            except Exception as e:
                print(f"   ❌ Lỗi: {e}")

            # Handle export request between apps
            if state.export_requested:
                state.finish_current_app()
                do_export(state)
                state.export_requested = False
                state.start_app(app)  # re-set (already finished)

            review_count = len(state.current_app_reviews)
            print(f"   ✅ App xong: {review_count} reviews")

            # Pause between apps (anti-detection)
            if i < len(selected) - 1 and not state.quit_requested:
                pause = random.uniform(2, 5)
                print(f"   ⏳ Nghỉ {pause:.0f}s trước app tiếp...")
                time.sleep(pause)

        # Finish last app
        state.finish_current_app()
        browser.close()

    # --- Final export ---
    filename = do_export(state, final=True)

    total_reviews = sum(len(app['reviews']) for app in state.all_data)
    total_apps = len(state.all_data)

    print(f"\n{'='*60}")
    print(f"  ✅ HOÀN TẤT!")
    print(f"  📊 Tổng: {total_reviews} reviews từ {total_apps} apps")
    if filename:
        print(f"  📁 File: {filename}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

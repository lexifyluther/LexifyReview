#!/usr/bin/env python3
"""
LexifyReview - Shopify App Store Review Scraper (GUI)

Usage:
    pip install playwright playwright-stealth
    playwright install chromium
    python3 shopify_reviews_gui.py
"""

import csv
import re
import time
import sys
import random
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

stealth = Stealth()


BASE_URL = "https://apps.shopify.com"
NAV_TIMEOUT = 60000

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

BLOCKED_RESOURCES = {"image", "font", "stylesheet", "media"}


# ============================================================
#  Browser helpers (same as CLI version)
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
    return browser.new_context(
        viewport={"width": random.randint(1280, 1440), "height": random.randint(800, 900)},
        user_agent=random.choice(USER_AGENTS),
        locale="en-US",
        timezone_id="America/New_York",
    )

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
                time.sleep(random.uniform(3, 8) * (attempt + 1))
            else:
                raise


# ============================================================
#  Scraping logic
# ============================================================
def search_apps(page, keyword):
    url = f"{BASE_URL}/search?q={keyword.replace(' ', '+')}"
    fast_goto(page, url, wait_selector='a[href*="/apps/"]')
    human_delay(1.5, 2.5)

    for _ in range(5):
        page.evaluate(f"window.scrollBy(0, {random.randint(600, 1000)})")
        short_delay()

    return page.evaluate("""
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
            // Multiple patterns to handle different renders (with/without CSS)
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


def get_app_info_from_review_page(page):
    """Extract app rating and review count from the review page header."""
    return page.evaluate("""
    () => {
        const body = document.body.innerText;
        let rating = '';
        let reviewCount = '';

        // Rating: try aria-label first (most reliable)
        const ariaEl = document.querySelector('[aria-label*="out of 5 stars"]');
        if (ariaEl) {
            const m = ariaEl.getAttribute('aria-label').match(/(\\d+\\.?\\d*)\\s*out of 5/);
            if (m) rating = m[1];
        }
        // Fallback: text patterns
        if (!rating) {
            const m = body.match(/Overall rating\\s*\\n?(\\d+\\.\\d+)/) ||
                      body.match(/(\\d+\\.\\d+)\\s*\\n?\\s*out of 5 stars/) ||
                      body.match(/(\\d+\\.\\d+)\\s*out of 5/);
            if (m) rating = m[1];
        }

        // Review count: "Reviews (1,805)" or "(1,805)\\n1805 total reviews"
        const cm = body.match(/Reviews\\s*\\(([\\d,]+)\\)/) ||
                   body.match(/\\(([\\d,]+)\\)\\s*\\n?\\s*[\\d,]+ total reviews/) ||
                   body.match(/(\\d[\\d,]+)\\s*total reviews/);
        if (cm) reviewCount = cm[1].replace(/,/g, '');

        return { rating: rating, review_count: reviewCount };
    }
    """)


def get_reviews_from_page(page):
    return page.evaluate("""
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
                if (t.length > 50 && t.length < 5000 && dateRe.test(t) && t.includes('using the app')) count++;
            }
            if (count > bestCount) { bestCount = count; bestContainer = div; }
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


def parse_single_review(text, stars):
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 3:
        return None
    review = {'rating': stars, 'date': '', 'content': '', 'reviewer': '', 'location': '', 'usage_time': ''}
    date_pattern = r'((?:Edited\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})'
    for line in lines[:3]:
        m = re.match(date_pattern, line)
        if m:
            review['date'] = m.group(1)
            break
    for i, line in enumerate(lines):
        if 'using the app' in line.lower():
            review['usage_time'] = line.strip()
            if i >= 2:
                candidate = lines[i - 2]
                if len(candidate) < 100 and 'show more' not in candidate.lower():
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
        content_lines = [l for l in lines[content_start:content_end]
                         if not any(s in l.lower() for s in ['was this review helpful', 'replied', 'reply', 'out of 5 stars', 'show more', 'show less'])]
        review['content'] = ' '.join(content_lines)
    return review if (review['content'] or review['date']) else None


# ============================================================
#  GUI Application
# ============================================================
class ShopifyScraperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LexifyReview v1.2 - Shopify App Store Review Scraper")
        self.root.geometry("880x720")
        self.root.minsize(800, 650)

        # State
        self.apps = []
        self.all_data = []
        self.current_reviews = []
        self.total_collected = 0
        self.is_running = False
        self.is_paused = False
        self.quit_flag = False
        self.browser = None
        self.pw = None

        self.build_ui()

    def build_ui(self):
        # --- Top: Search ---
        frm_search = ttk.LabelFrame(self.root, text="  1. Tìm kiếm  ", padding=10)
        frm_search.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(frm_search, text="Từ khóa:").pack(side="left")
        self.entry_keyword = ttk.Entry(frm_search, width=40, font=("", 13))
        self.entry_keyword.pack(side="left", padx=(8, 8))
        self.entry_keyword.bind("<Return>", lambda e: self.do_search())

        self.btn_search = ttk.Button(frm_search, text="🔍 Tìm kiếm", command=self.do_search)
        self.btn_search.pack(side="left")

        self.lbl_search_status = ttk.Label(frm_search, text="", foreground="gray")
        self.lbl_search_status.pack(side="left", padx=(12, 0))

        # --- Middle: App list (read-only display) ---
        frm_apps = ttk.LabelFrame(self.root, text="  2. Danh sách apps (từ trên xuống dưới)  ", padding=10)
        frm_apps.pack(fill="both", expand=True, padx=10, pady=5)

        # Treeview table for app list
        columns = ("stt", "name", "rating", "reviews")
        self.tree_apps = ttk.Treeview(frm_apps, columns=columns, show="headings", height=6)
        self.tree_apps.heading("stt", text="#")
        self.tree_apps.heading("name", text="Tên App")
        self.tree_apps.heading("rating", text="⭐ Rating")
        self.tree_apps.heading("reviews", text="📝 Số Reviews")
        self.tree_apps.column("stt", width=40, anchor="center")
        self.tree_apps.column("name", width=400)
        self.tree_apps.column("rating", width=100, anchor="center")
        self.tree_apps.column("reviews", width=120, anchor="center")

        tree_scroll = ttk.Scrollbar(frm_apps, orient="vertical", command=self.tree_apps.yview)
        self.tree_apps.configure(yscrollcommand=tree_scroll.set)
        self.tree_apps.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # --- Settings ---
        frm_settings = ttk.LabelFrame(self.root, text="  3. Cài đặt  ", padding=10)
        frm_settings.pack(fill="x", padx=10, pady=5)

        row0 = ttk.Frame(frm_settings)
        row0.pack(fill="x", pady=(0, 8))

        ttk.Label(row0, text="Số app muốn lấy:").pack(side="left")
        self.entry_num_apps = ttk.Entry(row0, width=10, font=("", 13))
        self.entry_num_apps.pack(side="left", padx=(8, 0))
        self.lbl_num_apps_hint = ttk.Label(row0, text="(để trống = tất cả, lấy từ trên xuống)", foreground="gray")
        self.lbl_num_apps_hint.pack(side="left", padx=(8, 0))

        row1 = ttk.Frame(frm_settings)
        row1.pack(fill="x", pady=(0, 8))

        ttk.Label(row1, text="Reviews tối đa / app:").pack(side="left")
        self.entry_max_reviews = ttk.Entry(row1, width=10, font=("", 13))
        self.entry_max_reviews.pack(side="left", padx=(8, 0))
        ttk.Label(row1, text="(để trống = lấy tất cả)", foreground="gray").pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(frm_settings)
        row2.pack(fill="x")

        ttk.Label(row2, text="Lọc theo sao:").pack(side="left")
        self.star_vars = {}
        for s in [1, 2, 3, 4, 5]:
            var = tk.BooleanVar(value=True)
            self.star_vars[s] = var
            cb = ttk.Checkbutton(row2, text=f"{'⭐' * s}", variable=var)
            cb.pack(side="left", padx=(10, 0))

        # --- Controls ---
        frm_controls = ttk.Frame(self.root, padding=(10, 5))
        frm_controls.pack(fill="x")

        self.btn_start = ttk.Button(frm_controls, text="▶  Bắt đầu", command=self.do_start)
        self.btn_start.pack(side="left", padx=(0, 5))

        self.btn_pause = ttk.Button(frm_controls, text="⏸  Tạm dừng", command=self.do_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(0, 5))

        self.btn_export = ttk.Button(frm_controls, text="💾 Export CSV", command=self.do_export, state="disabled")
        self.btn_export.pack(side="left", padx=(0, 5))

        self.btn_stop = ttk.Button(frm_controls, text="⏹  Dừng", command=self.do_stop, state="disabled")
        self.btn_stop.pack(side="left")

        # Progress
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(frm_controls, variable=self.progress_var, maximum=100, length=200)
        self.progress.pack(side="right", padx=(10, 0))

        self.lbl_stats = ttk.Label(frm_controls, text="", font=("", 11, "bold"))
        self.lbl_stats.pack(side="right", padx=(10, 0))

        # --- Log ---
        frm_log = ttk.LabelFrame(self.root, text="  Log  ", padding=5)
        frm_log.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.txt_log = tk.Text(frm_log, height=8, font=("Menlo", 11), wrap="word",
                               bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        log_scroll = ttk.Scrollbar(frm_log, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_scroll.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.log("Sẵn sàng. Nhập từ khóa và nhấn Tìm kiếm.")

    # --- Logging ---
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.txt_log.insert("end", f"[{timestamp}] {msg}\n")
        self.txt_log.see("end")

    def update_stats(self):
        self.lbl_stats.config(text=f"📊 {self.total_collected} reviews")

    # --- App list management ---
    def populate_app_list(self, apps):
        # Clear old rows
        for item in self.tree_apps.get_children():
            self.tree_apps.delete(item)

        for i, app in enumerate(apps):
            rating = app.get('rating') or 'N/A'
            count = app.get('review_count') or 'N/A'
            self.tree_apps.insert("", "end", values=(i + 1, app['name'], f"{rating}/5", count))

        # Pre-fill num_apps with total
        self.entry_num_apps.delete(0, "end")
        self.lbl_num_apps_hint.config(text=f"(1-{len(apps)}, để trống = tất cả {len(apps)} apps)")

    # --- Search ---
    def do_search(self):
        keyword = self.entry_keyword.get().strip()
        if not keyword:
            messagebox.showwarning("Lỗi", "Nhập từ khóa tìm kiếm!")
            return

        self.btn_search.config(state="disabled")
        self.lbl_search_status.config(text="Đang tìm...")
        self.log(f"🔍 Đang tìm kiếm: '{keyword}'...")

        threading.Thread(target=self._search_worker, args=(keyword,), daemon=True).start()

    def _search_worker(self, keyword):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = create_stealth_context(browser)
                page = create_stealth_page(context)
                apps = search_apps(page, keyword)
                page.close()
                context.close()
                browser.close()

            self.apps = apps
            self.root.after(0, self._search_done)
        except Exception as e:
            self.root.after(0, lambda: self._search_error(str(e)))

    def _search_done(self):
        self.btn_search.config(state="normal")
        if not self.apps:
            self.lbl_search_status.config(text="Không tìm thấy app nào!")
            self.log("❌ Không tìm thấy app nào!")
            return
        self.lbl_search_status.config(text=f"Tìm thấy {len(self.apps)} apps")
        self.log(f"✅ Tìm thấy {len(self.apps)} apps")
        self.populate_app_list(self.apps)

    def _search_error(self, error):
        self.btn_search.config(state="normal")
        self.lbl_search_status.config(text="Lỗi!")
        self.log(f"❌ Lỗi tìm kiếm: {error}")

    # --- Get selected settings ---
    def get_num_apps(self):
        """Get number of apps to scrape (top N from list)."""
        raw = self.entry_num_apps.get().strip()
        if not raw:
            return len(self.apps)
        try:
            n = int(raw)
            return max(1, min(n, len(self.apps)))
        except ValueError:
            return len(self.apps)

    def get_star_filter(self):
        return {s for s, var in self.star_vars.items() if var.get()}

    def get_max_reviews(self):
        raw = self.entry_max_reviews.get().strip()
        if not raw:
            return None
        try:
            n = int(raw)
            return n if n > 0 else None
        except ValueError:
            return None

    # --- Start scraping ---
    def do_start(self):
        if not self.apps:
            messagebox.showwarning("Lỗi", "Hãy tìm kiếm trước!")
            return

        num_apps = self.get_num_apps()
        selected = self.apps[:num_apps]  # Lấy từ trên xuống dưới

        star_filter = self.get_star_filter()
        if not star_filter:
            messagebox.showwarning("Lỗi", "Chọn ít nhất 1 mức sao!")
            return

        self.is_running = True
        self.is_paused = False
        self.quit_flag = False
        self.all_data = []
        self.current_reviews = []
        self.total_collected = 0
        self.progress_var.set(0)

        self.btn_start.config(state="disabled")
        self.btn_search.config(state="disabled")
        self.btn_pause.config(state="normal")
        self.btn_export.config(state="normal")
        self.btn_stop.config(state="normal")

        max_reviews = self.get_max_reviews()
        star_str = ','.join(str(s) for s in sorted(star_filter))
        review_str = str(max_reviews) if max_reviews else "tất cả"
        self.log(f"▶ Bắt đầu: {num_apps} apps (top {num_apps}), {review_str} reviews/app, sao: {star_str}")

        # Highlight selected rows in treeview
        all_items = self.tree_apps.get_children()
        for idx, item in enumerate(all_items):
            if idx < num_apps:
                self.tree_apps.item(item, tags=("selected",))
            else:
                self.tree_apps.item(item, tags=("dimmed",))
        self.tree_apps.tag_configure("selected", background="#d4edda")
        self.tree_apps.tag_configure("dimmed", background="#f0f0f0", foreground="#aaaaaa")

        threading.Thread(
            target=self._scrape_worker,
            args=(selected, star_filter, max_reviews),
            daemon=True
        ).start()

    def _scrape_worker(self, selected_apps, star_filter, max_reviews):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)

                for app_idx, app in enumerate(selected_apps):
                    if self.quit_flag:
                        break

                    self.root.after(0, lambda a=app, i=app_idx, t=len(selected_apps):
                                    self.log(f"\n[{i+1}/{t}] 📱 {a['name']}"))

                    self.current_reviews = []

                    try:
                        context = create_stealth_context(browser)
                        page = create_stealth_page(context)

                        # Build URL with star filter
                        rating_params = '&'.join(f'ratings%5B%5D={s}' for s in sorted(star_filter))
                        if star_filter == {1, 2, 3, 4, 5}:
                            review_url = f"{BASE_URL}/{app['slug']}/reviews"
                        else:
                            review_url = f"{BASE_URL}/{app['slug']}/reviews?{rating_params}"
                        fast_goto(page, review_url, wait_selector='text=using the app')
                        human_delay(1.0, 2.0)

                        # Get rating/count from review page (fills in if search missed it)
                        page_info = get_app_info_from_review_page(page)
                        if page_info.get('rating') and not app.get('rating'):
                            app['rating'] = page_info['rating']
                        if page_info.get('review_count') and not app.get('review_count'):
                            app['review_count'] = page_info['review_count']
                        # Always overwrite if search had empty values
                        if not app.get('rating'):
                            app['rating'] = page_info.get('rating', '')
                        if not app.get('review_count'):
                            app['review_count'] = page_info.get('review_count', '')

                        page_num = 1
                        while page_num <= 500:
                            # Pause check
                            while self.is_paused and not self.quit_flag:
                                time.sleep(0.2)
                            if self.quit_flag:
                                break

                            # Limit check
                            if max_reviews and len(self.current_reviews) >= max_reviews:
                                self.root.after(0, lambda mr=max_reviews:
                                                self.log(f"   ✅ Đạt giới hạn {mr} reviews"))
                                break

                            self.root.after(0, lambda pn=page_num: self.log(f"   Trang {pn}..."))

                            raw_reviews = get_reviews_from_page(page)
                            added = 0

                            for r in raw_reviews:
                                if max_reviews and len(self.current_reviews) >= max_reviews:
                                    break
                                if self.quit_flag:
                                    break

                                parsed = parse_single_review(r['text'], r['stars'])
                                if not parsed:
                                    continue

                                # Star filter
                                try:
                                    if int(parsed['rating']) not in star_filter:
                                        continue
                                except (ValueError, KeyError):
                                    pass

                                self.current_reviews.append(parsed)
                                self.total_collected += 1
                                added += 1

                            self.root.after(0, lambda a=added, t=len(self.current_reviews):
                                            self.log(f"      +{a} reviews (app: {t})"))
                            self.root.after(0, self.update_stats)

                            # Update progress
                            progress = ((app_idx + (page_num / 50)) / len(selected_apps)) * 100
                            self.root.after(0, lambda v=min(progress, 99): self.progress_var.set(v))

                            if len(raw_reviews) == 0:
                                break

                            if max_reviews and len(self.current_reviews) >= max_reviews:
                                break

                            # Next page
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
                                self.root.after(0, lambda: self.log("   ✅ Hết trang"))
                                break

                            human_delay(1.0, 2.5)
                            full_url = next_url if next_url.startswith('http') else BASE_URL + next_url
                            fast_goto(page, full_url, wait_selector='text=using the app')
                            short_delay()
                            page_num += 1

                        page.close()
                        context.close()

                    except Exception as e:
                        self.root.after(0, lambda err=str(e): self.log(f"   ❌ Lỗi: {err}"))

                    # Save app data
                    self.all_data.append({
                        'name': app['name'],
                        'rating': app['rating'],
                        'review_count': app['review_count'],
                        'url': f"{BASE_URL}/{app['slug']}",
                        'reviews': list(self.current_reviews)
                    })

                    count = len(self.current_reviews)
                    self.root.after(0, lambda c=count, n=app['name']:
                                    self.log(f"   ✅ {n}: {c} reviews"))

                    # Pause between apps
                    if app_idx < len(selected_apps) - 1 and not self.quit_flag:
                        time.sleep(random.uniform(2, 5))

                browser.close()

        except Exception as e:
            self.root.after(0, lambda err=str(e): self.log(f"❌ Lỗi nghiêm trọng: {err}"))

        self.root.after(0, self._scrape_done)

    def _scrape_done(self):
        self.is_running = False
        self.progress_var.set(100)
        self.btn_start.config(state="normal")
        self.btn_search.config(state="normal")
        self.btn_pause.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.btn_export.config(state="normal" if self.all_data else "disabled")

        total = sum(len(a['reviews']) for a in self.all_data)
        self.log(f"\n{'='*40}")
        self.log(f"✅ HOÀN TẤT: {total} reviews từ {len(self.all_data)} apps")
        self.log(f"Nhấn 💾 Export CSV để lưu file.")
        self.update_stats()

        if self.all_data:
            if messagebox.askyesno("Hoàn tất", f"Đã lấy {total} reviews.\nExport CSV ngay?"):
                self.do_export()

    # --- Pause / Resume ---
    def do_pause(self):
        if self.is_paused:
            self.is_paused = False
            self.btn_pause.config(text="⏸  Tạm dừng")
            self.log("▶ Tiếp tục...")
        else:
            self.is_paused = True
            self.btn_pause.config(text="▶  Tiếp tục")
            self.log("⏸ Tạm dừng. Nhấn lại để tiếp tục.")

    # --- Stop ---
    def do_stop(self):
        self.quit_flag = True
        self.is_paused = False
        self.log("🛑 Đang dừng...")

    # --- Export ---
    def do_export(self):
        data = list(self.all_data)
        # Include current in-progress app
        if self.is_running and self.current_reviews:
            pass  # current_reviews will be saved when app finishes

        if not data:
            messagebox.showinfo("Export", "Chưa có dữ liệu để export!")
            return

        keyword = self.entry_keyword.get().strip() or "export"
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_keyword = re.sub(r'[^\w\s-]', '', keyword).replace(' ', '_')
        default_name = f"shopify_reviews_{safe_keyword}_{timestamp}.csv"

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=default_name,
            title="Lưu file CSV"
        )

        if not filepath:
            return

        total = 0
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'App Name', 'App Rating', 'App Review Count', 'App URL',
                'Reviewer', 'Location', 'Review Date', 'Star Rating',
                'Usage Time', 'Review Content'
            ])
            for app in data:
                if not app['reviews']:
                    writer.writerow([app['name'], app['rating'], app['review_count'], app['url'],
                                     '', '', '', '', '', ''])
                    continue
                for review in app['reviews']:
                    writer.writerow([
                        app['name'], app['rating'], app['review_count'], app['url'],
                        review.get('reviewer', ''), review.get('location', ''),
                        review.get('date', ''), review.get('rating', ''),
                        review.get('usage_time', ''), review.get('content', '')
                    ])
                    total += 1

        self.log(f"💾 Đã lưu: {filepath} ({total} reviews)")
        messagebox.showinfo("Export thành công", f"Đã lưu {total} reviews\n{filepath}")


# ============================================================
#  Main
# ============================================================
def main():
    root = tk.Tk()

    # Style
    style = ttk.Style()
    try:
        style.theme_use("aqua")  # macOS native look
    except Exception:
        try:
            style.theme_use("clam")
        except Exception:
            pass

    app = ShopifyScraperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

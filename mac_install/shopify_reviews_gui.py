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
def human_delay(min_s=1.0, max_s=3.0, stop_flag=None):
    total = random.uniform(min_s, max_s)
    end = time.time() + total
    while time.time() < end:
        if stop_flag and stop_flag():
            return
        time.sleep(0.1)

def short_delay(stop_flag=None):
    total = random.uniform(0.3, 0.8)
    end = time.time() + total
    while time.time() < end:
        if stop_flag and stop_flag():
            return
        time.sleep(0.1)

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

def fast_goto(page, url, wait_selector=None, max_retries=3, stop_flag=None):
    for attempt in range(max_retries):
        if stop_flag and stop_flag():
            return False
        try:
            # Use shorter timeout (15s) so stop_flag can be checked sooner
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=5000)
                except Exception:
                    pass
            return True
        except Exception as e:
            if stop_flag and stop_flag():
                return False
            if attempt < max_retries - 1:
                for _ in range(int(random.uniform(2, 5) * (attempt + 1) * 10)):
                    if stop_flag and stop_flag():
                        return False
                    time.sleep(0.1)
            else:
                raise


# ============================================================
#  Scraping logic
# ============================================================
EXTRACT_APPS_JS = """
(useGrid) => {
    const root = useGrid
        ? (document.querySelector('#search_app_grid-content, .search-results-component') || document.body)
        : document.body;
    const allLinks = root.querySelectorAll('a');
    const results = [];
    const seen = new Set();
    for (const a of allLinks) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/apps\\.shopify\\.com\\/([a-z0-9][a-z0-9-]*)/) ||
                      href.match(/\\/apps\\/([a-z0-9][a-z0-9-]*)/);
        if (!match) continue;
        const slug = match[1];
        if (['search', 'categories', 'collections', 'partners', 'blog', 'app-store'].includes(slug)) continue;
        if (seen.has(slug)) continue;
        const name = a.innerText.trim();
        if (!name || name.length < 2) continue;
        if (/learn more|opens in new|log in|start for free|browse|sign up|try shopify/i.test(name)) continue;
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
"""


EXTRACT_APP_INFO_JS = """
() => {
    const body = document.body.innerText || '';
    const result = {
        app_name: '', rating: '', review_count: '', pricing: '',
        developer_name: '', developer_website: '',
        launched_date: '', languages: '', demo_store_url: '',
        merchants_think: '', pricing_model: '',
        plan_names: '', plan_prices: '', plan_details: '',
        has_transaction_fees: '', additional_charges: ''
    };

    // App name
    const h1 = document.querySelector('h1');
    if (h1) result.app_name = h1.innerText.trim();

    // Rating and review count from hero section
    const dtsHero = document.querySelectorAll('dt');
    for (const dt of dtsHero) {
        const label = dt.innerText.trim();
        const dd = dt.nextElementSibling;
        if (!dd) continue;
        if (label === 'Rating') {
            const ratingSpan = dd.querySelector('span');
            if (ratingSpan) result.rating = ratingSpan.innerText.trim();
            const reviewLink = dd.querySelector('a');
            if (reviewLink) {
                const m = reviewLink.innerText.match(/([\\d,]+)/);
                if (m) result.review_count = m[1].replace(/,/g, '');
            }
        }
        if (label === 'Pricing') {
            result.pricing = dd.innerText.trim();
        }
    }
    // Rating fallback from aria-label
    if (!result.rating) {
        const ariaEl = document.querySelector('[aria-label*="out of 5 stars"]');
        if (ariaEl) {
            const m = ariaEl.getAttribute('aria-label').match(/(\\d+\\.?\\d*)\\s*out of 5/);
            if (m) result.rating = m[1];
        }
    }

    // Developer section (#adp-developer)
    const devSection = document.querySelector('#adp-developer, section[id*="developer"]');
    if (devSection) {
        const devText = devSection.innerText || '';

        // Launched date
        const launchMatch = devText.match(/Launched\\s*\\n\\s*([A-Z][a-z]+ \\d{1,2}, \\d{4})/);
        if (launchMatch) result.launched_date = launchMatch[1];

        // Developer name
        const devNameEl = devSection.querySelector('a[href*="/partners/"]');
        if (devNameEl) result.developer_name = devNameEl.innerText.trim();

        // Developer website
        const websiteLinks = devSection.querySelectorAll('a[target="_blank"]');
        for (const a of websiteLinks) {
            if (a.innerText.trim().toLowerCase() === 'website') {
                result.developer_website = a.getAttribute('href') || '';
                break;
            }
        }

        // Languages
        const langMatch = devText.match(/Languages\\s*\\n\\s*([^\\n]+)/);
        if (langMatch) {
            result.languages = langMatch[1].trim();
        }
    }

    // Also check hero section for developer info
    if (!result.developer_name) {
        const dts = document.querySelectorAll('dt');
        for (const dt of dts) {
            if (dt.innerText.trim() === 'Developer') {
                const dd = dt.nextElementSibling;
                if (dd) {
                    const a = dd.querySelector('a');
                    if (a) result.developer_name = a.innerText.trim();
                }
            }
        }
    }

    // Languages fallback - check all grid/detail sections
    if (!result.languages) {
        const allPs = document.querySelectorAll('p');
        for (let i = 0; i < allPs.length; i++) {
            if (allPs[i].innerText.trim() === 'Languages' && i + 1 < allPs.length) {
                // Next sibling or next p might have the value
                const next = allPs[i].nextElementSibling || allPs[i + 1];
                if (next) {
                    const langText = next.innerText.trim();
                    if (langText && langText !== 'Languages') {
                        result.languages = langText;
                    }
                }
                break;
            }
        }
    }

    // Demo store URL
    const allLinks = document.querySelectorAll('a');
    for (const a of allLinks) {
        if (a.innerText.trim().toLowerCase().includes('view demo store')) {
            let href = a.getAttribute('href') || '';
            // Clean up tracking params
            if (href.includes('myshopify.com')) {
                const url = href.split('?')[0];
                result.demo_store_url = url;
            } else {
                result.demo_store_url = href;
            }
            break;
        }
    }

    // What merchants think
    const thinkEl = document.querySelector('p[data-truncate-content-copy]');
    if (thinkEl) {
        result.merchants_think = thinkEl.innerText.trim();
    } else {
        // Fallback: look for "What merchants think" heading
        const headings = document.querySelectorAll('h5, h4, h3');
        for (const h of headings) {
            if (h.innerText.includes('What merchants think')) {
                let container = h.parentElement;
                for (let i = 0; i < 3 && container; i++) {
                    const p = container.querySelector('p');
                    if (p && p.innerText.length > 50) {
                        result.merchants_think = p.innerText.trim();
                        break;
                    }
                    container = container.parentElement;
                }
                break;
            }
        }
    }

    // Pricing section
    const pricingSection = document.querySelector('#adp-pricing, section[id*="pricing"]');
    if (pricingSection) {
        const planCards = pricingSection.querySelectorAll('[class*="pricing-plan-card"], [class*="plan-card"]');
        const planNames = [];
        const planPrices = [];
        const planDetailsList = [];
        const additionalChargesList = [];
        let hasTransactionFees = false;

        if (planCards.length > 0) {
            for (const card of planCards) {
                // Plan name
                const nameEl = card.querySelector('[data-test-id="name"]');
                const name = nameEl ? nameEl.innerText.trim() : '';
                planNames.push(name);

                // Price
                const priceGroup = card.querySelector('[class*="pricing-format-group"], h3[aria-label]');
                let price = '';
                if (priceGroup) {
                    price = priceGroup.getAttribute('aria-label') || priceGroup.innerText.trim();
                } else {
                    const priceEl = card.querySelector('[data-test-id="price"]');
                    if (priceEl) price = priceEl.innerText.trim();
                }
                planPrices.push(price);

                // Additional charges
                const addChargeEl = card.querySelector('[data-test-id="additional-charges"]');
                if (addChargeEl) {
                    const chargeText = addChargeEl.innerText.trim();
                    additionalChargesList.push(chargeText);
                    if (/transaction|commission|%|GMV|revenue share/i.test(chargeText)) {
                        hasTransactionFees = true;
                    }
                }

                // Features
                const featuresEl = card.querySelector('[data-test-id="features"]');
                if (featuresEl) {
                    const features = Array.from(featuresEl.querySelectorAll('li'))
                        .map(li => li.innerText.trim())
                        .filter(t => t);
                    planDetailsList.push(name + ': ' + features.join('; '));
                }
            }
        } else {
            // Fallback: parse pricing from text
            const pText = pricingSection.innerText || '';
            const priceMatches = pText.match(/\\$[\\d,.]+\\/month|Free( to install)?|Free plan available/gi);
            if (priceMatches) {
                priceMatches.forEach(p => planPrices.push(p));
            }
        }

        result.plan_names = planNames.join(', ');
        result.plan_prices = planPrices.join(', ');
        result.plan_details = planDetailsList.join(' | ');
        result.additional_charges = additionalChargesList.join(', ');
        result.has_transaction_fees = hasTransactionFees ? 'Yes' : 'No';

        // Check footer text for transaction/usage fees
        const footerText = pricingSection.innerText || '';
        if (/usage-based|transaction fee|commission|% of|GMV/i.test(footerText)) {
            result.has_transaction_fees = 'Yes';
        }

        // Determine pricing model
        const allPricesLower = planPrices.join(' ').toLowerCase();
        if (planPrices.length === 0 || allPricesLower === '') {
            result.pricing_model = 'Unknown';
        } else if (planPrices.length === 1 && /free/i.test(allPricesLower) && !/\\$/i.test(allPricesLower)) {
            result.pricing_model = 'Free';
        } else if (/free/i.test(allPricesLower) && /\\$/i.test(allPricesLower)) {
            result.pricing_model = 'Freemium';
        } else if (/\\$/i.test(allPricesLower)) {
            result.pricing_model = 'Paid';
        } else {
            result.pricing_model = 'Free';
        }

        if (result.has_transaction_fees === 'Yes') {
            result.pricing_model += ' + Usage-based';
        }
    } else {
        // Check hero pricing
        const dts = document.querySelectorAll('dt');
        for (const dt of dts) {
            if (dt.innerText.trim() === 'Pricing') {
                const dd = dt.nextElementSibling;
                if (dd) {
                    const pricingText = dd.innerText.trim();
                    result.plan_prices = pricingText;
                    if (/free/i.test(pricingText) && !/\\$/i.test(pricingText)) {
                        result.pricing_model = 'Free';
                    } else if (/free/i.test(pricingText)) {
                        result.pricing_model = 'Freemium';
                    } else {
                        result.pricing_model = 'Paid';
                    }
                }
            }
        }
    }

    return result;
}
"""


def search_apps(page, keyword, log_fn=None, max_pages=200, max_apps=None, stop_flag=None):
    """Search apps with pagination support. Returns all apps across pages."""
    all_apps = []
    seen_slugs = set()
    url = f"{BASE_URL}/search?q={keyword.replace(' ', '+')}"

    for page_num in range(1, max_pages + 1):
        if stop_flag and stop_flag():
            if log_fn:
                log_fn(f"   ⏹ Dừng tìm kiếm tại trang {page_num}")
            break

        if max_apps and len(all_apps) >= max_apps:
            if log_fn:
                log_fn(f"   ✅ Đạt giới hạn {max_apps} apps")
            break

        page_url = url if page_num == 1 else f"{url}&page={page_num}"
        if not fast_goto(page, page_url, wait_selector='a[href*="/apps/"]', stop_flag=stop_flag):
            if log_fn:
                log_fn(f"   ⏹ Dừng tìm kiếm tại trang {page_num}")
            break

        if stop_flag and stop_flag():
            break
        human_delay(1.0, 2.0, stop_flag=stop_flag)
        if stop_flag and stop_flag():
            break

        for _ in range(3):
            if stop_flag and stop_flag():
                break
            page.evaluate(f"window.scrollBy(0, {random.randint(600, 1000)})")
            short_delay(stop_flag=stop_flag)

        if stop_flag and stop_flag():
            if log_fn:
                log_fn(f"   ⏹ Dừng tìm kiếm tại trang {page_num}")
            break

        page_apps = page.evaluate(EXTRACT_APPS_JS, True)
        new_count = 0
        for app in page_apps:
            if max_apps and len(all_apps) >= max_apps:
                break
            if app['slug'] not in seen_slugs:
                seen_slugs.add(app['slug'])
                all_apps.append(app)
                new_count += 1

        if log_fn:
            log_fn(f"   Trang {page_num}: +{new_count} apps (tổng: {len(all_apps)})")

        if new_count == 0:
            break

        # Check if there's a next page
        has_next = page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = a.innerText.trim();
                const href = a.getAttribute('href') || '';
                if (text === 'Next' && href.includes('page=')) return true;
            }
            return false;
        }""")
        if not has_next:
            break

        human_delay(1.0, 2.0, stop_flag=stop_flag)

    return all_apps


def browse_category(page, category_slug, log_fn=None, stop_flag=None):
    """Browse apps by category. Categories show all apps on one page."""
    url = f"{BASE_URL}/categories/{category_slug}"
    fast_goto(page, url, wait_selector='a[href*="/apps/"]', stop_flag=stop_flag)
    human_delay(1.5, 2.5, stop_flag=stop_flag)

    # Scroll to load all apps
    for _ in range(10):
        if stop_flag and stop_flag():
            break
        page.evaluate(f"window.scrollBy(0, {random.randint(600, 1000)})")
        short_delay(stop_flag=stop_flag)

    apps = page.evaluate(EXTRACT_APPS_JS, False)
    if log_fn:
        log_fn(f"   Tìm thấy {len(apps)} apps trong danh mục")
    return apps


def fuzzy_match(name, keyword, threshold=0.75):
    """Check if app name fuzzy-matches a keyword (case-insensitive).

    Handles: exact substring, word-level match, concatenated words (CrossSell vs cross sell).
    """
    name_lower = name.lower()
    kw_lower = keyword.lower()
    kw_nospace = kw_lower.replace(' ', '')

    # Exact substring match (with or without spaces)
    if kw_lower in name_lower:
        return True
    if kw_nospace in name_lower.replace(' ', ''):
        return True

    # Check each word and consecutive word pairs in the app name
    words = re.split(r'[\s\-_:,|/]+', name_lower)
    # Also check concatenated consecutive words (e.g. "cross"+"sell" = "crosssell")
    check_targets = list(words)
    for i in range(len(words) - 1):
        check_targets.append(words[i] + words[i + 1])

    for word in check_targets:
        if not word or len(word) < 2:
            continue
        # Compare against both kw_lower and kw_nospace
        for kw in (kw_lower, kw_nospace):
            if len(kw) < 2:
                continue
            if abs(len(word) - len(kw)) > max(2, len(kw) * 0.4):
                continue
            # Count matching characters in order
            matches = 0
            j = 0
            for ch in word:
                if j < len(kw) and ch == kw[j]:
                    matches += 1
                    j += 1
            ratio = (2.0 * matches) / (len(word) + len(kw))
            if ratio >= threshold:
                return True

    return False


def parse_search_keywords(raw_input):
    """Parse search input to extract broad and fuzzy keywords.

    - Words without quotes: broad match (default Shopify search)
    - Words in "quotes": fuzzy match (search broadly, then filter)
    - Multiple quoted: "kw1","kw2" -> search each, filter by fuzzy match

    Returns: (search_keyword, fuzzy_keywords_list)
    - search_keyword: string to send to Shopify search
    - fuzzy_keywords: list of keywords to fuzzy-filter, empty = no filter
    """
    raw = raw_input.strip()
    # Find all quoted keywords
    quoted = re.findall(r'"([^"]+)"', raw)
    # Remove quoted parts to get the broad keyword
    broad = re.sub(r'"[^"]*"', '', raw).strip()
    broad = re.sub(r',\s*,', ',', broad).strip(' ,')

    if quoted and not broad:
        # Only fuzzy keywords, use first as search term
        return quoted[0], quoted
    elif quoted and broad:
        # Mix: search with broad, also filter by fuzzy
        return broad, quoted
    else:
        # No quotes: broad match only
        return raw, []


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
        // Use data-merchant-review attribute to find review containers
        const reviewDivs = document.querySelectorAll('[data-merchant-review]');
        if (reviewDivs.length > 0) {
            for (const review of reviewDivs) {
                let stars = '';
                const starEl = review.querySelector('[aria-label*="out of 5 stars"]');
                if (starEl) {
                    const m = starEl.getAttribute('aria-label').match(/(\\d) out of 5/);
                    if (m) stars = m[1];
                }

                // Get review text EXCLUDING the dev reply
                // Child structure: [0]=content, [1]=reviewer info, [2]=dev reply (has data-merchant-review-reply)
                let reviewText = '';
                let reviewerInfo = '';
                for (const child of review.children) {
                    if (child.hasAttribute('data-merchant-review-reply') ||
                        child.dataset.merchantReviewReply !== undefined) {
                        continue; // Skip dev reply
                    }
                    const t = child.innerText || '';
                    // Reviewer info div contains "using the app"
                    if (t.includes('using the app') && t.length < 300) {
                        reviewerInfo = t;
                    } else {
                        reviewText += t + '\\n';
                    }
                }

                // Extract country from reviewer info (format: "Name\\nCountry\\nX using the app")
                let country = '';
                if (reviewerInfo) {
                    const infoLines = reviewerInfo.split('\\n').map(l => l.trim()).filter(l => l);
                    // Country is typically the second line (after reviewer name, before usage time)
                    if (infoLines.length >= 2) {
                        for (let i = 0; i < infoLines.length; i++) {
                            if (infoLines[i].toLowerCase().includes('using the app') && i >= 1) {
                                country = infoLines[i - 1];
                                break;
                            }
                        }
                    }
                }

                const fullText = reviewText.trim() + '\\n' + reviewerInfo;
                if (fullText.length > 30) {
                    results.push({ stars: stars, text: fullText, country: country });
                }
            }
            return results;
        }

        // Fallback: old method for pages without data-merchant-review
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
            results.push({ stars: stars, text: text, country: '' });
        }
        return results;
    }
    """)


def parse_single_review(text, stars, country=''):
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 3:
        return None
    review = {'rating': stars, 'date': '', 'content': '', 'reviewer': '', 'location': '', 'country': country, 'usage_time': ''}
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
    # Use extracted country if location-based country not found
    if not review['country'] and review['location']:
        review['country'] = review['location']
    return review if (review['content'] or review['date']) else None


# ============================================================
#  GUI Application
# ============================================================
class ShopifyScraperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LexifyReview v2.0 - Shopify App Store Review Scraper")
        self.root.geometry("950x780")
        self.root.minsize(900, 700)

        # State - Tab 1 (Reviews)
        self.apps = []
        self.all_data = []
        self.current_reviews = []
        self.total_collected = 0
        self.is_running = False
        self.is_paused = False
        self.quit_flag = False
        self.search_stop_flag = False
        self.browser = None
        self.pw = None

        # State - Tab 2 (App Info)
        self.info_app_links = []
        self.info_results = []
        self.info_is_running = False
        self.info_quit_flag = False

        self.build_ui()

    def build_ui(self):
        # === Notebook (Tabs) ===
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)

        # Tab 1: Review Scraper
        self.tab_reviews = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_reviews, text="  📝 Scrape Reviews  ")

        # Tab 2: App Info Scraper
        self.tab_info = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_info, text="  📊 App Info  ")

        self._build_tab_reviews()
        self._build_tab_info()

    def _build_tab_reviews(self):
        # --- Top: Search ---
        frm_search = ttk.LabelFrame(self.tab_reviews, text="  1. Tìm kiếm hoặc chọn danh mục  ", padding=10)
        frm_search.pack(fill="x", padx=8, pady=(8, 4))

        # Search mode selector
        self.search_mode = tk.StringVar(value="keyword")

        row_mode = ttk.Frame(frm_search)
        row_mode.pack(fill="x", pady=(0, 6))
        ttk.Radiobutton(row_mode, text="Tìm theo từ khóa", variable=self.search_mode,
                         value="keyword", command=self._toggle_search_mode).pack(side="left")
        ttk.Radiobutton(row_mode, text="Chọn danh mục", variable=self.search_mode,
                         value="category", command=self._toggle_search_mode).pack(side="left", padx=(20, 0))

        # Keyword row
        self.frm_keyword = ttk.Frame(frm_search)
        self.frm_keyword.pack(fill="x", pady=(0, 0))

        ttk.Label(self.frm_keyword, text="Từ khóa:").pack(side="left")
        self.entry_keyword = ttk.Entry(self.frm_keyword, width=40, font=("", 13))
        self.entry_keyword.pack(side="left", padx=(8, 8))
        self.entry_keyword.bind("<Return>", lambda e: self.do_search())

        # Category row (hidden by default)
        self.frm_category = ttk.Frame(frm_search)

        ttk.Label(self.frm_category, text="Danh mục:").pack(side="left")
        self.category_var = tk.StringVar()
        self.combo_category = ttk.Combobox(self.frm_category, textvariable=self.category_var,
                                            width=55, font=("", 12), state="readonly")
        self.combo_category.pack(side="left", padx=(8, 8))
        self._populate_categories()

        # Max apps row
        row_max = ttk.Frame(frm_search)
        row_max.pack(fill="x", pady=(4, 0))

        ttk.Label(row_max, text="Số app tối đa:").pack(side="left")
        self.entry_max_search_apps = ttk.Entry(row_max, width=10, font=("", 13))
        self.entry_max_search_apps.pack(side="left", padx=(8, 0))
        ttk.Label(row_max, text="(để trống = lấy tất cả)", foreground="gray").pack(side="left", padx=(8, 0))

        # Keyword hint
        self.lbl_keyword_hint = ttk.Label(frm_search,
            text='Gợi ý: dùng "từ khóa" (có ngoặc kép) để đối sánh gần đúng. VD: "upsell","cross sell"',
            foreground="gray", font=("", 10))
        self.lbl_keyword_hint.pack(fill="x", pady=(4, 0))

        # Search button & status (shared)
        row_btn = ttk.Frame(frm_search)
        row_btn.pack(fill="x", pady=(6, 0))

        self.btn_search = ttk.Button(row_btn, text="🔍 Tìm kiếm", command=self.do_search)
        self.btn_search.pack(side="left")

        self.btn_stop_search = ttk.Button(row_btn, text="⏹ Dừng tìm", command=self._stop_search)
        self.btn_stop_search.pack(side="left", padx=(5, 0))
        self.btn_stop_search.pack_forget()  # Hidden by default

        self.lbl_search_status = ttk.Label(row_btn, text="", foreground="gray")
        self.lbl_search_status.pack(side="left", padx=(12, 0))

        # --- Middle: App list (read-only display) ---
        frm_apps = ttk.LabelFrame(self.tab_reviews, text="  2. Danh sách apps (từ trên xuống dưới)  ", padding=10)
        frm_apps.pack(fill="both", expand=True, padx=8, pady=4)

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
        frm_settings = ttk.LabelFrame(self.tab_reviews, text="  3. Cài đặt  ", padding=10)
        frm_settings.pack(fill="x", padx=8, pady=4)

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
        frm_controls = ttk.Frame(self.tab_reviews, padding=(8, 4))
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
        frm_log = ttk.LabelFrame(self.tab_reviews, text="  Log  ", padding=5)
        frm_log.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.txt_log = tk.Text(frm_log, height=8, font=("Menlo", 11), wrap="word",
                               bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        log_scroll = ttk.Scrollbar(frm_log, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_scroll.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.log("Sẵn sàng. Nhập từ khóa hoặc chọn danh mục và nhấn Tìm kiếm.")

    # ===========================================================
    #  Tab 2: App Info Scraper
    # ===========================================================
    def _build_tab_info(self):
        # --- Import section ---
        frm_import = ttk.LabelFrame(self.tab_info, text="  1. Nhập danh sách App URLs  ", padding=10)
        frm_import.pack(fill="x", padx=8, pady=(8, 4))

        row_file = ttk.Frame(frm_import)
        row_file.pack(fill="x", pady=(0, 6))

        ttk.Label(row_file, text="File (CSV/XLS):").pack(side="left")
        self.info_file_path = tk.StringVar()
        self.entry_info_file = ttk.Entry(row_file, textvariable=self.info_file_path, width=50, font=("", 12))
        self.entry_info_file.pack(side="left", padx=(8, 8))
        ttk.Button(row_file, text="📁 Chọn file", command=self._info_browse_file).pack(side="left")

        ttk.Label(frm_import,
            text="File cần có cột chứa link app Shopify (VD: https://apps.shopify.com/omnisend)",
            foreground="gray", font=("", 10)).pack(fill="x")

        row_btn = ttk.Frame(frm_import)
        row_btn.pack(fill="x", pady=(6, 0))

        self.btn_info_load = ttk.Button(row_btn, text="📥 Tải danh sách", command=self._info_load_file)
        self.btn_info_load.pack(side="left")

        self.lbl_info_status = ttk.Label(row_btn, text="", foreground="gray")
        self.lbl_info_status.pack(side="left", padx=(12, 0))

        # --- App list ---
        frm_list = ttk.LabelFrame(self.tab_info, text="  2. Danh sách Apps  ", padding=10)
        frm_list.pack(fill="both", expand=True, padx=8, pady=4)

        cols = ("stt", "name", "url", "status")
        self.tree_info = ttk.Treeview(frm_list, columns=cols, show="headings", height=6)
        self.tree_info.heading("stt", text="#")
        self.tree_info.heading("name", text="App Name")
        self.tree_info.heading("url", text="URL")
        self.tree_info.heading("status", text="Trạng thái")
        self.tree_info.column("stt", width=40, anchor="center")
        self.tree_info.column("name", width=250)
        self.tree_info.column("url", width=350)
        self.tree_info.column("status", width=100, anchor="center")

        scroll = ttk.Scrollbar(frm_list, orient="vertical", command=self.tree_info.yview)
        self.tree_info.configure(yscrollcommand=scroll.set)
        self.tree_info.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # --- Controls ---
        frm_ctrl = ttk.Frame(self.tab_info, padding=(8, 4))
        frm_ctrl.pack(fill="x")

        self.btn_info_start = ttk.Button(frm_ctrl, text="▶  Bắt đầu lấy thông tin", command=self._info_start)
        self.btn_info_start.pack(side="left", padx=(0, 5))

        self.btn_info_stop = ttk.Button(frm_ctrl, text="⏹  Dừng", command=self._info_stop, state="disabled")
        self.btn_info_stop.pack(side="left", padx=(0, 5))

        self.btn_info_export = ttk.Button(frm_ctrl, text="💾 Export CSV", command=self._info_export, state="disabled")
        self.btn_info_export.pack(side="left")

        self.info_progress_var = tk.DoubleVar(value=0)
        self.info_progress = ttk.Progressbar(frm_ctrl, variable=self.info_progress_var, maximum=100, length=200)
        self.info_progress.pack(side="right", padx=(10, 0))

        self.lbl_info_stats = ttk.Label(frm_ctrl, text="", font=("", 11, "bold"))
        self.lbl_info_stats.pack(side="right", padx=(10, 0))

        # --- Log ---
        frm_log = ttk.LabelFrame(self.tab_info, text="  Log  ", padding=5)
        frm_log.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.txt_info_log = tk.Text(frm_log, height=6, font=("Menlo", 11), wrap="word",
                                     bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        log_scroll = ttk.Scrollbar(frm_log, orient="vertical", command=self.txt_info_log.yview)
        self.txt_info_log.configure(yscrollcommand=log_scroll.set)
        self.txt_info_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.info_log("Sẵn sàng. Chọn file CSV/XLS chứa danh sách link app Shopify.")

    def info_log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.txt_info_log.insert("end", f"[{timestamp}] {msg}\n")
        self.txt_info_log.see("end")

    def _info_browse_file(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("CSV/Excel files", "*.csv *.xls *.xlsx"), ("All files", "*.*")],
            title="Chọn file danh sách App URLs"
        )
        if filepath:
            self.info_file_path.set(filepath)

    def _info_load_file(self):
        filepath = self.info_file_path.get().strip()
        if not filepath:
            messagebox.showwarning("Lỗi", "Chọn file trước!")
            return

        try:
            links = []
            if filepath.lower().endswith(('.xls', '.xlsx')):
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(filepath, read_only=True)
                    ws = wb.active
                    for row in ws.iter_rows(values_only=True):
                        for cell in row:
                            if cell and isinstance(cell, str) and 'apps.shopify.com/' in cell:
                                links.append(cell.strip())
                    wb.close()
                except ImportError:
                    messagebox.showerror("Lỗi", "Cần cài openpyxl để đọc file Excel:\npip install openpyxl")
                    return
            else:
                with open(filepath, 'r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        for cell in row:
                            if cell and 'apps.shopify.com/' in cell:
                                links.append(cell.strip())

            # Deduplicate and extract slugs
            seen = set()
            self.info_app_links = []
            for link in links:
                # Normalize URL
                link = link.strip().rstrip('/')
                m = re.search(r'apps\.shopify\.com/([a-z0-9][a-z0-9-]*)', link)
                if m:
                    slug = m.group(1)
                    if slug not in seen and slug not in ('search', 'categories', 'collections', 'partners'):
                        seen.add(slug)
                        self.info_app_links.append({
                            'slug': slug,
                            'url': f"{BASE_URL}/{slug}",
                            'name': slug.replace('-', ' ').title()
                        })

            # Populate treeview
            for item in self.tree_info.get_children():
                self.tree_info.delete(item)
            for i, app in enumerate(self.info_app_links):
                self.tree_info.insert("", "end", iid=str(i),
                                       values=(i + 1, app['name'], app['url'], "Chờ"))

            self.lbl_info_status.config(text=f"Tìm thấy {len(self.info_app_links)} app links")
            self.info_log(f"✅ Tải {len(self.info_app_links)} app links từ file")
            self.info_results = []

        except Exception as e:
            messagebox.showerror("Lỗi", f"Không đọc được file:\n{e}")
            self.info_log(f"❌ Lỗi đọc file: {e}")

    def _info_start(self):
        if not self.info_app_links:
            messagebox.showwarning("Lỗi", "Tải danh sách app trước!")
            return

        self.info_is_running = True
        self.info_quit_flag = False
        self.info_results = []
        self.info_progress_var.set(0)

        self.btn_info_start.config(state="disabled")
        self.btn_info_load.config(state="disabled")
        self.btn_info_stop.config(state="normal")
        self.btn_info_export.config(state="normal")

        self.info_log(f"▶ Bắt đầu lấy thông tin {len(self.info_app_links)} apps...")

        threading.Thread(target=self._info_worker, daemon=True).start()

    def _info_stop(self):
        self.info_quit_flag = True
        self.info_log("🛑 Đang dừng...")

    def _info_worker(self):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)

                for idx, app in enumerate(self.info_app_links):
                    if self.info_quit_flag:
                        break

                    self.root.after(0, lambda i=idx, a=app, t=len(self.info_app_links):
                        self.info_log(f"\n[{i+1}/{t}] 📱 {a['name']}"))
                    self.root.after(0, lambda i=idx:
                        self.tree_info.set(str(i), "status", "Đang lấy..."))

                    try:
                        context = create_stealth_context(browser)
                        page = context.new_page()
                        stealth.apply_stealth_sync(page)
                        # Don't block stylesheets for this tab - need full page render
                        page.route("**/*", lambda route, req: route.abort()
                                   if req.resource_type in {"image", "font", "media"} else route.continue_())

                        page.goto(app['url'], wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_selector('#adp-pricing, section[id*="pricing"]', timeout=8000)
                        except Exception:
                            pass
                        human_delay(1.0, 2.0)

                        # Scroll to load all content
                        for _ in range(5):
                            page.evaluate(f"window.scrollBy(0, {random.randint(800, 1200)})")
                            short_delay()

                        info = page.evaluate(EXTRACT_APP_INFO_JS)
                        info['slug'] = app['slug']
                        info['url'] = app['url']

                        # Update name from actual page
                        if info.get('app_name'):
                            app['name'] = info['app_name']

                        self.info_results.append(info)

                        self.root.after(0, lambda i=idx, n=info.get('app_name', app['name']):
                            self._info_update_row(i, n, "✅ Xong"))
                        self.root.after(0, lambda i=idx, t=len(self.info_app_links):
                            self.info_log(f"   ✅ Xong - {info.get('pricing_model', 'N/A')}"))

                        page.close()
                        context.close()

                    except Exception as e:
                        self.root.after(0, lambda i=idx: self.tree_info.set(str(i), "status", "❌ Lỗi"))
                        self.root.after(0, lambda err=str(e): self.info_log(f"   ❌ Lỗi: {err}"))
                        self.info_results.append({
                            'slug': app['slug'], 'url': app['url'],
                            'app_name': app['name'], 'error': str(e)
                        })

                    # Update progress
                    progress = ((idx + 1) / len(self.info_app_links)) * 100
                    self.root.after(0, lambda v=progress: self.info_progress_var.set(v))
                    self.root.after(0, lambda c=len(self.info_results):
                        self.lbl_info_stats.config(text=f"📊 {c}/{len(self.info_app_links)} apps"))

                    if idx < len(self.info_app_links) - 1 and not self.info_quit_flag:
                        human_delay(1.5, 3.0)

                browser.close()

        except Exception as e:
            self.root.after(0, lambda err=str(e): self.info_log(f"❌ Lỗi nghiêm trọng: {err}"))

        self.root.after(0, self._info_done)

    def _info_update_row(self, idx, name, status):
        try:
            self.tree_info.set(str(idx), "name", name)
            self.tree_info.set(str(idx), "status", status)
        except Exception:
            pass

    def _info_done(self):
        self.info_is_running = False
        self.info_progress_var.set(100)
        self.btn_info_start.config(state="normal")
        self.btn_info_load.config(state="normal")
        self.btn_info_stop.config(state="disabled")
        self.btn_info_export.config(state="normal" if self.info_results else "disabled")

        self.info_log(f"\n{'='*40}")
        self.info_log(f"✅ HOÀN TẤT: {len(self.info_results)} apps")

        if self.info_results:
            if messagebox.askyesno("Hoàn tất", f"Đã lấy thông tin {len(self.info_results)} apps.\nExport CSV ngay?"):
                self._info_export()

    def _info_export(self):
        if not self.info_results:
            messagebox.showinfo("Export", "Chưa có dữ liệu!")
            return

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        default_name = f"shopify_app_info_{timestamp}.csv"

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=default_name,
            title="Lưu file CSV"
        )
        if not filepath:
            return

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'App Name', 'App URL', 'Rating', 'Review Count', 'Pricing',
                'Developer Name', 'Developer Website',
                'Launched Date', 'Languages', 'Demo Store URL',
                'What Merchants Think', 'Pricing Model',
                'Plan Names', 'Plan Prices', 'Plan Details',
                'Has Transaction Fees', 'Additional Charges Info'
            ])
            for info in self.info_results:
                writer.writerow([
                    info.get('app_name', ''),
                    info.get('url', ''),
                    info.get('rating', ''),
                    info.get('review_count', ''),
                    info.get('pricing', ''),
                    info.get('developer_name', ''),
                    info.get('developer_website', ''),
                    info.get('launched_date', ''),
                    info.get('languages', ''),
                    info.get('demo_store_url', ''),
                    info.get('merchants_think', ''),
                    info.get('pricing_model', ''),
                    info.get('plan_names', ''),
                    info.get('plan_prices', ''),
                    info.get('plan_details', ''),
                    info.get('has_transaction_fees', ''),
                    info.get('additional_charges', ''),
                ])

        self.info_log(f"💾 Đã lưu: {filepath} ({len(self.info_results)} apps)")
        messagebox.showinfo("Export thành công", f"Đã lưu {len(self.info_results)} apps\n{filepath}")

    def _populate_categories(self):
        """Populate category dropdown with Shopify app categories."""
        # Full category tree (122 categories) crawled from Shopify App Store
        self.categories = {
            # ── Sales channels ──
            "Sales channels": "sales-channels",
            "  Selling in person": "sales-channels-selling-in-person",
            "    Retail": "sales-channels-selling-in-person-retail",
            "    SKU and barcodes": "sales-channels-selling-in-person-sku-and-barcodes",
            "    Store locator": "sales-channels-selling-in-person-store-locator",
            "  Selling online": "sales-channels-selling-online",
            "    Marketplaces": "sales-channels-selling-online-marketplaces",
            "    Product feeds": "sales-channels-selling-online-product-feeds",
            "    Store data importer": "sales-channels-selling-online-store-data-importer",
            # ── Finding products ──
            "Finding products": "finding-products",
            "  Sourcing options": "finding-products-sourcing-options",
            "    Dropshipping": "finding-products-sourcing-options-dropshipping",
            "    Print on demand (POD)": "finding-products-sourcing-options-print-on-demand-pod",
            "    Wholesale": "finding-products-sourcing-options-wholesale",
            # ── Selling products ──
            "Selling products": "selling-products",
            "  Custom products": "selling-products-custom-products",
            "    Custom file upload": "selling-products-custom-products-custom-file-upload",
            "    Product variants": "selling-products-custom-products-product-variants",
            "  Digital goods and services": "selling-products-digital-goods-and-services",
            "    Digital products": "selling-products-digital-goods-and-services-digital-products",
            "    Event booking": "selling-products-digital-goods-and-services-event-booking",
            "    NFTs and tokengating": "selling-products-digital-goods-and-services-nfts-and-tokengating",
            "  Payments": "selling-products-payments",
            "    Pay later": "selling-products-payments-pay-later",
            "    Payment experience": "selling-products-payments-payment-experience",
            "    Subscriptions": "selling-products-payments-subscriptions",
            "  Pricing": "selling-products-pricing",
            "    Pricing optimization": "selling-products-pricing-pricing-optimization",
            "    Pricing quotes": "selling-products-pricing-pricing-quotes",
            # ── Orders and shipping ──
            "Orders and shipping": "orders-and-shipping",
            "  Inventory": "orders-and-shipping-inventory",
            "    ERP": "orders-and-shipping-inventory-erp",
            "    Inventory optimization": "orders-and-shipping-inventory-inventory-optimization",
            "    Inventory sync": "orders-and-shipping-inventory-inventory-sync",
            "  Orders": "orders-and-shipping-orders",
            "    Invoices and receipts": "orders-and-shipping-orders-invoices-and-receipts",
            "    Order editing": "orders-and-shipping-orders-order-editing",
            "    Order tracking": "orders-and-shipping-orders-order-tracking",
            "  Returns and warranty": "orders-and-shipping-returns-and-warranty",
            "    Returns and exchanges": "orders-and-shipping-returns-and-warranty-returns-and-exchanges",
            "    Warranties and insurance": "orders-and-shipping-returns-and-warranty-warranties-and-insurance",
            "  Shipping solutions": "orders-and-shipping-shipping-solutions",
            "    Delivery and pickup": "orders-and-shipping-shipping-solutions-delivery-and-pickup",
            "    Shipping": "orders-and-shipping-shipping-solutions-shipping",
            "    Shipping rates": "orders-and-shipping-shipping-solutions-shipping-rates",
            "    Third-party logistics (3PL)": "orders-and-shipping-shipping-solutions-third-party-logistics-3pl",
            # ── Store design ──
            "Store design": "store-design",
            "  Content": "store-design-content",
            "    Blogs": "store-design-content-blogs",
            "    Metafields": "store-design-content-metafields",
            "    Product content": "store-design-content-product-content",
            "  Design elements": "store-design-design-elements",
            "    Animation and effects": "store-design-design-elements-animation-and-effects",
            "    Badges and icons": "store-design-design-elements-badges-and-icons",
            "  Images and media": "store-design-images-and-media",
            "    3D/AR/VR": "store-design-images-and-media-3d-ar-vr",
            "    Image editor": "store-design-images-and-media-image-editor",
            "    Image gallery": "store-design-images-and-media-image-gallery",
            "    Video and livestream": "store-design-images-and-media-video-and-livestream",
            "  Internationalization": "store-design-internationalization",
            "    Cookie consent": "store-design-internationalization-cookie-consent",
            "    Currency and translation": "store-design-internationalization-currency-and-translation",
            "    Geolocation": "store-design-internationalization-geolocation",
            "  Notifications": "store-design-notifications",
            "    Banners": "store-design-notifications-banners",
            "    Forms": "store-design-notifications-forms",
            "    Pop-ups": "store-design-notifications-pop-ups",
            "  Product display": "store-design-product-display",
            "    Collections": "store-design-product-display-collections",
            "    Product comparison": "store-design-product-display-product-comparison",
            "  Search and navigation": "store-design-search-and-navigation",
            "    Navigation and menus": "store-design-search-and-navigation-navigation-and-menus",
            "    Search and filters": "store-design-search-and-navigation-search-and-filters",
            "  Site optimization": "store-design-site-optimization",
            "    Accessibility": "store-design-site-optimization-accessibility",
            "    SEO": "store-design-site-optimization-seo",
            "  Storefronts": "store-design-storefronts",
            "    Mobile app builder": "store-design-storefronts-mobile-app-builder",
            "    Page builder": "store-design-storefronts-page-builder",
            # ── Marketing and conversion ──
            "Marketing and conversion": "marketing-and-conversion",
            "  Advertising": "marketing-and-conversion-advertising",
            "    Ads": "marketing-and-conversion-advertising-ads",
            "    Affiliate programs": "marketing-and-conversion-advertising-affiliate-programs",
            "  Checkout": "marketing-and-conversion-checkout",
            "    Cart customization": "marketing-and-conversion-checkout-cart-customization",
            "    Order limits": "marketing-and-conversion-checkout-order-limits",
            "  Customer loyalty": "marketing-and-conversion-customer-loyalty",
            "    Donations": "marketing-and-conversion-customer-loyalty-donations",
            "    Loyalty and rewards": "marketing-and-conversion-customer-loyalty-loyalty-and-rewards",
            "    Wishlists": "marketing-and-conversion-customer-loyalty-wishlists",
            "  Gifts": "marketing-and-conversion-gifts",
            "    Gift cards": "marketing-and-conversion-gifts-gift-cards",
            "    Gift wrap and messages": "marketing-and-conversion-gifts-gift-wrap-and-messages",
            "  Marketing": "marketing-and-conversion-marketing",
            "    Abandoned cart": "marketing-and-conversion-marketing-abandoned-cart",
            "    Email marketing": "marketing-and-conversion-marketing-email-marketing",
            "    SMS marketing": "marketing-and-conversion-marketing-sms-marketing",
            "    Web push": "marketing-and-conversion-marketing-web-push",
            "  Promotions": "marketing-and-conversion-promotions",
            "    Discounts": "marketing-and-conversion-promotions-discounts",
            "    Giveaways and contests": "marketing-and-conversion-promotions-giveaways-and-contests",
            "  Social trust": "marketing-and-conversion-social-trust",
            "    Product reviews": "marketing-and-conversion-social-trust-product-reviews",
            "    Social proof": "marketing-and-conversion-social-trust-social-proof",
            "  Upsell and bundles": "marketing-and-conversion-upsell-and-bundles",
            "    Countdown timer": "marketing-and-conversion-upsell-and-bundles-countdown-timer",
            "    Pre-orders": "marketing-and-conversion-upsell-and-bundles-pre-orders",
            "    Product bundles": "marketing-and-conversion-upsell-and-bundles-product-bundles",
            "    Stock alerts": "marketing-and-conversion-upsell-and-bundles-stock-alerts",
            "    Upsell and cross-sell": "marketing-and-conversion-upsell-and-bundles-upsell-and-cross-sell",
            # ── Store management ──
            "Store management": "store-management",
            "  Finances": "store-management-finances",
            "    Accounting": "store-management-finances-accounting",
            "    Taxes": "store-management-finances-taxes",
            "  Operations": "store-management-operations",
            "    Analytics": "store-management-operations-analytics",
            "    Bulk editor": "store-management-operations-bulk-editor",
            "    Staff notifications": "store-management-operations-staff-notifications",
            "    Workflow automation": "store-management-operations-workflow-automation",
            "  Security": "store-management-security",
            "    Accounts and login": "store-management-security-accounts-and-login",
            "    Anti theft": "store-management-security-anti-theft",
            "    Fraud": "store-management-security-fraud",
            "    Legal": "store-management-security-legal",
            "  Support": "store-management-support",
            "    Chat": "store-management-support-chat",
            "    FAQ": "store-management-support-faq",
            "    Helpdesk": "store-management-support-helpdesk",
            "    Surveys": "store-management-support-surveys",
        }
        self._all_category_names = list(self.categories.keys())
        self.combo_category['values'] = self._all_category_names
        if self.categories:
            self.combo_category.current(0)

        # Make combobox searchable
        self.combo_category.configure(state="normal")  # Allow typing
        self.combo_category.bind('<KeyRelease>', self._filter_categories)
        self.combo_category.bind('<<ComboboxSelected>>', self._on_category_selected)

    def _filter_categories(self, event=None):
        """Filter category dropdown as user types."""
        typed = self.combo_category.get().lower().strip()
        if not typed:
            self.combo_category['values'] = self._all_category_names
            return
        filtered = [name for name in self._all_category_names
                     if typed in name.lower().strip()]
        self.combo_category['values'] = filtered if filtered else self._all_category_names

    def _on_category_selected(self, event=None):
        """Reset filter after selection."""
        self.combo_category['values'] = self._all_category_names

    def _toggle_search_mode(self):
        mode = self.search_mode.get()
        if mode == "keyword":
            self.frm_category.pack_forget()
            self.frm_keyword.pack(fill="x", pady=(0, 0), after=self.frm_keyword.master.winfo_children()[0])
        else:
            self.frm_keyword.pack_forget()
            self.frm_category.pack(fill="x", pady=(0, 0), after=self.frm_category.master.winfo_children()[0])

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

    def _get_max_search_apps(self):
        raw = self.entry_max_search_apps.get().strip()
        if not raw:
            return None
        try:
            n = int(raw)
            return n if n > 0 else None
        except ValueError:
            return None

    def _start_search_ui(self):
        """Switch UI to searching state."""
        self.search_stop_flag = False
        self.btn_search.pack_forget()
        self.btn_stop_search.pack(side="left", before=self.lbl_search_status)
        self.lbl_search_status.config(text="Đang tìm apps...")

    def _end_search_ui(self):
        """Switch UI back to normal state."""
        self.btn_stop_search.config(text="⏹ Dừng tìm", state="normal")
        self.btn_stop_search.pack_forget()
        self.btn_search.pack(side="left", before=self.lbl_search_status)
        self.btn_search.config(state="normal")

    def _stop_search(self):
        """User clicked stop search."""
        self.search_stop_flag = True
        self.btn_stop_search.config(text="⏳ Đang dừng...", state="disabled")
        self.lbl_search_status.config(text="Đang dừng, chờ trang hiện tại...")
        self.log("⏹ Đang dừng tìm kiếm...")

    # --- Search ---
    def do_search(self):
        mode = self.search_mode.get()
        max_apps = self._get_max_search_apps()

        if mode == "keyword":
            keyword = self.entry_keyword.get().strip()
            if not keyword:
                messagebox.showwarning("Lỗi", "Nhập từ khóa tìm kiếm!")
                return
            search_kw, fuzzy_kws = parse_search_keywords(keyword)
            self._start_search_ui()
            if fuzzy_kws:
                self.log(f'🔍 Tìm kiếm: "{search_kw}" (đối sánh gần đúng: {", ".join(fuzzy_kws)})')
            else:
                self.log(f"🔍 Đang tìm kiếm: '{search_kw}'...")
            threading.Thread(target=self._search_worker,
                             args=(search_kw, fuzzy_kws, max_apps), daemon=True).start()
        else:
            cat_name = self.category_var.get()
            if not cat_name or cat_name not in self.categories:
                typed = cat_name.lower().strip() if cat_name else ""
                match = None
                for name in self._all_category_names:
                    if typed and typed in name.lower().strip():
                        match = name
                        break
                if not match:
                    messagebox.showwarning("Lỗi", "Chọn danh mục hợp lệ!")
                    return
                cat_name = match
                self.category_var.set(cat_name)
            cat_slug = self.categories[cat_name]
            self._start_search_ui()
            self.log(f"📂 Đang tải danh mục: '{cat_name.strip()}'...")
            threading.Thread(target=self._category_worker,
                             args=(cat_slug, max_apps), daemon=True).start()

    def _search_worker(self, keyword, fuzzy_keywords, max_apps):
        try:
            def log_fn(msg):
                self.root.after(0, lambda m=msg: self.log(m))
            def stop_fn():
                return self.search_stop_flag

            all_apps = []
            seen_slugs = set()

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = create_stealth_context(browser)
                page = create_stealth_page(context)

                if fuzzy_keywords and len(fuzzy_keywords) > 1:
                    # Multiple fuzzy keywords: search each separately
                    for kw in fuzzy_keywords:
                        if self.search_stop_flag:
                            break
                        if max_apps and len(all_apps) >= max_apps:
                            break
                        self.root.after(0, lambda k=kw: self.log(f"\n🔍 Tìm: '{k}'..."))
                        remaining = (max_apps - len(all_apps)) if max_apps else None
                        apps = search_apps(page, kw, log_fn=log_fn,
                                           max_apps=remaining, stop_flag=stop_fn)
                        for app in apps:
                            if app['slug'] not in seen_slugs:
                                seen_slugs.add(app['slug'])
                                all_apps.append(app)
                else:
                    # Single keyword (broad or single fuzzy)
                    all_apps = search_apps(page, keyword, log_fn=log_fn,
                                           max_apps=max_apps, stop_flag=stop_fn)

                page.close()
                context.close()
                browser.close()

            # Apply fuzzy filter if needed
            if fuzzy_keywords:
                before = len(all_apps)
                all_apps = [app for app in all_apps
                            if any(fuzzy_match(app['name'], kw) for kw in fuzzy_keywords)]
                filtered = before - len(all_apps)
                if filtered > 0:
                    self.root.after(0, lambda f=filtered, a=len(all_apps):
                        self.log(f"🔍 Lọc đối sánh gần đúng: loại {f} apps, giữ {a} apps"))

            self.apps = all_apps
            self.root.after(0, self._search_done)
        except Exception as e:
            self.root.after(0, lambda: self._search_error(str(e)))

    def _category_worker(self, cat_slug, max_apps):
        try:
            def log_fn(msg):
                self.root.after(0, lambda m=msg: self.log(m))
            def stop_fn():
                return self.search_stop_flag

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = create_stealth_context(browser)
                page = create_stealth_page(context)
                apps = browse_category(page, cat_slug, log_fn=log_fn, stop_flag=stop_fn)
                page.close()
                context.close()
                browser.close()

            if max_apps:
                apps = apps[:max_apps]
            self.apps = apps
            self.root.after(0, self._search_done)
        except Exception as e:
            self.root.after(0, lambda: self._search_error(str(e)))

    def _search_done(self):
        self._end_search_ui()
        if not self.apps:
            self.lbl_search_status.config(text="Không tìm thấy app nào!")
            self.log("❌ Không tìm thấy app nào!")
            self._auto_start_after_search = False
            return
        self.lbl_search_status.config(text=f"Tìm thấy {len(self.apps)} apps")
        self.log(f"✅ Tìm thấy {len(self.apps)} apps")
        self.populate_app_list(self.apps)

        # Auto-start scraping if triggered from "Bắt đầu" button
        if getattr(self, '_auto_start_after_search', False):
            self._auto_start_after_search = False
            self.log("▶ Tự động bắt đầu scrape reviews...")
            self._begin_scraping()

    def _search_error(self, error):
        self._end_search_ui()
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
        # If no apps loaded yet, search first then scrape
        if not self.apps:
            has_input = False
            mode = self.search_mode.get()
            if mode == "keyword" and self.entry_keyword.get().strip():
                has_input = True
            elif mode == "category" and self.category_var.get():
                has_input = True

            if not has_input:
                messagebox.showwarning("Lỗi", "Nhập từ khóa hoặc chọn danh mục trước!")
                return

            # Search first, then auto-start scraping
            self._auto_start_after_search = True
            self.do_search()
            return

        self._begin_scraping()

    def _begin_scraping(self):
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

                                parsed = parse_single_review(r['text'], r['stars'], r.get('country', ''))
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

        if self.search_mode.get() == "keyword":
            keyword = self.entry_keyword.get().strip() or "export"
        else:
            keyword = self.category_var.get().strip() or "category"
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
                'Reviewer', 'Country', 'Review Date', 'Star Rating',
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
                        review.get('reviewer', ''), review.get('country', ''),
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

# LexifyReview - Shopify App Store Review Scraper

Công cụ scrape reviews từ Shopify App Store. Hỗ trợ CLI và GUI, chống anti-bot detection.

## Tính năng

- **Tìm kiếm linh hoạt** - Từ khóa hoặc chọn từ 122 danh mục Shopify
- **Lọc theo sao** - Chọn bất kỳ tổ hợp sao nào (1-5)
- **Giới hạn reviews/app** - Số lượng tùy chỉnh hoặc lấy tất cả
- **Pause / Resume / Export** - Điều khiển bất cứ lúc nào
- **Chống detect** - playwright-stealth, user-agent xoay vòng, delays ngẫu nhiên
- **App Info Scraper** (GUI) - Thu thập thông tin chi tiết app (giá, developer, pricing model...)
- **Export CSV** - UTF-8 BOM, sẵn sàng mở trong Excel

## Cài đặt

```bash
pip install playwright playwright-stealth
playwright install chromium
```

## Sử dụng

### CLI

```bash
python3 shopify_reviews.py
```

Nhập từ khóa, cài đặt số lượng, và các lệnh điều khiển trong khi chạy:

| Phím | Chức năng |
|------|-----------|
| `p` | Tạm dừng |
| `r` | Tiếp tục |
| `e` | Export CSV (tiếp tục chạy) |
| `q` | Dừng và Export |

### GUI

```bash
python3 shopify_reviews_gui.py
```

Giao diện có 2 tab:
- **Scrape Reviews** - Tìm kiếm & scrape reviews
- **App Info** - Nhập danh sách URL, lấy thông tin chi tiết từng app

## Cấu trúc thư mục

```
├── shopify_reviews.py        # CLI version
├── shopify_reviews_gui.py    # GUI version (LexifyReview v2.0)
├── index.html                # Landing page
├── README.md
├── mac_install/              # Hướng dẫn cài đặt macOS
└── windows_build/            # Build cho Windows
```

## Dữ liệu đầu ra

File CSV gồm các cột: App Name, App Rating, App Review Count, App URL, Reviewer, Country, Review Date, Star Rating, Usage Time, Review Content.

## Yêu cầu

- Python 3.8+
- Playwright + Chromium
- macOS hoặc Windows

## License

For research purposes only. Tuân thủ điều khoản sử dụng của Shopify App Store.

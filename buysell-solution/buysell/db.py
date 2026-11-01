"""통합 DB (SQLite) 스키마와 연결.

기획안 5번 데이터 구조를 그대로 구현한다.
- 상품 / 주문 / 발주·배송 / 비용·정산(3단계용 틀) / 경보 / 문의
민감 정보(개인통관고유부호·전화번호·주소 원문)는 저장하지 않거나 마스킹한다.
"""
import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.environ.get(
    "BUYSELL_DB", os.path.join(BASE_DIR, "data", "buysell.db")
)

# 주문 진행 단계 (기획안 모듈 2)
STAGES = [
    ("RECEIVED", "주문 접수"),
    ("ORDERED_CN", "타오바오 발주"),
    ("CN_SHIPPING", "중국 내 배송"),
    ("BAEDAEJI_IN", "배대지 입고"),
    ("INTL_SHIPPED", "국제 출고"),
    ("KR_DELIVERING", "국내 배송"),
    ("DELIVERED", "배송 완료"),
    ("CANCELLED", "취소"),
]
STAGE_LABELS = dict(STAGES)
# 배송 완료로 보는 단계
SHIPPED_STAGES = {"INTL_SHIPPED", "KR_DELIVERING", "DELIVERED"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT 'coupang',
    seller_product_id TEXT,
    product_id TEXT,
    name TEXT,
    taobao_url TEXT,
    sale_price INTEGER,
    status_name TEXT,
    on_sale INTEGER,
    unsuitable_reasons TEXT,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, seller_product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT 'coupang',
    channel_order_id TEXT NOT NULL,
    shipment_box_id TEXT,
    seller_product_id TEXT,
    product_name TEXT,
    buyer_masked TEXT,
    quantity INTEGER DEFAULT 1,
    order_price INTEGER,
    status_raw TEXT,
    stage TEXT NOT NULL DEFAULT 'RECEIVED',
    ordered_at TEXT,
    expected_ship_date TEXT,
    shipped_at TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, channel_order_id, shipment_box_id)
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    taobao_order_no TEXT,
    cn_tracking_no TEXT,
    intl_tracking_no TEXT,
    kr_carrier TEXT,
    kr_tracking_no TEXT,
    baedaeji_status TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(order_id)
);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item TEXT NOT NULL,
    amount_krw INTEGER NOT NULL DEFAULT 0,
    amount_cny REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    target_type TEXT NOT NULL,
    target_id TEXT,
    title TEXT NOT NULL,
    detail TEXT,
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT 'coupang',
    channel_inquiry_id TEXT,
    order_id INTEGER REFERENCES orders(id),
    product_name TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'unanswered',
    received_at TEXT,
    due_at TEXT,
    answered_at TEXT,
    template_used TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, channel_inquiry_id)
);

CREATE TABLE IF NOT EXISTS sourcing_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taobao_url TEXT NOT NULL,
    platform TEXT,
    item_id TEXT,
    title_cn TEXT,
    desc_text TEXT,
    price_cny REAL,
    cn_shipping_cny REAL DEFAULT 0,
    exchange_rate REAL DEFAULT 200,
    weight_kg REAL,
    intl_rate_per_kg INTEGER DEFAULT 9000,
    intl_shipping_krw INTEGER,
    tax_rate REAL DEFAULT 0.10,
    fee_rate REAL DEFAULT 0.11,
    target_margin_rate REAL DEFAULT 0.20,
    sale_price_krw INTEGER,
    cost_breakdown TEXT,
    margin_krw INTEGER,
    margin_rate REAL,
    generated_names TEXT,
    search_tags TEXT,
    name_method TEXT,
    unsuitable_reasons TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    draft_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(taobao_url)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    ran_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_stage ON orders(stage);
CREATE INDEX IF NOT EXISTS idx_orders_ordered ON orders(ordered_at);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, severity);
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | None = None) -> str:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    return db_path or DEFAULT_DB_PATH


@contextmanager
def db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

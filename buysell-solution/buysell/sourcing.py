"""2단계: 소싱·상품 등록 자동화.

흐름: 타오바오 링크 등록 → 상품명 생성(기존 optimizer 이관, Gemini 있으면 AI·없으면 규칙)
     → 마진 자동 계산(판매가 역산) → 등록 전 부적합 사전 검사(34규칙)
     → 통과 건만 product_uploader용 등록 초안 JSON 생성

기본값(모두 후보별로 수정 가능, 가정이므로 화면에 그대로 표시한다):
- 환율 200원/CNY (2026-11-01 시세 약 200.5원 기준 반올림)
- 국제배송 단가 9,000원/kg (배대지 항공 기준 가정)
- 관부가세율 10% (부가세 기준, 관세는 품목별로 달라 기본 0으로 두고 세율에 합산 입력)
- 쿠팡 수수료율 11% (카테고리별로 4~15%라 중간값 가정)
- 목표 마진율 20%
"""
import json
import math
import os

from .db import BASE_DIR, db
from .timeutil import now_iso
from .titles import clean_title_rule_based, generate_with_gemini, parse_taobao_url
from .unsuitable import check_unsuitable

DEFAULTS = {
    "exchange_rate": 200.0,
    "intl_rate_per_kg": 9000,
    "tax_rate": 0.10,
    "fee_rate": 0.11,
    "target_margin_rate": 0.20,
}


def _round100(x: float) -> int:
    return int(math.ceil(x / 100.0)) * 100


def calc_margin(c: dict) -> dict:
    """원가 구성과 판매가 역산. c는 후보 dict (입력값). 결과 dict 반환."""
    rate = float(c.get("exchange_rate") or DEFAULTS["exchange_rate"])
    price_cny = float(c.get("price_cny") or 0)
    cn_ship_cny = float(c.get("cn_shipping_cny") or 0)
    goods_krw = _round100((price_cny + cn_ship_cny) * rate) if (price_cny + cn_ship_cny) else 0

    intl_override = c.get("intl_shipping_krw")
    if intl_override not in (None, "", 0):
        intl_krw = int(intl_override)
        intl_source = "직접 입력"
    elif c.get("weight_kg"):
        intl_krw = _round100(float(c["weight_kg"]) * int(c.get("intl_rate_per_kg") or DEFAULTS["intl_rate_per_kg"]))
        intl_source = f"무게 {c['weight_kg']}kg × {int(c.get('intl_rate_per_kg') or DEFAULTS['intl_rate_per_kg']):,}원"
    else:
        intl_krw = 0
        intl_source = "무게 미입력 — 국제배송비 0으로 계산됨"

    tax_rate = float(c.get("tax_rate") if c.get("tax_rate") is not None else DEFAULTS["tax_rate"])
    tax_krw = _round100((goods_krw + intl_krw) * tax_rate) if (goods_krw + intl_krw) else 0

    fee_rate = float(c.get("fee_rate") if c.get("fee_rate") is not None else DEFAULTS["fee_rate"])
    target = float(c.get("target_margin_rate") if c.get("target_margin_rate") is not None
                   else DEFAULTS["target_margin_rate"])

    base_cost = goods_krw + intl_krw + tax_krw
    denom = 1 - fee_rate - target
    suggested = _round100(base_cost / denom) if base_cost and denom > 0 else 0

    sale = int(c.get("sale_price_krw") or 0) or suggested
    fee_krw = _round100(sale * fee_rate) if sale else 0
    margin_krw = sale - base_cost - fee_krw if sale else 0
    margin_rate = (margin_krw / sale) if sale else 0.0

    return {
        "goods_krw": goods_krw,
        "intl_krw": intl_krw,
        "intl_source": intl_source,
        "tax_krw": tax_krw,
        "tax_rate": tax_rate,
        "base_cost_krw": base_cost,
        "fee_rate": fee_rate,
        "fee_krw": fee_krw,
        "suggested_sale_price_krw": suggested,
        "sale_price_krw": sale,
        "margin_krw": margin_krw,
        "margin_rate": round(margin_rate, 4),
        "target_margin_rate": target,
        "exchange_rate": rate,
    }


def _status_for(reasons: list, margin: dict, has_price: bool) -> str:
    if any("판매불가" in r for r in reasons):
        return "blocked"
    if reasons:
        return "check_needed"
    # 100원 단위 반올림 오차(0.5%p 이내)는 부족으로 보지 않는다
    if has_price and margin["margin_rate"] < margin["target_margin_rate"] - 0.005:
        return "low_margin"
    if has_price:
        return "ready"
    return "draft"


def _generate_names(title_cn: str, desc: str, url_info: dict) -> tuple[dict, str]:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        try:
            data = generate_with_gemini(title_cn, desc, url_info, api_key,
                                        os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
            return data, "gemini"
        except Exception:
            pass
    return clean_title_rule_based(title_cn, desc), "rule-based"


def _recalc_and_save(conn, cid: int, manual_sale=None):
    """재계산해 저장한다. manual_sale이 없으면 판매가는 목표 마진으로 자동 역산한다.
    판매가를 직접 고정하려면 저장할 때 판매가 값을 함께 넘겨야 한다."""
    row = dict(conn.execute("SELECT * FROM sourcing_candidates WHERE id=?", (cid,)).fetchone())
    calc_input = {**row, "sale_price_krw": manual_sale}
    margin = calc_margin(calc_input)
    # 부적합 사전 검사: 원제목 + 생성 상품명을 합쳐 검사 (구매대행 상향 적용)
    # 원제목에 위험 키워드가 있는데 생성명이 일반명으로 바뀌어 검사를 빠져나가는 경우를 막는다.
    names = json.loads(row.get("generated_names") or "{}")
    title_for_check = " ".join(x for x in [
        row.get("title_cn") or "",
        names.get("displayProductName_search") or "",
        names.get("generalProductName") or "",
    ] if x).strip()
    reasons = check_unsuitable({
        "sellerProductName": title_for_check,
        "displayProductName": title_for_check,
        "generalProductName": names.get("generalProductName") or "",
        "brand": "",
        "items": [{"overseasPurchased": "Y"}],
    }) if title_for_check else []
    status = _status_for(reasons, margin, bool(row.get("price_cny")))
    conn.execute(
        """
        UPDATE sourcing_candidates SET
            sale_price_krw=?, cost_breakdown=?, margin_krw=?, margin_rate=?,
            unsuitable_reasons=?, status=?, updated_at=?
        WHERE id=?
        """,
        (margin["sale_price_krw"] or None, json.dumps(margin, ensure_ascii=False),
         margin["margin_krw"], margin["margin_rate"],
         json.dumps(reasons, ensure_ascii=False), status, now_iso(), cid),
    )
    return status, reasons, margin


def add_candidate(url: str, title_cn: str = "", desc: str = "", price_cny=None,
                  cn_shipping_cny=0, weight_kg=None, db_path=None, **overrides) -> int:
    info = parse_taobao_url(url)
    names, method = ({}, "") if not title_cn else _generate_names(title_cn, desc, info)
    ts = now_iso()
    values = {**DEFAULTS, **{k: v for k, v in overrides.items() if v not in (None, "")}}
    with db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sourcing_candidates
                (taobao_url, platform, item_id, title_cn, desc_text, price_cny,
                 cn_shipping_cny, exchange_rate, weight_kg, intl_rate_per_kg,
                 intl_shipping_krw, tax_rate, fee_rate, target_margin_rate,
                 generated_names, search_tags, name_method, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            ON CONFLICT(taobao_url) DO UPDATE SET
                title_cn=excluded.title_cn, desc_text=excluded.desc_text,
                price_cny=excluded.price_cny, cn_shipping_cny=excluded.cn_shipping_cny,
                weight_kg=excluded.weight_kg, generated_names=excluded.generated_names,
                search_tags=excluded.search_tags, name_method=excluded.name_method,
                updated_at=excluded.updated_at
            """,
            (url, info["platform"], info["item_id"], title_cn, desc, price_cny,
             cn_shipping_cny, values["exchange_rate"], weight_kg,
             values["intl_rate_per_kg"], overrides.get("intl_shipping_krw"),
             values["tax_rate"], values["fee_rate"], values["target_margin_rate"],
             json.dumps(names, ensure_ascii=False),
             json.dumps(names.get("searchTags", []), ensure_ascii=False),
             method, ts, ts),
        )
        cid = conn.execute(
            "SELECT id FROM sourcing_candidates WHERE taobao_url=?", (url,)).fetchone()["id"]
        _recalc_and_save(conn, cid)
    return cid


def update_candidate(cid: int, fields: dict, db_path=None):
    allowed = ("title_cn", "desc_text", "price_cny", "cn_shipping_cny", "exchange_rate",
               "weight_kg", "intl_rate_per_kg", "intl_shipping_krw", "tax_rate",
               "fee_rate", "target_margin_rate", "sale_price_krw")
    sets = {k: (None if v == "" else v) for k, v in fields.items() if k in allowed}
    with db(db_path) as conn:
        if sets:
            clause = ", ".join(f"{k}=?" for k in sets)
            conn.execute(f"UPDATE sourcing_candidates SET {clause}, updated_at=? WHERE id=?",
                         (*sets.values(), now_iso(), cid))
        row = dict(conn.execute("SELECT * FROM sourcing_candidates WHERE id=?", (cid,)).fetchone())
        # 원제목이 바뀌었으면 상품명 재생성
        if "title_cn" in sets and sets["title_cn"]:
            info = parse_taobao_url(row["taobao_url"])
            names, method = _generate_names(row["title_cn"], row.get("desc_text") or "", info)
            conn.execute(
                "UPDATE sourcing_candidates SET generated_names=?, search_tags=?, name_method=? WHERE id=?",
                (json.dumps(names, ensure_ascii=False),
                 json.dumps(names.get("searchTags", []), ensure_ascii=False), method, cid))
        _recalc_and_save(conn, cid, manual_sale=sets.get("sale_price_krw"))


def list_candidates(db_path=None):
    with db(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sourcing_candidates ORDER BY updated_at DESC").fetchall()]


def export_draft(cid: int, db_path=None, out_dir=None) -> str:
    """product_uploader용 등록 초안 JSON 생성. blocked면 생성하지 않는다."""
    with db(db_path) as conn:
        row = dict(conn.execute(
            "SELECT * FROM sourcing_candidates WHERE id=?", (cid,)).fetchone())
    if row["status"] == "blocked":
        raise ValueError("판매불가 판정 상품은 등록 초안을 만들 수 없습니다. 사전 검사 사유를 확인하세요.")
    names = json.loads(row.get("generated_names") or "{}")
    tags = json.loads(row.get("search_tags") or "[]")
    sale = int(row.get("sale_price_krw") or 0)
    title = names.get("displayProductName_search") or row.get("title_cn") or ""
    payload = {
        "_comment": "구매대행 통합 솔루션 자동 생성 초안. displayCategoryCode·이미지·고시정보를 채운 뒤 product_uploader.py --dry-run으로 확인하세요.",
        "_source_url": row["taobao_url"],
        "_source_title_cn": row.get("title_cn") or "",
        "_margin": json.loads(row.get("cost_breakdown") or "{}"),
        "_unsuitable_check": json.loads(row.get("unsuitable_reasons") or "[]"),
        "sellerProductName": title,
        "displayProductName": title,
        "generalProductName": names.get("generalProductName") or "",
        "brand": "바이셀",
        "displayCategoryCode": 0,
        "originalPrice": sale,
        "salePrice": sale,
        "images": [],
        "searchTags": tags,
        "overseasPurchased": "Y",
        "detailHtml": f"<p>{names.get('analysis', '')}</p>",
    }
    out_dir = out_dir or os.path.join(BASE_DIR, "drafts")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"candidate_{cid:03d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with db(db_path) as conn:
        conn.execute(
            "UPDATE sourcing_candidates SET draft_path=?, status=CASE WHEN status='blocked' THEN status ELSE 'exported' END, updated_at=? WHERE id=?",
            (path, now_iso(), cid))
    return path

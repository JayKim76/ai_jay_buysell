"""웹 대시보드 (표준 라이브러리 http.server만 사용 — 추가 설치 없음).

화면: 홈(요약+경보+주문) / 주문·배송 / 문의
조작: 경보 확인·해소, 주문 단계 변경, 운송장 입력, 문의 등록·답변 처리
"""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import json

from . import alerts as alert_engine
from .db import STAGE_LABELS, STAGES, db
from .cs_templates import list_templates, render as render_template
from .settlement import (available_months, distribute_ad_cost, get_settlement,
                         monthly_summary, order_profits, product_summary, set_settlement)
from .sourcing import add_candidate, export_draft, list_candidates, update_candidate
from .sync import add_inquiry, answer_inquiry, update_shipment, update_stage

CANDIDATE_STATUS = {
    "draft": "초안", "ready": "등록 가능", "check_needed": "확인 필요",
    "low_margin": "마진 부족", "blocked": "등록 불가", "exported": "초안 생성됨",
}

CSS = """
body{font-family:-apple-system,'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:0;background:#f6f7f8;color:#1c1e21}
header{background:#22272e;color:#fff;padding:18px 28px}
header h1{font-size:20px;margin:0 0 6px}
nav a{color:#ffd866;margin-right:18px;text-decoration:none;font-size:14px}
main{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
.cards{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}
.card{background:#fff;border:1px solid #dde1e4;border-radius:10px;padding:18px 22px;min-width:150px}
.card .num{font-size:28px;font-weight:700}
.card.critical .num{color:#c0392b}.card.warning .num{color:#b7791f}
section{background:#fff;border:1px solid #dde1e4;border-radius:10px;padding:22px 24px;margin-bottom:24px}
h2{font-size:16px;margin:0 0 14px;border-bottom:2px solid #eef0f2;padding-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #eef0f2;vertical-align:top}
th{color:#5a6169;font-weight:600}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}
.badge.critical{background:#fdecea;color:#c0392b}.badge.warning{background:#fdf6e0;color:#8a6d00}
.badge.stage{background:#e8f0fe;color:#1a56db}
button,input[type=submit]{background:#1a56db;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-size:13px}
button.gray, input.gray{background:#6b7280}
input[type=text],input[type=datetime-local],select{padding:6px 8px;border:1px solid #c9ced4;border-radius:6px;font-size:13px}
form.inline{display:inline}
.small{color:#5a6169;font-size:12.5px}
.empty{color:#5a6169;padding:10px 0}
"""


def _page(title, body):
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<header><h1>바이셀 구매대행 통합 관리</h1>
<nav><a href="/">홈·경보</a><a href="/sourcing">소싱·등록</a><a href="/orders">주문·배송</a><a href="/settlement">정산·손익</a><a href="/inquiries">문의</a>
<a href="/morning">아침 요약</a></nav></header><main>{body}</main></body></html>"""


def _alert_rows(alerts):
    if not alerts:
        return '<p class="empty">열린 경보가 없습니다.</p>'
    rows = []
    for a in alerts:
        rows.append(f"""<tr>
<td><span class="badge {a['severity']}">{'긴급' if a['severity']=='critical' else '주의'}</span></td>
<td><b>{html.escape(a['title'])}</b><br><span class="small">{html.escape(a['detail'] or '')}</span></td>
<td class="small">{html.escape(a['created_at'][:16].replace('T',' '))}</td>
<td>
<form class="inline" method="post" action="/alert/ack"><input type="hidden" name="id" value="{a['id']}"><button class="gray">확인</button></form>
<form class="inline" method="post" action="/alert/resolve"><input type="hidden" name="id" value="{a['id']}"><button>해소</button></form>
</td></tr>""")
    return "<table><tr><th>등급</th><th>경보</th><th>발생</th><th>처리</th></tr>" + "".join(rows) + "</table>"


def _order_rows(orders, shipments):
    if not orders:
        return '<p class="empty">주문이 없습니다. 동기화를 실행하거나 CSV를 임포트하세요.</p>'
    stage_options = "".join(f'<option value="{k}">{v}</option>' for k, v in STAGES)
    rows = []
    for o in orders:
        s = shipments.get(o["id"], {})
        stage_sel = stage_options.replace(
            f'value="{o["stage"]}"', f'value="{o["stage"]}" selected')
        rows.append(f"""<tr>
<td><b>{html.escape(o['channel_order_id'])}</b><br><span class="small">{html.escape(o['ordered_at'][:16].replace('T',' ') if o['ordered_at'] else '')}</span></td>
<td>{html.escape(o['product_name'] or '')}<br><span class="small">수량 {o['quantity']} · {o['order_price'] or ''}원</span></td>
<td><span class="badge stage">{STAGE_LABELS.get(o['stage'], o['stage'])}</span>
<form method="post" action="/order/stage" style="margin-top:6px">
<input type="hidden" name="id" value="{o['id']}">
<select name="stage">{stage_sel}</select> <button class="gray">변경</button></form></td>
<td class="small">출고예정 {html.escape(o['expected_ship_date'] or '-')}</td>
<td><form method="post" action="/order/shipment">
<input type="hidden" name="id" value="{o['id']}">
<input type="text" name="taobao_order_no" placeholder="타오바오 주문번호" value="{html.escape(s.get('taobao_order_no') or '')}" size="13"><br>
<input type="text" name="cn_tracking_no" placeholder="중국 운송장" value="{html.escape(s.get('cn_tracking_no') or '')}" size="13">
<input type="text" name="intl_tracking_no" placeholder="국제 운송장" value="{html.escape(s.get('intl_tracking_no') or '')}" size="13"><br>
<input type="text" name="kr_carrier" placeholder="국내 택배사" value="{html.escape(s.get('kr_carrier') or '')}" size="8">
<input type="text" name="kr_tracking_no" placeholder="국내 운송장" value="{html.escape(s.get('kr_tracking_no') or '')}" size="13">
<button>운송장 저장</button></form></td></tr>""")
    return ("<table><tr><th>주문</th><th>상품</th><th>단계</th><th>일정</th><th>발주·운송장</th></tr>"
            + "".join(rows) + "</table>")


def _candidate_rows(candidates):
    if not candidates:
        return '<p class="empty">소싱 후보가 없습니다. 타오바오 링크를 등록해 보세요.</p>'
    rows = []
    for c in candidates:
        names = json.loads(c.get("generated_names") or "{}")
        bd = json.loads(c.get("cost_breakdown") or "{}")
        reasons = json.loads(c.get("unsuitable_reasons") or "[]")
        tags = json.loads(c.get("search_tags") or "[]")
        reason_html = ""
        if reasons:
            reason_html = "<br><span class='small' style='color:#c0392b'>" + "<br>".join(
                html.escape(r) for r in reasons) + "</span>"
        badge_cls = "critical" if c["status"] == "blocked" else (
            "warning" if c["status"] in ("check_needed", "low_margin") else "stage")
        draft_btn = ""
        if c["status"] != "blocked":
            draft_btn = (f'<form class="inline" method="post" action="/sourcing/draft">'
                         f'<input type="hidden" name="id" value="{c["id"]}"><button>등록 초안 생성</button></form>')
        draft_info = f"<br><span class='small'>초안: {html.escape(c['draft_path'])}</span>" if c.get("draft_path") else ""
        rows.append(f"""<tr>
<td><a href="{html.escape(c['taobao_url'])}" target="_blank">{html.escape(c['platform'] or '')} {html.escape(c['item_id'] or '')}</a>
<br><span class="small">{html.escape(c['title_cn'] or '')}</span></td>
<td><b>{html.escape(names.get('displayProductName_search') or '-')}</b>
<br><span class="small">간결: {html.escape(names.get('displayProductName_short') or '')}</span>
<br><span class="small">태그: {html.escape(' '.join(tags))} · 생성 {html.escape(c['name_method'] or '')}</span>{reason_html}</td>
<td class="small">구매 {bd.get('goods_krw', 0):,}원<br>국제배송 {bd.get('intl_krw', 0):,}원 ({html.escape(str(bd.get('intl_source','')))})<br>
관부가세 {bd.get('tax_krw', 0):,}원 · 수수료 {bd.get('fee_krw', 0):,}원<br>
<b>판매가 {bd.get('sale_price_krw', 0):,}원</b> (역산 {bd.get('suggested_sale_price_krw', 0):,}원)<br>
마진 {bd.get('margin_krw', 0):,}원 ({(bd.get('margin_rate') or 0)*100:.1f}% / 목표 {(bd.get('target_margin_rate') or 0)*100:.0f}%)</td>
<td><span class="badge {badge_cls}">{CANDIDATE_STATUS.get(c['status'], c['status'])}</span><br><br>{draft_btn}{draft_info}
<form method="post" action="/sourcing/update" style="margin-top:10px">
<input type="hidden" name="id" value="{c['id']}">
<input type="text" name="price_cny" placeholder="구매가 CNY" value="{c['price_cny'] if c['price_cny'] is not None else ''}" size="8">
<input type="text" name="weight_kg" placeholder="무게 kg" value="{c['weight_kg'] if c['weight_kg'] is not None else ''}" size="6">
<input type="text" name="intl_shipping_krw" placeholder="국제배송 직접입력" value="{c['intl_shipping_krw'] if c['intl_shipping_krw'] is not None else ''}" size="12"><br>
<input type="text" name="sale_price_krw" placeholder="판매가 (비우면 자동 역산)" value="" size="16">
<button class="gray">재계산</button></form></td></tr>""")
    return ("<table><tr><th>타오바오</th><th>생성 상품명</th><th>마진 계산</th><th>상태·처리</th></tr>"
            + "".join(rows) + "</table>")


def _fetch(db_path):
    with db(db_path) as conn:
        orders = [dict(r) for r in conn.execute(
            "SELECT * FROM orders ORDER BY ordered_at DESC LIMIT 200").fetchall()]
        shipments = {r["order_id"]: dict(r) for r in conn.execute("SELECT * FROM shipments").fetchall()}
        inquiries = [dict(r) for r in conn.execute(
            "SELECT * FROM inquiries ORDER BY received_at DESC LIMIT 200").fetchall()]
    return orders, shipments, inquiries


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def log_message(self, *args):
        pass

    def _send(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path="/"):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        alert_engine.evaluate(self.db_path)
        orders, shipments, inquiries = _fetch(self.db_path)
        alerts = alert_engine.open_alerts(self.db_path)
        if path == "/":
            crit = sum(1 for a in alerts if a["severity"] == "critical")
            unans = sum(1 for q in inquiries if q["status"] == "unanswered")
            inprog = sum(1 for o in orders if o["stage"] not in ("DELIVERED", "CANCELLED"))
            body = f"""
<div class="cards">
<div class="card critical"><div class="num">{crit}</div>긴급 경보</div>
<div class="card warning"><div class="num">{len(alerts)-crit}</div>주의 경보</div>
<div class="card"><div class="num">{inprog}</div>진행 중 주문</div>
<div class="card"><div class="num">{unans}</div>미답변 문의</div>
</div>
<section><h2>경보</h2>{_alert_rows(alerts)}</section>
<section><h2>주문·배송 (최근)</h2>{_order_rows(orders[:30], shipments)}
<p class="small"><a href="/orders">전체 주문 보기</a></p></section>"""
            self._send(_page("홈·경보", body))
        elif path == "/sourcing":
            candidates = list_candidates(self.db_path)
            form = """<form method="post" action="/sourcing/add" style="margin-bottom:18px">
<input type="text" name="taobao_url" placeholder="타오바오 링크" size="42" required>
<input type="text" name="title_cn" placeholder="원제목(중국어)" size="30">
<input type="text" name="desc" placeholder="설명(재질·사이즈 등)" size="24"><br>
<input type="text" name="price_cny" placeholder="구매가 CNY" size="10">
<input type="text" name="cn_shipping_cny" placeholder="중국 배송비 CNY" size="12">
<input type="text" name="weight_kg" placeholder="무게 kg" size="8">
<button>후보 등록 + 상품명 생성</button>
<p class="small">기본 가정: 환율 200원 · 국제배송 9,000원/kg · 관부가세 10% · 쿠팡 수수료 11% · 목표 마진 20% (후보별로 재계산 폼에서 조정 가능). GEMINI_API_KEY가 .env에 있으면 AI 생성, 없으면 규칙 생성으로 동작합니다.</p></form>"""
            self._send(_page("소싱·등록", f"<section><h2>소싱·상품 등록 (2단계)</h2>{form}{_candidate_rows(candidates)}</section>"))
        elif path == "/settlement":
            months = available_months(self.db_path)
            month = query.get("month") or (months[0] if months else "")
            s = monthly_summary(month, self.db_path) if month else None
            month_opts = "".join(
                f'<option value="{m}" {"selected" if m == month else ""}>{m}</option>' for m in months)
            cards = ""
            if s:
                cards = f"""
<div class="cards">
<div class="card"><div class="num">{s['profit']:,}원</div>실이익 ({month}, 정산 {s['orders_settled']}/{s['orders_total']}건)</div>
<div class="card"><div class="num">{s['revenue']:,}원</div>매출 (정산 입력분)</div>
<div class="card"><div class="num">{s['total_cost']:,}원</div>총비용</div>
<div class="card"><div class="num">{s['margin_rate']*100:.1f}%</div>실마진율</div>
</div>
<p class="small">비용 구성: 구매 {s['purchase_krw']:,} · 중국배송 {s['cn_shipping_krw']:,} · 국제배송 {s['intl_shipping_krw']:,} · 관부가세 {s['customs_krw']:,} · 수수료 {s['fee_krw']:,} · 광고 {s['ad_krw']:,} · 기타 {s['other_krw']:,} (원) · 월 귀속은 배송완료일 기준, 없으면 주문일 기준 · 정산 미입력 주문은 손익 합계에서 제외</p>"""
            rows = []
            for r in order_profits(self.db_path, month or None):
                o = r["order"]
                st = "정산됨" if r["settled"] else "미입력"
                profit_html = (f"<b>{r['profit']:,}원</b> ({r['margin_rate']*100:.1f}%)" if r["settled"] else "-")
                rows.append(f"""<tr>
<td><b>{html.escape(o['channel_order_id'])}</b><br><span class="small">{html.escape(o['product_name'] or '')}</span></td>
<td>{r['revenue']:,}원</td>
<td><form method="post" action="/settlement/save">
<input type="hidden" name="id" value="{o['id']}"><input type="hidden" name="month" value="{month}">
<input type="text" name="purchase_cny" placeholder="구매 CNY" size="7">
<input type="text" name="cn_shipping_krw" placeholder="중국배송" size="7">
<input type="text" name="intl_shipping_krw" placeholder="국제배송" size="7">
<input type="text" name="customs_krw" placeholder="관부가세" size="7">
<input type="text" name="other_krw" placeholder="기타" size="6">
<button class="gray">비용 저장</button></form>
<span class="small">구매 {r['purchase_krw']:,} · 배송 {r['cn_shipping_krw'] + r['intl_shipping_krw']:,} · 관부가세 {r['customs_krw']:,} · 수수료 {r['fee_krw']:,} · 광고 {r['ad_krw']:,}</span></td>
<td>{profit_html}<br><span class="small">{st}</span></td></tr>""")
            order_table = ("<table><tr><th>주문</th><th>매출</th><th>실비용 입력 (수수료는 비우면 판매가×11% 자동)</th><th>실마진</th></tr>"
                           + "".join(rows) + "</table>") if rows else '<p class="empty">해당 월 주문이 없습니다.</p>'
            prod_rows = []
            for p in product_summary(self.db_path, month or None):
                color = "#c0392b" if p["margin_rate"] < 0 else ("#b7791f" if p["margin_rate"] < 0.10 else "#1c1e21")
                prod_rows.append(f"""<tr><td>{html.escape(p['product_name'])}</td><td>{p['orders']}건</td>
<td>{p['revenue']:,}원</td><td>{p['profit']:,}원</td><td style="color:{color}"><b>{p['margin_rate']*100:.1f}%</b></td></tr>""")
            prod_table = ("<table><tr><th>상품</th><th>주문</th><th>매출</th><th>이익</th><th>마진율 (낮은 순)</th></tr>"
                          + "".join(prod_rows) + "</table>") if prod_rows else '<p class="empty">정산 입력된 주문이 없습니다.</p>'
            ad_form = f"""<form method="post" action="/settlement/ad" style="margin-bottom:16px">
<input type="hidden" name="month" value="{month}">
<input type="text" name="total_ad_krw" placeholder="{month} 광고비 총액 (원)" size="20">
<button>광고비 매출 비례 배분</button>
<select name="month_select" onchange="location.href='/settlement?month='+this.value">{month_opts}</select>
</form>"""
            body = f"<section><h2>정산·손익 (3단계)</h2>{cards}{ad_form}{order_table}</section><section><h2>상품별 손익</h2>{prod_table}</section>"
            self._send(_page("정산·손익", body))
        elif path == "/orders":
            self._send(_page("주문·배송", f"<section><h2>주문·배송 관리</h2>{_order_rows(orders, shipments)}</section>"))
        elif path == "/inquiries":
            tpl_opts = "".join(f'<option value="{k}">{v}</option>' for k, v in list_templates().items())
            rows = []
            for q in inquiries:
                act = ""
                if q["status"] == "unanswered":
                    act = (f'<form method="post" action="/inquiry/preview" style="margin-bottom:6px">'
                           f'<input type="hidden" name="id" value="{q["id"]}">'
                           f'<select name="template_key">{tpl_opts}</select> '
                           f'<button class="gray">템플릿 미리보기</button></form>'
                           f'<form class="inline" method="post" action="/inquiry/answer">'
                           f'<input type="hidden" name="id" value="{q["id"]}">'
                           f'<input type="text" name="template_used" placeholder="사용 템플릿" size="10"> '
                           f'<button>답변 완료</button></form>')
                rows.append(f"""<tr><td>{html.escape(q['received_at'][:16].replace('T',' ') if q['received_at'] else '')}</td>
<td>{html.escape(q['product_name'] or '')}</td><td>{html.escape(q['summary'] or '')}</td>
<td>{'미답변' if q['status']=='unanswered' else '답변 완료'}</td>
<td class="small">기한 {html.escape(q['due_at'][:16].replace('T',' ') if q['due_at'] else '')}</td><td>{act}</td></tr>""")
            table = "<table><tr><th>접수</th><th>상품</th><th>내용</th><th>상태</th><th>회신 기한</th><th></th></tr>" + "".join(rows) + "</table>" if rows else '<p class="empty">문의가 없습니다.</p>'
            answered = [q for q in inquiries if q["status"] == "answered" and q["answered_at"] and q["received_at"]]
            avg_txt = ""
            if answered:
                from .timeutil import hours_between
                hs = [hours_between(q["received_at"], q["answered_at"]) for q in answered]
                hs = [h for h in hs if h is not None]
                if hs:
                    avg_txt = f'<p class="small">답변 완료 {len(answered)}건 · 평균 답변 소요 {sum(hs)/len(hs):.1f}시간</p>'
            form = """<form method="post" action="/inquiry/add" style="margin-bottom:18px">
<input type="text" name="product_name" placeholder="상품명" size="16">
<input type="text" name="summary" placeholder="문의 내용 요약" size="34">
<input type="datetime-local" name="received_at">
<button>문의 등록</button> <span class="small">회신 기한은 접수 +72시간으로 자동 계산됩니다.</span></form>"""
            self._send(_page("문의", f"<section><h2>CS 문의</h2>{avg_txt}{form}{table}</section>"))
        elif path == "/morning":
            text = html.escape(alert_engine.morning_summary(self.db_path)).replace("\n", "<br>")
            self._send(_page("아침 요약", f"<section><h2>매일 아침 경보 요약</h2><p>{text}</p></section>"))
        else:
            self._send(_page("없음", "<section>페이지를 찾을 수 없습니다.</section>"), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        try:
            if path == "/sourcing/add":
                def _num(key, cast=float):
                    v = form.get(key, "")
                    return cast(v) if v not in ("", None) else None
                add_candidate(
                    form["taobao_url"], form.get("title_cn", ""), form.get("desc", ""),
                    price_cny=_num("price_cny"), cn_shipping_cny=_num("cn_shipping_cny") or 0,
                    weight_kg=_num("weight_kg"), db_path=self.db_path)
                self._redirect("/sourcing")
            elif path == "/sourcing/update":
                fields = {}
                for key in ("price_cny", "weight_kg", "intl_shipping_krw", "sale_price_krw"):
                    v = form.get(key, "")
                    if v != "":
                        fields[key] = float(v) if "." in v else int(v)
                update_candidate(int(form["id"]), fields, self.db_path)
                self._redirect("/sourcing")
            elif path == "/sourcing/draft":
                export_draft(int(form["id"]), self.db_path)
                self._redirect("/sourcing")
            elif path == "/alert/ack":
                alert_engine.set_alert_status(int(form["id"]), "ack", self.db_path)
                self._redirect("/")
            elif path == "/alert/resolve":
                alert_engine.set_alert_status(int(form["id"]), "resolved", self.db_path)
                self._redirect("/")
            elif path == "/order/stage":
                update_stage(int(form["id"]), form["stage"], self.db_path)
                self._redirect("/orders")
            elif path == "/order/shipment":
                update_shipment(int(form["id"]), {k: form.get(k, "") for k in
                                ("taobao_order_no", "cn_tracking_no", "intl_tracking_no",
                                 "kr_carrier", "kr_tracking_no")}, self.db_path)
                self._redirect("/orders")
            elif path == "/inquiry/add":
                received = form.get("received_at") or None
                add_inquiry(form.get("summary", ""), form.get("product_name", ""),
                            received_at=received.replace("T", " ") if received else None,
                            db_path=self.db_path)
                self._redirect("/inquiries")
            elif path == "/inquiry/answer":
                answer_inquiry(int(form["id"]), form.get("template_used", ""), self.db_path)
                self._redirect("/inquiries")
            elif path == "/inquiry/preview":
                qid = int(form["id"])
                with db(self.db_path) as conn:
                    q = conn.execute("SELECT * FROM inquiries WHERE id=?", (qid,)).fetchone()
                tpl_name = list_templates()[form["template_key"]]
                text = render_template(form["template_key"],
                                       product_name=(q["product_name"] if q else "") or "")
                body = f"""<section><h2>템플릿 미리보기 — {html.escape(tpl_name)}</h2>
<textarea rows="12" style="width:100%;font-size:14px;padding:10px" readonly>{html.escape(text)}</textarea>
<p class="small">위 문구를 복사해 쿠팡 문의 답변에 붙여 넣으세요. 확인 후 아래 버튼을 누르면 이 문의가 답변 완료로 처리됩니다.</p>
<form method="post" action="/inquiry/answer">
<input type="hidden" name="id" value="{qid}">
<input type="hidden" name="template_used" value="{html.escape(tpl_name)}">
<button>이 템플릿으로 답변 완료 처리</button></form>
<p><a href="/inquiries">문의 목록으로</a></p></section>"""
                self._send(_page("템플릿 미리보기", body))
            elif path == "/settlement/save":
                fields = {}
                for key in ("purchase_cny", "cn_shipping_krw", "intl_shipping_krw",
                            "customs_krw", "other_krw"):
                    v = form.get(key, "")
                    if v != "":
                        fields[key] = float(v) if "." in v else int(v)
                set_settlement(int(form["id"]), fields, self.db_path)
                self._redirect(f"/settlement?month={form.get('month', '')}")
            elif path == "/settlement/ad":
                distribute_ad_cost(form["month"], int(form.get("total_ad_krw") or 0), self.db_path)
                self._redirect(f"/settlement?month={form.get('month', '')}")
            else:
                self._send(_page("없음", "<section>페이지를 찾을 수 없습니다.</section>"), 404)
        except Exception as e:
            self._send(_page("오류", f"<section>처리 중 오류가 발생했습니다: {html.escape(str(e))}</section>"), 500)


def serve(port=8765, db_path=None):
    Handler.db_path = db_path
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"대시보드 실행 중: http://127.0.0.1:{port}  (종료: Ctrl+C)")
    server.serve_forever()

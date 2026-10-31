"""웹 대시보드 (표준 라이브러리 http.server만 사용 — 추가 설치 없음).

화면: 홈(요약+경보+주문) / 주문·배송 / 문의
조작: 경보 확인·해소, 주문 단계 변경, 운송장 입력, 문의 등록·답변 처리
"""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import alerts as alert_engine
from .db import STAGE_LABELS, STAGES, db
from .sync import add_inquiry, answer_inquiry, update_shipment, update_stage

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
<nav><a href="/">홈·경보</a><a href="/orders">주문·배송</a><a href="/inquiries">문의</a>
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
        path = urlparse(self.path).path
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
        elif path == "/orders":
            self._send(_page("주문·배송", f"<section><h2>주문·배송 관리</h2>{_order_rows(orders, shipments)}</section>"))
        elif path == "/inquiries":
            rows = []
            for q in inquiries:
                act = ""
                if q["status"] == "unanswered":
                    act = (f'<form class="inline" method="post" action="/inquiry/answer">'
                           f'<input type="hidden" name="id" value="{q["id"]}">'
                           f'<input type="text" name="template_used" placeholder="사용 템플릿" size="10"> '
                           f'<button>답변 완료</button></form>')
                rows.append(f"""<tr><td>{html.escape(q['received_at'][:16].replace('T',' ') if q['received_at'] else '')}</td>
<td>{html.escape(q['product_name'] or '')}</td><td>{html.escape(q['summary'] or '')}</td>
<td>{'미답변' if q['status']=='unanswered' else '답변 완료'}</td>
<td class="small">기한 {html.escape(q['due_at'][:16].replace('T',' ') if q['due_at'] else '')}</td><td>{act}</td></tr>""")
            table = "<table><tr><th>접수</th><th>상품</th><th>내용</th><th>상태</th><th>회신 기한</th><th></th></tr>" + "".join(rows) + "</table>" if rows else '<p class="empty">문의가 없습니다.</p>'
            form = """<form method="post" action="/inquiry/add" style="margin-bottom:18px">
<input type="text" name="product_name" placeholder="상품명" size="16">
<input type="text" name="summary" placeholder="문의 내용 요약" size="34">
<input type="datetime-local" name="received_at">
<button>문의 등록</button> <span class="small">회신 기한은 접수 +72시간으로 자동 계산됩니다.</span></form>"""
            self._send(_page("문의", f"<section><h2>CS 문의</h2>{form}{table}</section>"))
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
            if path == "/alert/ack":
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
            else:
                self._send(_page("없음", "<section>페이지를 찾을 수 없습니다.</section>"), 404)
        except Exception as e:
            self._send(_page("오류", f"<section>처리 중 오류가 발생했습니다: {html.escape(str(e))}</section>"), 500)


def serve(port=8765, db_path=None):
    Handler.db_path = db_path
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"대시보드 실행 중: http://127.0.0.1:{port}  (종료: Ctrl+C)")
    server.serve_forever()

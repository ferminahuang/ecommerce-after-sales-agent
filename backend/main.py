"""电商售后 Agent Demo 模拟后端（全部为虚构数据，与任何平台无关）。"""
import json, uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATA = json.loads((Path(__file__).parent / "mock_data.json").read_text(encoding="utf-8"))
TODAY = datetime.now().replace(microsecond=0)
def d(off): return None if off is None else (TODAY + timedelta(days=off)).strftime("%Y-%m-%d %H:%M")

app = FastAPI(title="电商售后 Agent Demo Mock API", version="1.0.0")
TICKETS: dict = {}
NOTIFICATIONS: list = []

def _order(order_id):
    for o in DATA["orders"]:
        if o["order_id"] == order_id.strip().upper():
            return o
    raise HTTPException(404, f"订单 {order_id} 不存在")

@app.get("/orders/{order_id}", operation_id="get_order", summary="查询订单")
def get_order(order_id: str):
    o = _order(order_id)
    u = DATA["users"][o["user_id"]]
    days = None if o["signed_offset"] is None else -o["signed_offset"]
    return {"order_id": o["order_id"], "item": o["item"], "category": o["category"], "amount": o["amount"],
            "merchant_id": o["merchant_id"], "merchant_name": o["merchant_name"], "status": o["status"],
            "paid_at": d(o["paid_offset"]), "signed_at": d(o["signed_offset"]), "days_since_signed": days,
            "tracking_no": o["tracking_no"], "note": o.get("note"),
            "buyer": {"user_id": o["user_id"], "after_sale_count_30d": u["after_sale_count_30d"], "vip": u["vip"]}}

@app.get("/logistics/{order_id}", operation_id="get_logistics", summary="查询物流轨迹")
def get_logistics(order_id: str):
    o = _order(order_id)
    if not o["tracking_no"]:
        return {"order_id": o["order_id"], "status": o["status"], "events": [], "stalled_days": 0}
    ev = DATA["logistics"][o["tracking_no"]]
    stalled = 0 if o["status"] == "已签收" else -ev[-1]["offset"]
    return {"order_id": o["order_id"], "tracking_no": o["tracking_no"], "status": o["status"],
            "events": [{"time": d(e["offset"]), "event": e["event"]} for e in ev], "stalled_days": stalled,
            "stalled": stalled > DATA["policy"]["logistics_stall_days"]}

@app.get("/policy", operation_id="get_policy", summary="查询售后政策")
def get_policy(category: Optional[str] = None):
    p = dict(DATA["policy"])
    if category == "生鲜":
        p["category_note"] = "生鲜不支持 7 天无理由；签收 48 小时内坏损可凭照片描述仅退款"
    return p

COUPONS: dict = {}

@app.post("/logistics/{order_id}/urge", operation_id="urge_logistics", summary="催件；物流停滞超过阈值时自动发 3 元券（每单限 1 张）")
def urge_logistics(order_id: str):
    lg = get_logistics(order_id)
    if lg["status"] == "已签收":
        raise HTTPException(422, "订单已签收，无需催件")
    p = DATA["policy"]; coupon = None
    if lg["stalled_days"] > p["logistics_stall_days"]:
        first = lg["order_id"] not in COUPONS
        COUPONS[lg["order_id"]] = True
        coupon = {"coupon": "3 元无门槛券", "count": 1, "newly_issued": first}
    target = "商家（督促发货）" if lg["status"] == "待发货" else "物流方"
    return {"order_id": lg["order_id"], "urged": True, "urge_target": target, "stalled_days": lg["stalled_days"],
            "coupon": coupon, "sla": p["sla"]["催件反馈"] if target == "物流方" else p["sla"]["商家处理工单"]}

def policy_check(o, t):
    """后端硬校验：返回违规原因（None 表示合规）。转人工永远允许。"""
    if t.resolution == "转人工":
        return None
    p = DATA["policy"]; days = None if o["signed_offset"] is None else -o["signed_offset"]
    cnt = DATA["users"][o["user_id"]]["after_sale_count_30d"]
    if o["amount"] > p["high_amount_handoff"]:
        return f"订单金额 {o['amount']} 元 > {p['high_amount_handoff']} 元，必须转人工"
    if t.resolution in ("仅退款", "退货退款") and cnt >= p["fraud_after_sale_count_30d"]:
        return f"买家近 30 天售后 {cnt} 次（≥{p['fraud_after_sale_count_30d']}），退款类诉求必须转人工"
    if o["status"] == "待发货":
        return None if t.resolution == "仅退款" else "未发货订单只能仅退款"
    if days is None:
        return "订单未签收，不能办理退货/换货/补发，请先查物流"
    if t.resolution == "补发":
        return None if o.get("note") else "无称重或商家记录佐证，不能补发，请转人工"
    if o["category"] == "生鲜":
        return None if (t.resolution == "仅退款" and days <= 2) else "生鲜仅支持签收 48 小时内坏损仅退款"
    if days > p["quality_issue_days"]:
        return f"签收 {days} 天，已超出 {p['quality_issue_days']} 天售后期，必须转人工"
    if t.resolution == "仅退款" and o["amount"] > p["refund_only_quality_max_amount"]:
        return f"金额 > {p['refund_only_quality_max_amount']} 元的已签收订单不支持仅退款，请用退货退款或换货"
    return None

class TicketIn(BaseModel):
    order_id: str
    resolution: str            # 仅退款 / 退货退款 / 换货 / 补发 / 转人工
    reason: str
    evidence: Optional[str] = None   # 照片描述
    summary: str                # 给商家/人工的对话摘要
    handoff: bool = False
    handoff_reason: Optional[str] = None
    merchant_suggestion: Optional[str] = None  # 非转人工工单：建单时自动推送给商家的处理建议

@app.post("/tickets", operation_id="create_after_sale_ticket", summary="创建售后工单")
def create_ticket(t: TicketIn):
    o = _order(t.order_id)
    if t.resolution not in {"仅退款", "退货退款", "换货", "补发", "转人工"}:
        raise HTTPException(422, "resolution 必须是 仅退款/退货退款/换货/补发/转人工")
    if t.handoff != (t.resolution == "转人工"):
        raise HTTPException(422, "resolution=转人工 时 handoff 必须为 true，反之亦然")
    why = policy_check(o, t)
    if why:
        raise HTTPException(422, f"方案不符合售后政策：{why}")
    tid = "AS" + uuid.uuid4().hex[:8].upper()
    TICKETS[tid] = {"ticket_id": tid, **t.model_dump(), "amount": o["amount"], "merchant_id": o["merchant_id"],
                    "status": "待人工处理" if t.handoff else "待商家处理", "created_at": TODAY.strftime("%Y-%m-%d %H:%M")}
    sla = DATA["policy"]["sla"]
    if t.handoff:
        TICKETS[tid]["merchant_notification"] = {"sent": False, "reason": "转人工工单由人工专员处理，不推送商家"}
        TICKETS[tid]["sla"] = sla["人工客服跟进"]
    else:   # 自动通知商家（取代单独调用 notify_merchant，避免拿错 ticket_id）
        msg = {"merchant_id": o["merchant_id"], "ticket_id": tid, "resolution": t.resolution, "summary": t.summary,
               "suggestion": t.merchant_suggestion or f"按政策同意{t.resolution}", "sent": True, "auto": True}
        NOTIFICATIONS.append(msg)
        TICKETS[tid]["merchant_notification"] = msg
        TICKETS[tid]["sla"] = {"仅退款": sla["退款到账"], "退货退款": sla["退款到账"], "换货": sla["换货/补发发出"], "补发": sla["换货/补发发出"]}[t.resolution]
    return TICKETS[tid]

class NotifyIn(BaseModel):
    ticket_id: str
    suggestion: str   # 给商家的处理建议

@app.post("/merchant/notify", operation_id="notify_merchant", summary="通知商家并附处理建议")
def notify_merchant(n: NotifyIn):
    if n.ticket_id not in TICKETS:
        raise HTTPException(404, "工单不存在：ticket_id 必须使用 create_after_sale_ticket 返回值（建单已自动通知商家，一般无需再调用本接口）")
    t = TICKETS[n.ticket_id]
    if t["handoff"]:
        raise HTTPException(422, "转人工工单不推送商家，由人工专员处理")
    msg = {"merchant_id": t["merchant_id"], "ticket_id": n.ticket_id, "resolution": t["resolution"],
           "summary": t["summary"], "suggestion": n.suggestion, "sent": True, "supplement": True}
    NOTIFICATIONS.append(msg)
    return msg

@app.get("/merchant/{merchant_id}/tickets", summary="商家端：查看收到的工单（演示用）")
def merchant_tickets(merchant_id: str):
    return [t for t in TICKETS.values() if t["merchant_id"] == merchant_id]

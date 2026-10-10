"""确定性路由测试：不调用任何大模型。
用一个按 system_prompt 规则写死的决策函数 + 真实的 mock 后端（FastAPI TestClient），
检查“政策规则 + 模拟数据 + 测试用例期望”三者是否一致，以及工具调用顺序/工单是否能成功创建。
它验证的是规则与工具链，不代表 LLM 实际表现。"""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "harness")]
from fastapi.testclient import TestClient
from main import app
from cases import CASES
c = TestClient(app)

def decide(order_id, intent, signals):
    calls = ["get_order"]; o = c.get(f"/orders/{order_id}").json()
    if intent == "logistics":
        calls.append("get_logistics"); return calls, "物流答复", None
    calls.append("get_policy"); p = c.get("/policy", params={"category": o["category"]}).json()
    days, amt = o["days_since_signed"], o["amount"]
    hand = None
    if amt > p["high_amount_handoff"]: hand = "高金额"
    elif "angry" in signals: hand = "情绪激动"
    elif o["buyer"]["after_sale_count_30d"] >= p["fraud_after_sale_count_30d"] or {"other_account","mismatch"} & set(signals): hand = "疑似薅羊毛"
    if hand is None:
        if o["status"] == "待发货": res = "仅退款"
        elif days is None: hand = "疑似薅羊毛"   # 未签收却称收到货
        elif intent == "missing" and o.get("note"): res = "补发"
        elif o["category"] == "生鲜": res = "仅退款" if days <= 2 and intent == "quality" else None
        elif intent == "unwanted": res = "退货退款" if days <= p["no_reason_return_days"] else None
        elif intent == "size": res = "换货" if days <= p["no_reason_return_days"] else None
        elif intent.startswith("quality"):
            if days > p["quality_issue_days"]: res = None
            elif intent == "quality" and amt <= p["refund_only_quality_max_amount"]: res = "仅退款"
            elif intent == "quality_exchange": res = "换货"
            else: res = "退货退款"
        if hand is None and res is None: hand = "超出政策"
    resolution = "转人工" if hand else res
    calls.append("create_after_sale_ticket")
    t = c.post("/tickets", json={"order_id": order_id, "resolution": resolution, "reason": intent,
              "summary": f"harness:{intent}", "handoff": bool(hand), "handoff_reason": hand})
    assert t.status_code == 200, t.text
    if not hand:
        calls.append("notify_merchant")
        assert c.post("/merchant/notify", json={"ticket_id": t.json()["ticket_id"], "suggestion": "按政策处理"}).status_code == 200
    return calls, resolution, hand

ok = fail = skip = 0
for cid, cat, _, tools, outcome, _, h in CASES:
    if h is None:
        skip += 1
        if cid != "TC30": print(f"SKIP {cid} {cat}（需 LLM/人工评测）")
        continue
    calls, res, hand = decide(*h)
    exp_res = "转人工" if "转人工" in outcome else ("物流答复" if cat == "物流查询" else outcome.split("（")[0])
    exp_calls = [x.split("(")[0].strip() for x in tools.split("→")]
    good = (exp_res in res or res.startswith(exp_res)) and calls == exp_calls
    ok += good; fail += not good
    print(f"{'PASS' if good else 'FAIL'} {cid} {cat}: 期望[{outcome}] 得到[{res}{'/'+hand if hand else ''}] 调用{calls}")
m = c.get("/merchant/M01/tickets").json()
need = {"resolution", "summary", "reason", "ticket_id"}
good = any(t["resolution"] == "换货" for t in m) and all(need <= set(t) for t in m)
print(f"{'PASS' if good else 'FAIL'} TC30 商家端视角: M01 收到 {len(m)} 张工单，含换货工单={good}")
skip -= 1; ok += good; fail += not good
print(f"\n确定性路由测试：{ok} 通过，{fail} 失败，{skip} 跳过（共 {len(CASES)} 条）")
sys.exit(1 if fail else 0)

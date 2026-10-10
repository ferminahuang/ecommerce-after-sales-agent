"""对 Dify 云端 Agent 跑 30 条用例。每条用例前重启 mock 后端，保证工单状态独立。API key 从环境变量 DIFY_APP_KEY 读取，不打印。"""
import csv, json, re, subprocess, time, os, sys, signal, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = os.environ["DIFY_APP_KEY"]  # your Dify app API key
API = "https://api.dify.ai/v1/chat-messages"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
BACK = "http://127.0.0.1:8000"

def restart_backend(tag):
    subprocess.run("pkill -f 'uvicorn main:app'", shell=True); time.sleep(1)
    log = open(f"{ROOT}/eval/uvicorn_logs/{tag}.log", "w")
    subprocess.Popen([f"{ROOT}/backend/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
                     cwd=f"{ROOT}/backend", stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(30):
        try:
            if requests.get(f"{BACK}/orders/PD1001", timeout=2).ok: return
        except Exception: pass
        time.sleep(0.5)
    raise RuntimeError("backend not up")

def chat(query, conv):
    body = {"inputs": {}, "query": query, "response_mode": "streaming", "user": "eval-runner", "conversation_id": conv or ""}
    for attempt in range(2):
        try:
            r = requests.post(API, headers=H, json=body, stream=True, timeout=(15, 180))
            if r.status_code != 200:
                return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
            answer, thoughts, cid, err = "", {}, conv, None
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"): continue
                try: ev = json.loads(line[5:].strip())
                except Exception: continue
                cid = ev.get("conversation_id") or cid
                e = ev.get("event")
                if e == "agent_message" or e == "message": answer += ev.get("answer", "")
                elif e == "agent_thought": thoughts[ev["id"]] = ev
                elif e == "error": err = ev.get("message")
            th = [{"position": t.get("position"), "thought": t.get("thought"), "tool": t.get("tool"),
                   "tool_input": t.get("tool_input"), "observation": t.get("observation")} for t in sorted(thoughts.values(), key=lambda x: x.get("position", 0))]
            return {"answer": answer, "thoughts": th, "conversation_id": cid, "error": err}
        except requests.RequestException as ex:
            if attempt == 0: time.sleep(5); continue
            return {"error": f"network: {ex}"}

def turns_of(text):
    return [re.sub(r"^（[^）]*）", "", t).strip() for t in text.split("｜") if t.strip()]

rows = list(csv.DictReader(open(f"{ROOT}/测试用例.csv", encoding="utf-8-sig")))
only = sys.argv[1:] 
out = open(f"{ROOT}/eval/transcripts.jsonl", "a" if only else "w", encoding="utf-8")
by_id = {r["用例ID"]: r for r in rows}
for r in rows:
    cid = r["用例ID"]
    if only and cid not in only: continue
    restart_backend(cid)
    script = turns_of(by_id["TC06"]["用户输入/多轮（｜分隔轮次）"]) if cid == "TC30" else turns_of(r["用户输入/多轮（｜分隔轮次）"])
    conv, turns = None, []
    for q in script:
        res = chat(q, conv); time.sleep(2.5)
        conv = res.get("conversation_id") or conv
        turns.append({"user": q, **res})
        if res.get("error") and not res.get("answer"): break
    tickets = {}
    for m in ["M01","M02","M03","M04","M05","M06"]:
        t = requests.get(f"{BACK}/merchant/{m}/tickets", timeout=5).json()
        if t: tickets[m] = t
    rec = {"id": cid, "category": r["类别"], "turns": turns, "tickets": tickets}
    out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
    tools = [th["tool"] for t in turns for th in t.get("thoughts", []) if th.get("tool")]
    print(cid, "| tools:", tools, "| tickets:", [(x["resolution"], x["handoff"]) for v in tickets.values() for x in v], "| err:", [t.get("error") for t in turns if t.get("error")], flush=True)
# 最后恢复常驻后端（主日志）
subprocess.run("pkill -f 'uvicorn main:app'", shell=True); time.sleep(1)
subprocess.Popen(f"cd {ROOT}/backend && nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 >> {ROOT}/logs/uvicorn.log 2>&1 &", shell=True)

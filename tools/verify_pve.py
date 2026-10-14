"""Drive ONLY one human seat through HTTP; the server supplies all three AIs."""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from tractor_sim.enoch import AI_ID
from tractor_sim.policy import RandomPolicy


def verify(base, seat=0):
    latencies = []
    def request(path, data=None, token=None):
        headers = {"Content-Type":"application/json"}
        if token:
            headers["Authorization"] = "Bearer "+token
        req = urllib.request.Request(base+path,headers=headers,
                    data=json.dumps(data).encode() if data is not None else None)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=10) as reply:
                result = reply.status,json.load(reply)
        except urllib.error.HTTPError as error:
            result = error.code,json.load(error)
        latencies.append(time.monotonic()-started)
        return result
    assert request("/api/health")[1]["pve_ai"] == AI_ID
    status, snap = request("/api/session",{"mode":"pve","name":"QA human","seat":seat,"seed":121,
                          "config":{"initial_level":11,"auction":False,"banker":0}})
    assert status == 200
    token = snap["token"]
    human = RandomPolicy(123)
    human_actions = 0
    phases = set(); thinking_seen = False
    started = time.monotonic()
    try:
        while time.monotonic()-started < 180:
            state = snap["state"]
            assert "hands" not in state and "deck" not in state
            assert sum(p["bot"] for p in snap["players"]) == 3
            assert snap["controlled"] == [seat] and not snap["paused"]
            assert state["kitty"] is None or state["kitty_public"]
            phases.add(state["phase"]); thinking_seen |= snap["bot_thinking"]
            if state["phase"] in ("round_end","match_end"):
                return {"policy":AI_ID,"human_seat":seat,"human_actions":human_actions,
                        "phases":sorted(phases),"three_server_bots":True,
                        "private_hands":True,"search_seen":thinking_seen,
                        "max_http_ms":round(max(latencies)*1000,2),
                        "seconds":round(time.monotonic()-started,2),"result":state["result"]}
            grace = state.get('bidding_window',{}).get('complete',False)
            if state["current_player"] == seat or grace:
                action = human.act(state)
                if grace and action['type'] != 'bid':
                    time.sleep(.05);_,snap=request('/api/state',token=token);continue
                realtime = state['phase']=='dealing' and action['type']=='bid'
                status,response = request('/api/bid' if realtime else '/api/action',
                    {'version':snap['version'],'round':state['round'],'redeals':state['redeals'],'action':action},token)
                if status == 409 or realtime and status == 422 and response.get('code') in ('BID_TOO_WEAK','BIDDING_CLOSED','SELF_COUNTER'):
                    _,snap = request("/api/state",token=token)
                    continue
                assert status == 200,response
                snap = response; human_actions += 1
            else:
                # Frequent state requests exercise responsiveness while planning.
                time.sleep(.05)
                status,snap = request("/api/state",token=token)
                assert status == 200
        raise AssertionError("PVE round timed out")
    finally:
        request("/api/leave",{},token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--seat",type=int,choices=range(4),default=0)
    parser.add_argument("--output",type=Path,default=Path("build/pve-validation.json"))
    args = parser.parse_args()
    result = verify(args.url.rstrip("/"),args.seat)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

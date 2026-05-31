#!/usr/bin/env python3
"""Ad-hoc: gather per-file vs batch cost/accuracy + per-doc no_skill result.json usage."""
import json, os, glob

SLUGS = ["ffiec_call_reports", "sec_10q_insurance", "irs_form_990", "ctgov_protocols"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        return {"_err": str(e), "_path": p}

print("=" * 90)
print("AGGREGATE: per-file (summary.json) vs batch (summary_batch.json)")
print("=" * 90)
for slug in SLUGS:
    rd = os.path.join(ROOT, "results", f"{slug}_rerun_2026-05-30")
    pf = load(os.path.join(rd, "summary.json"))
    ba = load(os.path.join(rd, "summary_batch.json"))
    print(f"\n### {slug}")
    for label, s in [("PER-FILE", pf), ("BATCH", ba)]:
        if "_err" in s:
            print(f"  {label}: MISSING ({s['_err']})")
            continue
        m = s.get("metrics", {})
        acc = m.get("accuracy", {})
        cost = m.get("cost", {})
        ws, ns = cost.get("with_skill", {}), cost.get("no_skill", {})
        print(f"  {label}: n_pairs={s.get('n_pairs')}")
        print(f"    acc:  with={acc.get('with_skill'):.3f}  no={acc.get('no_skill'):.3f}")
        print(f"    with_skill: tok=${ws.get('tokens_usd',0):.3f} cred=${ws.get('credits_usd',0):.3f} "
              f"total=${ws.get('total_usd',0):.3f} pages={ws.get('pages')}")
        print(f"    no_skill:   tok=${ns.get('tokens_usd',0):.3f} cred=${ns.get('credits_usd',0):.3f} "
              f"total=${ns.get('total_usd',0):.3f} pages={ns.get('pages')}")
        if ws.get('total_usd') and ns.get('total_usd'):
            ratio = ws['total_usd'] / ns['total_usd']
            print(f"    ratio with/no (total): {ratio:.2f}x   "
                  f"no_skill cheaper? {'YES' if ns['total_usd'] < ws['total_usd'] else 'NO'}")

print("\n" + "=" * 90)
print("PER-DOC no_skill result.json usage (per-file mode) — token composition per document")
print("=" * 90)
IN, OUT, CR, CW = 5/1e6, 25/1e6, 0.5/1e6, 6.25/1e6  # opus-4.7 reconstructed rates
for slug in SLUGS:
    docs = sorted(glob.glob(os.path.join(ROOT, "runs", slug, "*_no_skill")))
    print(f"\n### {slug}  ({len(docs)} no_skill docs)")
    print(f"  {'doc':<34} {'turns':>5} {'out':>8} {'cache_rd':>10} {'cache_wr':>9} {'in':>6} {'cost$':>8}")
    tot = {"out": 0, "cr": 0, "cw": 0, "in": 0, "cost": 0, "turns": 0}
    for d in docs:
        r = load(os.path.join(d, "session", "result.json"))
        if "_err" in r:
            print(f"  {os.path.basename(d):<34} MISSING")
            continue
        u = r.get("usage", {})
        o = u.get("output_tokens", 0); cr = u.get("cache_read_input_tokens", 0)
        cw = u.get("cache_creation_input_tokens", 0); i = u.get("input_tokens", 0)
        cost = r.get("total_cost_usd", 0); turns = r.get("num_turns", 0)
        name = os.path.basename(d).replace("_no_skill", "")
        print(f"  {name:<34} {turns:>5} {o:>8} {cr:>10} {cw:>9} {i:>6} {cost:>8.3f}")
        tot["out"] += o; tot["cr"] += cr; tot["cw"] += cw; tot["in"] += i
        tot["cost"] += cost; tot["turns"] += turns
    n = max(1, len([d for d in docs]))
    print(f"  {'TOTAL':<34} {tot['turns']:>5} {tot['out']:>8} {tot['cr']:>10} {tot['cw']:>9} {tot['in']:>6} {tot['cost']:>8.3f}")
    # cost decomposition
    c_out = tot['out']*OUT; c_cr = tot['cr']*CR; c_cw = tot['cw']*CW; c_in = tot['in']*IN
    tc = c_out + c_cr + c_cw + c_in
    if tc > 0:
        print(f"  cost decomp: out={c_out:.3f}({100*c_out/tc:.0f}%) "
              f"cache_rd={c_cr:.3f}({100*c_cr/tc:.0f}%) "
              f"cache_wr={c_cw:.3f}({100*c_cw/tc:.0f}%) in={c_in:.3f}({100*c_in/tc:.0f}%)")
        print(f"  avg turns/doc: {tot['turns']/n:.1f}   avg cost/doc: ${tot['cost']/n:.3f}")

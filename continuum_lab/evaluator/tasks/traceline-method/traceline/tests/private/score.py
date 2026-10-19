"""Independent inspection reducer. Uses the private frozen catalog, never world code."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def score_payload(payload, contract, catalog=None):
    errors=[]
    def require(ok,message):
        if not ok: errors.append(message)
    def fail():
        return {"passed":False,"criteria":{},"metrics":{},"integrity_errors":errors}
    if catalog is None:
        try:
            catalog=json.loads(Path(__file__).with_name("catalog.json").read_text())
        except Exception as exc:
            errors.append("Private frozen catalog cannot be loaded: "+str(exc))
            return fail()
    require(isinstance(payload,dict),"payload must be an object")
    if errors: return fail()
    require(payload.get("contract")==contract,"exported contract differs from frozen contract")
    require(payload.get("schema_version")==contract["schema_version"],"schema mismatch")
    require(payload.get("catalog_sha256")==hashlib.sha256(canonical(catalog)).hexdigest(),"private asset catalog hash mismatch")
    require(payload.get("mode")=="live","developer traces cannot be submitted")
    require(payload.get("speed")==1,"live world was not at wall-clock speed")
    require(finite(payload.get("duration_ms")) and payload["duration_ms"]==contract["horizon_ms"],"episode is incomplete")
    require(isinstance(payload.get("session_id"),str) and bool(payload["session_id"]),"session identity missing")
    manifest,actions=payload.get("manifest"),payload.get("actions")
    require(isinstance(manifest,list),"missing private sampled-image manifest")
    require(isinstance(actions,list),"missing raw actions")
    if errors: return fail()
    try:
        queries={x["asset_id"]:x for x in catalog["items"]}
        references={x["asset_id"]:x for x in catalog["references"]}
    except (KeyError,TypeError) as exc:
        errors.append("malformed private catalog: "+str(exc)); return fail()
    require(len(manifest)==contract["defect_count"]+contract["normal_count"],"incorrect scene size")
    ids,asset_ids,image_hashes=set(),set(),set()
    carriers={}
    for index,row in enumerate(manifest):
        try:
            ident=row["carrier_id"]
            require(isinstance(ident,str) and ident not in ids,"duplicate/invalid carrier identity")
            ids.add(ident)
            asset=queries.get(row["asset_id"])
            reference=references.get(row["reference_asset_id"])
            require(asset is not None and reference is not None,"sampled image or normal reference absent from frozen catalog")
            if asset is None or reference is None: continue
            require(row["asset_id"] not in asset_ids and row["image_sha256"] not in image_hashes,"query photographs were repeated")
            asset_ids.add(row["asset_id"]); image_hashes.add(row["image_sha256"])
            require(asset["split"]=="live" and row["split"]=="live","developer photograph leaked into live scene")
            require(asset["label"] in ["normal","defect"],"invalid official quality label")
            require(row["label"]==asset["label"],"scene quality label differs from official catalog")
            require(row["sku"]==asset["sku"]==reference["sku"],"query and reference SKU differ")
            require(row["image_sha256"]==asset["sha256"] and row["reference_sha256"]==reference["sha256"],"image hash differs from frozen asset")
            require(row["asset_id"]!=row["reference_asset_id"] and row["image_sha256"]!=row["reference_sha256"],"query reuses normal reference image")
            require(row["lane"]==contract["lane_keys"][index%4],"carrier lane assignment differs from declared cadence")
            entry=row["entry_ms"]
            require(finite(entry) and (entry==0 if index==0 else abs(entry-index*contract["entry_interval_ms"])<=contract["entry_jitter_ms"]),"entry time outside frozen cadence")
            if errors: continue
            arrival=entry+1000*contract["distance_to_gate_mm"]/contract["lane_speed_mm_s"][row["lane"]]
            require(arrival+contract["grab_window_ms"]<contract["horizon_ms"],"carrier has no complete rejection opportunity")
            carriers[ident]={"id":ident,"lane":row["lane"],"entry_ms":entry,"arrival_ms":arrival,
                             "label":asset["label"],"asset_id":row["asset_id"]}
        except (KeyError,TypeError,ValueError,ZeroDivisionError) as exc:
            errors.append("malformed sampled-image manifest: "+str(exc))
    require(sum(x["label"]=="defect" for x in carriers.values())==contract["defect_count"],"incorrect defective-image count")
    require(sum(x["label"]=="normal" for x in carriers.values())==contract["normal_count"],"incorrect normal-image count")
    if errors: return fail()
    removed,ids=set(),set()
    last={key:-math.inf for key in contract["lane_keys"]}
    counts={key:0 for key in ["target_grab","wrong_grab","empty","collision","expired","wrong_session","not_running"]}
    prior=-math.inf
    outcomes={ident:{"carrier_id":ident,"truth":row["label"],"rejected":False,"arrival_ms":row["arrival_ms"],
                     "action_ms":None,"timing_error_ms":None} for ident,row in carriers.items()}
    for index,event in enumerate(actions):
        prefix=f"action {index}: "
        try:
            req,at=event["request"],event["clock_ms"]
            require(finite(at) and 0<=at<=contract["horizon_ms"] and at>=prior,prefix+"invalid/nonmonotonic timestamp")
            prior=at
            ident,key=req["id"],req["key"]
            require(isinstance(ident,str) and 1<=len(ident)<=128 and ident not in ids,prefix+"duplicate/invalid action id")
            ids.add(ident)
            require(key in contract["lane_keys"],prefix+"invalid key")
            phase=event["phase"]
            require(phase in ["ready","running","finished"],prefix+"invalid world phase")
            require(phase!="ready" or at==0,prefix+"ready clock advanced")
            require(phase!="finished" or at==contract["horizon_ms"],prefix+"premature finish")
            expires=req.get("expires_ms")
            require(expires is None or finite(expires),prefix+"invalid expiry")
            if errors: continue
            hit=None
            if req.get("session_id",payload["session_id"])!=payload["session_id"]:
                result="wrong_session"
            elif phase!="running":
                result="not_running"
            elif expires is not None and at>expires:
                result="expired"
            elif at-last[key]<contract["arm_cooldown_ms"]:
                result="collision"
            else:
                last[key]=at
                candidates=[(abs(at-x["arrival_ms"]),x["id"],x) for x in carriers.values()
                            if x["lane"]==key and x["id"] not in removed and at>=x["entry_ms"]
                            and abs(at-x["arrival_ms"])<=contract["grab_window_ms"]]
                result="empty"
                if candidates:
                    carrier=min(candidates)[2]
                    hit=carrier["id"]; removed.add(hit)
                    result="target_grab" if carrier["label"]=="defect" else "wrong_grab"
                    outcomes[hit].update(rejected=True,action_ms=at,timing_error_ms=at-carrier["arrival_ms"])
            counts[result]+=1
            expected={"id":ident,"key":key,"clock_ms":at,"result":result,"session_id":payload["session_id"]}
            require(event["receipt"]==expected,prefix+"receipt disagrees with independent reduction")
            require(event.get("hit_id")==hit,prefix+"hit identity disagrees with independent reduction")
        except (KeyError,TypeError,ValueError) as exc:
            errors.append(prefix+"malformed event: "+str(exc))
    captures=payload.get("captures",[])
    require(isinstance(captures,list),"capture log is malformed")
    if isinstance(captures,list):
        for capture in captures:
            at=capture.get("clock_ms")
            require(finite(at) and 0<=at<=contract["horizon_ms"],"invalid capture timestamp")
            require(capture.get("kind") in ["frame","inspection"],"unknown capture kind")
            require(isinstance(capture.get("sha256"),str) and bool(re.fullmatch("[0-9a-f]{64}",capture["sha256"])),"invalid capture hash")
            if capture.get("kind")=="inspection":
                item=carriers.get(capture.get("carrier_id"))
                require(item is not None and finite(at) and item["entry_ms"]<=at,"future/unknown inspection image was exposed")
    metrics={"defect_count":contract["defect_count"],"normal_count":contract["normal_count"],
             "defect_grabs":counts["target_grab"],"normal_grabs":counts["wrong_grab"],
             "missed_defects":contract["defect_count"]-counts["target_grab"],
             "defect_recall":counts["target_grab"]/contract["defect_count"],
             "normal_preservation":1-counts["wrong_grab"]/contract["normal_count"],
             "empty_grabs":counts["empty"],"collisions":counts["collision"],"expired_actions":counts["expired"],
             "rejected_session_actions":counts["wrong_session"],"not_running_actions":counts["not_running"],
             "action_count":len(actions),"capture_count":len(captures),"duration_ms":payload["duration_ms"]}
    criteria={"complete_live_episode":payload["duration_ms"]==contract["horizon_ms"],
              "defect_recall":counts["target_grab"]>=contract["minimum_defect_grabs"],
              "normal_false_rejection_budget":counts["wrong_grab"]<=contract["maximum_normal_grabs"],
              "empty_operation_budget":counts["empty"]<=contract["maximum_empty_grabs"],
              "no_collisions":counts["collision"]<=contract["maximum_collisions"],"trace_integrity":not errors}
    return {"passed":all(criteria.values()),"criteria":criteria,"metrics":metrics,
            "item_outcomes":list(outcomes.values()),"integrity_errors":errors}

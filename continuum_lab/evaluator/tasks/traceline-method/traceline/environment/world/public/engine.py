"""Private photographic inspection world; online interfaces contain no quality labels."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from scene import canonical as canonical_json, catalog_hash, create_manifest

PUBLIC_CONTRACT = json.loads(Path(__file__).with_name("contract.json").read_text())


def _font(size, bold=False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans"+("-Bold" if bold else "")+".ttf",
                  "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


class World:
    def __init__(self, seed, task_id, mode="live", speed=1.0, contract=None, asset_catalog=None, asset_root=None):
        self.contract = dict(contract or PUBLIC_CONTRACT)
        self.seed, self.task_id, self.mode = int(seed), str(task_id), mode
        self.speed = float(speed) if mode == "dev" else 1.0
        self.asset_catalog_path = Path(asset_catalog).resolve()
        self.asset_root = Path(asset_root).resolve() if asset_root else self.asset_catalog_path.parent
        self.catalog = json.loads(self.asset_catalog_path.read_text())
        self.catalog_sha256 = catalog_hash(self.catalog)
        self.manifest, self.horizon_ms = create_manifest(self.seed, mode, self.catalog, self.contract)
        self.asset_rows = {x["asset_id"]: x for x in self.catalog["items"]+self.catalog["references"]}
        self.images = {}
        for ident in {row[k] for row in self.manifest for k in ["asset_id","reference_asset_id"]}:
            info = self.asset_rows[ident]
            path = (self.asset_root/info["image"]).resolve()
            if not path.is_relative_to(self.asset_root):
                raise ValueError("Catalog asset escapes its private root")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise ValueError("Asset hash mismatch: "+ident)
            self.images[ident] = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        self.session_id = str(uuid.uuid4())
        self.started_monotonic, self.started_unix = None, None
        self.created_unix = time.time()
        self.lock = threading.RLock()
        self.actions, self.captures, self.events = [], [], []
        self.receipts, self.removed = {}, set()
        self.last_grab_ms = {key: -math.inf for key in self.contract["lane_keys"]}
        self.fonts = {size: _font(size, size>=20) for size in [13,15,17,20,24,28]}

    def clock_ms(self):
        if self.started_monotonic is None:
            return 0.0
        return min(float(self.horizon_ms),(time.monotonic()-self.started_monotonic)*1000*self.speed)

    def phase(self):
        return "ready" if self.started_monotonic is None else ("finished" if self.clock_ms()>=self.horizon_ms else "running")

    def due_ms(self, carrier):
        return carrier["entry_ms"]+1000*self.contract["distance_to_gate_mm"]/self.contract["lane_speed_mm_s"][carrier["lane"]]

    def active(self, now):
        if self.started_monotonic is None:
            return []
        return [x for x in self.manifest if x["entry_ms"]<=now<=self.due_ms(x)+self.contract["grab_window_ms"] and x["carrier_id"] not in self.removed]

    def begin(self):
        with self.lock:
            if self.started_monotonic is None:
                self.started_monotonic = time.monotonic()
                self.started_unix = time.time()
                self.events.append({"kind":"begin","clock_ms":0.0})
            return self.observe()

    def act(self, request):
        with self.lock:
            now, phase = self.clock_ms(), self.phase()
            ident, key = request.get("id"), request.get("key")
            if not isinstance(ident,str) or not 1<=len(ident)<=128:
                raise ValueError("id must be a nonempty string of at most 128 characters")
            if key not in self.contract["lane_keys"]:
                raise ValueError("key must be d, f, j, or k")
            if ident in self.receipts:
                prior=self.receipts[ident]
                if prior["request"]!=request:
                    raise ValueError("id was reused with a different request")
                return dict(prior["receipt"])
            expires=request.get("expires_ms")
            if expires is not None and (isinstance(expires,bool) or not isinstance(expires,(int,float)) or not math.isfinite(expires)):
                raise ValueError("expires_ms must be finite")
            hit=None
            if request.get("session_id",self.session_id)!=self.session_id:
                result="wrong_session"
            elif phase!="running":
                result="not_running"
            elif expires is not None and now>expires:
                result="expired"
            elif now-self.last_grab_ms[key]<self.contract["arm_cooldown_ms"]:
                result="collision"
            else:
                self.last_grab_ms[key]=now
                eligible=[(abs(now-self.due_ms(x)),x["carrier_id"],x) for x in self.manifest
                          if x["lane"]==key and x["carrier_id"] not in self.removed
                          and x["entry_ms"]<=now and abs(now-self.due_ms(x))<=self.contract["grab_window_ms"]]
                result="empty"
                if eligible:
                    carrier=min(eligible)[2]
                    hit=carrier["carrier_id"]
                    self.removed.add(hit)
                    result="target_grab" if carrier["label"]=="defect" else "wrong_grab"
            receipt={"id":ident,"key":key,"clock_ms":round(now,6),"result":result,"session_id":self.session_id}
            self.actions.append({"request":dict(request),"clock_ms":round(now,6),"phase":phase,"receipt":dict(receipt),"hit_id":hit})
            self.receipts[ident]={"request":dict(request),"receipt":receipt}
            return dict(receipt)

    def _photo(self, image, ident, box):
        x,y,w,h=box
        photo=ImageOps.contain(self.images[ident],(w,h),Image.Resampling.LANCZOS)
        image.paste(photo,(x+(w-photo.width)//2,y+(h-photo.height)//2))

    def render_inspection(self, carrier, now):
        image=Image.new("RGB",(1920,1120),"#111b27")
        draw=ImageDraw.Draw(image)
        draw.text((30,18),f"INSPECTION  {carrier['carrier_id']}  |  LANE {carrier['lane'].upper()}  |  {carrier['sku']}",font=self.fonts[28],fill="#edf3fa")
        draw.text((30,60),f"Public capture {now/1000:.3f}s  |  Entry sensor {carrier['entry_ms']/1000:.3f}s  |  Gate estimate {self.due_ms(carrier)/1000:.3f}s",font=self.fonts[20],fill="#a5bdcf")
        draw.text((30,110),"QUERY — decide quality from this image",font=self.fonts[24],fill="#f2c678")
        draw.text((990,110),"INDEPENDENT KNOWN-NORMAL REFERENCE",font=self.fonts[24],fill="#8ed1cf")
        self._photo(image,carrier["asset_id"],(25,160,920,890))
        self._photo(image,carrier["reference_asset_id"],(975,160,920,890))
        draw.text((30,1080),"Whole photographs. No defect mask, annotation, quality label or ground-truth crop is shown.",font=self.fonts[17],fill="#b6c5d4")
        stream=io.BytesIO(); image.save(stream,format="PNG"); return stream.getvalue()

    def render(self, now):
        image=Image.new("RGB",(1920,1200),"#0d1722")
        draw=ImageDraw.Draw(image)
        draw.text((25,16),"MULTI-SKU BOARD INSPECTION / TIMED REJECT CELL",font=self.fonts[28],fill="#edf5fb")
        draw.text((1550,18),f"CLOCK {now/1000:07.3f}s",font=self.fonts[24],fill="#edf5fb")
        active=self.active(now)
        for index,key in enumerate(self.contract["lane_keys"]):
            x,y=(index%2)*960,70+(index//2)*470
            draw.rounded_rectangle((x+12,y,x+948,y+455),radius=8,fill="#172535",outline="#3f5368",width=2)
            items=[c for c in active if c["lane"]==key]
            draw.text((x+28,y+10),f"LANE {key.upper()}",font=self.fonts[24],fill="#e9f2fb")
            if not items:
                draw.text((x+28,y+65),"Awaiting an inspection carrier",font=self.fonts[20],fill="#8fa7ba")
                continue
            carrier=min(items,key=lambda c:c["entry_ms"])
            draw.text((x+165,y+12),f"{carrier['sku']}  /  {carrier['carrier_id']}",font=self.fonts[20],fill="#e9f2fb")
            draw.text((x+28,y+43),f"Entry {carrier['entry_ms']/1000:.3f}s   Gate {self.due_ms(carrier)/1000:.3f}s   Remaining {(self.due_ms(carrier)-now)/1000:.2f}s",font=self.fonts[17],fill="#b1c5d8")
            draw.text((x+28,y+71),"QUERY",font=self.fonts[17],fill="#f2c678")
            draw.text((x+647,y+71),"KNOWN NORMAL",font=self.fonts[17],fill="#8ed1cf")
            self._photo(image,carrier["asset_id"],(x+22,y+100,610,340))
            self._photo(image,carrier["reference_asset_id"],(x+642,y+105,290,315))
        draw.text((25,1016),"PUBLIC ENCODER VIEW — each key fires one immediate reject actuator",font=self.fonts[17],fill="#b1c5d8")
        for index,key in enumerate(self.contract["lane_keys"]):
            y=1054+index*31
            draw.text((25,y-9),key.upper(),font=self.fonts[20],fill="#dce8f4")
            draw.line((80,y+2,1810,y+2),fill="#486277",width=3)
            draw.line((1810,y-12,1810,y+14),fill="#7ad8d2",width=3)
            draw.text((1830,y-9),f"{self.contract['lane_speed_mm_s'][key]} mm/s",font=self.fonts[15],fill="#b1c5d8")
            if self.started_monotonic is not None:
                for carrier in self.manifest:
                    if carrier["lane"]!=key or carrier["entry_ms"]>now or carrier["carrier_id"] in self.removed:
                        continue
                    progress=(now-carrier["entry_ms"])/(self.due_ms(carrier)-carrier["entry_ms"])
                    if progress>1.04: continue
                    cx=80+progress*1730
                    draw.rounded_rectangle((cx-36,y-10,cx+36,y+13),radius=3,fill="#587da0")
                    draw.text((cx-28,y-9),carrier["carrier_id"][-6:],font=self.fonts[13],fill="#ffffff")
        stream=io.BytesIO(); image.save(stream,format="PNG"); return stream.getvalue()

    def capture(self, carrier_id=None):
        with self.lock:
            now=round(self.clock_ms(),6)
            if carrier_id is None:
                png=self.render(now)
                kind="frame"
            else:
                carrier=next((x for x in self.manifest if x["carrier_id"]==carrier_id),None)
                if carrier is None or self.started_monotonic is None or carrier["entry_ms"]>now:
                    raise KeyError("carrier is not yet available")
                png=self.render_inspection(carrier,now)
                kind="inspection"
            self.captures.append({"kind":kind,"carrier_id":carrier_id,"clock_ms":now,"sha256":hashlib.sha256(png).hexdigest()})
            return now,png

    def observe(self, inline=False):
        with self.lock:
            now=round(self.clock_ms(),6)
            value={"schema_version":self.contract["schema_version"],"phase":self.phase(),"mode":self.mode,
                   "session_id":self.session_id,"clock_ms":now,"clock_rate":self.speed,"horizon_ms":self.horizon_ms,
                   "calibration":self.contract,"frame_url":"/frame.png",
                   "episode_counts":{label:sum(x["label"]==label for x in self.manifest) for label in ["defect","normal"]},
                   "carriers":[{k:x[k] for k in ["carrier_id","lane","sku","entry_ms"]}|{"inspection_url":"/inspection/"+x["carrier_id"]+".png"} for x in self.active(now)],
                   "receipts":[x["receipt"] for x in list(self.receipts.values())[-40:]]}
            if inline:
                now,png=self.capture()
                value["clock_ms"]=now
                value["image_base64"]=base64.b64encode(png).decode("ascii")
            return value

    def export_payload(self):
        with self.lock:
            if self.phase()!="finished":
                raise ValueError("export is available only after the full episode finishes")
            return {"schema_version":self.contract["schema_version"],"task_id":self.task_id,"seed":self.seed,"mode":self.mode,
                    "session_id":self.session_id,"contract":self.contract,"catalog_sha256":self.catalog_sha256,
                    "created_unix":self.created_unix,"started_unix":self.started_unix,"duration_ms":self.clock_ms(),"speed":self.speed,
                    "manifest":self.manifest,"actions":self.actions,"captures":self.captures,"events":self.events}

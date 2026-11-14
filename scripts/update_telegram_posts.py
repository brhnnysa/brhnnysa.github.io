#!/usr/bin/env python3
"""Refresh recent messages from BRHN NYSA's public Telegram channel."""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"telegram-posts.json"
SOCIALFETCH=os.environ.get("SOCIALFETCH_BIN","socialfetch")
CHANNEL=os.environ.get("TELEGRAM_CHANNEL","brhnnysa")

def first(obj,*keys):
    if not isinstance(obj,dict): return None
    for key in keys:
        value=obj.get(key)
        if value not in (None,"",[],{}): return value
    return None

def find_media(value):
    if isinstance(value,str):
        return value if value.startswith(("https://","http://")) else None
    if isinstance(value,list):
        for item in value:
            found=find_media(item)
            if found: return found
    if isinstance(value,dict):
        for key in ("url","media_url","src","video_url","photo_url","thumbnail_url","download_url"):
            candidate=value.get(key)
            if isinstance(candidate,str) and candidate.startswith(("https://","http://")): return candidate
        for key in ("media","photo","video","document","thumbnail","previews","files"):
            found=find_media(value.get(key))
            if found: return found
    return None

def main():
    try:
        proc=subprocess.run([SOCIALFETCH,"tg","channel",CHANNEL,"--limit","10","--json"],capture_output=True,text=True,timeout=90)
        if proc.returncode: raise RuntimeError((proc.stderr or proc.stdout or "Telegram fetch failed").strip()[-1200:])
        result=json.loads(proc.stdout)
        if result.get("ok") is False: raise RuntimeError(result.get("error") or "Telegram returned an unsuccessful result")
        payload=result.get("data") if isinstance(result.get("data"),dict) else result
        messages=first(payload,"messages","items","posts","results")
        if messages is None and isinstance(payload,list): messages=payload
        if not isinstance(messages,list) or not messages: raise RuntimeError("No public Telegram channel messages found; keeping the previous feed.")
        posts=[]
        for item in messages:
            if not isinstance(item,dict): continue
            mid=first(item,"id","message_id","messageId")
            if mid is None: continue
            date=first(item,"date","published_at","timestamp","datetime")
            body=first(item,"text","message","caption","content") or ""
            media_obj=first(item,"media","attachments","photo","video")
            media_url=find_media(media_obj)
            media_type="video" if isinstance(media_obj,dict) and ("video" in str(media_obj.get("type","")).lower() or media_obj.get("video")) else "image"
            if media_url and any(ext in media_url.lower() for ext in (".mp4",".webm",".mov")): media_type="video"
            posts.append({"id":str(mid),"platform":"Telegram","author":CHANNEL,"caption":str(body).strip(),"url":f"https://t.me/{CHANNEL}/{mid}","media_url":media_url,"media_type":media_type,"published_at":date,"likes":None,"views":first(item,"views","view_count")})
        if not posts: raise RuntimeError("Telegram returned no usable public messages; keeping the previous feed.")
        OUT.parent.mkdir(parents=True,exist_ok=True)
        document={"source":"Telegram public channel preview","channel":CHANNEL,"updated_at":datetime.now(timezone.utc).isoformat(),"posts":posts[:10]}
        fd,tmp=tempfile.mkstemp(prefix="telegram-posts-",suffix=".json",dir=OUT.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(document,f,ensure_ascii=False,indent=2); f.write("\n")
            os.replace(tmp,OUT)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        print(f"Updated {len(posts[:10])} public Telegram messages from @{CHANNEL}.")
        return 0
    except Exception as exc:
        print(f"Telegram refresh skipped: {exc}",file=sys.stderr)
        return 1
if __name__=="__main__": raise SystemExit(main())

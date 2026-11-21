"""Hourly Furas monitor. Public GIS data; one notification per opportunity.

No browser session or AI service is required. GET requests retry; notification
POSTs deliberately do not retry because a timeout can follow an accepted send.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("furas")
RIYADH = ZoneInfo("Asia/Riyadh")
CONFIG = "https://gisapps.balady.gov.sa/opportunities/assets/config.json"
FIELDS = "OBJECTID,OPPORTUNITYID,OPPORTUNITYDESCRIPTION,STATUS,OPPORTUNITYACTIVESTATUS,OPPORTUNITYTYPE,ACTIVITYTYPE,SUBACTIVITYTYPE,STARTDATE,ENDDATE,LASTRFPSELLDATE,REGIONNAME,CITYNAME,AMANA,BALADYA"

class MonitorError(Exception):
    """Safe-to-log operational error (never includes a credential or response body)."""

def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise MonitorError(f"Missing environment variable: {name}")
    return value

def session():
    s = requests.Session()
    s.headers["User-Agent"] = "FurasOpportunityMonitor/1.0"
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]))))
    return s

def get_json(s, url):
    try:
        r = s.get(url, timeout=(10, 45))
        r.raise_for_status()
        value = r.json()
    except (requests.RequestException, ValueError):
        raise MonitorError("Source request failed or returned invalid JSON") from None
    if not isinstance(value, dict) or "error" in value:
        raise MonitorError("Source API reported an error")
    return value

def fetch(s, now):
    """Snapshot object IDs, then fetch every batch; fail on partial results.

    Uses exactly the public proxy and layer configured by the Furas map.
    Date filter follows its LASTRFPSELLDATE + Announced criterion; local
    validation additionally checks the precise cutoff and end date.
    """
    cfg = get_json(s, CONFIG)
    try:
        base = cfg["gisLayerUrl"]["investmentMapServerInvestmentP"]
        layer = cfg["gisLayerIndex"]["investmentMapServerInvestmentP"]
        proxy = cfg["proxy"]["proxyUrl"]
    except (KeyError, TypeError):
        raise MonitorError("Furas map configuration changed") from None
    if not base.startswith("https://gisOppertunities.momra.gov.sa/") or proxy != "https://gisapps.balady.gov.sa/opportunities/proxy/proxy.ashx":
        raise MonitorError("Source hosts changed; review configuration before proceeding")
    endpoint = f"{proxy}?{base}/{layer}/query?"
    where = f"STATUS='Announced' AND LASTRFPSELLDATE >= DATE '{now.astimezone(RIYADH):%Y-%m-%d}'"
    data = get_json(s, endpoint + urlencode(dict(f="json", where=where, returnIdsOnly="true")))
    ids = data.get("objectIds")
    if not isinstance(ids, list) or any(type(i) is not int for i in ids):
        raise MonitorError("Missing object ID list; refusing to treat this as zero opportunities")
    ids = sorted(set(ids))
    if len(ids) > 100000:
        raise MonitorError("Unexpected source size")
    rows = []
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset + 100]
        page = get_json(s, endpoint + urlencode(dict(
            f="json", objectIds=",".join(map(str, batch)), outFields=FIELDS,
            returnGeometry="false")))
        features = page.get("features")
        if not isinstance(features, list) or page.get("exceededTransferLimit"):
            raise MonitorError("Incomplete source batch")
        try:
            attrs = [item["attributes"] for item in features]
            if {a["OBJECTID"] for a in attrs} != set(batch):
                raise MonitorError("Source changed during scan; retry next run")
            for a in attrs:
                if not set(FIELDS.split(",")).issubset(a):
                    raise MonitorError("Source schema changed")
        except (KeyError, TypeError):
            raise MonitorError("Unexpected feature schema") from None
        rows.extend(attrs)
        time.sleep(0.15)
    return rows

def normalize(text):
    text = re.sub(r"[\u064b-\u065f\u0670ـ]", "", str(text or ""))
    return text.translate(str.maketrans("أإآى", "اااي"))

def category(a):
    text = normalize(a.get("OPPORTUNITYDESCRIPTION"))
    # Remove adjacent landmark references without matching their activity.
    text = re.sub(r"(?:بجوار|مجاور(?:ة)?\s+ل?|مقابل|بالقرب من|قرب)\s+(?:ال)?(?:فندق|فنادق|محطة|محطات)[^-،\n]*", "", text)
    text += " " + normalize(a.get("ACTIVITYTYPE")) + " " + normalize(a.get("SUBACTIVITYTYPE"))
    if re.search(r"محط(?:ة|ه|ات)\s+(?:(?:ال)?(?:تعبئة|بيع)\s+)?(?:ال)?(?:وقود|محروقات|بنزين)", text):
        return "محطات الوقود"
    if re.search(r"(?:فندق|فنادق|فندقي)", text):
        return "الفنادق والاستخدام الفندقي"
    return None

def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    return None

def active(a, now):
    if a.get("STATUS") != "Announced" or a.get("OPPORTUNITYACTIVESTATUS") != "Announced":
        return False
    cutoff = timestamp(a.get("LASTRFPSELLDATE"))
    end = timestamp(a.get("ENDDATE"))
    start = timestamp(a.get("STARTDATE"))
    # Unknown required dates are not guessed. Null optional end/start are OK.
    if cutoff is None or cutoff <= now:
        return False
    if a.get("ENDDATE") is not None and (end is None or end <= now):
        return False
    if a.get("STARTDATE") is not None and (start is None or start > now):
        return False
    return True

def format_message(a):
    kind = "TempRental" if a["OPPORTUNITYTYPE"] == "TemporaryRental" else a["OPPORTUNITYTYPE"]
    url = "https://furas.momah.gov.sa/opportunity/" + quote(str(a["OPPORTUNITYID"]), safe="") + "?" + urlencode({"type": kind})
    location = " — ".join(str(a[k]).strip() for k in ["REGIONNAME", "CITYNAME", "BALADYA"] if str(a.get(k) or "").strip()) or "راجع تفاصيل الفرصة"
    deadline = timestamp(a["LASTRFPSELLDATE"]).astimezone(RIYADH).strftime("%Y-%m-%d %H:%M")
    description = re.sub(r"\s+", " ", str(a["OPPORTUNITYDESCRIPTION"])).strip()
    return (f"فرصة سارية | {category(a)}\nرقم الفرصة: {a['OPPORTUNITYID']}\n"
            f"{description[:1700]}\nالموقع: {location}\n"
            f"آخر موعد لبيع الكراسة: {deadline} (السعودية)\nالتفاصيل: {url}")

def post_json(url, payload, headers=None):
    """No automatic POST retry. HTTP acceptance is not delivery confirmation."""
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=(10, 40))
        if not r.ok:
            raise MonitorError(f"Notification rejected: HTTP {r.status_code}")
        data = r.json()
    except (requests.RequestException, ValueError):
        raise MonitorError("Notification result uncertain; inspect recipient before retry") from None
    return data

def send_whatsapp(message):
    """Official Meta Groups API. Requires an eligible OBA + approved template.

    Template body must contain one text variable {{1}} and no other required
    components. This avoids relying on a customer-service conversation window.
    """
    version = required("WA_API_VERSION")
    phone = required("WA_PHONE_NUMBER_ID")
    if not re.fullmatch(r"v\d+\.\d+", version) or not phone.isdigit():
        raise MonitorError("Invalid WhatsApp API version or phone number ID")
    data = post_json(f"https://graph.facebook.com/{version}/{phone}/messages", {
        "messaging_product": "whatsapp", "recipient_type": "group",
        "to": required("WA_GROUP_ID"), "type": "template", "template": {
            "name": required("WA_TEMPLATE_NAME"),
            "language": {"code": os.getenv("WA_TEMPLATE_LANGUAGE", "ar")},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": re.sub(r"\s+", " ", message)}]}]
        }}, {"Authorization": "Bearer " + required("WA_ACCESS_TOKEN")})
    if not data.get("messages", [{}])[0].get("id"):
        raise MonitorError("WhatsApp did not return a message ID")

def send_telegram(message):
    data = post_json(f"https://api.telegram.org/bot{required('TELEGRAM_BOT_TOKEN')}/sendMessage", {
        "chat_id": required("TELEGRAM_CHAT_ID"), "text": message,
        "link_preview_options": {"is_disabled": True}})
    if data.get("ok") is not True or not data.get("result", {}).get("message_id"):
        raise MonitorError("Telegram did not accept the message")

def save_state(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)

def process(rows, state, path, channel, recipient, sender, now, limit, delay):
    namespace = hashlib.sha256((channel + ":" + recipient).encode()).hexdigest()
    seen = state["sent"].setdefault(namespace, {})
    sent = 0
    for a in sorted(rows, key=lambda a: str(a.get("OPPORTUNITYID"))):
        if not active(a, now) or not category(a):
            continue
        if not a.get("OPPORTUNITYID") or not a.get("OPPORTUNITYTYPE") or not a.get("OPPORTUNITYDESCRIPTION"):
            raise MonitorError("Matching record is missing identity or description")
        key = str(a["OPPORTUNITYTYPE"]) + ":" + str(a["OPPORTUNITYID"])
        if key in seen:
            continue
        if sent >= limit:
            LOG.info("Message limit reached; remaining opportunities wait for next run")
            break
        sender(format_message(a))
        seen[key] = now.isoformat()
        save_state(path, state)  # checkpoint after every accepted send
        sent += 1
        time.sleep(delay)
    return sent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dry-run", "test", "monitor"], default="dry-run")
    args = parser.parse_args()
    channel = os.getenv("NOTIFIER", "telegram")
    if channel not in {"telegram", "whatsapp"}:
        raise MonitorError("NOTIFIER must be telegram or whatsapp")
    sender = send_telegram if channel == "telegram" else send_whatsapp
    if args.mode == "test":
        sender("اختبار مراقب فرص: تم ربط قناة الإرسال. هذه رسالة اختبار وليست فرصة استثمارية.")
        LOG.info("Test accepted by provider; check the destination for delivery")
        return
    now = datetime.now(timezone.utc)
    with session() as s:
        rows = fetch(s, now)
    matches = [a for a in rows if active(a, now) and category(a)]
    LOG.info("Source records: %d; matching active records: %d", len(rows), len(matches))
    if args.mode == "dry-run":
        for a in matches:
            print(format_message(a) + "\n")
        return
    path = Path(os.getenv("STATE_FILE", "state/sent.json"))
    # Missing/corrupt history fails closed instead of resending the full backlog.
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or not isinstance(state.get("sent"), dict) or any(not isinstance(v, dict) for v in state["sent"].values()):
            raise ValueError()
    except (OSError, ValueError, AttributeError):
        raise MonitorError("State file missing/corrupt; restore it from Git history") from None
    recipient = required("TELEGRAM_CHAT_ID" if channel == "telegram" else "WA_GROUP_ID")
    limit = int(os.getenv("MAX_MESSAGES_PER_RUN", "100"))
    if not 1 <= limit <= 100:
        raise MonitorError("MAX_MESSAGES_PER_RUN must be 1..100")
    count = process(rows, state, path, channel, recipient, sender, now, limit, 3.2)
    LOG.info("Accepted new notifications: %d", count)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        main()
    except Exception as error:
        # Do not log raw HTTP exceptions: Telegram URLs contain the bot token.
        LOG.error("%s", str(error) if isinstance(error, MonitorError) else f"Unexpected failure ({type(error).__name__})")
        raise SystemExit(1)

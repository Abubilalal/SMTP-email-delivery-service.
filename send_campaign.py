#!/usr/bin/env python3
"""
send_campaign.py — a free, self-hosted bulk email sender.

Sends through YOUR OWN domain mailbox over SMTP. No third-party / paid service.
Built-in deliverability guards: throttling, suppression list, one-click
unsubscribe header, plain+HTML body, personalization, dry-run/test, logging,
and resume.

Usage examples:
  # 1. Send ONE test to yourself first:
  python3 send_campaign.py --test you@yourdomain.com

  # 2. Dry run (renders + validates everything, sends nothing):
  python3 send_campaign.py --dry-run

  # 3. Real send:
  python3 send_campaign.py

  # 4. Resume an interrupted run (skips anyone already sent in the log):
  python3 send_campaign.py --resume
"""

import argparse
import csv
import hashlib
import hmac
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import quote

# --------------------------------------------------------------------------- #
# Minimal .env loader (no external dependency)
# --------------------------------------------------------------------------- #
def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


load_env()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def cfg(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"ERROR: missing required config '{name}' (set it in .env)")
    return val


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def render(text, row):
    """Replace {{column}} tokens with values from the contact row."""
    return TOKEN_RE.sub(lambda m: str(row.get(m.group(1), "")), text)


def unsubscribe_url(email, base, secret):
    if not base:
        return ""
    e = quote(email)
    if secret:
        sig = hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:16]
        return f"{base}?e={e}&t={sig}"
    return f"{base}?e={e}"


def load_suppression(path):
    suppressed = set()
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.reader(f):
                if row and "@" in row[0]:
                    suppressed.add(row[0].strip().lower())
    return suppressed


def load_already_sent(log_path):
    sent = set()
    if os.path.exists(log_path):
        with open(log_path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "sent":
                    sent.add(row["email"].strip().lower())
    return sent


def log_result(log_path, email, status, detail=""):
    new = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "email", "status", "detail"])
        w.writerow([datetime.now(timezone.utc).isoformat(), email, status, detail])


# --------------------------------------------------------------------------- #
# Message building
# --------------------------------------------------------------------------- #
def build_message(row, subject_tpl, text_tpl, conf):
    email = row["email"].strip()
    unsub = unsubscribe_url(email, conf["unsub_base"], conf["unsub_secret"])
    row = {**row, "unsubscribe_url": unsub}

    msg = EmailMessage()
    msg["From"] = formataddr((conf["sender_name"], conf["sender_email"]))
    msg["To"] = formataddr((row.get("first_name", ""), email))
    if conf["reply_to"]:
        msg["Reply-To"] = conf["reply_to"]
    msg["Subject"] = render(subject_tpl, row)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=conf["sender_email"].split("@")[-1])

    # One-click unsubscribe (RFC 8058). mailto: works with no hosting needed.
    unsub_targets = []
    if unsub:
        unsub_targets.append(f"<{unsub}>")
    if conf["unsub_mailto"]:
        unsub_targets.append(f"<mailto:{conf['unsub_mailto']}?subject=unsubscribe>")
    if unsub_targets:
        msg["List-Unsubscribe"] = ", ".join(unsub_targets)
        if unsub:  # https one-click only valid if you host the endpoint
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    text_body = render(text_tpl, row)
    msg.set_content(text_body)
    # if html_tpl:
    #     msg.add_alternative(render(html_tpl, row), subtype="html")
    return msg


# --------------------------------------------------------------------------- #
# SMTP
# --------------------------------------------------------------------------- #
def connect_smtp(conf):
    host, port = conf["smtp_host"], int(conf["smtp_port"])
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
    server.login(conf["smtp_user"], conf["smtp_pass"])
    return server


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Free SMTP bulk email sender")
    ap.add_argument("--contacts", default="contacts.csv")
    # ap.add_argument("--html", default="templates/email.html")
    ap.add_argument("--text", default="templates/email.txt")
    ap.add_argument("--subject", default=os.environ.get("SUBJECT", "Hello {{first_name}}"))
    ap.add_argument("--suppression", default="suppression.csv")
    ap.add_argument("--log", default="send_log.csv")
    ap.add_argument("--delay", type=float, default=float(os.environ.get("DELAY", "6")),
                    help="seconds to wait between emails (throttle)")
    ap.add_argument("--limit", type=int, default=0, help="max emails to send (0 = no cap)")
    ap.add_argument("--dry-run", action="store_true", help="render & validate, send nothing")
    ap.add_argument("--test", metavar="EMAIL", help="send a single test to this address")
    ap.add_argument("--resume", action="store_true", help="skip addresses already marked sent")
    args = ap.parse_args()

    conf = {
        "smtp_host": cfg("SMTP_HOST", required=not args.dry_run),
        "smtp_port": cfg("SMTP_PORT", "587"),
        "smtp_user": cfg("SMTP_USER", required=not args.dry_run),
        "smtp_pass": cfg("SMTP_PASS", required=not args.dry_run),
        "sender_email": cfg("SENDER_EMAIL", required=True),
        "sender_name": cfg("SENDER_NAME", ""),
        "reply_to": cfg("REPLY_TO", ""),
        "unsub_base": cfg("UNSUBSCRIBE_URL_BASE", ""),
        "unsub_mailto": cfg("UNSUBSCRIBE_MAILTO", cfg("SENDER_EMAIL")),
        "unsub_secret": cfg("UNSUBSCRIBE_SECRET", ""),
    }

    # Load templates
    # html_tpl = ""
    # if args.html and os.path.exists(args.html):
    #     html_tpl = open(args.html, encoding="utf-8").read()
    text_tpl = ""
    if args.text and os.path.exists(args.text):
        text_tpl = open(args.text, encoding="utf-8").read()
    if not text_tpl:
        sys.exit("ERROR: a plain-text template is required (templates/email.txt). "
                 "Text+HTML together improves deliverability.")

    # Build recipient list
    if args.test:
        contacts = [{"email": args.test, "first_name": "Test"}]
    else:
        if not os.path.exists(args.contacts):
            sys.exit(f"ERROR: contacts file not found: {args.contacts}")
        with open(args.contacts, newline="", encoding="utf-8") as f:
            contacts = list(csv.DictReader(f))

    suppressed = load_suppression(args.suppression)
    already = load_already_sent(args.log) if args.resume else set()

    # Filter / validate / dedupe
    queue, seen = [], set()
    skipped = {"invalid": 0, "suppressed": 0, "duplicate": 0, "already_sent": 0}
    for row in contacts:
        email = (row.get("email") or "").strip().lower()
        if not EMAIL_RE.match(email):
            skipped["invalid"] += 1; continue
        if email in suppressed:
            skipped["suppressed"] += 1; continue
        if email in already:
            skipped["already_sent"] += 1; continue
        if email in seen:
            skipped["duplicate"] += 1; continue
        seen.add(email)
        row["email"] = email
        queue.append(row)

    if args.limit:
        queue = queue[: args.limit]

    print(f"\nRecipients ready: {len(queue)}")
    print(f"Skipped -> invalid:{skipped['invalid']} suppressed:{skipped['suppressed']} "
          f"duplicate:{skipped['duplicate']} already_sent:{skipped['already_sent']}")
    print(f"Throttle: 1 email / {args.delay}s  (~{len(queue) * args.delay / 60:.1f} min total)\n")

    if args.dry_run:
        if queue:
            sample = build_message(queue[0], args.subject, text_tpl, conf)
            print("----- DRY RUN: first message preview -----")
            print(f"From:    {sample['From']}")
            print(f"To:      {sample['To']}")
            print(f"Subject: {sample['Subject']}")
            print(f"List-Unsubscribe: {sample.get('List-Unsubscribe')}")
            print("------------------------------------------")
        print("Dry run complete. No emails were sent.")
        return

    # Send loop
    server = connect_smtp(conf)
    sent = failed = 0
    try:
        for i, row in enumerate(queue, 1):
            email = row["email"]
            msg = build_message(row, args.subject, text_tpl, conf)
            for attempt in range(1, 4):
                try:
                    server.send_message(msg)
                    sent += 1
                    log_result(args.log, email, "sent")
                    print(f"[{i}/{len(queue)}] sent -> {email}")
                    break
                except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
                    server = connect_smtp(conf)  # reconnect & retry
                except smtplib.SMTPException as e:
                    if attempt == 3:
                        failed += 1
                        log_result(args.log, email, "failed", str(e))
                        print(f"[{i}/{len(queue)}] FAILED -> {email}: {e}")
                    else:
                        time.sleep(2 * attempt)
            if i < len(queue):
                time.sleep(args.delay)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    print(f"\nDone. sent:{sent} failed:{failed}. Log: {args.log}")


if __name__ == "__main__":
    main()

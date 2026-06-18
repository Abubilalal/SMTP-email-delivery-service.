# Free SMTP Bulk Mailer

Send 100–200 emails through **your own domain mailbox** — no Brevo, no paid
service. Pure Python standard library (no `pip install` needed). Includes the
deliverability guards that keep you out of spam and off the freeze list:
throttling, suppression list, one-click unsubscribe, plain+HTML body,
personalization, dry-run/test, logging, and resume.

> **Reality check:** sending bulk through a personal mailbox is the riskiest
> route for spam/freezes. It works fine **only** if you (1) authenticate your
> domain, (2) send slowly, and (3) mail people who opted in. See the checklist.

---

## 1. Files

| File | What it is |
|------|------------|
| `send_campaign.py` | the sender |
| `.env.example` | copy to `.env` and fill in your details |
| `contacts.sample.csv` | rename to `contacts.csv`, add your list (`email,first_name`) |
| `templates/email.html` | HTML body (edit it) |
| `templates/email.txt` | plain-text body (required) |
| `suppression.csv` | anyone who unsubscribed / bounced — they get skipped |
| `send_log.csv` | auto-created; one row per send |

Tokens like `{{first_name}}` in the subject/body are filled from your CSV
columns. `{{unsubscribe_url}}` is filled automatically.

---

## 2. Setup (5 minutes)

```bash
cp .env.example .env          # then edit .env
cp contacts.sample.csv contacts.csv   # then add your real contacts
```

Fill `.env` with your mailbox's SMTP settings:

| Provider (free/included) | SMTP_HOST | Port | Notes |
|--------------------------|-----------|------|-------|
| **Zoho Mail** | `smtp.zoho.com` (or `smtp.zoho.in`) | 587 | Use an **app-specific password**. Free plan has low daily caps — verify yours. |
| **Google Workspace** | `smtp.gmail.com` | 587 | Needs an **App Password** (2FA on). ~2,000 recipients/day; bulk patterns still risky. |
| **Outlook / Microsoft 365** | `smtp.office365.com` | 587 | SMTP AUTH must be enabled on the mailbox. |
| **cPanel / shared hosting** | `mail.yourdomain.com` | 465 or 587 | Shared sending IP → often weakest deliverability. |

> Most providers block your normal login password over SMTP — generate an
> **app password** and put that in `SMTP_PASS`.

---

## 3. Before your first real send

**Authenticate your domain** (this is what actually keeps you out of spam):

- **SPF** — a TXT record listing your provider as an authorized sender.
- **DKIM** — turn it on in your mail provider's admin; add the record they give you.
- **DMARC** — a TXT record at `_dmarc.yourdomain.com`, start with `p=none`.

Your provider's help pages have copy-paste values. Without these, your mail
lands in spam (or your provider silently rewrites your "from" address).

---

## 4. Run it

```bash
# 1) Send a test to yourself and check how it looks + lands:
python3 send_campaign.py --test you@yourdomain.com

# 2) Dry run — renders & validates everyone, sends nothing:
python3 send_campaign.py --dry-run

# 3) Real send:
python3 send_campaign.py

# Resume if it got interrupted (skips anyone already sent):
python3 send_campaign.py --resume
```

Useful flags: `--delay 10` (slower = safer), `--limit 50` (cap per run),
`--subject "..."`, `--contacts mylist.csv`.

---

## 5. Deliverability & "don't get frozen" checklist

- [ ] SPF + DKIM + DMARC set up on your domain
- [ ] Sending **only** to people who opted in (no scraped/bought lists)
- [ ] Working unsubscribe (the `List-Unsubscribe` header is added for you; add
      the visible link too — it's already in the template)
- [ ] **Warm up:** first runs small (20–30), increase over days — don't blast
      200 from a cold mailbox on day one
- [ ] Throttle on (default 1 email / 6s). Raise `--delay` for a new domain.
- [ ] Spread large sends across days; stay well under your provider's daily cap
- [ ] Keep spam complaints near zero — one bad blast can get the mailbox frozen
- [ ] Remove bounces and add unsubscribes to `suppression.csv` after each send

## 6. Unsubscribe handling

The free path uses a `mailto:` unsubscribe — clicking "unsubscribe" in the
recipient's email client sends a message to your `UNSUBSCRIBE_MAILTO` inbox.
Watch that inbox and add those addresses to `suppression.csv`.

(If you later want true **one-click** unsubscribe, you need a tiny hosted page
that records the opt-out — that part can't be done from a script alone. Happy
to build that endpoint separately if you want it.)

## 7. Legal

You're in India (DPDP Act 2023) — and if you ever mail US/EU recipients,
CAN-SPAM and GDPR apply too. All require the same basics: **consent**, a real
sender identity, and a working **unsubscribe**. The tool is built around these.

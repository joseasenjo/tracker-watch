# Email digest (optional, off by default)

After the bot publishes on Bluesky, the same text can be emailed to the people who asked for it. It runs in GitHub
Actions, costs nothing, and does nothing unless the repository variable `DIGEST_ENABLED` is `true`.

## How it works

```
publish (Bluesky)  ->  traceguard.digest  ->  mailing.make_sender("smtp")  ->  one plain-text message per subscriber
                           |                         ^ swappable
                           +-- reads the same data/drafts/<date>/bluesky.json the bot posted
```

| Piece | File | Replaceable by |
|---|---|---|
| Who receives it | `traceguard/mailing.py` (`load_subscribers`) | another store: change this one function |
| What is sent | `traceguard/digest.py` (`build_digest`) | another format (HTML, a different layout) |
| How it is sent | `traceguard/mailing.py` (`SENDERS`) | a new class with `@register_sender("name")` |
| Which posts | `DIGEST_KINDS` (default `weekly,midweek`) | any of `weekly,midweek,alert,hook,announce` |
| Who gets which | `--segment all|interest` | new segments in `mailing.SEGMENTS` |

### Adding another way of sending

A back end is a class whose constructor takes the environment and that has `send_batch(messages) -> BatchResult`:

```python
@mailing.register_sender("myprovider")
class MyProviderSender:
    def __init__(self, env): ...
    def send_batch(self, messages): ...   # return mailing.BatchResult(sent=..., failed=..., errors=[...])
```

Then set the repository variable `DIGEST_SENDER=myprovider`. Nothing else changes: the list, the message, the
safeguards and the workflows stay as they are. A hosted email service with an API is the natural second back end.

## Safeguards (enforced in code, tested)

- Only **confirmed** subscribers are written to; each gets **its own message** (nobody sees the others).
- Only a draft that has **already been published** is sent; the same digest is **never sent twice** (`digest.sent.json`
  in the draft folder holds a date and a count, no addresses).
- Without `--send` it is a **dry run**. With `--send` it needs `DIGEST_ENABLED=true` and `CONTACT_EMAIL`.
- Every message says how to leave (reply `UNSUBSCRIBE`) and has a `List-Unsubscribe` header. Plain text: no images,
  no pixels, no wrapped links.
- A mail problem never fails or undoes a publication (`continue-on-error`).
- Logs and errors never contain a full address.

## Setting it up

1. **The list stays private.** `data/private/subscribers.json` is git-ignored. Manage it with
   `python -m traceguard.subscribers add|confirm|remove|list|push-secret`.
2. **How a person gets on it:** they write to you asking for it (for example from the home page mail link); you `add`
   them (pending) and reply asking them to confirm; when they answer, you `confirm` them. Their reply is the proof of
   consent: write it in `--note`.
3. `python -m traceguard.subscribers push-secret` copies the list into one repository secret, `SUBSCRIBERS_JSON`
   (GitHub secrets hold up to 48 KB, roughly 300 people: beyond that, change the store).
4. **Sender account.** It uses `MAIL_SMTP_HOST`, `MAIL_USER`, `MAIL_PASSWORD` (the account that already sends the
   publication notice). A dedicated account is better: set the secrets `DIGEST_MAIL_USER` and `DIGEST_MAIL_PASSWORD`
   (used only when both are present). Free personal mailboxes cap daily sends and can be suspended for bulk mail:
   keep the list small, or move to another sender.
5. Set the variable `DIGEST_SIGNATURE` to the sender identity you want in every email (name, contact), and
   `DIGEST_ENABLED=true` to switch it on.
6. Try it first: `python -m traceguard.digest data/drafts/<folder>` (dry run, shows the message).

## What is still needed before using it with real people

- **A legal identity for the sender** and a privacy text that says who handles the addresses, why, for how long, and how
  to leave. The site's privacy page has a subscription branch waiting for this.
- **Consent wording** for the way people ask (mail link or form), and the record of each.
- **A decision on the sender account** (see step 4) and, if it grows, a custom domain and a hosted service.
- Not legal advice: have the signup text and the privacy wording reviewed.

## Settings

| Name | Where | Meaning |
|---|---|---|
| `DIGEST_ENABLED` | variable | `true` switches the digest on |
| `DIGEST_SENDER` | variable | back end, default `smtp` |
| `DIGEST_KINDS` | variable | which publications are emailed, default `weekly,midweek` |
| `DIGEST_SIGNATURE` | variable | sender identity line in every email |
| `SUBSCRIBERS_JSON` | secret | the list |
| `DIGEST_MAIL_USER`, `DIGEST_MAIL_PASSWORD` | secrets | optional dedicated account |
| `DIGEST_DELAY_SECONDS`, `DIGEST_MAX_PER_RUN` | variables | pause between messages (1) and cap per run (100) |
| `CONTACT_EMAIL` | variable | where people write to unsubscribe |

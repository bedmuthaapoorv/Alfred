# Google setup (Gmail + Calendar)

Alfred talks to Gmail and Google Calendar directly from your server, so it
needs its own OAuth client. This is a one-time setup of about 15 minutes. You
need a Google account and a browser. The server can be headless.

Google renames these console pages from time to time. If a label doesn't
match, look for the closest equivalent; the steps stay the same.

---

## What Alfred will ask for

| Scope | Why | Phase |
|---|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | Read mail and threads for triage and the brief | 1 |
| `https://www.googleapis.com/auth/calendar.readonly` | Read events for the brief and meeting prep | 1 |
| `https://www.googleapis.com/auth/gmail.send` | Email the brief and approval requests **to you** | 1 |
| `https://www.googleapis.com/auth/gmail.compose` | Create drafts (not send) | 2 |
| `https://www.googleapis.com/auth/calendar.events` | Propose and accept events (approval-gated) | 2 |

Only the Phase 1 scopes are needed now. Adding scopes later means running
`alfred google-auth` once more.

> **About `gmail.send`:** Google cannot limit this scope to "only send to
> myself". Alfred enforces that in code instead: until Phase 2's approval
> gate exists, the sender refuses any recipient other than the
> `notify.email` address in your config, and this is covered by a test. If
> you'd rather not grant send at all, see [Alternative: no send
> scope](#alternative-no-send-scope).

---

## 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com/> and sign in with the Google
   account Alfred will manage.
2. Use the project picker (top bar) → **New project**. Name it `alfred` and
   click **Create**.
3. Make sure the new project is selected in the picker.

There is no billing for this. The Gmail and Calendar APIs are free at
personal volumes.

## 2. Enable the APIs

**APIs & Services → Library.** Search for each API and click **Enable**:

- **Gmail API**
- **Google Calendar API**

## 3. Configure the consent screen

Open **Google Auth Platform** (it may be listed as **APIs & Services → OAuth
consent screen**). Click **Get started** if prompted.

1. **App information:** App name `Alfred`. User support email: your address.
2. **Audience:** choose **External**. "Internal" is only available to Google
   Workspace organizations. If you are on Workspace, choose Internal and skip
   the publishing step below.
3. **Contact information:** your address. Agree to the policy and click **Create**.
4. **Data access → Add or remove scopes:** paste the three Phase 1 scopes
   from the table above under "Manually add scopes", then click **Update** and
   **Save**.
5. **Audience → Test users → Add users:** add your own Gmail address.

### Testing vs. production (important for an always-on server)

While the app is in **Testing**, Google expires its refresh tokens after
**7 days**, so Alfred would lose access every week. For an always-on server:

- **Audience → Publish app → Confirm.** The app moves to "In production".
- You do **not** need to submit for verification for personal use. When you
  sign in you'll see a warning that Google hasn't verified this app. Click
  **Advanced → Go to Alfred (unsafe)**. That's expected: you are the
  developer and the only user. Unverified apps are capped at 100 users,
  which is irrelevant here.

If you'd rather stay in Testing for now, that works too. Just re-run
`alfred google-auth` when Alfred reports that its token has expired.

## 4. Create the OAuth client

1. **Google Auth Platform → Clients → Create client** (or **APIs &
   Services → Credentials → Create credentials → OAuth client ID**).
2. Application type: **Desktop app**. Name: `alfred-server`. Click **Create**.
3. Click **Download JSON**.

Put it on the server:

```bash
mkdir -p ~/.config/alfred && chmod 700 ~/.config/alfred
scp client_secret_*.json server:~/.config/alfred/google_client_secret.json
ssh server chmod 600 ~/.config/alfred/google_client_secret.json
```

Treat this file as a secret. Never commit it. `.gitignore` already excludes
`alfred.toml`. Keep both files under `~/.config/alfred/`, outside the repo.

## 5. Authorize Alfred (one time, from a headless server)

The Desktop-app flow sends your browser back to `http://localhost:<port>` on
the machine running Alfred. On a headless server, forward that port over SSH:

```bash
# on your laptop
ssh -L 8765:localhost:8765 you@server

# in that SSH session, on the server
alfred google-auth --port 8765
```

Alfred prints a URL. Open it in your laptop's browser, pick your account,
accept the warning from step 3 if you published the app, and grant the
scopes. The browser lands on `localhost:8765`, which the tunnel carries back to
Alfred. Alfred then saves the token to `~/.config/alfred/google_token.json`
(mode 600) and confirms which scopes were granted.

## 6. Configure Alfred

Add to `alfred.toml`:

```toml
[google]
client_secret = "~/.config/alfred/google_client_secret.json"
token         = "~/.config/alfred/google_token.json"

[notify]
channel = "email"
email   = "you@gmail.com"   # the ONLY address Alfred may send to in Phase 1
```

Then restart the service (`systemctl --user restart alfred`) and check:

```bash
alfred status   # "google": "ok", or what's wrong and how to fix it
```

---

## Revoking access

At any time: <https://myaccount.google.com/permissions> → **Alfred** →
**Remove access**. Alfred stops working immediately. Delete
`~/.config/alfred/google_token.json` too.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `access_denied` / "app is being tested" | Your account isn't in **Test users** (step 3.5), or you signed in with a different account. |
| `redirect_uri_mismatch` | The client type isn't **Desktop app**. Create a new Desktop client. |
| Browser can't reach `localhost:8765` | The SSH tunnel isn't open, or `--port` doesn't match the `-L` port. |
| `invalid_grant` after about a week | The app is still in Testing. Publish it (step 3) and re-run `alfred google-auth`. |
| `insufficient permissions` on a call | A scope was added after authorizing. Re-run `alfred google-auth`. |

## Alternative: no send scope

If you don't want to grant `gmail.send`, Alfred can deliver email through
Gmail SMTP with an **app password** instead. This requires 2-Step Verification
on your account; create the password at
<https://myaccount.google.com/apppasswords>. In that case set:

```toml
[notify]
channel = "smtp"
email   = "you@gmail.com"
# password is read from the ALFRED_SMTP_PASSWORD environment variable, never from this file
```

The trade-off: an app password grants full account access over legacy
protocols. The OAuth `gmail.send` scope is narrower, and it's revocable on its
own. The OAuth route is recommended.

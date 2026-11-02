# Gmail setup and draft review

1. Create a Google Cloud project, enable the Gmail API, configure OAuth consent, and create a **Desktop app** OAuth client. For a personal test app, add your own account as a test user. Public distribution and restricted Gmail scopes require Google's applicable verification process.
2. Download the client JSON outside the repository. In the installed app's venv run:

   ```bash
   cd ~/.local/share/qareen/app
   .venv/bin/pip install -r requirements-gmail.txt
   .venv/bin/python gmail_setup.py --client-secrets /absolute/path/client.json --drafts
   ```

   Omit `--drafts` for read-only sync. Consent happens in your own browser. A working desktop keyring is required; credentials are not silently written to a plaintext fallback.
3. In Qareen Privacy & health, enable Gmail. Initial sync imports up to 50 matching messages and the last 30 messages of each relevant thread. Old mail does not trigger automatic proposals. Subsequent sync uses history IDs; an expired cursor falls back to the bounded initial import. Attachments and unsent drafts are excluded.
4. Enable cloud sharing for Gmail and configure Groq only if you want AI reply drafts. Set preferences and the daily call budget. Select an incoming mail event and create a proposal. Review the exact recipient, evidence and text. Save any edit; then approve the new revision.
5. Click **Export Gmail draft**. Qareen checks approval, revision, source account and thread snapshot, then creates an unsent draft. Open Gmail and review it again before sending manually. There is no Qareen send button or send API.

Replies are single-recipient and use the observed sender; groups/multiple recipients are refused. Reply-To is not silently substituted for the sender. Mailing-list/automatic messages do not trigger automatic replies. The account is identified from the Gmail profile, and SENT labels help classify aliases.

If export times out, its state becomes unknown. Retry performs a Message-ID lookup instead of another create call. If exactly one match cannot be found, inspect Gmail manually; Qareen deliberately does not guess whether the first request succeeded. A service restart converts interrupted exports to unknown. Only one sync can run at a time.

Google references: [sync](https://developers.google.com/workspace/gmail/api/guides/sync), [drafts](https://developers.google.com/workspace/gmail/api/guides/drafts), [OAuth scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

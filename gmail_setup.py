"""Explicit local OAuth enrollment. Credentials go to the OS keyring, never the repo."""

import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secrets", required=True, help="Google Desktop OAuth client JSON")
    parser.add_argument(
        "--drafts",
        action="store_true",
        help="Also request compose scope for user-approved draft exports (scope includes send; Qareen exposes no send tool)",
    )
    args = parser.parse_args()
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    if args.drafts:
        scopes.append("https://www.googleapis.com/auth/gmail.compose")
    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True)
    keyring.set_password("qareen", "gmail-oauth", credentials.to_json())
    print(
        "Connected. Enable Gmail in dashboard settings; enable cloud sharing separately for AI proposals."
    )


if __name__ == "__main__":
    main()

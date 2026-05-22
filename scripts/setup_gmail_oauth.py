from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.send']

def main():
    client_secrets_file = r"C:\vtx-os\config\gmail-oauth-client.json"

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
    creds = flow.run_local_server(port=0)

    output_path = r"C:\vtx-os\config\gmail-oauth-credentials.json"
    with open(output_path, "w") as token_file:
        token_file.write(creds.to_json())

    print(f"\n✅ Success! Credentials saved to:\n{output_path}")
    print("Next step: Upload this file to Secret Manager.")

if __name__ == "__main__":
    main()
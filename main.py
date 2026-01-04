from pathlib import Path
from datetime import date
import base64
import bs4
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google import genai 
from pydantic import BaseModel, Field
import streamlit as st
import json
from typing import Dict, Optional
import pandas as pd


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
base_path = Path('.')
creds_path = base_path / 'creds'


def autheticate_user() -> str:
    # Check for token.json. If it exists, load credentials from it.
    creds = None
    token_file = creds_path / 'token.json'
    creds_file = creds_path / 'credentials.json'
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(token_file.absolute(), SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # print("Refreshing expired user token...")
            creds.refresh(Request())
        else:
            # print("User not logged in. Please log in...")
            flow = InstalledAppFlow.from_client_secrets_file(creds_file.absolute(), SCOPES)
            flow.redirect_uri = 'http://localhost:44444'
            creds = flow.run_local_server(port=44444)

    # Save the credentials for the next run
    with open(token_file.absolute(), 'w') as token:
        # print("Saving user token...")
        token.write(creds.to_json())
    
    return creds.to_json()

@st.cache_data(ttl=60*60)
def search_emails(creds: str):
    # connect to gmails service
    credentials: Credentials = Credentials.from_authorized_user_info(json.loads(creds))
    service = build("gmail", "v1", credentials=credentials)

    # search for emails with PDFs and subject: "Statement"
    query = "subject:Credit Card Statement in:inbox has:attachment filename:pdf after:2025/12/01 before:2025/12/31"
    # print(f"Searching for emails using query: {query}")

    results = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
    messages = results.get('messages', [])

    # check the emails if found for CC due amounts
    if not messages:
        st.write("No Credit Card statement emails found.")
    else:
        st.write(f"Found {len(messages)} Credit Card statement emails.")

        for i, message in enumerate(messages):
            msg = service.users().messages().get(userId="me", id=message["id"], format="full").execute()
            # print(f"\n\nEmail #{i+1}")
            cc_statement_data = parse_emails(msg)
            df = pd.DataFrame(cc_statement_data.items(), columns=["", " "])
            with st.container(border=True, width="stretch"):
                st.subheader(f"{cc_statement_data.pop('subject')}")
                df = df.drop(0)
                st.dataframe(df, hide_index=True, width="stretch")

def parse_emails(email_data) -> Dict[str, str]:
    payload = email_data.get("payload")
    headers = payload.get("headers")
    parts = payload.get("parts")
    subject = None
    body = None
    for header in headers:
        if header["name"] == "Subject":
            subject = header["value"] 
    
    for part in parts:
        if subject and "HDFC" in subject:
            if part.get("mimeType") == "multipart/alternative":
                for sub_part in part.get("parts"):
                    if sub_part.get("mimeType") == "text/html":
                        body = sub_part.get("body").get("data")
                        break
        else:
            if part.get("mimeType") == "text/html":
                body = part.get("body").get("data")
                break
    # print(f"Subject: {subject}")

    if body is None:
        # print("Non HTML body found. Skipping...")
        # print("Payload: \n", payload)
        st.write(f"CC: {subject}")
        st.write("Non HTML body found. Skipping...")
        return {}

    # decoded body and parse html
    body_html = base64.urlsafe_b64decode(body.encode('utf-8'))
    soup = bs4.BeautifulSoup(body_html, 'html.parser')

    clean_text = soup.get_text(separator="\n", strip=True)
    # print(f"Clean Text: {clean_text}")
    cc_statement_details = get_cc_statement_details(clean_text)
    # print(f"CC Statement Details: {cc_statement_details.model_dump_json()}")
    if cc_statement_details is None:
        return {}
    cc_statement_data = {
        "subject": subject,
        "Total Amount Due (in INR)": cc_statement_details.total_amount_due,
        "Minimum Amount Due (in INR)": cc_statement_details.minimum_amount_due,
        "Payment Due Date": cc_statement_details.payment_due_date
    }
    return cc_statement_data


class CCStatementDetails(BaseModel):
    total_amount_due: float = Field(description="Total amount due")
    minimum_amount_due: float = Field(description="Minimum amount due")
    payment_due_date: date = Field(description="Payment due date")


@st.cache_data(ttl=24*60*60, show_spinner="Fetching CC statement details...")
def get_cc_statement_details(cc_statement_text: str) -> Optional[CCStatementDetails]:
    api_key = None
    with open(creds_path / 'api_key.txt', 'r') as f:
        api_key = json.load(f)
        api_key = api_key['google-gemini']

    if api_key is None:
        st.error("❌ API key not found. Please add it to the api_key.txt file.")
        return None
    
    client = genai.Client(api_key=api_key)
    prompt = f"""
    Extract the following information from the text:
    - Total amount due
    - Minimum amount due
    - Payment due date

    Do check the format in which payment due date is mentioned. It will mostly be in DD-MM-YYYY or YYYY-MM-DD formats or their variants. The format for payment due date can be mentioned in the text as well.

    Text: "{cc_statement_text}"
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": CCStatementDetails.model_json_schema(),
        },
    )
    cc_statement_details = CCStatementDetails.model_validate_json(json_data=response.text)
    return cc_statement_details

    
def main():
    creds = autheticate_user()
    if creds:
        search_emails(creds)
    else:
        st.error("❌ User not logged in. Please log in.")



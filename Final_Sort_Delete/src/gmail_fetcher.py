import os
import pickle
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from .utils.logger import setup_logger
import base64
import asyncio
from bs4 import BeautifulSoup
import html
import re
import time
import ssl
import http.client
from google.auth.transport import requests
from google.oauth2.credentials import Credentials
from google.auth import transport
from google.auth.transport.requests import AuthorizedSession

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'

logger = setup_logger()

class GmailFetcher:
    def __init__(self):
        # If modifying these scopes, delete the file token.pickle.
        self.SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 
                       'https://www.googleapis.com/auth/gmail.modify',
                       'https://www.googleapis.com/auth/gmail.labels',
                       'https://www.googleapis.com/auth/gmail.trash']
        self.creds = None
        self.service = None
        self.batch_size = 100  # Changed from 500 to 100
        self._session_pool = []
        self.max_pool_size = 3
        self.session_ttl = 300  # 5 minutes
        
    def authenticate(self, force_refresh=False):
        """Authenticate with Gmail API"""
        SCOPES = [
            'https://www.googleapis.com/auth/gmail.modify',  # Required for moving to trash
            'https://www.googleapis.com/auth/gmail.readonly'
        ]
        
        creds = None
        token_path = 'token.pickle'
        
        # Load existing credentials
        if os.path.exists(token_path) and not force_refresh:
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
        
        # If credentials are invalid or don't exist
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.error(f"Error refreshing credentials: {str(e)}")
                    creds = None
            
            # If still no valid credentials, need new ones
            if not creds:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                
                # Save the credentials
                with open(token_path, 'wb') as token:
                    pickle.dump(creds, token)
        
        self.service = build('gmail', 'v1', credentials=creds)
        logger.info("Successfully authenticated with Gmail API")
        return self.service

    def fetch_emails(self, max_results=None):
        """Fetches emails in batches and creates batch files"""
        logger.debug("Starting fetch_emails method")
        try:
            if not self.service:
                self.authenticate()
            
            batch_size = 500  # Increased from 20
            page_token = None
            batch_number = 0
            os.makedirs('cache/email_batches', exist_ok=True)
            
            while True:
                # Fetch batch of message IDs
                results = self.service.users().messages().list(
                    userId='me',
                    q='in:inbox -in:trash',
                    maxResults=batch_size,
                    pageToken=page_token
                ).execute()
                
                messages = results.get('messages', [])
                if not messages:
                    break
                    
                # Create batch file
                batch_file = f'cache/email_batches/batch_{batch_number}.json'
                with open(batch_file, 'w') as f:
                    json.dump(messages, f)
                
                logger.info(f"Created batch file {batch_number} with {len(messages)} messages")
                batch_number += 1
                
                # Get next page token
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
            
            return True

        except Exception as e:
            logger.error(f"Error fetching emails: {str(e)}", exc_info=True)
            raise

    def _parse_message(self, message):
        """Parses a Gmail message into required fields"""
        try:
            # Handle case where message might be a string
            if isinstance(message, str):
                logger.error(f"Received string instead of message object: {message[:100]}...")
                return {
                    'message_id': 'unknown',
                    'subject': 'Error: Invalid message format',
                    'sender': 'unknown',
                    'body': 'Error: Could not parse message',
                    'has_attachments': False
                }

            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
            
            def clean_text(text):
                # Decode HTML entities
                text = html.unescape(text)
                
                # Truncate long bodies
                if len(text) > 1000:
                    text = text[:1000] + "..."
                
                # Remove Unicode formatting characters and zero-width spaces
                text = re.sub(r'[\u200b\u200c\u200d\u034f\ufeff]', '', text)
                
                # Remove common marketing/footer content more aggressively
                patterns_to_remove = [
                    r'View in browser.*?(?=\n|$)',
                    r'Unsubscribe.*?(?=\n|$)',
                    r'Privacy Policy.*?(?=\n|$)',
                    r'Terms of Service.*?(?=\n|$)',
                    r'Copyright.*?(?=\n|$)',
                    r'Sent from.*?(?=\n|$)',
                    r'This email was sent.*?(?=\n|$)',
                    r'To unsubscribe.*?(?=\n|$)',
                    r'\[?[A-Za-z\s]+ on Twitter\]?',
                    r'\[?[A-Za-z\s]+ on Facebook\]?',
                    r'\[?[A-Za-z\s]+ on Instagram\]?',
                    r'Follow us on.*?(?=\n|$)',
                    r'Like us on.*?(?=\n|$)',
                    r'\[Image\]',
                    r'Click here.*?(?=\n|$)'
                ]
                
                for pattern in patterns_to_remove:
                    text = re.sub(pattern, '', text, flags=re.IGNORECASE)
                
                # Remove URLs more aggressively
                text = re.sub(r'https?://\S+|www\.\S+', '', text)
                
                # Remove extra whitespace and newlines
                text = re.sub(r'\s+', ' ', text)
                text = text.strip()
                
                return text if text else "No content available"

            def extract_text_from_html(html_content):
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Remove unwanted elements
                for element in soup.find_all([
                    'script', 'style', 'head', 'title', 'meta',
                    'img', 'footer', 'header', 'nav',
                    'link', 'meta', 'noscript'
                ]):
                    element.decompose()
                
                # Remove elements with common marketing/footer classes
                for element in soup.find_all(class_=re.compile(
                    r'footer|signature|disclaimer|unsubscribe|social-media|marketing|banner|ad',
                    re.I
                )):
                    element.decompose()
                
                # Get text with better spacing
                lines = [line.strip() for line in soup.get_text(separator='\n').splitlines()]
                text = ' '.join(line for line in lines if line)
                
                return text

            # Get message body
            body = ''
            parts = []
            
            if 'parts' in message['payload']:
                parts = message['payload']['parts']
            else:
                parts = [message['payload']]

            for part in parts:
                if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                    body = base64.urlsafe_b64decode(part['body']['data']).decode()
                    body = clean_text(body)
                    break
                elif part.get('mimeType') == 'text/html' and 'data' in part.get('body', {}):
                    html_content = base64.urlsafe_b64decode(part['body']['data']).decode()
                    body = extract_text_from_html(html_content)
                    body = clean_text(body)
                    break
                elif 'parts' in part:
                    for subpart in part['parts']:
                        if subpart.get('mimeType') == 'text/plain' and 'data' in subpart.get('body', {}):
                            body = base64.urlsafe_b64decode(subpart['body']['data']).decode()
                            body = clean_text(body)
                            break
                        elif subpart.get('mimeType') == 'text/html' and 'data' in subpart.get('body', {}):
                            html_content = base64.urlsafe_b64decode(subpart['body']['data']).decode()
                            body = extract_text_from_html(html_content)
                            body = clean_text(body)
                            break

            # Check for attachments
            has_attachments = False
            for part in parts:
                if 'filename' in part and part['filename']:
                    has_attachments = True
                    break

            return {
                'message_id': message['id'],
                'subject': subject,
                'sender': sender,
                'body': body,
                'has_attachments': has_attachments
            }

        except Exception as e:
            logger.error(f"Error parsing message: {str(e)}")
            return {
                'message_id': message.get('id', 'unknown'),
                'subject': 'Error: Could not parse message',
                'sender': 'unknown',
                'body': f'Error parsing message: {str(e)}',
                'has_attachments': False
            }

    async def delete_email(self, email_id):
        """Move an email to trash using Gmail API"""
        if not self.service:
            self.authenticate()
        
        max_retries = 3
        base_delay = 2
        
        for attempt in range(max_retries):
            try:
                # Check if already in trash
                message = await asyncio.to_thread(
                    self.service.users().messages().get(
                        userId='me',
                        id=email_id,
                        format='minimal'
                    ).execute
                )
                
                if 'TRASH' in message.get('labelIds', []):
                    logger.info(f"Email {email_id} already in trash")
                    return True
                
                # Attempt to trash the message
                await asyncio.sleep(1)  # Small delay before delete
                await asyncio.to_thread(
                    self.service.users().messages().trash(
                        userId='me',
                        id=email_id
                    ).execute
                )
                logger.info(f"Successfully moved email {email_id} to trash")
                return True
                
            except (ssl.SSLError, http.client.IncompleteRead) as e:
                if attempt == max_retries - 1:
                    logger.error(f"SSL error deleting {email_id} after {max_retries} attempts: {str(e)}")
                    return False
                
                delay = min(300, base_delay * (2 ** attempt))
                logger.warning(f"SSL error deleting {email_id}, attempt {attempt + 1}: {str(e)}")
                await asyncio.sleep(delay)
                
                # Clear SSL state and re-authenticate on SSL errors
                self.clear_ssl_state()
                continue
                
        return False

    async def batch_delete_emails(self, email_ids):
        """Batch delete multiple emails at once"""
        try:
            if not self.service:
                self.authenticate()
            
            # Split into smaller batches to avoid API limits
            batch_size = 50
            for i in range(0, len(email_ids), batch_size):
                batch = email_ids[i:i + batch_size]
                
                # Create batch request
                batch_request = self.service.new_batch_http_request()
                for email_id in batch:
                    batch_request.add(
                        self.service.users().messages().trash(
                            userId='me',
                            id=email_id
                        )
                        )
                
                # Execute batch request with retry
                for attempt in range(3):
                    try:
                        await asyncio.to_thread(batch_request.execute)
                        break
                    except (ssl.SSLError, http.client.IncompleteRead) as e:
                        if attempt == 2:
                            logger.error(f"SSL error deleting batch after 3 attempts: {str(e)}")
                            return False
                        logger.warning(f"SSL error deleting batch, attempt {attempt + 1}: {str(e)}")
                        await asyncio.sleep(2)
                        continue
                    except Exception as e:
                        logger.error(f"Error deleting batch: {str(e)}")
                        return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error in batch delete: {str(e)}")
            return False

    def test_delete_functionality(self):
        """Test the delete functionality with a single email"""
        try:
            # Get the first email from inbox
            results = self.service.users().messages().list(
                userId='me',
                maxResults=1
            ).execute()
            
            messages = results.get('messages', [])
            if not messages:
                print(f"{Colors.YELLOW}No messages found to test deletion{Colors.RESET}")
                return
            
            test_email = messages[0]
            msg = self.service.users().messages().get(
                userId='me', 
                id=test_email['id']
            ).execute()
            
            # Print email details
            headers = msg['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No subject')
            print(f"\n{Colors.CYAN}Testing deletion with:{Colors.RESET}")
            print(f"Email ID: {test_email['id']}")
            print(f"Subject: {subject}\n")
            
            # Attempt deletion
            if self.delete_email(test_email['id']):
                print(f"{Colors.GREEN}Delete test successful!{Colors.RESET}")
            else:
                print(f"{Colors.RED}Delete test failed!{Colors.RESET}")
            
        except Exception as e:
            print(f"{Colors.RED}Error testing delete functionality: {str(e)}{Colors.RESET}")

    async def process_emails(self):
        page_size = 500  # Process 100 emails at a time
        next_page_token = None
        
        while True:
            try:
                results = self.service.users().messages().list(
                    userId='me', 
                    maxResults=page_size,
                    pageToken=next_page_token
                ).execute()
                
                emails = results.get('messages', [])
                if not emails:
                    break
                    
                # Process this batch of emails
                for email in emails:
                    await self.process_single_email(email['id'])
                    await asyncio.sleep(0.1)  # Small delay to prevent rate limiting
                
                # Get next page token
                next_page_token = results.get('nextPageToken')
                if not next_page_token:
                    break
                    
            except Exception as e:
                logger.error(f"Error processing emails batch: {str(e)}")
                break

    def fetch_batch(self, page_token=None):
        """Fetch a batch of emails using pagination"""
        try:
            if not self.service:
                self.authenticate()
            
            logger.info("Executing Gmail API batch request")
            results = self.service.users().messages().list(
                userId='me',
                q='in:inbox -in:trash',
                maxResults=500,  # Increased from 100 to 500
                pageToken=page_token
            ).execute()
            
            messages = results.get('messages', [])
            if messages:
                logger.info(f"Found {len(messages)} messages in this batch")
                # Get full message details for each ID
                detailed_messages = []
                for message in messages:
                    full_message = self.service.users().messages().get(
                        userId='me',
                        id=message['id']
                    ).execute()
                    detailed_messages.append(self._parse_message(full_message))
                
                return {
                    'messages': detailed_messages,
                    'nextPageToken': results.get('nextPageToken')
                }
            
            return None

        except Exception as e:
            logger.error(f"Error fetching batch: {str(e)}")
            raise

    async def fetch_next_batch(self, page_token=None):
        try:
            if not self.service:
                self.authenticate()
            
            logger.info(f"Fetching next batch with page token: {page_token}")
            
            # Get list of 500 message IDs
            results = await asyncio.wait_for(
                asyncio.to_thread(
                    self.service.users().messages().list(
                        userId='me',
                        q='in:inbox -in:trash',
                        maxResults=500,
                        pageToken=page_token
                    ).execute
                ),
                timeout=30
            )
            
            if not results or not results.get('messages'):
                return None
            
            messages = results.get('messages', [])
            logger.info(f"Found {len(messages)} messages in response")
            
            # Process in chunks of 20 to avoid quota limits
            detailed_messages = []
            for i in range(0, len(messages), 20):
                chunk = messages[i:i+20]
                batch = self.service.new_batch_http_request()
                
                def callback(request_id, response, exception):
                    if exception:
                        logger.error(f"Batch request error: {str(exception)}")
                    else:
                        try:
                            detailed_messages.append(self._parse_message(response))
                        except Exception as e:
                            logger.error(f"Error parsing message in callback: {str(e)}")
                
                for msg in chunk:
                    request = self.service.users().messages().get(userId='me', id=msg['id'])
                    batch.add(request, callback=callback)
                
                logger.info(f"Processing chunk {i//20 + 1} of {(len(messages) + 19)//20}")
                await asyncio.to_thread(batch.execute)
                await asyncio.sleep(.01)  # Rate limiting delay between chunks
            
            return {
                'messages': detailed_messages,
                'nextPageToken': results.get('nextPageToken')
            }
            
        except Exception as e:
            logger.error(f"Error in fetch_next_batch: {str(e)}", exc_info=True)
            return None

    async def execute_with_retry(self, func):
        """Execute API calls with timeout and retry logic"""
        for attempt in range(self.max_retries):
            try:
                # Add 30 second timeout
                result = await asyncio.wait_for(
                    asyncio.to_thread(func),
                    timeout=30
                )
                return result
                
            except asyncio.TimeoutError:
                logger.error(f"Operation timed out on attempt {attempt + 1}")
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(self.base_delay * (2 ** attempt))
                continue
                
            except (ssl.SSLError, http.client.IncompleteRead) as e:
                if attempt == self.max_retries - 1:
                    raise
                delay = min(300, self.base_delay * (2 ** attempt))
                logger.warning(f"SSL error occurred, retrying in {delay}s...")
                await asyncio.sleep(delay)
                self.clear_ssl_state()
                continue

    def create_ssl_context(self):
        """Create SSL context with more robust error handling"""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.options |= ssl.OP_NO_COMPRESSION  # Disable compression
        context.load_default_certs()
        context.set_ciphers('HIGH:!DH:!aNULL')
        return context

    async def clear_ssl_state(self):
        """Clear SSL state with simplified error handling"""
        try:
            # Clear existing credentials
            token_path = 'token.pickle'
            if os.path.exists(token_path):
                os.remove(token_path)
                logger.info("Removed existing token.pickle")
            
            # Create fresh SSL context
            self._ssl_context = self.create_ssl_context()
            
            # Force new authentication
            self.authenticate(force_refresh=True)
            logger.info("SSL state cleared and authentication reset")
            return True
            
        except Exception as e:
            logger.error(f"Error clearing SSL state: {str(e)}")
            return False

    def get_session(self):
        """Get a session with explicit SSL protocol version"""
        session = AuthorizedSession(self.creds)
        session.verify = True
        
        # Create adapter with specific SSL settings
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=3,
            pool_block=False
        )
        
        # Force TLS 1.2
        context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        context.options |= ssl.OP_NO_SSLv2
        context.options |= ssl.OP_NO_SSLv3
        context.options |= ssl.OP_NO_TLSv1
        context.options |= ssl.OP_NO_TLSv1_1
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        
        # Apply context to adapter
        adapter.init_poolmanager(
            connections=1,
            maxsize=1,
            ssl_version=ssl.PROTOCOL_TLS,
            ssl_context=context
        )
        
        session.mount('https://', adapter)
        return session

__all__ = ['GmailFetcher']

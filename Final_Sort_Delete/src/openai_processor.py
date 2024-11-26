import os
import json
import time
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv
from .utils.logger import setup_logger
import sys
import subprocess
from datetime import datetime

# Enable ANSI colors in Windows
if sys.platform == "win32":
    subprocess.run("", shell=True)

# ANSI color codes
class Colors:
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'

load_dotenv()
logger = setup_logger()

class OpenAIProcessor:
    def __init__(self, gmail_fetcher, max_concurrent=3):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
            
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = "gpt-4o-mini"
        self.processed_batches = set()
        self.gmail_fetcher = gmail_fetcher
        self.total_processed = 0
        self.total_kept = 0
        self.total_deleted = 0
        self.start_time = time.time()
        self.delete_queue = []
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent * 5)
        self.status_line = "=== Email Processing Active ==="
        
        # Setup console display
        print("\033[?25l")  # Hide cursor
        self.clear_console()
        self.output_buffer = []
        
        logger.info(f"OpenAI processor initialized with model: {self.model} (max concurrent: {max_concurrent})")
        
        self.batch_start_time = time.time()
        self.batch_processed = 0
        self.previous_batch_rate = 0

    def clear_console(self):
        """Clear the console screen"""
        print("\033[2J\033[H", end="")
        
    def update_display(self):
        """Update the console display with better formatting"""
        self.clear_console()
        
        # Print fixed header
        print(f"\n{Colors.CYAN}=== Email Processing Status ==={Colors.RESET}")
        print(f"{Colors.YELLOW}Processed: {self.total_processed} | "
              f"Kept: {self.total_kept} | "
              f"Deleted: {self.total_deleted}{Colors.RESET}")
        print(f"{Colors.MAGENTA}Processing Rate: {self._calculate_rate():.1f} emails/sec{Colors.RESET}")
        print(f"{Colors.CYAN}{'='*40}{Colors.RESET}\n")
        
        # Show only last 5 decisions to prevent cluttering
        for msg in self.output_buffer[-5:]:
            if "[KEEP]" in msg:
                color = Colors.GREEN
            elif "[DELETE]" in msg:
                color = Colors.RED
            else:
                color = Colors.RESET
            
            # Format decision output more compactly
            if "[KEEP]" in msg or "[DELETE]" in msg:
                decision = msg.split("|")[0].strip()
                subject = msg.split("Subject:")[1].split("|")[0].strip()
                print(f"{color}{decision:<8} {subject[:50]}{Colors.RESET}")
            else:
                print(f"{color}{msg}{Colors.RESET}")

    def add_to_buffer(self, message, color=Colors.RESET):
        """Add message to buffer with improved formatting"""
        if "Successfully deleted" in message:
            return  # Skip individual deletion confirmations
        
        if "Processing" in message and "sub-batch" in message:
            return  # Skip sub-batch processing messages
        
        # Keep buffer size manageable
        if len(self.output_buffer) > 20:
            self.output_buffer.pop(0)
        
        self.output_buffer.append(message)
        self.update_display()
        
    async def process_delete_queue(self):
        if self.delete_queue:
            for email_id in self.delete_queue:
                success = await self.gmail_fetcher.delete_email(email_id)
                if success:
                    self.add_to_buffer(f"Successfully deleted email: {email_id}", Colors.GREEN)
                else:
                    self.add_to_buffer(f"Failed to delete email: {email_id}", Colors.RED)
            self.delete_queue.clear()

    async def process_batch(self, batch_file):
        try:
            with open(batch_file, 'r') as f:
                messages = json.load(f)
            
            sub_batch_size = 50
            sub_batches = [
                messages[i:i + sub_batch_size] 
                for i in range(0, len(messages), sub_batch_size)
            ]
            
            for i, sub_batch in enumerate(sub_batches):
                try:
                    await self._process_sub_batch(sub_batch, i + 1, len(sub_batches))
                    if self.delete_queue:
                        await self.process_delete_queue()
                    if i < len(sub_batches) - 1:
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Error in sub-batch {i + 1}: {str(e)}")
                    continue
            
            # Mark batch as processed
            self.processed_batches.add(batch_file)
            
        except Exception as e:
            logger.error(f"Error processing batch {batch_file}: {str(e)}")
            raise

    async def _process_sub_batch(self, emails, batch_num, total_batches):
        try:
            self.add_to_buffer(f"Processing sub-batch {batch_num} of {total_batches}", Colors.YELLOW)
            
            # Add 5 second delay between batches
            if batch_num > 1:
                await asyncio.sleep(1)
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an email retention assistant. You must respond with valid JSON only."},
                    {"role": "user", "content": self._construct_batch_prompt(emails)}
                ],
                response_format={ "type": "json_object" }
            )
            
            return await self._handle_openai_response(response, emails)
            
        except Exception as e:
            self.add_to_buffer(f"Error in sub-batch {batch_num}: {str(e)}", Colors.RED)
            return []

    async def _handle_openai_response(self, response, emails):
        try:
            logger.debug("Received response from OpenAI")
            
            try:
                decisions = json.loads(response.choices[0].message.content)
                
                if isinstance(decisions, dict) and 'decisions' in decisions:
                    results = decisions['decisions']
                else:
                    results = decisions if isinstance(decisions, list) else []
                
                for result in results:
                    if isinstance(result, dict):
                        # Format decision output
                        decision_str = (
                            f"[{Colors.GREEN}KEEP{Colors.RESET}]" 
                            if result.get('decision') == 'KEEP' 
                            else f"[{Colors.RED}DELETE{Colors.RESET}]"
                        )
                        
                        print(f"{decision_str} Subject: {result.get('subject')[:50]}...")
                        print(f"       Reason: {result.get('reason')[:100]}...")
                        print("-" * 80)
                        
                        if result.get('decision') == 'KEEP':
                            self.total_kept += 1
                        elif result.get('decision') == 'DELETE':
                            self.total_deleted += 1
                            self.delete_queue.append(result['email_id'])
                            if len(self.delete_queue) >= 25:
                                await self.process_delete_queue()
                        
                        self.total_processed += 1
                        self.batch_processed += 1
                        self._update_status_line()
                    
            except json.JSONDecodeError as je:
                logger.error(f"Failed to parse OpenAI response: {je}")
                return []
                
        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            return []

    async def watch_and_process(self, batch_directory='cache/email_batches', running_flag=None):
        """Watch for new batch files and process them concurrently"""
        logger.info("Starting OpenAI processor watch service...")
        
        if running_flag is None:
            running_flag = lambda: True
        
        while running_flag():
            try:
                batch_files = [
                    os.path.join(batch_directory, f) 
                    for f in os.listdir(batch_directory) 
                    if f.startswith('batch_') and f.endswith('.json')
                ]
                
                unprocessed_batches = [
                    batch for batch in batch_files 
                    if batch not in self.processed_batches
                ]
                
                if unprocessed_batches:
                    tasks = [
                        self.process_batch(batch) 
                        for batch in unprocessed_batches[:self.max_concurrent]
                    ]
                    await asyncio.gather(*tasks)
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in watch_and_process: {str(e)}")
                if running_flag():
                    await asyncio.sleep(5)

    def _calculate_rate(self):
        elapsed_time = time.time() - self.start_time
        return self.total_processed / elapsed_time if elapsed_time > 0 else 0

    def _construct_prompt(self, email):
        """Construct prompt for email retention analysis"""
        return f"""
Act as an intelligent email assistant to organize and retain emails carefully. Your goal is to ensure no potentially important emails are lost, especially those that may hold value or be needed in the future. Always err on the side of caution, keeping emails unless their irrelevance is absolutely certain.

Analyze this email:
Subject: {email['subject']}
From: {email['sender']}
Has Attachments: {email['has_attachments']}

Body:
{email['body']}

Use these specific principles in your analysis:
1. RETAIN if there are attachments, calendar invites, or any indication of future events, actions, or deadlines
2. Consider sender identity and frequency of contact (prioritize emails from colleagues, clients, or frequently interacted senders)
3. Evaluate if this is part of an ongoing discussion or project that needs context preservation
4. Be aggressive in deleting promotional and social emails UNLESS:
   - There's evidence of a purchase or signup
   - It contains specific, personal relevance
   - It might be needed for future reference
5. Take a human approach to understanding the email's long-term value

Provide your decision in JSON format:
{{
    "subject": "the email subject",
    "decision": "KEEP or DELETE",
    "reason": "detailed explanation of why, referencing the specific principles above that led to this decision"
}}

Remember: Always err on the side of caution - keep emails unless their irrelevance is absolutely certain."""

    def _construct_batch_prompt(self, emails):
        """Construct prompt for batch email retention analysis"""
        email_list = []
        for email in emails:
            email_list.append(f"""
Email ID: {email['message_id']}
Subject: {email['subject']}
From: {email['sender']}
Has Attachments: {email['has_attachments']}
Body:
{email['body']}
---""")
        
        return f"""Analyze these emails and return a JSON object with a 'decisions' array containing analysis for each email.

{chr(10).join(email_list)}

Analysis principles:
1. RETAIN if there are attachments, calendar invites, or future events/deadlines
2. Consider sender importance and contact frequency
3. Evaluate ongoing discussion/project context
4. Delete promotional/social emails unless they contain:
   - Purchase/signup evidence
   - Personal relevance
   - Future reference value
5. Be cautious - keep if uncertain

Return ONLY a JSON object in this format:
{{
    "decisions": [
        {{
            "email_id": "message_id",
            "subject": "email subject",
            "decision": "KEEP|DELETE",
            "reason": "explanation"
        }}
    ]
}}"""
    def _update_status_line(self):
        """Update status with basic formatting"""
        total_rate = self._calculate_rate()
        status = (
            f"\n{Colors.CYAN}{'='*50}\n"
            f"Processed: {self.total_processed} | "
            f"{Colors.GREEN}Kept: {self.total_kept}{Colors.RESET} | "
            f"{Colors.RED}Deleted: {self.total_deleted}{Colors.RESET} | "
            f"Rate: {total_rate:.1f}/s\n"
            f"{'='*50}{Colors.RESET}\n"
        )
        print(status)

    def update_status(self, new_status=None):
        """Update the pinned status line"""
        if new_status:
            self.status_line = new_status
        # Move cursor up one line, clear line, print status
        print(f"\033[2K\033[1A{self.status_line}", end='\r')

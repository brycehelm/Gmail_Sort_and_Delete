# Email Sorting Assistant

An AI-powered email management tool that automatically sorts Gmail inbox using GPT-4-mini to make intelligent keep/delete decisions.

## Core Features

- Batch Processing: Processes emails in groups of 10 for efficient API usage
- Smart Text Parsing: Cleans email content to pure readable text
- AI Analysis: Uses GPT-4-mini to evaluate email importance
- Automated Cleanup: Moves irrelevant emails to trash
- Real-time Logging: Color-coded progress and performance metrics

## Process Flow

1. **Email Fetching**
   - Retrieves emails in batches of 10
   - Extracts: ID, subject, labels, body
   - Creates manageable batch files

2. **Content Cleaning**
   - Removes HTML, signatures, footers
   - Strips marketing elements
   - Converts to plain English text

3. **AI Analysis**
   - Sends cleaned batches to GPT-4-mini
   - Receives structured JSON decisions
   - Format:
     ```json
     {
       "email_id": "unique_id",
       "subject": "email subject",
       "decision": "KEEP/DELETE",
       "reason": "detailed explanation"
     }
     ```

4. **Batch Actions**
   - Processes AI decisions
   - Moves DELETE emails to trash
   - Maintains email integrity

5. **Performance Monitoring**
   - Color-coded terminal output
     - Green: Successful operations
     - Yellow: Warnings
     - Red: Errors
   - Real-time statistics
     - Emails processed/second
     - Success/failure rates
     - Batch completion status

## Key Components

- GmailFetcher: Handles Gmail API interactions
- OpenAIProcessor: Manages AI analysis
- Logger: Tracks operations and statistics

## Requirements

- Python 3.10+
- Gmail API credentials
- OpenAI API key
- Required Python packages (see requirements.txt)

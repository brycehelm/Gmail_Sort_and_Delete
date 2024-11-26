import asyncio
import signal
import os
import json
from datetime import datetime
from .gmail_fetcher import GmailFetcher
from .openai_processor import OpenAIProcessor
from .utils.logger import setup_logger

# Global flag for graceful shutdown
running = True
logger = setup_logger()

def signal_handler(signum, frame):
    global running
    logger.info("Signal handler called - initiating shutdown")
    print("\nShutting down gracefully... Please wait.")
    running = False

async def main():
    logger.info("=== Starting Email Processing ===")
    signal.signal(signal.SIGINT, signal_handler)
    
    # Clear all cache files
    cache_dir = 'cache/email_batches'
    if os.path.exists(cache_dir):
        logger.info("Clearing email batch cache...")
        for file in os.listdir(cache_dir):
            file_path = os.path.join(cache_dir, file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                    logger.debug(f"Deleted cache file: {file}")
            except Exception as e:
                logger.error(f"Error deleting cache file {file_path}: {e}")
    
    try:
        logger.info("Initializing GmailFetcher...")
        fetcher = GmailFetcher()
        
        logger.info("Clearing SSL state and refreshing authentication...")
        fetcher.clear_ssl_state()
        logger.info("Gmail authentication successful")
        
        logger.info("Initializing OpenAI processor...")
        processor = OpenAIProcessor(gmail_fetcher=fetcher, max_concurrent=10)
        
        # Create cache directory
        os.makedirs('cache/email_batches', exist_ok=True)
        
        # Process emails in chunks
        processed_count = 0
        batch_number = 0
        start_time = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Start first batch fetch
        logger.info(f"Fetching first batch (processed so far: {processed_count})")
        current_batch = await fetcher.fetch_next_batch()
        
        while running and current_batch and current_batch.get('messages'):
            try:
                batch_file = f'cache/email_batches/batch_{start_time}_{batch_number}.json'
                with open(batch_file, 'w') as f:
                    json.dump(current_batch['messages'], f)
                
                next_batch_task = asyncio.create_task(
                    asyncio.wait_for(
                        fetcher.fetch_next_batch(current_batch.get('nextPageToken')),
                        timeout=60
                    )
                )
                
                await processor.process_batch(batch_file)
                processed_count += len(current_batch['messages'])
                
                try:
                    next_batch = await next_batch_task
                except asyncio.TimeoutError:
                    logger.error("Timeout fetching next batch, retrying...")
                    await asyncio.sleep(5)
                    next_batch = await fetcher.fetch_next_batch(current_batch.get('nextPageToken'))
                
                if not next_batch or not next_batch.get('messages'):
                    logger.info("No more messages to process")
                    break
                
                batch_number += 1
                current_batch = next_batch
                logger.info(f"Moving to batch {batch_number} (processed so far: {processed_count})")
                
            except Exception as e:
                logger.error(f"Error in processing loop: {str(e)}")
                if running:
                    await asyncio.sleep(10)
                    try:
                        await fetcher.clear_ssl_state()
                        current_batch = await fetcher.fetch_next_batch(current_batch.get('nextPageToken'))
                    except Exception as inner_e:
                        logger.error(f"Failed to recover: {str(inner_e)}")
                        break
                else:
                    break
        
        logger.info(f"=== Processing Complete ===")
        logger.info(f"Total emails processed: {processed_count}")
        
    except Exception as e:
        logger.error(f"Process failed: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        logger.info("Starting application...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)

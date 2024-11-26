import logging
import os

def setup_logger():
    # Get logger instance
    logger = logging.getLogger('EmailProcessor')
    
    # Only set up handlers if they haven't been set up already
    if not logger.handlers:
        # Create logs directory if it doesn't exist
        if not os.path.exists('logs'):
            os.makedirs('logs')

        # Configure logging
        logger.setLevel(logging.DEBUG)

        # Create file handler with UTF-8 encoding
        file_handler = logging.FileHandler('logs/app.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)

        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Add handlers to logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger

__all__ = ['setup_logger'] 
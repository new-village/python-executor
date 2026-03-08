import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def main():
    # Cloud Run Jobs environment variable
    index = os.getenv("CLOUD_RUN_TASK_INDEX", "0")
    logger.info(f"Hello from Cloud Run Jobs! Task Index: {index}")

if __name__ == "__main__":
    main()
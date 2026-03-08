import logging
import os

# Configure logging at the module level for direct execution
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def run():
    """A simple task that prints a greeting."""
    logger.info("--- Executing the 'hello' task ---")
    
    # Example of getting task-specific args from environment variables if needed
    # Cloud Run Jobs default variables
    index = os.getenv("CLOUD_RUN_TASK_INDEX", "0")
    logger.info(f"Task Index: {index}")
    
    logger.info("Hello, World!")
    logger.info("----------------------------------")

if __name__ == "__main__":
    run()

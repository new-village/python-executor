import logging
import os

# Configure logging at the module level for direct execution
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def run():
    """A simple task that prints a greeting."""
    logger.info("--- Executing the 'hello' task ---")
    
    # Log the running module name
    task_module = os.getenv("TASK_MODULE", "tasks.hello")
    logger.info(f"TASK_MODULE: {task_module}")
    
    logger.info("Hello, World!")
    logger.info("----------------------------------")

if __name__ == "__main__":
    run()

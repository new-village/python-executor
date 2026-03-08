import logging
import os
from datetime import datetime
import pandas as pd
import jpcorpreg

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    """
    Fetch data from NTA Corporate Number Publication Site and save as Parquet.
    """
    try:
        # 1. Fetch data using jpcorpreg
        # Instantiate the client first
        logger.info("Initializing CorporateRegistryClient...")
        client = jpcorpreg.CorporateRegistryClient()
        
        logger.info("Fetching corporate registry data from NTA (this may take a while)...")
        df = client.fetch()
        
        if df is None or df.empty:
            logger.warning("No data fetched from NTA.")
            return

        record_count = len(df)
        logger.info(f"Successfully fetched {record_count} records.")

        # 2. Prepare destination
        data_dir = "/data"
        if not os.path.exists(data_dir):
            # For local testing if /data is not root
            if not os.access("/", os.W_OK):
                data_dir = "./data"
            
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
                logger.info(f"Created directory: {data_dir}")

        # 3. Save as Parquet
        current_yyyymm = datetime.now().strftime("%Y%m")
        filename = f"corpreg_nta_{current_yyyymm}.parquet"
        filepath = os.path.join(data_dir, filename)

        logger.info(f"Saving data to {filepath}...")
        df.to_parquet(filepath, engine='pyarrow', index=False)
        
        logger.info("Task completed successfully.")
        logger.info(f"Filename: {filename}")
        logger.info(f"Record count: {record_count}")

    except Exception as e:
        logger.error(f"Error in fetch_corpreg_nta_all: {str(e)}")
        raise

if __name__ == "__main__":
    main()

import logging
import os
import sys
from datetime import datetime
import pandas as pd
import jpcorpreg

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    """
    Fetch diff data from NTA Corporate Number Publication Site and save as Parquet.
    Accepted argument: YYYYMMDD
    """
    try:
        # Get target date from arguments if provided
        target_date = None
        if len(sys.argv) > 1:
            target_date = sys.argv[1]
            logger.info(f"Target date provided via arguments: {target_date}")
        else:
            # Default to today's date
            target_date = datetime.now().strftime("%Y%m%d")
            logger.info(f"No target date provided. Defaulting to today: {target_date}")

        # 1. Fetch data using jpcorpreg
        logger.info("Initializing CorporateRegistryClient...")
        client = jpcorpreg.CorporateRegistryClient()
        
        logger.info(f"Fetching corporate registry DIFF data for {target_date}...")
        df = client.fetch_diff(date=target_date)
        
        if df is None or df.empty:
            logger.info(f"No diff data found for {target_date}.")
            return

        record_count = len(df)
        logger.info(f"Successfully fetched {record_count} records.")

        # 2. Prepare destination
        data_dir = "/data"
        if not os.path.exists(data_dir):
            if not os.access("/", os.W_OK):
                data_dir = "./data"
            
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
                logger.info(f"Created directory: {data_dir}")

        # 3. Save as Parquet
        filename = f"corpreg_nta_{target_date}.parquet"
        filepath = os.path.join(data_dir, filename)

        logger.info(f"Saving data to {filepath}...")
        df.to_parquet(filepath, engine='pyarrow', index=False)
        
        logger.info("Task completed successfully.")
        logger.info(f"Filename: {filename}")
        logger.info(f"Record count: {record_count}")

    except ValueError as ve:
        if "No sabun data found" in str(ve):
            logger.info(f"No diff data published yet for {target_date} (this is normal for holidays or early morning).")
        else:
            logger.error(f"Value error in fetch_corpreg_nta_diff: {str(ve)}")
            raise
    except Exception as e:
        logger.error(f"Error in fetch_corpreg_nta_diff: {str(e)}")
        raise

if __name__ == "__main__":
    main()

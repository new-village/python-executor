import os
import datetime
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from jpcorpreg import CorporateRegistryClient

def run(column_mapping="english", data_dir="/data"):
    """
    Downloads the entire Japanese corporate registry and saves it to a Parquet file.
    Fetches all data at once as requested.
    """
    # 1. Determine the filename based on the current year and month
    now = datetime.datetime.now()
    yyyymm = now.strftime("%Y%m")
    filename = f"corporate_registry_nta_{yyyymm}.parquet"
    filepath = os.path.join(data_dir, filename)

    print(f"Target file: {filepath}")
    
    # Ensure data directory exists
    if not os.path.exists(data_dir):
        print(f"Warning: Directory {data_dir} does not exist. Attempting to create it...")
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception as e:
            print(f"Error: Could not create directory {data_dir}: {e}")
            # Fallback to local 'data' directory for local testing
            data_dir = "./data"
            os.makedirs(data_dir, exist_ok=True)
            filepath = os.path.join(data_dir, filename)
            print(f"Falling back to: {filepath}")

    print(f"Initializing CorporateRegistryClient with mapping: {column_mapping}")
    client = CorporateRegistryClient(column_mapping=column_mapping)

    try:
        print("Fetching full national registry (this may take a few minutes)...")
        # Fetch nationwide data at once
        df = client.fetch()
        
        if df.empty:
            print("Error: No data fetched from NTA.")
            return

        print(f"Successfully fetched {len(df)} records.")
        
        # Save to Parquet (overwrite by default when calling to_parquet)
        print(f"Saving to {filepath}...")
        df.to_parquet(filepath, index=False)
        print(f"Full download and save completed.")

    except Exception as e:
        print(f"An error occurred during the process: {e}")
        raise

if __name__ == "__main__":
    # For testing purposes
    # os.environ["TASK_ARGS"] = '{"data_dir": "./data"}'
    import json
    args_json = os.environ.get("TASK_ARGS", "{}")
    args = json.loads(args_json)
    run(**args)

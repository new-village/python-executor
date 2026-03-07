from jpcorpreg import CorporateRegistryClient
import pandas as pd
import os

def run(prefecture=None, date=None, column_mapping="english"):
    """
    Downloads corporate registry data using jpcorpreg.
    """
    print(f"Initializing CorporateRegistryClient with mapping: {column_mapping}")
    client = CorporateRegistryClient(column_mapping=column_mapping)

    if date:
        print(f"Fetching differential updates since: {date}")
        df = client.fetch_diff(date)
    else:
        print(f"Fetching full registry for prefecture: {prefecture if prefecture else 'All Japan'}")
        df = client.fetch(prefecture)

    print(f"Successfully fetched {len(df)} records.")
    
    # In a real scenario, we would save this to a database or Cloud Storage.
    # For now, we'll just show the first few rows.
    print(df.head())
    
    # Save to a temporary CSV for demonstration if needed
    # os.makedirs("output", exist_ok=True)
    # df.to_csv("output/latest_data.csv", index=False)

if __name__ == "__main__":
    # This allows direct execution for testing
    import json
    args = json.loads(os.environ.get("TASK_ARGS", "{}"))
    run(**args)

import os
import re
import psycopg
import pandas as pd
import csv

# Connect to the database
with psycopg.connect(host="host", dbname="db", user="username") as conn:
    # Create a cursor
    with conn.cursor() as cur:
        # SQL query to retrieve system serial numbers
        cur.execute(
            """
            SELECT ps.system_serial
            FROM production_part pp 
            JOIN production_system ps ON ps.id = pp.system_id 
            JOIN production_configuration pc ON pc.id = ps.config_name_id 
            JOIN production_order po ON po.id = pc.order_number_id 
            WHERE start_date::text LIKE '2023-01%' 
            AND pp.manufacturer_id = 166
            ORDER BY start_date ASC, ps.system_serial ASC
            """
        )
        # Fetch the data
        sql_list = cur.fetchall()
        # Remove asterisks from each element in the list
        cleaned_sql_list = [(re.sub(r"\*", "", element[0]),) for element in sql_list]
        serial_list = list(sum(cleaned_sql_list, ()))
# Create lists to store test_date[0] outputs and DIMM contents
burn_in_directory = []
dimm_contents = []

# Set the path for the output CSV file
output_csv_path = os.sep.join([os.path.dirname(__file__), "A2Z_Affected_Systems.csv"])

# Create a DataFrame to store the data
data = {
    "System Serial": [],
    "DIMM Serial": [],
    "DIMM Manufacturer": [],
    "DIMM Part Number": [],
}

# Iterate through each system_serial in the serial_list
for system_serial in serial_list:
    directory_path = f"//10.246.0.110/pbsv4/pbs_logs/{system_serial}"

    try:
        test_date = sorted(os.listdir(directory_path), reverse=True)
        if test_date:
            # Append test_date[0] to the burn_in_directory list
            burn_in_directory.append(test_date[0])

            # Complete the directory path
            complete_path = os.path.join(directory_path, test_date[0])

            # Define the path to DIMM_MemoryChipData.txt
            dimm_data_path = os.path.join(complete_path, "DIMM_MemoryChipData.txt")

            # Check if DIMM_MemoryChipData.txt exists and read its contents
            if os.path.exists(dimm_data_path):
                with open(dimm_data_path, "r", encoding="utf-16-le") as dimm_file:
                    dimm_contents = dimm_file.read()

                    # Split the data into lines
                    lines = dimm_contents.split("\n")

                    # Process each line and split it into columns by two or more whitespaces
                    for line in lines[1:]:
                        if line.strip():  # Check if the line is not empty
                            columns = [
                                col
                                for col in re.split(r"\s{2,}", line.strip())
                                if len(col) > 5
                            ]  # Split by two or more whitespaces & ignore columns with > 5 characters
                            if (
                                len(columns) >= 6
                            ):  # Make sure there are at least 6 columns
                                data["System Serial"].append(
                                    str(system_serial)
                                )  # Convert to string to preserve leading zeros
                                data["DIMM Serial"].append(
                                    str(columns[4].strip())
                                )  # Convert to string to preserve leading zeros
                                data["DIMM Manufacturer"].append(
                                    str(columns[2].strip())
                                )  # Convert to string to preserve leading zeros
                                data["DIMM Part Number"].append(
                                    str(columns[3].strip())
                                )  # Convert to string to preserve leading zeros
            else:
                print(f"DIMM_MemoryChipData.txt not found in {complete_path}")
    except FileNotFoundError:
        print(f"Directory not found: {directory_path}")
# Create a DataFrame from the collected data
df = pd.DataFrame(data)

# Save the DataFrame to a CSV file with quoting set to QUOTE_ALL
df.to_csv(output_csv_path, index=None, quoting=csv.QUOTE_ALL)

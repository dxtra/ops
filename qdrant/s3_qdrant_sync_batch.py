import boto3
import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.http import models
from tqdm import tqdm
import ast
import random
import polars as pl
import json
import io
import tempfile


s3_params = {
    'bucket': 'bucket',
    'access_key': 'access_key',
    'secret_key': 'secret_key',
    'aws_endpoint': "https://storage.net"
}

bucket_name = 'bucket_name'
prefix = 'bucket/path/to/csv'

# Initialize boto3 to access S3
s3_client = boto3.client(
    's3',
    endpoint_url=s3_params['aws_endpoint'],
    aws_access_key_id=s3_params['access_key'],
    aws_secret_access_key=s3_params['secret_key']
)

qdrant_client = QdrantClient(host="localhost", port=6333, timeout=300)


collection_name = "collection"
# collection_creation_payload = models.CreateCollection(
#     collection_name=collection_name,
#     vectors_config=models.VectorParams(
#         size=512,
#         distance=models.Distance.COSINE  # adjust accordingly
#     )
# )

# qdrant_client.recreate_collection(collection_name=collection_name, collection=collection_creation_payload)


def generate_payload():
    payload = {}
    payload_threshold = {
        "views": [1, 50000],
        "duration": [0, 180],
        "creator_id": [0, 50_000],
    }
    for entity in payload_threshold.keys():
        min_val, max_val = payload_threshold[entity][0], payload_threshold[entity][1]
        value = random.randint(min_val, max_val)
        payload[entity] = value
    payload["likes"] = random.randint(0, min(1000, payload["views"]))
    payload["viewed"] = random.randint(0, min(10_000,payload["views"]))
    payload["er"] = payload["likes"] / payload["views"]
    is_banned = 1 if random.randint(0, 10) >= 7 else 0
    payload["is_banned"] = is_banned
    return payload


def upload_dataframe_to_qdrant(df, collection_name):
    batch_size = 512

    num_batches = (df.height + batch_size - 1) // batch_size  # Compute the number of batches needed

    # Iterate through each batch
    for batch_index in tqdm(range(num_batches), desc="Uploading batches"):
        # Extract the current batch from Polars DataFrame
        start_index = batch_index * batch_size
        end_index = min(start_index + batch_size, df.height)
        batch = df.slice(start_index, end_index - start_index)

        points = []
        # Iterate through rows in the batch, Polars DataFrames use 'to_dicts' method for row-wise iteration
        for row in batch.rows():
            payload = generate_payload()
            payload["loaded_at"] = ast.literal_eval(row[2])
            # payload = {"loaded_at": ast.literal_eval(row["payload"])}  # Payload processing
            point = models.PointStruct(
                id=row[0],
                payload=payload,
                vector=row[1]
            )
            points.append(point)

        # Perform batch upload with upsert call inside the loop
        qdrant_client.upsert(
            collection_name=collection_name,
            points=points
        )



# List all csv files from the S3 directory and process each
paginator = s3_client.get_paginator('list_objects_v2')
page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

visited_files = set()

try:
    with open('last_processed_date2qdrant.json', 'r') as file:
        data = json.load(file)
        visited_files = set(data['processed_files'])
except FileNotFoundError:
    visited_files = set(visited_files)


for page in page_iterator:
    if "Contents" in page:
        for obj in page['Contents']:
            file_key = obj['Key']
            if file_key in visited_files:
                continue
            if file_key.endswith('.csv'):
                print(f"start processing {file_key.split('/')[-1]}")
                response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
                with tempfile.NamedTemporaryFile() as temp_file:
                    temp_file.write(response['Body'].read())
                    temp_file.seek(0)
                    df = pl.read_csv(temp_file.name)
                # df = df.head(1000)
                if not df.is_empty():
                    # Assuming columns "embedding" and "uuid" are correctly formatted
                    print("start convert embeddings")
                    df = df.with_columns(
                            df["embedding"].map_elements(ast.literal_eval)
                    )
                    upload_dataframe_to_qdrant(df, collection_name)
                visited_files.add(file_key)
                with open('last_processed_date2qdrant.json', 'w') as file:
                    json.dump({'processed_files': list(visited_files)}, file)

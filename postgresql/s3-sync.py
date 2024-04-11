import boto3
import psycopg2
from psycopg2 import sql
import time

def connect_to_s3():
    try:
        s3 = boto3.client('s3', aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key, region_name=region_name)
        return s3
    except Exception as e:
        print(f"Error connecting to S3: {e}")
        time.sleep(5)  # Ждем 5 секунд перед повторной попыткой
        return connect_to_s3()

def connect_to_postgres():
    try:
        conn = psycopg2.connect(
            host=pg_host,
            database=pg_db,
            user=pg_user,
            password=pg_password
        )
        return conn, conn.cursor()
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        time.sleep(5)  # Ждем 5 секунд перед повторной попыткой
        return connect_to_postgres()


s3 = connect_to_s3()
conn, cur = connect_to_postgres()

try:
    objects = s3.list_objects_v2(Bucket=bucket_name)['Contents']
except Exception as e:
    print(f"Error listing objects in S3 bucket: {e}")
    s3 = connect_to_s3()  # Переподключаемся к S3
    objects = s3.list_objects_v2(Bucket=bucket_name)['Contents']

for obj in objects:
    key = obj['Key']
    
    try:
        response = s3.get_object(Bucket=bucket_name, Key=key)
        data = response['Body'].read()
    except Exception as e:
        print(f"Error downloading object {key} from S3: {e}")
        s3 = connect_to_s3()  # Переподключаемся к S3
        response = s3.get_object(Bucket=bucket_name, Key=key)
        data = response['Body'].read()

    try:
        cur.execute(
            sql.SQL("INSERT INTO your_table_name (file_name, file_data) VALUES (%s, %s)")
            .format(sql.Identifier('file_name'), sql.ByteString), [key, data]
        )
    except Exception as e:
        print(f"Error inserting object {key} into PostgreSQL: {e}")
        conn.close()
        conn, cur = connect_to_postgres()  # Переподключаемся к PostgreSQL
        cur.execute(
            sql.SQL("INSERT INTO your_table_name (file_name, file_data) VALUES (%s, %s)")
            .format(sql.Identifier('file_name'), sql.ByteString), [key, data]
        )

conn.commit()
cur.close()
conn.close()

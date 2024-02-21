import os
import requests
import json
import logging
import boto3
from botocore.exceptions import ClientError
from datetime import datetime

replicas=int(os.environ.get("QDRANT_DB_REPLICAS"))
namespace=os.environ.get("QDRANT_DB_NAMESPACE")
environment=os.environ.get("QDRANT_DB_ENVIRONMENT")
bucket=os.environ.get("QDRANT_DB_BACKUP_S3_BUCKET")
aws_access_key_id=os.environ.get("QDRANT_DB_BACKUP_S3_ACCESS_KEY")
aws_secret_access_key=os.environ.get("QDRANT_DB_BACKUP_S3_SECRET_KEY")
s3_url=os.environ.get("QDRANT_DB_BACKUP_S3_URL")

def endpoint_url(environment:str=environment, replica:int=0, namespace:str=namespace, point:str="/cluster"):
    """
    Create url
    """
    url=f"http://qdrant-db-{environment}-{replica}.qdrant-db-{environment}-headless.{namespace}.svc.cluster.local:6333{point}"
    return url

def request(method:str="get", url:str=endpoint_url()):
    """
    Create url request
    """
    try:
        r = getattr(requests, method)(url)
        r.raise_for_status()
        return r
    except requests.exceptions.HTTPError as errh:
        logging.error(f"HTTP Error: {errh}")
        raise SystemExit()
    except requests.exceptions.RequestException as erra:
        logging.error(f"Error: {erra}")
        raise SystemExit()

def get_node_collections(replica:int):
    """
    Return list of collections on one node
    """
    col = json.loads(request(url=endpoint_url(replica=replica, point="/collections")).text)["result"]
    collections_list=[]
    for i in range (len(list(col.values())[0])):
        collections_list.append(list((list(col.values()))[0][i].values())[0])
    return (collections_list)

def nodes_collections(replica:int):
    """
    Return dict of collections on one node where key is number of replica 
    and value is list of collections on this replica
    """
    collections_per_node_dict={}
    for i in range(replica):
        collections_per_node_dict[i]=get_node_collections(i)
    return collections_per_node_dict

def create_collection_snapshot(coll:str, replica:int):
    """
    Create snapshot for one collection on one replica
    """
    res=request(method="post", url=endpoint_url(replica=replica, point=f"/collections/{coll}/snapshots")).text
    logging.info(f"Snapshot: {list(list(json.loads(res).values())[0].values())[0]} for collection {coll} on node qdrant-db-{environment}-{replica} has been created!")
    return list(list(json.loads(res).values())[0].values())[0]

def create_node_collections_snapshots(coll_dict:dict, replica:int):
    """
    Create snapshot of all collections on one replica
    """
    snapshots_list=[]
    for i in coll_dict[replica]:
        snapshots_list.append(create_collection_snapshot(i, replica))
    return snapshots_list

def create_all_snapshots(coll_dict:dict):
    """
    Create snapshots for all collection on all replica and return dict of all snapshots 
    where key is number of replica and value is list of snapshots on this replica
    """
    snapshots_per_node_dict={}
    for i in range(replicas):
        snapshots_per_node_dict[i] = create_node_collections_snapshots(coll_dict,i)
    return snapshots_per_node_dict

def get_node_collection_snapshot_name(snap_dict:dict, coll:str, replica:int):
    """
    Return snapshot name on one replica from dict of all snapshots by prefix of collection name
    """
    return list(filter(lambda x: x.startswith(f"{coll}-"), snap_dict[replica]))[0]
    
def download_node_collection_snapshot(coll:str,snap_name:str, replica:int):
    """
    Download to local disk snapshot of one collection on one replica
    """
    if not os.path.exists(str(replica)):
        os.mkdir(str(replica))
    logging.info(f"Downloading snapshot: {snap_name} of node qdrant-db-{environment}-{replica} and saving to file...")
    res=request(url=endpoint_url(replica=replica, point=f"/collections/{coll}/snapshots/{snap_name}"))
    with open(os.path.join(str(replica),snap_name), "wb") as file:
        for chunk in res.iter_content(chunk_size=1024):
            file.write(chunk)

def download_node_collections_snapshots(coll_dict:dict, snap_dict:dict, replica:int):
    """
    Download to local disk all snapshots of collection on one replica
    """
    for i in coll_dict[replica]:
        download_node_collection_snapshot(i, get_node_collection_snapshot_name(snap_dict, i, replica), replica)

def download_all_snapshots(coll_dict:dict, snap_dict:dict):
    """
    Download to local disk all snapshots of collection on all replicas
    """
    for i in range(replicas):
        download_node_collections_snapshots(coll_dict, snap_dict, i)

def upload_snapshot_to_s3(replica:int, snap_name:str, buck:str=bucket):
    """
    Upload to s3 snapshot of one collection on one replica
    """
    object_name=f"Backup/{environment}/qdrant_db_{replica}/{datetime.today().strftime('%d_%m_%Y')}/{snap_name}"
    s3_client = boto3.client(service_name="s3", endpoint_url=s3_url, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)
    logging.info(f"Uploading snapshot: {snap_name} of node qdrant-db-{environment}-{replica} to s3")
    try:
        response = s3_client.upload_file(os.path.join(str(replica),snap_name), buck, object_name, ExtraArgs={"ACL": "public-read"})
    except ClientError as e:
        logging.error(e)
        return False
    return True
    
def upload_node_snapshots_to_s3(coll_dict:dict, snap_dict:dict, replica:int):
    """
    Upload to s3 snapshots of all collection on one replica
    """
    for i in coll_dict[replica]:
        upload_snapshot_to_s3(snap_name=get_node_collection_snapshot_name(snap_dict, i, replica), replica=replica)

def upload_all_snapshots_to_s3(coll_dict:dict, snap_dict:dict):
    """
    Upload to s3 snapshots of all collection on all replicas
    """
    for i in range(replicas):
        upload_node_snapshots_to_s3(coll_dict, snap_dict, i)

def get_collections_snapshots(coll:str, replica:int):
    """
    Return list of all existing snapshot on replica for next deletion
    """
    res=request(method="get", url=endpoint_url(replica=replica, point=f"/collections/{coll}/snapshots")).text
    snapshots_list=[]
    for i in list(json.loads(res).values())[0]:
        snapshots_list.append(list(i.values())[0])
    return snapshots_list

def delete_collection_snapshots(coll:str, snap_names:list, replica:int):
    """
    Delete snapshots of collection on replica
    """
    for i in snap_names:
        res=request(method="delete", url=endpoint_url(replica=replica, point=f"/collections/{coll}/snapshots/{i}"))
        logging.info(f"Snapshot: {i} for collection {coll} on node qdrant-db-{environment}-{replica} has been deleted!")

def delete_node_collections_snapshots(coll_dict:dict, replica:int):
    """
    Delete all snapshots of all collection on one replica
    """
    for i in coll_dict[replica]:
        delete_collection_snapshots(i, get_collections_snapshots(i, replica), replica)

def delete_all_snapshots(coll_dict:dict):
    """
    Delete all snapshots on all replicas
    """
    for i in range(replicas):
        delete_node_collections_snapshots(coll_dict, i)
        
def main():
    """
    1) List all existing collections on nodes and save to dict
    2) Create all snapshots and saving it to dict
    3) Download all created snapshots on previous step
    4) Upload all snapshots to s3 
    5) Delete all snapshots on replicas
    """
    log_level = logging.INFO
    logging.basicConfig(format = "[%(levelname)s] %(asctime)s: %(message)s",
                            level=log_level)

    collections_per_node=nodes_collections(replicas)
    snapshots_per_node=create_all_snapshots(collections_per_node)
    download_all_snapshots(collections_per_node, snapshots_per_node)
    upload_all_snapshots_to_s3(collections_per_node, snapshots_per_node)
    delete_all_snapshots(collections_per_node)

if __name__ == "__main__":
    main()

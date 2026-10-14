"""Measured, clearly mocked S3 integration evaluation using Moto."""
import hashlib,json,tempfile,time
from pathlib import Path
import boto3
from moto import mock_aws
from backupproof import restore_and_promote
from sample_backup import FILES

def put_fixture(client,bucket,mutation):
    client.create_bucket(Bucket=bucket)
    entries=[]
    for path,data in FILES.items():
        entries.append({'path':path,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        if not (mutation=='missing' and path=='notes/readme.txt'):
            body=b'tampered' if mutation=='corrupt' and path=='data/users.csv' else data
            client.put_object(Bucket=bucket,Key='backup/'+path,Body=body)
    client.put_object(Bucket=bucket,Key='backup/manifest.json',Body=json.dumps({'schema_version':1,'files':entries}).encode())

def run(output=Path('reports/mock-evaluation.json')):
    results=[];started=time.perf_counter()
    with mock_aws():
        for mutation in ('none','corrupt','missing'):
            client=boto3.client('s3',region_name='us-east-1');bucket=f'case-{mutation}';put_fixture(client,bucket,mutation)
            with tempfile.TemporaryDirectory() as work:
                root=Path(work);report=restore_and_promote(client,bucket,'backup',root/'restore',root/'report.json')
                results.append({'scenario':mutation,'verified':report['verified'],'counts':report['counts'],'promoted':(root/'restore').exists()})
    payload={'integration':'Moto 5.1.15 mocked S3 API; no real object storage or cloud claims','scenarios':results,'expected_behavior_passed':results==[
        {'scenario':'none','verified':True,'counts':{'verified':3,'missing':0,'corrupt':0},'promoted':True},
        {'scenario':'corrupt','verified':False,'counts':{'verified':2,'missing':0,'corrupt':1},'promoted':False},
        {'scenario':'missing','verified':False,'counts':{'verified':2,'missing':1,'corrupt':0},'promoted':False}],
        'duration_seconds':time.perf_counter()-started}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(payload,indent=2)+'\n');return payload
if __name__=='__main__':print(json.dumps(run(),indent=2))

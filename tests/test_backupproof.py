import hashlib,json
from pathlib import Path
import boto3,pytest
from moto import mock_aws
from backupproof import ManifestError,parse_manifest,restore_backup,restore_and_promote,safe_path
from sample_backup import FILES

BUCKET='backup-fixtures'
def manifest(files=FILES):return {'schema_version':1,'files':[{'path':p,'size':len(d),'sha256':hashlib.sha256(d).hexdigest()} for p,d in files.items()]}
def seed(client,files=FILES,body=None):
    client.create_bucket(Bucket=BUCKET)
    for p,d in files.items():client.put_object(Bucket=BUCKET,Key='nightly/'+p,Body=d)
    client.put_object(Bucket=BUCKET,Key='nightly/manifest.json',Body=json.dumps(body or manifest(files)).encode())
def s3():return boto3.client('s3',region_name='us-east-1')

@mock_aws
def test_verified_restore_and_promote(tmp_path):
    client=s3();seed(client);dest=tmp_path/'restored';report=tmp_path/'report.json'
    result=restore_and_promote(client,BUCKET,'nightly',dest,report)
    assert result['verified'] and result['counts']=={'verified':3,'missing':0,'corrupt':0}
    assert (dest/'data/users.csv').read_bytes()==FILES['data/users.csv']
    assert json.loads(report.read_text())['verified'] is True
    assert not list(tmp_path.glob('backupproof-*'))

@mock_aws
def test_corruption_is_reported_and_not_promoted(tmp_path):
    client=s3();seed(client);client.put_object(Bucket=BUCKET,Key='nightly/data/users.csv',Body=b'tampered')
    result=restore_and_promote(client,BUCKET,'nightly',tmp_path/'restore',tmp_path/'report.json')
    assert not result['verified'] and result['counts']['corrupt']==1
    assert not (tmp_path/'restore').exists() and not list(tmp_path.glob('backupproof-*'))

@mock_aws
def test_missing_and_unexpected(tmp_path):
    client=s3();seed(client);client.delete_object(Bucket=BUCKET,Key='nightly/notes/readme.txt');client.put_object(Bucket=BUCKET,Key='nightly/extra.bin',Body=b'x')
    temp,result=restore_backup(client,BUCKET,'nightly',work_parent=tmp_path)
    try:
        assert result['counts']['missing']==1 and result['unexpected_objects']==['nightly/extra.bin']
    finally:
        import shutil;shutil.rmtree(temp)

@pytest.mark.parametrize('path',['../secret','/absolute','a\\b','a/../b','','./a','a//b','a/','C:/escape','a:stream','CON','dir/NUL.txt','a.','a '])
def test_unsafe_paths(path):
    with pytest.raises(ManifestError):safe_path(path)

@pytest.mark.parametrize('payload',[b'bad',json.dumps({'schema_version':2,'files':[]}).encode(),json.dumps({'schema_version':1,'files':[]}).encode()])
def test_bad_manifest(payload):
    with pytest.raises(ManifestError):parse_manifest(payload)

def test_duplicate_and_hash_validation():
    row={'path':'a','size':1,'sha256':'0'*64}
    with pytest.raises(ManifestError):parse_manifest(json.dumps({'schema_version':1,'files':[row,row]}).encode())
    row={**row,'sha256':'bad'}
    with pytest.raises(ManifestError):parse_manifest(json.dumps({'schema_version':1,'files':[row]}).encode())

@mock_aws
def test_invalid_manifest_cleans_temp(tmp_path):
    client=s3();client.create_bucket(Bucket=BUCKET);client.put_object(Bucket=BUCKET,Key='manifest.json',Body=b'bad')
    with pytest.raises(ManifestError):restore_backup(client,BUCKET,'',work_parent=tmp_path)
    assert not list(tmp_path.glob('backupproof-*'))

@mock_aws
def test_refuses_existing_dest_or_report(tmp_path):
    client=s3();seed(client);dest=tmp_path/'dest';dest.mkdir()
    with pytest.raises(ValueError):restore_and_promote(client,BUCKET,'nightly',dest,tmp_path/'r.json')
    dest.rmdir();report=tmp_path/'r.json';report.write_text('keep')
    with pytest.raises(ValueError):restore_and_promote(client,BUCKET,'nightly',dest,report)
    assert report.read_text()=='keep'

@mock_aws
def test_report_cannot_be_inside_destination(tmp_path):
    client=s3();seed(client);dest=tmp_path/'dest'
    with pytest.raises(ValueError):restore_and_promote(client,BUCKET,'nightly',dest,dest/'report.json')

@pytest.mark.parametrize('paths',[['a','a/b'],['A','a'],['a/b','a']])
def test_path_conflicts(paths):
    data={'schema_version':1,'files':[{'path':p,'size':0,'sha256':hashlib.sha256(b'').hexdigest()} for p in paths]}
    with pytest.raises(ManifestError):parse_manifest(json.dumps(data))

@mock_aws
def test_same_size_checksum_corruption(tmp_path):
    client=s3();seed(client);path='data/users.csv'
    client.put_object(Bucket=BUCKET,Key='nightly/'+path,Body=b'x'*len(FILES[path]))
    r=restore_and_promote(client,BUCKET,'nightly',tmp_path/'out',tmp_path/'r.json')
    row=next(x for x in r['files'] if x['path']==path)
    assert row['reason']=='checksum_mismatch' and not (tmp_path/'out').exists()

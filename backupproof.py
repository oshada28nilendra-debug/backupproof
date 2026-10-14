"""Read-only S3-compatible backup restore verifier."""
import argparse, hashlib, json, os, re, shutil, tempfile, time
from pathlib import Path, PurePosixPath

SHA256=re.compile(r'^[0-9a-f]{64}$')
MAX_FILES=1000
MAX_FILE_BYTES=64*1024*1024
MAX_TOTAL_BYTES=256*1024*1024

class ManifestError(ValueError):pass

def safe_path(raw):
    if not isinstance(raw,str) or '\\' in raw or '\x00' in raw:raise ManifestError(f'Unsafe path: {raw!r}')
    p=PurePosixPath(raw)
    if not raw or p.is_absolute() or any(x in ('','.','..') for x in raw.split('/')):raise ManifestError(f'Unsafe path: {raw!r}')
    for part in p.parts:
        if any(ord(c)<32 or c in ':<>"|?*' for c in part) or part.endswith((' ','.')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?',part):
            raise ManifestError(f'Nonportable path: {raw!r}')
    return p

def parse_manifest(data):
    try:payload=json.loads(data)
    except (json.JSONDecodeError,UnicodeDecodeError) as exc:raise ManifestError('Manifest is not valid UTF-8 JSON.') from exc
    if not isinstance(payload,dict) or payload.get('schema_version')!=1 or not isinstance(payload.get('files'),list):raise ManifestError('Manifest requires schema_version 1 and a files array.')
    if not payload['files'] or len(payload['files'])>MAX_FILES:raise ManifestError(f'Manifest must contain 1–{MAX_FILES} files.')
    seen=set();total=0;files=[]
    for item in payload['files']:
        if not isinstance(item,dict):raise ManifestError('Every files entry must be an object.')
        path=safe_path(item.get('path'));checksum=item.get('sha256');size=item.get('size')
        canonical=str(path).casefold()
        if any(canonical==old or canonical.startswith(old+'/') or old.startswith(canonical+'/') for old in seen):raise ManifestError(f'Duplicate or conflicting path: {path}')
        if not isinstance(checksum,str) or not SHA256.fullmatch(checksum):raise ManifestError(f'Invalid SHA-256 for {path}')
        if type(size) is not int or not 0<=size<=MAX_FILE_BYTES:raise ManifestError(f'Invalid size for {path}')
        seen.add(canonical);total+=size
        if total>MAX_TOTAL_BYTES:raise ManifestError('Manifest total exceeds 256 MiB.')
        files.append({'path':str(path),'sha256':checksum,'size':size})
    return {'schema_version':1,'files':files}

def object_bytes(client,bucket,key,limit):
    response=client.get_object(Bucket=bucket,Key=key);body=response['Body'];chunks=[];size=0
    try:
        while True:
            chunk=body.read(min(65536,limit-size+1))
            if not chunk:break
            size+=len(chunk)
            if size>limit:raise ValueError(f'Object exceeds allowed size: {key}')
            chunks.append(chunk)
    finally:
        body.close()
    return b''.join(chunks)

def keys_under(client,bucket,prefix):
    paginator=client.get_paginator('list_objects_v2');keys=[]
    for page in paginator.paginate(Bucket=bucket,Prefix=prefix):keys.extend(x['Key'] for x in page.get('Contents',[]))
    return keys

def restore_backup(client,bucket,prefix,manifest_key='manifest.json',work_parent=None):
    """Restore to an isolated temporary directory. Caller owns returned cleanup."""
    prefix=prefix.strip('/')
    base=f'{prefix}/' if prefix else ''
    started=time.perf_counter();temp=Path(tempfile.mkdtemp(prefix='backupproof-',dir=work_parent));rows=[]
    try:
        manifest=parse_manifest(object_bytes(client,bucket,base+manifest_key,2*1024*1024).decode('utf-8'))
        expected={base+x['path'] for x in manifest['files']}
        listed=set(keys_under(client,bucket,base));unexpected=sorted(listed-expected-{base+manifest_key})
        for item in manifest['files']:
            key=base+item['path'];target=temp.joinpath(*PurePosixPath(item['path']).parts);row={**item,'key':key}
            try:
                data=object_bytes(client,bucket,key,item['size'])
                actual=hashlib.sha256(data).hexdigest();row.update(actual_size=len(data),actual_sha256=actual)
                if len(data)!=item['size']:row.update(status='corrupt',reason='size_mismatch')
                elif actual!=item['sha256']:row.update(status='corrupt',reason='checksum_mismatch')
                else:
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);row.update(status='verified')
            except client.exceptions.NoSuchKey:row.update(status='missing',reason='object_not_found')
            except ValueError:row.update(status='corrupt',reason='object_exceeds_size_limit')
            rows.append(row)
        counts={s:sum(r['status']==s for r in rows) for s in ('verified','missing','corrupt')}
        report={'schema_version':1,'bucket':bucket,'prefix':prefix,'manifest_key':manifest_key,'counts':counts,'unexpected_objects':unexpected,'verified':counts['missing']==counts['corrupt']==0,'duration_seconds':time.perf_counter()-started,'files':rows,'limitations':['A matching checksum proves byte integrity against this manifest, not backup freshness, semantic validity, or successful application recovery.']}
        return temp,report
    except Exception:
        shutil.rmtree(temp,ignore_errors=True);raise

def restore_and_promote(client,bucket,prefix,destination,report_path,manifest_key='manifest.json'):
    destination=Path(destination);report_path=Path(report_path)
    if destination.exists() or destination.is_symlink():raise ValueError('Destination must not already exist.')
    if report_path.exists() or report_path.is_symlink():raise ValueError('Report path must not already exist.')
    try:report_path.resolve().relative_to(destination.resolve());raise ValueError('Report must be outside the destination.')
    except ValueError as exc:
        if str(exc)=='Report must be outside the destination.':raise
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp,report=restore_backup(client,bucket,prefix,manifest_key,destination.parent)
    try:
        report_path.parent.mkdir(parents=True,exist_ok=True)
        with report_path.open('x',encoding='utf-8') as stream:stream.write(json.dumps(report,indent=2)+'\n')
        if destination.exists() or destination.is_symlink():raise ValueError('Destination appeared during restore; refusing promotion.')
        if report['verified']:temp.replace(destination);temp=None
        return report
    finally:
        if temp is not None:shutil.rmtree(temp,ignore_errors=True)

def client_from_args(a):
    import boto3
    from botocore.config import Config
    return boto3.client('s3',endpoint_url=a.endpoint_url,region_name=a.region,config=Config(s3={'addressing_style':'path'}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bucket',required=True);p.add_argument('--prefix',default='');p.add_argument('--manifest-key',default='manifest.json');p.add_argument('--endpoint-url');p.add_argument('--region',default=os.getenv('AWS_REGION','us-east-1'));p.add_argument('--destination',type=Path,required=True);p.add_argument('--report',type=Path,default=Path('outputs/restore-report.json'));a=p.parse_args()
    try:
        report=restore_and_promote(client_from_args(a),a.bucket,a.prefix,a.destination,a.report,a.manifest_key);print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2));raise SystemExit(0 if report['verified'] else 2)
    except (ManifestError,ValueError,OSError) as exc:p.exit(2,f'Error: {exc}\n')
if __name__=='__main__':main()

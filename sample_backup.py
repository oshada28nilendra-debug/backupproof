"""Create a synthetic manifest and files locally; this never contacts S3."""
import hashlib,json
from pathlib import Path
FILES={'config/app.json':b'{"mode":"demo"}\n','data/users.csv':b'id,name\n1,Ada\n2,Linus\n','notes/readme.txt':b'Synthetic BackupProof fixture.\n'}
def create(root):
    root=Path(root);entries=[]
    for name,data in FILES.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);entries.append({'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    manifest={'schema_version':1,'files':entries};(root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest
if __name__=='__main__':create(Path('outputs/sample-backup'))

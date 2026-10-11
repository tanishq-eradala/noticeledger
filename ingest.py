"""Repeatable local-folder acquisition; outputs a machine-readable run report."""
import argparse
import json
from pathlib import Path
from server import pdf_text
from store import Store

def main():
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--db',type=Path,default=Path(__file__).with_name('noticeledger.db'));args=p.parse_args()
    if not args.folder.is_dir():p.error('Provide an existing folder of .txt and .pdf notices.')
    store=Store(args.db);results=[]
    for file in sorted(args.folder.iterdir()):
        if not file.is_file() or file.suffix.lower() not in ('.txt','.pdf'):continue
        try:
            if file.stat().st_size>2_000_000:raise ValueError('File exceeds 2 MB.')
            if file.suffix.lower()=='.pdf':
                raw=file.read_bytes();text,error=pdf_text(raw);result=store.import_notice(file.name,text,'pdf',error,raw)
            else:result=store.import_notice(file.name,file.read_text(encoding='utf-8'))
            results.append({'file':file.name,**result})
        except (ValueError,UnicodeError,OSError) as exc:results.append({'file':file.name,'error':str(exc)})
    print(json.dumps({'results':results,'errors':sum('error' in r for r in results)},indent=2))
    return int(any('error' in r for r in results))

if __name__=='__main__':raise SystemExit(main())

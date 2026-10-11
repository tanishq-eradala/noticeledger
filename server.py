"""Local, single-origin internal-tool prototype. No external accounts required."""
import argparse
import base64
import binascii
import csv
import io
import json
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from demo import seed
from store import Store, Conflict

BASE = Path(__file__).resolve().parent

def pdf_text(raw):
    if len(raw)>2_000_000 or not raw.startswith(b'%PDF-'): raise ValueError('Provide a PDF smaller than 2 MB.')
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted: raise ValueError('Encrypted PDFs are not supported.')
        if len(reader.pages)>20: raise ValueError('Use a PDF with at most 20 pages.')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        if len(text.encode())>100000: raise ValueError('Extracted text exceeds 100 KB.')
        if not text.strip(): return '', 'No selectable text was found. OCR is required; paste verified text instead.'
        return text, None
    except ValueError: raise
    except Exception:
        return '', 'PDF extraction failed. Check the original file and import verified text instead.'

class Handler(BaseHTTPRequestHandler):
    server_version = 'NoticeLedger/0.1'

    def log_message(self, fmt, *args): pass

    @property
    def store(self): return self.server.store

    def send(self, code, data, content_type='application/json', headers=None):
        raw = json.dumps(data).encode() if content_type=='application/json' else data.encode() if isinstance(data,str) else data
        self.send_response(code)
        self.send_header('Content-Type', content_type + ('; charset=utf-8' if content_type.startswith('text/') else ''))
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/api/health': return self.send(200, {'status':'ok'})
            if path == '/api/documents': return self.send(200, self.store.documents())
            if path == '/api/snapshot': return self.send(200, self.store.snapshot())
            if path == '/api/examples':
                from demo import EXAMPLES
                return self.send(200,[{'name':n,'text':t} for n,t in EXAMPLES])
            if path == '/api/export.csv':
                return self.send(200,self.store.export(),'text/csv',{'Content-Disposition':'attachment; filename="approved-cash-flows.csv"'})
            if path.startswith('/api/documents/'):
                tail = path.removeprefix('/api/documents/')
                if tail.endswith('/original'):
                    doc_id = tail[:-len('/original')]
                    with self.store.db() as c: row=c.execute('SELECT data FROM source_files WHERE document_id=?',(doc_id,)).fetchone()
                    if row is None: raise KeyError('Original PDF not found.')
                    return self.send(200,row['data'],'application/pdf',{'Content-Disposition':'attachment; filename="source-notice.pdf"'})
                return self.send(200,self.store.get(tail))
            if path == '/metrics':
                s=self.store.snapshot()
                lines=['# TYPE notice_documents_total gauge']
                for status in ('needs_review','approved','rejected','duplicate','failed'):
                    lines.append(f'notice_documents_total{{status="{status}"}} {s["counts"].get(status,0)}')
                lines += ['# TYPE notice_processing_jobs_total counter',f'notice_processing_jobs_total {s["processing"]["total"]}', '# TYPE notice_failed_jobs_total counter',f'notice_failed_jobs_total {s["processing"]["failed"]}', '# TYPE notice_processing_mean_milliseconds gauge',f'notice_processing_mean_milliseconds {s["processing"]["mean_ms"]}', '# TYPE notice_approved_cash_flows_total counter',f'notice_approved_cash_flows_total {len(s["ledger"])}']
                return self.send(200,'\n'.join(lines)+'\n','text/plain')
            files={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css'}
            if path not in files: return self.send(404,{'error':'Page not found.'})
            file=BASE/'web'/files[path]
            return self.send(200,file.read_bytes(),mimetypes.guess_type(file)[0] or 'text/plain')
        except KeyError as exc: self.send(404, {'error':str(exc).strip("'")})
        except (OSError,sqlite3.Error): self.send(503,{'error':'Workspace storage is unavailable. Try again.'})

    def do_POST(self):
        # Browser mutations must originate in this exact local app.
        origin=self.headers.get('Origin')
        host=self.headers.get('Host')
        if origin and origin != 'http://'+str(host): return self.send(403,{'error':'Use this workspace to submit changes.'})
        if not self.headers.get('Content-Type','').startswith('application/json'):
            return self.send(415,{'error':'Send JSON request data.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>3_000_000: raise ValueError('Request must contain less than 3 MB of JSON.')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict): raise ValueError('Request must be a JSON object.')
            path=urlsplit(self.path).path
            if path=='/api/import':
                kind=payload.get('kind','text'); name=payload.get('name',''); content=payload.get('content','')
                if not isinstance(content,str): raise ValueError('Source content must be text.')
                if kind=='pdf':
                    try: raw=base64.b64decode(content,validate=True)
                    except (ValueError,binascii.Error) as exc: raise ValueError('Invalid PDF encoding.') from exc
                    text,error=pdf_text(raw)
                    return self.send(201,{'results':[self.store.import_notice(name,text,'pdf',error,raw)]})
                if kind=='csv':
                    if len(content.encode())>500000: raise ValueError('CSV batch exceeds 500 KB.')
                    reader=csv.DictReader(io.StringIO(content)); rows=list(reader)
                    if not reader.fieldnames or not {'source_name','source_text'}.issubset(reader.fieldnames): raise ValueError('CSV needs source_name and source_text columns.')
                    if not 1<=len(rows)<=50: raise ValueError('Use a batch of 1–50 notices.')
                    # Validate all rows before importing any; processing is per notice.
                    for row in rows:
                        if not isinstance(row.get('source_name'),str) or not 1<=len(row['source_name'].strip())<=160 or not isinstance(row.get('source_text'),str) or not row['source_text'].strip() or len(row['source_text'].encode())>100000:
                            raise ValueError('Every CSV row needs a source name and nonempty text under 100 KB.')
                    return self.send(201,{'results':[self.store.import_notice(row['source_name'],row['source_text'],'csv') for row in rows]})
                if kind!='text': raise ValueError('Supported sources are text, CSV and PDF.')
                return self.send(201,{'results':[self.store.import_notice(name,content)]})
            if path.startswith('/api/documents/'):
                tail=path.removeprefix('/api/documents/').split('/')
                if len(tail)!=2: return self.send(404,{'error':'Review action not found.'})
                result=self.store.review(tail[0],tail[1],payload.get('version'),payload.get('actor'),payload.get('reason',''),payload.get('fields'))
                return self.send(200,result)
            return self.send(404,{'error':'Endpoint not found.'})
        except Conflict as exc: self.send(409,{'error':str(exc)})
        except KeyError as exc: self.send(404,{'error':str(exc).strip("'")})
        except (ValueError,json.JSONDecodeError) as exc: self.send(400,{'error':str(exc)})
        except sqlite3.IntegrityError: self.send(409,{'error':'This operation conflicts with an existing approved notice. Reload the workspace.'})
        except (sqlite3.Error,OSError): self.send(503,{'error':'Workspace storage is unavailable. Try again.'})

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=5187);p.add_argument('--db',default=str(BASE/'noticeledger.db'));p.add_argument('--empty',action='store_true');args=p.parse_args()
    store=Store(args.db)
    if not args.empty: seed(store)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.store=store
    print(f'NoticeLedger ready at http://127.0.0.1:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()

"""Start a disposable workspace, run browser checks, and clean up."""
import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=5197);p.add_argument('--channel');args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='noticeledger-test-') as tmp:
        child=subprocess.Popen([sys.executable,str(root/'server.py'),'--port',str(args.port),'--db',str(Path(tmp)/'test.db')],cwd=root)
        try:
            ready=False
            for _ in range(50):
                if child.poll() is not None:raise RuntimeError('Test server failed to start. Choose an unused port.')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/health',timeout=.5):ready=True
                    if ready:break
                except OSError:time.sleep(.1)
            if not ready:raise RuntimeError('Test server did not become ready.')
            cmd=[sys.executable,str(root/'tests/test_workflows.py'),'--url',f'http://127.0.0.1:{args.port}']
            if args.channel:cmd+=['--channel',args.channel]
            return subprocess.call(cmd,cwd=root)
        finally:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':raise SystemExit(main())

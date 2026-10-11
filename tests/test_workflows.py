"""Browser and HTTP contract checks against a disposable, seeded workspace."""
import argparse
import base64
import csv
import io
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo import CALL

def main():
    from playwright.sync_api import sync_playwright, expect
    arg=argparse.ArgumentParser();arg.add_argument('--url',default='http://127.0.0.1:5197');arg.add_argument('--channel');arg.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[1]/'docs/screenshots');a=arg.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);checks=[]
    def passed(label): checks.append(label);print('PASS',label,flush=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'channel':a.channel} if a.channel else {}))
        context=browser.new_context(viewport={'width':1440,'height':1050},accept_downloads=True,bypass_csp=True)
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(a.url);page.wait_for_load_state('networkidle');expect(page.get_by_role('heading',name='Notice review',exact=True)).to_be_visible()
        # Abort if this looks like a previously used/live workspace.
        initial=context.request.get(a.url+'/api/snapshot').json()
        assert len(initial['jobs'])==6 and not initial['ledger'],'Run only against a fresh disposable seeded database.'
        axe=os.environ.get('AXE_SCRIPT')
        def audit(label):
            if axe:
                page.add_script_tag(path=axe)
                result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")
                assert not result['violations'],[(r['id'],r['nodes'][0]['html']) for r in result['violations']]
                passed(label+' accessibility checks')
        passed('initial seeded review workspace');audit('review workspace')
        page.screenshot(path=str(a.out/'review-desktop.png'),full_page=True)
        page.locator('button.queue-item').filter(has_text='cedar-call-reconciliation.txt').click()
        expect(page.get_by_role('button',name='Approve notice',exact=True)).to_be_disabled()
        doc=context.request.get(a.url+'/api/documents').json();cedar=next(d for d in doc if d['source_name']=='cedar-call-reconciliation.txt')
        denied=context.request.post(a.url+f'/api/documents/{cedar["id"]}/approve',data={'version':1,'actor':'Test reviewer'})
        assert denied.status==409 and not context.request.get(a.url+'/api/snapshot').json()['ledger'];passed('reconciliation blocks UI and API approval')
        page.get_by_role('button',name='Source line 10 for Total amount',exact=True).click()
        expect(page.locator('[data-source-line="10"]')).to_have_class('source-line highlight');passed('field-to-source evidence link')
        page.get_by_label('Reviewer name',exact=True).fill('Tanishq test reviewer')
        page.get_by_label('Total amount',exact=True).fill('$100,000.00')
        page.get_by_role('button',name='Save corrections',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Explain the correction');passed('correction requires an audit reason')
        page.get_by_label('Reason for corrections or source confirmation').fill('Investment and fee components total $100,000.00.')
        page.get_by_role('button',name='Save corrections',exact=True).click()
        expect(page.get_by_role('button',name='Approve notice',exact=True)).to_be_enabled()
        expect(page.locator('.audit-list')).to_contain_text('$101,000.00');passed('corrected fields rerun validation and retain audit evidence')
        page.screenshot(path=str(a.out/'corrected-review.png'),full_page=True)
        page.get_by_role('button',name='Approve notice',exact=True).click()
        expect(page.get_by_role('dialog')).to_be_visible();audit('approval confirmation')
        page.get_by_role('button',name='Approve and record',exact=True).click()
        expect(page.locator('#review-error')).to_contain_text('Confirm');passed('approval requires explicit evidence confirmation')
        page.get_by_label('I checked the values against the source notice.').check()
        lost={'done':False}
        def drop_once(route):
            if lost['done']:route.continue_();return
            response=route.fetch();assert response.status==200;lost['done']=True;route.abort('failed')
        pattern=f'**/api/documents/{cedar["id"]}/approve';page.route(pattern,drop_once)
        page.get_by_role('button',name='Approve and record',exact=True).click()
        expect(page.locator('#review-error')).to_contain_text('Cannot reach')
        page.get_by_role('button',name='Approve and record',exact=True).click()
        expect(page.get_by_role('dialog')).not_to_be_visible();page.unroute(pattern)
        snapshot=context.request.get(a.url+'/api/snapshot').json();assert len(snapshot['ledger'])==1 and snapshot['ledger'][0]['signed_amount_minor']==-10000000
        passed('lost approval response safely retries without a duplicate cash flow')
        page.get_by_role('button',name='Approved ledger',exact=True).click()
        expect(page.locator('tbody')).to_contain_text('-$100,000.00');audit('approved ledger')
        with page.expect_download() as info:page.get_by_role('button',name='Export approved CSV').click()
        rows=list(csv.DictReader(open(info.value.path(),newline='')));assert len(rows)==1 and rows[0]['signed_cash_flow']=='-100000.00'
        passed('CSV contains only approved entries with exact signed amounts')
        page.screenshot(path=str(a.out/'ledger-desktop.png'),full_page=True)
        # Atomic version check: another reviewer saves before this browser.
        page.get_by_role('button',name='Review queue').click()
        page.locator('button.queue-item').filter(has_text='juniper-missing-date.txt').click()
        juniper=next(d for d in doc if d['source_name']=='juniper-missing-date.txt')
        page.get_by_label('Due / payment date',exact=True).fill('2026-10-20')
        page.get_by_label('Reason for corrections or source confirmation').fill('Confirmed due date.')
        r=context.request.post(a.url+f'/api/documents/{juniper["id"]}/correct',data={'version':1,'actor':'Other reviewer','reason':'Confirmed from follow-up','fields':{'effective_date':'2026-10-21'}});assert r.status==200
        page.get_by_role('button',name='Save corrections',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('This notice changed')
        expect(page.get_by_label('Due / payment date',exact=True)).to_have_value('2026-10-21');passed('stale corrections cannot overwrite another review')
        page.locator('button.queue-item').filter(has_text='northstar-call-forwarded.txt').click()
        expect(page.get_by_role('button',name='Approve notice',exact=True)).to_be_disabled()
        page.get_by_role('button',name='Reject notice',exact=True).click()
        page.get_by_label('Reason for rejecting',exact=True).fill('Forwarded copy of the original capital call.')
        page.get_by_role('dialog').get_by_role('button',name='Reject notice',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Notice rejected');passed('duplicate copy rejected with retained reason')
        # CSV batch importer and PDF original preservation.
        batch=io.StringIO();w=csv.writer(batch);w.writerow(['source_name','source_text']);w.writerow(['batch-call.txt',CALL.replace('Northstar Growth Fund IV','Batch Fund')]);w.writerow(['batch-copy.txt',CALL.replace('Northstar Growth Fund IV','Batch Fund')])
        r=context.request.post(a.url+'/api/import',data={'kind':'csv','name':'batch.csv','content':batch.getvalue()});assert r.status==201
        assert r.json()['results'][1]['duplicate'];passed('multiline CSV ingestion safely deduplicates a repeated source')
        bad=context.request.post(a.url+'/api/import',data={'kind':'csv','name':'bad.csv','content':'other\nvalue'});assert bad.status==400
        passed('malformed batch contract is rejected')
        from reportlab.pdfgen import canvas
        out=io.BytesIO();c=canvas.Canvas(out);y=800
        for line in CALL.replace('Northstar Growth Fund IV','Granite Growth Fund III').splitlines():c.drawString(30,y,line);y-=16
        c.save();raw=out.getvalue()
        r=context.request.post(a.url+'/api/import',data={'kind':'pdf','name':'source.pdf','content':base64.b64encode(raw).decode()});assert r.status==201
        pdf_id=r.json()['results'][0]['id'];assert context.request.get(a.url+f'/api/documents/{pdf_id}/original').body()==raw;passed('selectable PDF extraction preserves the original bytes')
        page.get_by_role('button',name='Import notices',exact=True).click();audit('import dialog')
        page.get_by_label('Start with a synthetic example').select_option('0')
        page.get_by_role('button',name='Import for review').click();expect(page.get_by_role('dialog')).not_to_be_visible()
        expect(page.get_by_role('status')).to_contain_text('duplicate matches');passed('UI text import recognizes an existing source')
        # Confirm untrusted document markup is rendered as text.
        r=context.request.post(a.url+'/api/import',data={'name':'markup.txt','content':CALL.replace('Northstar Growth Fund IV','<b>Source markup</b>')});assert r.status==201
        markup_id=r.json()['results'][0]['id'];page.goto(a.url+'/?notice='+markup_id);page.wait_for_load_state('networkidle')
        expect(page.get_by_label('Fund',exact=True)).to_have_value('<b>Source markup</b>');assert page.locator('.source-sheet b').count()==0;passed('source markup remains inert text')
        page.goto(a.url+'/?notice='+pdf_id);page.wait_for_load_state('networkidle')
        for width in (390,320):
            page.set_viewport_size({'width':width,'height':844});page.wait_for_load_state('networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Layout overflow at {width}'
            for control in page.locator('[id^=field-]').all():
                box=control.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width,f'Clipped input at {width}'
            expect(page.get_by_role('button',name='Approve notice',exact=True)).to_be_visible()
            audit(f'{width}px review layout');passed(f'{width}px mobile layout containment')
        page.screenshot(path=str(a.out/'review-mobile.png'),full_page=True)
        page.get_by_role('button',name='Processing history',exact=True).click();expect(page.get_by_role('heading',name='Recent extraction jobs')).to_be_visible();audit('processing history')
        passed('processing history exposes durable acquisition results')
        assert not errors,errors;passed('no unhandled browser errors')
        report={'checks':checks,'count':len(checks),'axe_enabled':bool(axe),'scope':'Disposable synthetic workspace; selected automated WCAG rules, not a complete accessibility audit.'}
        (a.out/'verification.json').write_text(json.dumps(report,indent=2));browser.close()
    print(f'{len(checks)} workflow checks passed.')

if __name__=='__main__':main()

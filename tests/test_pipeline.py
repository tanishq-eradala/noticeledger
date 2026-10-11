import csv
import io
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import CALL, DIST
from parser import extract, money, validate
from server import pdf_text
from store import Conflict, Store

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.db')
    def tearDown(self): self.tmp.cleanup()
    def add(self,text=CALL,name='notice.txt'):
        return self.store.get(self.store.import_notice(name,text)['id'])
    def approve(self,d): return self.store.review(d['id'],'approve',d['version'],'Test reviewer')

    def test_exact_cents_without_rounding(self):
        self.assertEqual(money('$42,750.25'),4275025)
        self.assertEqual(money('0.10')+money('0.20'),money('0.30'))
        for raw in ('1.001','1e3','-50','(50)','12,34.00','NaN','Infinity'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):money(raw)

    def test_evidence_and_original_fields(self):
        d=self.add();self.assertEqual(d['evidence']['amount'][0]['line'],10)
        self.assertEqual(d['original_fields']['amount'],'$100,000.00')
        self.assertEqual(d['issues'],[])

    def test_no_ledger_before_approval(self):
        self.add();self.assertEqual(self.store.snapshot()['ledger'],[])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.store.export())))),0)

    def test_call_sign_and_approval_retry(self):
        d=self.add();a=self.approve(d);self.assertEqual(a['status'],'approved')
        replay=self.approve(d);self.assertEqual(replay['version'],a['version'])
        ledger=self.store.snapshot()['ledger'];self.assertEqual(len(ledger),1)
        self.assertEqual(ledger[0]['signed_amount_minor'],-10000000)
        self.assertEqual([x['action'] for x in replay['audit']].count('approved'),1)

    def test_distribution_recallable_and_currency_separation(self):
        self.approve(self.add(DIST))
        self.approve(self.add(DIST.replace('USD','EUR').replace('$','€')))
        s=self.store.snapshot();self.assertEqual({t['currency'] for t in s['totals']},{'USD','EUR'})
        for row in s['ledger']:
            self.assertEqual(row['signed_amount_minor'],4275025);self.assertEqual(row['recallable_minor'],1250000)

    def test_exact_source_deduplication(self):
        d=self.add();r=self.store.import_notice('copy.txt',CALL)
        self.assertTrue(r['duplicate']);self.assertEqual(r['id'],d['id'])
        self.assertEqual(len(self.store.documents()),1)
        self.assertEqual(self.store.get(d['id'])['audit'][0]['action'],'duplicate_import')

    def test_business_duplicate_blocks_approval(self):
        original=self.add();copy=self.add(CALL+'\nForwarded copy')
        self.assertEqual(copy['status'],'duplicate');self.assertEqual(copy['duplicate_of'],original['id'])
        with self.assertRaises(Conflict):self.approve(copy)
        self.approve(original);self.assertEqual(len(self.store.snapshot()['ledger']),1)

    def test_reconciliation_blocks_without_partial_writes(self):
        d=self.add(CALL.replace('$100,000.00','$101,000.00'))
        self.assertEqual(d['issues'][0]['code'],'reconciliation')
        with self.assertRaises(Conflict):self.approve(d)
        self.assertEqual(len(self.store.snapshot()['ledger']),0)
        self.assertEqual(self.store.get(d['id'])['version'],1)
        self.assertEqual(len(self.store.get(d['id'])['audit']),1)

    def test_correction_preserves_source_and_reruns_validation(self):
        d=self.add(CALL.replace('$100,000.00','$101,000.00'))
        a=self.store.review(d['id'],'correct',1,'Reviewer','Corrected total from components',{'amount':'$100,000.00'})
        self.assertEqual(a['source_text'],d['source_text']);self.assertEqual(a['original_fields']['amount'],'$101,000.00')
        self.assertEqual(a['fields']['amount'],'$100,000.00');self.assertEqual(a['issues'],[])
        self.assertEqual(a['audit'][0]['details']['changes']['amount']['before'],'$101,000.00')
        self.approve(a)

    def test_stale_review_is_rejected(self):
        d=self.add();self.store.review(d['id'],'correct',1,'Reviewer','Fixing investor',{'investor':'Other Family'})
        with self.assertRaises(Conflict):self.approve(d)
        with self.assertRaises(Conflict):self.store.review(d['id'],'correct',1,'Other Reviewer','Old edit',{'fund':'Stale name'})
        self.assertEqual(self.store.get(d['id'])['fields']['investor'],'Other Family')

    def test_concurrent_approval_records_once(self):
        d=self.add()
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:self.approve(d),range(4)))
        self.assertTrue(all(r['status']=='approved' for r in results));self.assertEqual(len(self.store.snapshot()['ledger']),1)

    def test_concurrent_import_records_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:self.store.import_notice('notice.txt',CALL),range(4)))
        self.assertEqual(len({r['id'] for r in results}),1);self.assertEqual(len(self.store.documents()),1)

    def test_approved_records_are_immutable(self):
        d=self.approve(self.add())
        for action in ('correct','reject'):
            with self.assertRaises(Conflict):self.store.review(d['id'],action,d['version'],'Reviewer','Change',{'amount':'1'})

    def test_rejection_and_required_reason(self):
        d=self.add()
        with self.assertRaises(ValueError):self.store.review(d['id'],'reject',1,'Reviewer')
        a=self.store.review(d['id'],'reject',1,'Reviewer','Superseded notice')
        self.assertEqual(a['status'],'rejected');self.assertEqual(self.store.snapshot()['ledger'],[])

    def test_required_date_and_date_order(self):
        for text in (CALL.replace('Due date: 2026-10-20\n',''), CALL.replace('2026-10-20','2026-10-01'), CALL.replace('2026-10-20','2026-02-30')):
            d=self.add(text)
            with self.assertRaises(Conflict):self.approve(d)

    def test_currency_and_recallable_checks(self):
        for text in (CALL.replace('Currency: USD','Currency: EUR'),DIST.replace('$12,500.00','$99,000.00')):
            d=self.add(text);self.assertTrue(d['issues'])
            with self.assertRaises(Conflict):self.approve(d)

    def test_conflicting_source_values_require_confirmation(self):
        d=self.add(CALL+'Total amount: $900,000.00\n');self.assertIn('amount',d['ambiguity'])
        a=self.store.review(d['id'],'correct',1,'Reviewer','Confirmed first total against components',{'amount':'$100,000.00'})
        self.assertEqual(a['ambiguity'],[]);self.assertEqual(a['issues'],[])

    def test_export_precision_and_formula_escaping(self):
        self.approve(self.add(DIST.replace('Harbor Family Office','=Imported name')))
        rows=list(csv.DictReader(io.StringIO(self.store.export())))
        self.assertEqual(rows[0]['amount'],'42750.25');self.assertEqual(rows[0]['signed_cash_flow'],'42750.25')
        self.assertTrue(rows[0]['investor'].startswith("'="))

    def test_original_pdf_is_retained(self):
        from reportlab.pdfgen import canvas
        out=io.BytesIO();c=canvas.Canvas(out)
        y=800
        for line in CALL.splitlines():c.drawString(30,y,line);y-=16
        c.save();raw=out.getvalue();text,error=pdf_text(raw)
        self.assertIsNone(error);r=self.store.import_notice('original.pdf',text,'pdf',error,raw)
        with self.store.db() as db:stored=db.execute('SELECT data FROM source_files WHERE document_id=?',(r['id'],)).fetchone()[0]
        self.assertEqual(stored,raw);self.assertEqual(self.store.get(r['id'])['issues'],[])

    def test_pdf_without_text_is_a_failed_job(self):
        from pypdf import PdfWriter
        w=PdfWriter();w.add_blank_page(width=600,height=800);out=io.BytesIO();w.write(out)
        raw=out.getvalue();text,error=pdf_text(raw);self.assertTrue(error)
        r=self.store.import_notice('scan.pdf',text,'pdf',error,raw)
        self.assertEqual(self.store.get(r['id'])['status'],'failed')
        self.assertEqual(self.store.snapshot()['jobs'][0]['status'],'failed')

if __name__=='__main__':unittest.main()

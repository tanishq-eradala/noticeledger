"""Synthetic examples, including deliberate extraction and arithmetic exceptions."""
CALL = '''Northstar Growth Fund IV
Capital Call Notice
Fund: Northstar Growth Fund IV
Investor: Harbor Family Office
Currency: USD
Notice date: 2026-10-06
Due date: 2026-10-20
Investment amount: $95,000.00
Management fee: $5,000.00
Total amount: $100,000.00
Please reference this notice when arranging the capital contribution.
Synthetic portfolio demonstration. No payment instructions.
'''
DIST = '''Distribution notice
Fund: Alder Infrastructure II
Investor: Harbor Family Office
Currency: USD
Notice date: October 7, 2026
Payment date: October 15, 2026
Distribution amount: $42,750.25
Recallable amount: $12,500.00
Synthetic portfolio demonstration. No payment instructions.
'''
EXAMPLES = [
    ('northstar-call-1006.txt', CALL),
    ('alder-distribution-1007.txt', DIST),
    ('cedar-call-reconciliation.txt', CALL.replace('Northstar Growth Fund IV','Cedar Ventures III').replace('$100,000.00','$101,000.00')),
    ('juniper-missing-date.txt', CALL.replace('Northstar Growth Fund IV','Juniper Credit Partners').replace('Due date: 2026-10-20\n','')),
    ('northstar-call-forwarded.txt', CALL + '\nForwarded to operations for review.\n'),
    ('meridian-euro-distribution.txt', DIST.replace('Alder Infrastructure II','Meridian Opportunities I').replace('USD','EUR').replace('$','€')),
]

def seed(store):
    if not store.documents():
        for name,text in EXAMPLES: store.import_notice(name,text)

"""Conservative notice extraction with exact cents and source-line evidence."""
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

ALIASES = {
    'fund': ['fund', 'fund name'],
    'investor': ['investor', 'lp', 'limited partner'],
    'notice_type': ['notice type', 'transaction type'],
    'currency': ['currency'],
    'notice_date': ['notice date', 'issue date'],
    'effective_date': ['due date', 'payment date', 'effective date'],
    'amount': ['total amount', 'call amount', 'distribution amount', 'lp amount'],
    'investment_amount': ['investment amount', 'investment component'],
    'fee_amount': ['management fee', 'fee amount'],
    'recallable_amount': ['recallable amount', 'recallable distribution'],
}
FIELDS = tuple(ALIASES)
MONEY_FIELDS = ('amount', 'investment_amount', 'fee_amount', 'recallable_amount')

def money(raw):
    value = str(raw).strip()
    value = re.sub(r'^(USD|EUR|GBP)\s*', '', value, flags=re.I)
    value = re.sub(r'\s*(USD|EUR|GBP)$', '', value, flags=re.I)
    value = re.sub(r'^[$€£]\s*', '', value)
    # Reject malformed grouping, exponent notation and rounding rather than guessing.
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?', value):
        raise ValueError('Use a nonnegative amount with at most two decimal places.')
    try:
        result = Decimal(value.replace(',', ''))
    except InvalidOperation as exc:
        raise ValueError('Invalid amount.') from exc
    if result > Decimal('1000000000000'):
        raise ValueError('Amount exceeds the supported range.')
    return int(result * 100)

def date_value(raw):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError('Use YYYY-MM-DD, a US month/day/year date or a spelled-out month.')

def extract(text):
    fields, evidence, ambiguity = {}, {}, []
    for field, labels in ALIASES.items():
        pattern = re.compile(r'^\s*(?:' + '|'.join(map(re.escape, labels)) + r')\s*[:=]\s*(.*?)\s*$', re.I)
        hits = [(i, line, m.group(1)) for i, line in enumerate(text.splitlines(), 1)
                if (m := pattern.match(line))]
        if hits:
            fields[field] = hits[0][2]
            evidence[field] = [{'line': i, 'text': line, 'value': v} for i, line, v in hits]
            if len({v.casefold() for _, _, v in hits}) > 1:
                ambiguity.append(field)
    if not fields.get('notice_type'):
        call = bool(re.search(r'\bcapital call\b', text, re.I))
        distribution = bool(re.search(r'\bdistribution notice\b', text, re.I))
        if call != distribution:
            fields['notice_type'] = 'capital_call' if call else 'distribution'
            line = next((i for i, t in enumerate(text.splitlines(), 1)
                         if re.search(r'capital call|distribution notice', t, re.I)), 1)
            evidence['notice_type'] = [{'line': line, 'text': text.splitlines()[line-1], 'value': fields['notice_type']}]
    fields['notice_type'] = re.sub(r'[\s-]+', '_', fields.get('notice_type', '').lower())
    fields['currency'] = fields.get('currency', '').upper()
    for field in ('notice_date', 'effective_date'):
        if fields.get(field):
            try: fields[field] = date_value(fields[field])
            except ValueError: pass  # Retain the original for review.
    return fields, evidence, ambiguity

def validate(fields, ambiguity=()):
    errors, normalized = [], dict(fields)
    def issue(field, code, message):
        errors.append({'field': field, 'code': code, 'message': message})
    for field in ('fund', 'investor', 'notice_type', 'currency', 'notice_date', 'effective_date', 'amount'):
        if not str(fields.get(field, '')).strip(): issue(field, 'missing', 'This required value was not found.')
    for field in ambiguity:
        issue(field, 'ambiguous', 'Conflicting source values. Confirm and save the correct value.')
    for field in ('fund', 'investor'):
        normalized[field] = ' '.join(str(fields.get(field, '')).split())
        if len(normalized[field]) > 200: issue(field, 'length', 'Use at most 200 characters.')
    if fields.get('notice_type') not in ('capital_call', 'distribution'):
        issue('notice_type', 'type', 'Only capital calls and distributions are supported.')
    if fields.get('currency') not in ('USD', 'EUR', 'GBP'):
        issue('currency', 'currency', 'Supported currencies are USD, EUR and GBP.')
    for field in ('notice_date', 'effective_date'):
        if fields.get(field):
            try: normalized[field] = date_value(fields[field])
            except ValueError as exc: issue(field, 'date', str(exc))
    if not any(e['field'] in ('notice_date', 'effective_date') for e in errors):
        if normalized.get('effective_date', '') < normalized.get('notice_date', ''):
            issue('effective_date', 'date_order', 'The due/payment date precedes the notice date.')
    for field in MONEY_FIELDS:
        raw = fields.get(field, '')
        if raw == '' or raw is None:
            normalized[field + '_minor'] = None
            continue
        try:
            normalized[field + '_minor'] = money(raw)
            symbols = {'$': 'USD', '€': 'EUR', '£': 'GBP'}
            codes = re.findall(r'\b(?:USD|EUR|GBP)\b', str(raw).upper())
            codes += [v for k, v in symbols.items() if k in str(raw)]
            if any(c != fields.get('currency') for c in codes):
                issue(field, 'currency_mismatch', 'The amount currency disagrees with the notice currency.')
        except ValueError as exc:
            issue(field, 'amount', str(exc))
            normalized[field + '_minor'] = None
    total = normalized.get('amount_minor')
    if total is not None and total <= 0: issue('amount', 'positive', 'The total amount must be greater than zero.')
    investment, fees = normalized.get('investment_amount_minor'), normalized.get('fee_amount_minor')
    if fields.get('notice_type') == 'capital_call' and (investment is not None or fees is not None):
        if investment is None or fees is None:
            issue('amount', 'components', 'Both investment and fee components are needed for reconciliation.')
        elif total is not None and investment + fees != total:
            issue('amount', 'reconciliation', 'Investment plus fee components do not equal the total amount.')
    recallable = normalized.get('recallable_amount_minor')
    if recallable is not None:
        if fields.get('notice_type') != 'distribution': issue('recallable_amount', 'type', 'Recallable amounts apply to distributions.')
        elif total is not None and recallable > total: issue('recallable_amount', 'range', 'Recallable amount exceeds the distribution.')
    return errors, normalized

def fingerprint(normalized):
    # Currency and direction remain separate; dates alone are insufficient.
    values = [normalized.get(k) for k in ('fund', 'investor', 'notice_type', 'currency', 'notice_date', 'effective_date', 'amount_minor')]
    values = [' '.join(v.casefold().split()) if isinstance(v, str) else v for v in values]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()

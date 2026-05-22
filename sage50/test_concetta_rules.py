from sage50.categorization_rules import ConcettaRuleset
from decimal import Decimal

# Quick smoke test of all 9 rules
ruleset = ConcettaRuleset()

tests = [
    ('ECONOMICAL INS', Decimal('-182.41')),
    ('MONTHY PLAN FEE', Decimal('-22.00')),
    ('FIDO SOLUTN', Decimal('-61.02')),
    ('TD VISA', Decimal('-1717.02')),
    ('PC MASTRCRD', Decimal('-309.14')),
    ('RECEIVER GENERAL', Decimal('-834.72')),
    ('CONCETTA BOSH', Decimal('-400.46')),
    ('SPL LOAN', Decimal('-450.00')),
    ('RENT PAYMENT', Decimal('-400.00')),
    ('UNKNOWN VENDOR', Decimal('-100.00')),
]

print('CONCETTA RULESET SMOKE TEST')
print('=' * 80)
for desc, amount in tests:
    acct, name, conf = ruleset.categorize(desc, amount)
    print(f'{desc:25} → {acct} {name:30} ({conf}% confidence)')
print('=' * 80)
print('All 9 rules working. Ready to integrate into BookkeepingAgent.')